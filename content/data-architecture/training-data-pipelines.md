---
id: training-data-pipelines
module: data-architecture
name: Training data pipelines
kind: pattern
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - training-adaptation/data-curation
  - training-adaptation/synthetic-data-generation
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/data-labeling
  - evals/dataset-contamination
  - data-architecture/model-and-dataset-provenance
  - data-architecture/lakehouse
  - model-internals/tokenization
sources:
  - Lee et al., 2021 — Deduplicating Training Data Makes Language Models Better
  - Penedo et al., 2024 — The FineWeb Datasets: Decanting the Web for the Finest Text Data at Scale
  - Raffel et al., 2020 — Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer
  - Brown et al., 2020 — Language Models are Few-Shot Learners
---

## TL;DR
A training data pipeline turns raw sources into versioned, deduplicated, decontaminated, token-ready
shards with full provenance. Its architecture matters because what you *remove* (duplicates, eval
leakage, junk, PII) shapes the model at least as much as what you add, because every run must be
reproducible from a dataset hash, and because tokenizing, packing and globally shuffling raw text at
training time can starve the GPUs. This lesson covers the pipeline; the curation, labeling and synthetic-data lessons in Training &
adaptation cover what makes an individual example good.

## Why it exists
The naive pipeline is "export a JSONL file and point the trainer at it". It fails in ways that are
invisible in the loss curve and expensive to discover afterwards.

Duplicates are the first problem. Web and enterprise corpora contain the same page, template or ticket
thousands of times. A model trained on them sees those sequences many more times than the rest of the
data, memorizes them, and spends capacity on boilerplate; Lee et al. found that near-duplicate removal
made models emit memorized training text roughly ten times less often and reach the same or better
accuracy in fewer training steps.
Contamination is the second: if the eval set's questions leaked into training through a scraped forum or
an internal doc, the benchmark score is a measure of memorization, and the regression you see at
deployment has no explanation in your dashboards. Third, formatting noise: SFT examples assembled by hand
with inconsistent chat templates, stray system prompts or missing end-of-turn tokens teach the model the
noise, and the symptom is a fine-tuned model that stops at the wrong place or emits template fragments.
Fourth, reproducibility: without a dataset version and a deterministic shuffle, a loss divergence cannot
be reproduced, a resumed run does not match the original, and a regression cannot be attributed to a data
change. Fifth, throughput: tokenizing, packing and globally shuffling raw text inside the dataloader is
CPU- and I/O-bound work that can leave the accelerators idle in a sawtooth pattern, most visibly for
smaller models whose per-GPU token consumption is high.

The pipeline exists to make these properties structural rather than hoped for: filter, deduplicate,
decontaminate, format, tokenize, pack and shard offline, and record what came from where.

## How it works
### Stage order, and why
The stages run in an order chosen so that cheap operations shrink the data before expensive ones run.

1. **Sourcing and provenance.** Every source gets a manifest: origin, license or contractual basis,
   snapshot date, raw hash. This is the only point where that information is cheap to capture.
2. **Heuristic filtering.** Language identification, length bounds, symbol-to-word ratio, fraction of
   lines ending in terminal punctuation, repeated-line ratio, blocklisted domains. These are the C4 and
   Gopher-style rules: cheap per document, remove a large fraction of junk, and run first because
   everything after is costlier per byte.
3. **Deduplication.** Exact dedup by document hash catches byte-identical copies only. Near-duplicate
   detection uses MinHash: shingle each document into overlapping n-grams, hash each shingle with *k*
   hash functions and keep the minimum per function to form a signature, then band the signature with
   locality-sensitive hashing so documents sharing a band become candidate pairs, and cluster candidates
   with union-find, keeping one per cluster. It exists because exact hashing misses the same article with
   a different footer, and pairwise comparison of a billion documents is impossible.
4. **Decontamination.** Compare training text against every evaluation set the model will be judged on,
   using n-gram overlap (the GPT-3 report used 13-gram overlap; the right *n* is a judgement between
   missing paraphrases and deleting common phrases), and remove or flag overlapping training documents.
   This must be redone whenever an eval set is added.
5. **Model-based quality filtering.** A small classifier or a perplexity threshold from a reference model
   ranks remaining documents for "educational" or domain quality. It runs late because it is the most
   expensive per document and operates on the smallest set. The FineWeb work is a good public reference
   for how much each of these stages removes on web data.
6. **Safety and PII scrubbing.** Regex and NER passes for emails, keys, phone numbers, plus toxicity
   filters, with the policy decided by legal rather than by the pipeline author.
7. **Formatting.** For pretraining, documents are concatenated with an end-of-sequence token between
   them. For SFT, each example is rendered through the exact chat template the model will serve with,
   and the loss mask is computed so that only assistant tokens contribute to the loss; the prompt tokens
   get the ignore index. Doing this offline, deterministically, removes a whole class of template bugs.
8. **Tokenization and packing.** Tokenize offline and store token ids. Pack multiple short examples into
   fixed-length sequences to avoid padding waste (a batch of 4,096-token slots filled with 300-token
   examples is over 90 % padding otherwise). Packing requires either resetting position ids and masking
   attention across document boundaries or accepting a small amount of cross-document attention; which
   you choose depends on the trainer's support.
9. **Sharding and mixture.** Write fixed-size shards (Parquet, Arrow, WebDataset tar, or a streaming
   format such as MDS) with a shard index, so the loader can shuffle at shard level and within a buffer,
   resume from a global sample offset, and sample sources according to a mixture weight (for example
   up-weighting the customer's domain data relative to general text).
10. **Versioning.** A dataset version is the ordered list of shard hashes plus the pipeline config. The
    training run records that version; lineage goes from any training sample back to its source manifest.

### The dataloader contract
The loader's job at training time is reduced to reading shards, buffered shuffling with a seeded RNG, and
collation. Its throughput must exceed the model's consumption in tokens per second with headroom, which
is why the expensive work was moved offline. Prefetching and multiple workers handle the rest.

## Example
Offline SFT formatting and packing: render the chat template, mask prompt tokens with the ignore index,
pack examples into fixed-length sequences separated by EOS, and emit shards with a content hash for the
dataset manifest. The tokenizer is any Hugging Face tokenizer with a chat template.

```python
import hashlib, json
IGNORE, SEQ_LEN, SHARD_ROWS = -100, 4096, 2048

def render(tok, messages):
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full   = tok.apply_chat_template(messages, tokenize=False)
    enc = lambda s: tok(s, add_special_tokens=False)["input_ids"]   # template already has BOS
    p_ids, f_ids = enc(prompt), enc(full)
    assert f_ids[:len(p_ids)] == p_ids, "template must be prefix-stable"
    labels = [IGNORE] * len(p_ids) + f_ids[len(p_ids):]      # loss only on the final assistant turn
    return f_ids, labels

def pack(examples, tok):
    ids, labels, doc_ids = [], [], []
    for n, msgs in enumerate(examples):
        i, l = render(tok, msgs)
        if len(i) + 1 > SEQ_LEN:
            continue                                            # too long: drop (or truncate) and count it
        if len(ids) + len(i) + 1 > SEQ_LEN:
            yield ids, labels, doc_ids; ids, labels, doc_ids = [], [], []
        ids += i + [tok.eos_token_id]; labels += l + [tok.eos_token_id]; doc_ids += [n] * (len(i) + 1)
    if ids:
        yield ids, labels, doc_ids                              # trainer pads this last one

def write_shards(packed, path):
    manifest, rows = [], []
    def flush():
        blob = json.dumps(rows).encode(); name = f"{path}/shard-{len(manifest):05d}.json"
        with open(name, "wb") as f: f.write(blob)
        manifest.append({"shard": name, "rows": len(rows), "sha256": hashlib.sha256(blob).hexdigest()})
    for ids, labels, doc_ids in packed:
        rows.append({"input_ids": ids, "labels": labels, "doc_ids": doc_ids})  # doc_ids -> attention mask
        if len(rows) == SHARD_ROWS:
            flush(); rows = []
    if rows:
        flush()                                                 # keep the final partial shard
    with open(f"{path}/manifest.json", "w") as f:
        json.dump(manifest, f)                                  # the dataset version is this file's hash
```

The `doc_ids` column lets a trainer that supports it block attention across packed documents; the manifest
of shard hashes is what the training config references so the run is reproducible.

## Trade-offs
- Aggressive heuristic filtering removes junk cheaply but also removes legitimate code, tables and
  non-English text; the thresholds flip when the target domain is exactly the content the rules distrust.
- Near-duplicate removal improves generalization and reduces memorization, but a low similarity threshold
  deletes templated-but-distinct documents such as product pages, which can matter for a domain model.
- Packing eliminates padding waste and raises tokens per second, but without cross-document attention
  masking it lets examples attend to unrelated neighbours; for short SFT examples the masking is worth it.
- Offline tokenization maximizes training throughput and reproducibility, yet it binds the dataset to one
  tokenizer, so a tokenizer or model-family change means re-tokenizing from the text tables.
- Strict n-gram decontamination protects eval validity but at small *n* removes documents that merely
  share common phrases with the eval set, shrinking the training set for no gain.

## Pitfalls
- Decontaminating with exact match only; the symptom is a benchmark score that collapses on a freshly
  written eval set of the same difficulty.
- Rendering SFT examples with a template that differs from serving; the symptom is the fine-tuned model
  emitting role tags or failing to stop at the end of its turn.
- Tokenizing, packing or globally shuffling raw text inside the dataloader at scale; the symptom is GPU
  utilization oscillating between near zero and full while CPUs are saturated.
- Non-deterministic shuffling without a seeded, offset-addressable order; the symptom is a resumed run
  whose loss curve does not continue the original, and a divergence nobody can reproduce.
- Running near-dup detection before cheap filters; the symptom is MinHash jobs that take days on data
  most of which is then discarded by a one-line length rule.
- Dropping provenance at the first join; the symptom is a takedown or license question that can only be
  answered by retraining without the disputed source.

## When to use
Build this pipeline, at whatever scale, the moment training data is assembled from more than one source
or will be used more than once. For a fine-tune on a few thousand customer examples the stages are small
scripts, but the properties still matter: template-exact rendering, loss masking, dedup against the eval
set, and a manifest hash in the training config. Measure the fraction removed at each stage, the
near-duplicate cluster size distribution, overlap rate with each eval set, padding ratio after packing,
and loader tokens per second against model consumption. In interviews, "how would you prepare data to
fine-tune on a customer's support tickets" is answered by walking these stages, and the differentiating
points are decontamination against the eval set, prompt-token loss masking and a reproducible dataset
version.

## Role lens
- **Engineer:** Writes the filtering, MinHash dedup, template rendering and packing jobs, verifies the
  loss mask on a handful of examples by eye, and checks loader throughput before launching a run.
- **Architect:** Sets the stage order and the quality, PII and licensing policies, chooses the shard
  format and storage layout, and requires that every run references a hashed dataset version.
- **FDE:** Assembles customer data into the pipeline with provenance intact, agrees PII rules with the
  customer's legal team, and holds back the eval set from the data the customer hands over for training.

## Check yourself
**Q:** A fine-tuned support model scores well on the team's internal benchmark but performs no better than
the base model on live tickets, and it sometimes ends its replies with a stray assistant tag. Which
pipeline stages would you inspect first and why?
**A:** Two stages explain both symptoms. The benchmark gap points to contamination: the internal eval set
was probably drawn from the same ticket export as the training data, so near-identical tickets appear in
both and the benchmark measures memorization; check n-gram overlap between training shards and the eval
set and decontaminate or rebuild the eval from tickets dated after the export. The stray tag points to
formatting: examples were rendered with a chat template that differs from the serving template, or the
loss mask included prompt tokens so the model learned to reproduce template structure; inspect a few
rendered examples token by token, confirm the ignore index covers the prompt and that the end-of-turn
token terminates the labels. Near-duplicate inflation in the training set would compound both, so look at
the dedup cluster sizes as well.

## Quiz
### Q: Why do near-duplicate removal with MinHash rather than relying on exact document hashing?
- [ ] MinHash is faster than computing a SHA-256 per document
- [x] Most duplicates differ in small ways such as footers or timestamps, which exact hashing misses, and MinHash with LSH finds similar pairs without comparing every pair
- [ ] Exact hashing cannot be parallelized across shards
- [ ] MinHash also detects contamination against evaluation sets
> Real corpora contain the same content with trivial variations. MinHash signatures approximate shingle
> similarity, and LSH banding turns the impossible all-pairs comparison into candidate lookups, so near
> duplicates are found at scale; decontamination is a separate n-gram check.

### Q: In SFT formatting, what does setting prompt tokens' labels to the ignore index achieve?
- [ ] It removes the prompt from the model's context so it only sees the answer
- [ ] It prevents the tokenizer from splitting special template tokens
- [x] It excludes prompt tokens from the loss so the model learns to produce assistant turns rather than to reproduce prompts and templates
- [ ] It allows examples to be packed without attention masking
> The prompt stays in the input, so the model conditions on it, but gradients only flow from assistant
> tokens. Training on prompt tokens wastes capacity and teaches the model to emit template structure,
> which shows up as stray role tags at inference.

### Q: What is the strongest reason to tokenize and pack offline rather than in the training dataloader?
- [ ] Offline tokenization produces smaller files than raw text
- [ ] The trainer cannot apply chat templates at runtime
- [x] Tokenization and packing are CPU-bound; done at training time they bottleneck the loader and leave accelerators idle, and done offline they are also reproducible
- [ ] Packing is only possible when all examples are known in advance
> When accelerators consume tokens faster than the loader's CPU workers can tokenize and pack them, the
> result is a sawtooth utilization pattern; whether that happens depends on model size and worker count,
> but the reproducibility benefit holds regardless. Precomputed shards make the loader a simple reader and make the exact
> sequence of training tokens part of the versioned dataset.
