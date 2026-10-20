---
id: data-curation
module: training-adaptation
name: Data curation
kind: practice
difficulty: core
est_minutes: 16
related:
  - training-adaptation/data-labeling
  - training-adaptation/synthetic-data-generation
  - training-adaptation/train-val-test-split
  - training-adaptation/supervised-fine-tuning
  - ml-fundamentals/train-test-leakage
  - evals/dataset-contamination
  - data-architecture/training-data-pipelines
  - safety-security/pii-redaction
sources:
  - Lee et al., 2022 — Deduplicating Training Data Makes Language Models Better
  - Zhou et al., 2023 — LIMA: Less Is More for Alignment
  - Rae et al., 2021 — Scaling Language Models: Methods, Analysis & Insights from Training Gopher
  - Penedo et al., 2024 — The FineWeb Datasets: Decanting the Web for the Finest Text Data at Scale
---

## TL;DR
Data curation is the deliberate selection, cleaning, deduplication, decontamination and mixing of what a
model trains on. It exists because gradient descent learns whatever is in the batch, boilerplate,
duplicates, leaked test sets and labelling noise included, and no architecture or hyperparameter choice
recovers what bad data teaches. For fine-tuning, a few thousand carefully chosen examples routinely beat a
hundred thousand scraped ones; for pretraining, filtering and deduplication are worth more than extra
tokens.

## Why it exists
A language model is a compressed copy of its training distribution. That sentence sounds harmless until
you look at what a raw crawl or a raw log export contains: navigation menus repeated across ten thousand
pages, the same licence header in every file, cookie banners, SEO spam, half-translated product listings,
test questions from the benchmarks you plan to report, customer emails with phone numbers, and support
transcripts where the agent's "resolution" was wrong. Train on it and the model learns all of it with
equal diligence. Duplicates are the quietest problem: a passage repeated a thousand times is not "more
evidence", it is a thousand gradient steps toward memorising that passage, and the reported effect of
deduplicating a web corpus was both better perplexity and an order-of-magnitude drop in verbatim
regurgitation.

The naive response, "get more data", makes it worse: more of the same distribution, more compute burned
on repeats, and no fix for contamination. The second naive response, "the model is big enough to figure
out what is good", is contradicted by every published ablation: the same architecture trained on a
filtered subset of a corpus beats the one trained on the whole corpus on knowledge and reasoning
benchmarks, by margins that would otherwise take a substantially larger model or far more tokens to buy.

For fine-tuning the lesson is even sharper. LIMA fine-tuned a 65B model on about a thousand curated
prompt–response pairs and was competitive with models trained on orders of magnitude more, the argument
being that alignment mostly teaches format and style, and that a small set with diverse prompts and
uniformly excellent responses teaches it cleanly, while a huge noisy set teaches the average of its
noise. Curation is the lever that decides what the model becomes; architecture decides how well it can
become it.

## How it works
A curation pipeline is a sequence of filters, each cheap enough to run on everything that survives the
previous one, with the expensive judgement calls last. The order matters because every stage is also a
place where good data is lost, so you measure drop rates per stage and sample the rejects by hand.

### 1. Provenance and permission
Record source, licence, collection date and a content hash for every document before anything else,
because you will need to delete by source (a takedown, a customer leaving), reweight by source, and
explain where a behaviour came from. Strip or pseudonymise PII at this stage with pattern rules plus a
named-entity model; a model trained on real emails will emit real emails.

### 2. Extraction and format normalisation
Pull main text out of HTML, fix encodings, identify language with a fast classifier and keep the ones you
intend to serve. For fine-tuning data, parse every example into one fixed structure (system, user,
assistant turns) and one template, and drop anything that does not parse, because the model learns the
template as rigidly as the content.

### 3. Heuristic quality filters
Cheap rules that remove boilerplate with high precision: document length bounds, mean word length in a
sane range, a cap on the symbol-to-word ratio, a cap on the fraction of lines that are bullets or that
end in an ellipsis, a requirement that common function words appear, a requirement that lines end in
terminal punctuation. These are the Gopher and C4 style rules, and they work because machine-generated
junk and navigation chrome fail them while prose passes. They are deliberately blunt; some rules
(bad-word lists, punctuation requirements) over-remove dialects and informal text, which is a bias you
must know you are introducing.

### 4. Deduplication
Exact duplicates fall to a hash of normalised text. Near-duplicates need MinHash: represent each document
as its set of word 5-gram shingles, apply *k* hash functions (128 is typical) and keep each function's
minimum, which gives a signature whose per-position match probability equals the Jaccard similarity of
the shingle sets. Comparing all pairs is O(n²), impossible at billions of documents, so locality-sensitive
hashing splits the signature into *b* bands of *r* rows and only compares documents that collide in at
least one band. The probability two documents with similarity *J* become candidates is
1 − (1 − Jʳ)ᵇ: with *r* = 8, *b* = 16 that is about 0.95 at *J* = 0.8 and 0.06 at *J* = 0.5, an S-curve
with its threshold near (1/*b*)^(1/*r*) ≈ 0.71. Within-document repeated substrings (boilerplate pasted
into otherwise unique pages) need a suffix-array pass. For fine-tuning sets, deduplicate *prompts*, not
just whole examples, and add an embedding-based pass to catch paraphrases.

### 5. Decontamination
Index every evaluation set you will ever report and remove training documents that share long n-grams
with any of them; 13-gram overlap was the GPT-3 rule and 8–13 remains common. Do this for your internal
golden sets as well as public benchmarks, because the goal is a valid measurement, not a clean public
record. N-gram overlap misses paraphrases and translations, so a similarity search against eval
embeddings is a worthwhile second pass.

### 6. Model-based quality scoring
Heuristics cannot judge content. Train a small classifier on "good" versus "random" (early LLM corpora used
reference text as the positive class), or have a strong LLM score a sample for educational value and
distil that into a fast classifier run over everything, as FineWeb-Edu did; keeping only high scores gave
large gains on knowledge benchmarks. The cost is the labeller's taste imported at scale: a classifier
tuned for textbook prose will quietly down-weight code, forums and dialects unless you score those
domains separately.

### 7. Mixing and curriculum
Decide weights per source (web, code, books, maths, your domain). Small high-quality sources are
up-sampled, with the caveat that repeating data beyond a few epochs returns little; learned weighting
with a proxy model exists but most teams tune mixes empirically on a small run. Pretraining runs commonly
anneal onto the highest-quality slice at the end. For fine-tuning, balance task types and response lengths
to match what you want the model to do in production, because the model learns the length distribution
as surely as the content.

### 8. Fine-tuning specifics
Verify responses where you can (execute code, check answers), remove hedging or refusals you do not want,
enforce one style, check inter-annotator agreement on a sample, and look at the data: a few hundred
examples read by a human expose systematic problems no metric reports. Then version the dataset, pin it to
the model it produced, and feed error analysis from evals back into the next version.

## Example
A compact curation pass over raw documents with Gopher-style heuristics, exact deduplication, MinHash/LSH
near-deduplication via `datasketch`, and 13-gram decontamination against evaluation sets. Each stage
returns early so you can count what it dropped.

```python
import re, hashlib
from datasketch import MinHash, MinHashLSH

def shingles(text, n=5):
    toks = re.findall(r"\w+", text.lower())
    return {" ".join(toks[i:i + n]) for i in range(max(1, len(toks) - n + 1))}

def passes_heuristics(text):
    w = re.findall(r"\w+", text)
    if not 50 <= len(w) <= 100_000: return False                   # length bounds
    if not 3 <= sum(map(len, w)) / len(w) <= 10: return False      # mean word length
    lines = [l for l in text.splitlines() if l.strip()]
    if sum(l.lstrip().startswith(("-", "*", "•")) for l in lines) > 0.9 * len(lines): return False
    if sum(l.rstrip().endswith("...") for l in lines) > 0.3 * len(lines): return False
    return True

def curate(docs, eval_texts, num_perm=128, threshold=0.7):
    eval_grams = set().union(*(shingles(t, 13) for t in eval_texts))   # decontamination index
    seen, lsh, kept, dropped = set(), MinHashLSH(threshold=threshold, num_perm=num_perm), [], {}
    for i, text in enumerate(docs):
        if not passes_heuristics(text): dropped["heuristic"] = dropped.get("heuristic", 0) + 1; continue
        h = hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()
        if h in seen: dropped["exact"] = dropped.get("exact", 0) + 1; continue
        seen.add(h)
        if shingles(text, 13) & eval_grams: dropped["contaminated"] = dropped.get("contaminated", 0) + 1; continue
        m = MinHash(num_perm=num_perm)
        for g in shingles(text): m.update(g.encode())
        if lsh.query(m): dropped["near_dup"] = dropped.get("near_dup", 0) + 1; continue
        lsh.insert(str(i), m)
        kept.append(text)
    return kept, dropped

# kept, dropped = curate(raw_docs, eval_texts); print(len(kept), dropped)
# Then: read 100 random rejects per stage and 100 random keeps, run a quality classifier on the keeps,
# and only afterwards split train/validation, so near-duplicates cannot straddle the split.
```

## Trade-offs
- Aggressive filtering raises average quality but shrinks volume and narrows diversity (dialects, code,
  niche domains); when you are data-constrained, a cleaner smaller set repeated a few times beats a larger
  noisy one, but beyond a few repeats more fresh data wins again.
- A low near-duplicate threshold removes legitimate templated text such as contracts and forms, which you
  may want the model to learn; a high threshold keeps near-copies that drive memorisation. Tune per source.
- Model-based scoring judges content far better than heuristics but imports the labeller's preferences at
  scale, costs a classifier pass over everything, and can over-select benchmark-like prose.
- Decontamination protects eval validity, but it also deletes genuinely useful documents that happen to be
  quoted in benchmarks, and it cannot catch paraphrased leaks without an embedding pass.
- A small curated SFT set is cheap and effective for teaching format, tone and task structure, but it
  cannot inject much new knowledge; for that, volume and pretraining-style data come back into play.

## Pitfalls
- Deduplicating only exact matches: near-duplicate boilerplate survives, so the symptom is a model that
  regurgitates licence headers, cookie notices and canned openings, and inflated validation scores.
- Splitting train/validation before deduplicating: near-duplicates straddle the split, so validation loss
  looks excellent and production performance does not match it.
- Selecting examples by length or by surface polish: the model learns to be long and ornate, so the
  symptom is responses that grew after fine-tuning without any gain on task metrics.
- Mixing templates (some examples with system prompts, some without; two different turn formats): the
  symptom is unpredictable adherence to instructions depending on the prompt's shape.
- Decontaminating against public benchmarks only: the internal golden set leaks in, so your own numbers
  are the ones you cannot trust.
- Training on logs without PII scrubbing: the symptom is the model emitting real names, emails or account
  numbers, found by a customer or an auditor.
- Discarding rejected examples without the reason: you cannot audit why a behaviour is missing, tune a
  threshold, or explain to a reviewer what was excluded.

## When to use
Curation is not optional; what varies is the effort. A one-off fine-tune on a few thousand examples
deserves a human read of all of them, a dedup pass and template enforcement. A production pipeline that
regenerates training sets from logs deserves the full staged pipeline with per-stage drop metrics,
contamination checks against every eval set, PII scrubbing and dataset versioning. Measure drop rate per
stage, duplicate rate, contamination hits, inter-annotator agreement on labels, and the downstream eval
delta between dataset versions, which is the only number that matters in the end. Day to day this is
scripts over JSONL, a dashboard of stage counts and a habit of reading samples; in interviews, expect to
explain MinHash and LSH, why deduplication reduces memorisation, what LIMA showed, and how you would
decontaminate.

## Role lens
- **Engineer:** Owns the filter, dedup and decontamination code, versions each dataset with its stage
  counts, and reads samples of what every stage rejected before trusting the pipeline.
- **Architect:** Sets the data policy: licensing and PII rules, which eval sets are protected by
  decontamination, retention and deletion paths, and the versioning contract between datasets and models.
- **FDE:** Turns a customer's messy export (tickets, documents, logs) into a clean, deduplicated,
  de-identified training set inside their environment and documents exactly what was removed and why.

## Check yourself
**Q:** You inherit 200,000 customer support conversations to fine-tune a drafting model. What do you do
before you train, and what do you measure to know the data is ready?
**A:** First provenance and PII: confirm the data may be used, then scrub names, emails, phone and account
numbers. Parse every conversation into one turn template and drop the unparseable. Filter by heuristics
(empty or one-line replies, macros, auto-responses) and by outcome where available, keeping conversations
with good customer ratings or verified resolutions. Deduplicate exactly, then near-deduplicate with
MinHash on both whole conversations and customer prompts, since macros produce thousands of near-copies.
Decontaminate against the evaluation set built with the support leads. Read a few hundred examples with
an SME and label a sample for quality to check agreement. Balance ticket categories and response lengths
to the production distribution, then version the set. I would measure per-stage drop counts, duplicate
rate, contamination hits, agreement on the quality sample, and finally the eval delta versus the base
model, which decides whether the data is ready.

## Quiz
### Q: What problem do MinHash and locality-sensitive hashing solve together?
- [ ] Removing PII from documents at scale
- [x] Finding near-duplicate documents without comparing every pair
- [ ] Scoring documents for educational quality
- [ ] Measuring n-gram overlap with evaluation sets
> MinHash signatures estimate Jaccard similarity, and LSH banding only compares documents that collide in
> a band, turning an O(n²) pairwise search into a near-linear candidate-generation step.

### Q: Why can 13-gram overlap decontamination still miss contamination?
- [ ] Because 13 is too short to match any real sentence
- [x] Because paraphrased or translated copies of a test item share no long exact n-gram with it
- [ ] Because evaluation sets are never public
- [ ] Because n-gram checks only run on the validation split
> Exact n-gram matching catches verbatim leaks. A benchmark question rewritten in different words, or
> translated, carries the same answer with no shared 13-gram, so an embedding-similarity pass is needed.

### Q: What did LIMA demonstrate about fine-tuning data?
- [ ] That more instruction data always improves alignment
- [ ] That synthetic data outperforms human-written data
- [x] That around a thousand diverse, uniformly high-quality examples can teach format and style competitively
- [ ] That pretraining data should be deduplicated before fine-tuning
> LIMA's claim is that alignment mostly teaches format and style, so a small set with diverse prompts and
> excellent responses suffices; large noisy sets teach the average of their noise.
