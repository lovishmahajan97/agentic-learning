---
id: data-curation
module: training-adaptation
name: Data curation
kind: practice
difficulty: core
est_minutes: 16
status: crisp
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
Data curation is selecting, cleaning, deduplicating, decontaminating and mixing training data. Models learn
whatever is in the batch, so a few thousand curated fine-tuning examples beat a hundred thousand scraped
ones, and filtering beats extra pretraining tokens.

## Why it exists
- **A model is a compressed copy of its training distribution.** Gradient descent learns boilerplate,
  duplicates, leaked test sets and labelling noise with equal diligence, and no architecture or
  hyperparameter choice recovers what bad data teaches.
- **Raw data is full of junk.** A crawl or log export holds navigation menus repeated across ten thousand
  pages, the same licence header in every file, cookie banners, SEO spam, half-translated listings,
  benchmark test questions, customer emails with phone numbers, and transcripts where the agent's
  "resolution" was wrong.
- **Duplicates are the quietest problem.** A passage repeated a thousand times is a thousand gradient steps
  toward memorising it, not "more evidence". Deduplicating a web corpus gave better perplexity and an
  order-of-magnitude drop in verbatim regurgitation.
- **"Get more data" and "the model will figure it out" both fail.** More data means more of the same
  distribution, more compute on repeats, and no fix for contamination. Every published ablation shows the
  same architecture on a filtered subset beating the whole corpus on knowledge and reasoning, by margins
  that would otherwise cost a much larger model or far more tokens.
- **For fine-tuning it is sharper still.** LIMA fine-tuned a 65B model on about a thousand curated
  prompt–response pairs and was competitive with models trained on orders of magnitude more. Alignment
  mostly teaches format and style; a small diverse, uniformly excellent set teaches it cleanly, a huge noisy
  set teaches the average of its noise. Curation decides what the model becomes; architecture decides how
  well it can become it.

## How it works
A pipeline is a sequence of filters, each cheap enough to run on everything surviving the previous one,
with expensive judgement calls last. Every stage also loses good data, so measure drop rates per stage and
sample the rejects by hand.

| # | Stage | Main tool | Why here |
|---|---|---|---|
| 1 | Provenance and permission | metadata, content hash, PII scrub | needed for deletion and audit |
| 2 | Extraction and normalisation | HTML extraction, language ID, one template | everything later assumes clean text |
| 3 | Heuristic filters | Gopher/C4 rules | cheap, high precision |
| 4 | Deduplication | hash, MinHash + LSH, suffix arrays | stops memorisation |
| 5 | Decontamination | n-gram and embedding overlap | keeps evals valid |
| 6 | Model-based scoring | quality classifier | judges content, costly |
| 7 | Mixing and curriculum | per-source weights | sets what the model sees most |
| 8 | Fine-tuning specifics | verification, coverage sampling, reading | quality of each example |

### 1. Provenance and permission
- **Record first:** source, licence, collection date and a content hash for every document, because you
  will need to delete by source (a takedown, a customer leaving), reweight by source, and explain where a
  behaviour came from.
- **Scrub PII here:** pattern rules plus a named-entity model. A model trained on real emails will emit
  real emails.

### 2. Extraction and format normalisation
- Pull main text out of HTML, fix encodings, and identify language with a fast classifier, keeping the
  ones you intend to serve.
- **Fine-tuning data:** parse every example into one fixed structure (system, user, assistant turns) and
  one template, and drop anything that does not parse, because the model learns the template as rigidly as
  the content.

### 3. Heuristic quality filters
Cheap, high-precision rules that remove boilerplate (the Gopher and C4 style rules):
- document length bounds;
- mean word length in a sane range;
- a cap on the symbol-to-word ratio;
- a cap on the fraction of lines that are bullets or end in an ellipsis;
- common function words must appear;
- lines must end in terminal punctuation.

They work because machine-generated junk and navigation chrome fail them while prose passes. **Caveat:**
they are deliberately blunt; some rules (bad-word lists, punctuation requirements) over-remove dialects and
informal text, a bias you must know you are introducing.

### 4. Deduplication
1. **Exact duplicates:** a hash of normalised text.
2. **Near-duplicates with MinHash:** represent each document as its set of word 5-gram shingles, apply *k*
   hash functions (128 is typical), and keep each function's minimum. Each signature position matches with
   probability equal to the Jaccard similarity of the shingle sets.
3. **LSH banding:** all-pairs comparison is O(n²), impossible at billions of documents. Split the signature
   into *b* bands of *r* rows and compare only documents colliding in at least one band.
4. **Within-document repeats** (boilerplate pasted into otherwise unique pages) need a suffix-array pass.
5. **Fine-tuning sets:** deduplicate *prompts*, not just whole examples, and add an embedding pass to
   catch paraphrases.

The candidate probability for similarity *J* is an S-curve:

```
P(candidate) = 1 − (1 − Jʳ)ᵇ        threshold ≈ (1/b)^(1/r)
```

With *r* = 8, *b* = 16: about 0.95 at *J* = 0.8 and 0.06 at *J* = 0.5, threshold near
(1/*b*)^(1/*r*) ≈ 0.71.

### 5. Decontamination
- **Index every eval set you will ever report** and remove training documents sharing long n-grams with
  any of them. 13-gram overlap was the GPT-3 rule; 8–13 remains common.
- **Include internal golden sets**, not just public benchmarks: the goal is a valid measurement, not a
  clean public record.
- **Add an embedding pass.** N-gram overlap misses paraphrases and translations, so a similarity search
  against eval embeddings is a worthwhile second pass.

### 6. Model-based quality scoring
- **Why:** heuristics cannot judge content.
- **How:** train a small classifier on "good" versus "random" (early LLM corpora used reference text as the
  positive class), or have a strong LLM score a sample for educational value and distil that into a fast
  classifier run over everything, as FineWeb-Edu did. Keeping only high scores gave large gains on
  knowledge benchmarks.
- **Cost:** the labeller's taste is imported at scale. A classifier tuned for textbook prose quietly
  down-weights code, forums and dialects unless you score those domains separately.

### 7. Mixing and curriculum
- **Weights per source** (web, code, books, maths, your domain). Up-sample small high-quality sources, but
  repeating data beyond a few epochs returns little.
- **Tuning:** learned weighting with a proxy model exists, but most teams tune mixes empirically on a small
  run.
- **Annealing:** pretraining runs commonly anneal onto the highest-quality slice at the end.
- **Fine-tuning:** balance task types and response lengths to match production, because the model learns
  the length distribution as surely as the content.

### 8. Fine-tuning specifics
- **Verify** responses where you can (execute code, check answers).
- **Remove** hedging or refusals you do not want; **enforce** one style.
- **Check** inter-annotator agreement on a sample.
- **Select for coverage as well as quality:** embed prompts, cluster them, and sample across clusters,
  because a quality-only filter collapses onto the most common, easiest request types and leaves the long
  tail untaught.
- **Look at the data:** a few hundred examples read by a human expose systematic problems no metric
  reports.
- **Version** the dataset, pin it to the model it produced, and feed eval error analysis into the next
  version.

## Examples
### Compact curation pass
Gopher-style heuristics, exact deduplication, MinHash/LSH near-deduplication via `datasketch`, and
13-gram decontamination against eval sets. Each stage returns early so you can count what it dropped.

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

### Choose LSH bands for your duplicate threshold
Pure Python. Same 128 hash functions, three band layouts: the S-curve's threshold moves with *r* and *b*.

```python
def p_candidate(J, r, b):
    """Probability two docs with Jaccard J share at least one LSH band of r rows (b bands)."""
    return 1 - (1 - J ** r) ** b

for r, b in [(8, 16), (16, 8), (4, 32)]:
    thresh = (1 / b) ** (1 / r)
    row = "  ".join(f"J={J:.1f}:{p_candidate(J, r, b):.2f}" for J in (0.5, 0.6, 0.7, 0.8, 0.9))
    print(f"r={r:2d} b={b:2d} (k={r * b}) threshold~{thresh:.2f}  {row}")
# r= 8 b=16 (k=128) threshold~0.71  J=0.5:0.06  J=0.6:0.24  J=0.7:0.61  J=0.8:0.95  J=0.9:1.00
# r=16 b= 8 (k=128) threshold~0.88  J=0.5:0.00  J=0.6:0.00  J=0.7:0.03  J=0.8:0.20  J=0.9:0.81
# r= 4 b=32 (k=128) threshold~0.42  J=0.5:0.87  J=0.6:0.99  J=0.7:1.00  J=0.8:1.00  J=0.9:1.00
```

- **r = 16, b = 8** keeps templated contracts and forms that differ in a few fields.
- **r = 4, b = 32** catches loose paraphrases but floods you with candidates to verify.

### Worked scenario: a support-log funnel
An illustrative stage report for 200,000 support conversations (the numbers show the shape to expect, not
a benchmark):

| Stage | Dropped | Remaining | What a reject sample showed |
|---|---|---|---|
| Parse into one template | 6,000 | 194,000 | truncated exports, mixed formats |
| Heuristics (empty, one-line, auto-replies) | 38,000 | 156,000 | macros and "ticket closed" bots |
| Exact dedup | 21,000 | 135,000 | copy-pasted macro replies |
| MinHash near-dedup | 47,000 | 88,000 | the same macro with a name swapped |
| Decontamination vs eval set | 400 | 87,600 | eval tickets pulled from the same logs |
| Outcome filter (rated or resolved) | 52,000 | 35,600 | unresolved or low-rated threads |

The lesson: macros dominate support logs, so near-dedup and outcome filtering remove far more than
expected, and the eval set leaked because it was sampled from the same export.

## Production patterns
- **Per-stage drop dashboard plus reject sampling.** Emit counts per stage and source on every run and
  alert when a stage's drop rate shifts sharply between versions. Store each reject with its reason so
  thresholds can be tuned and audited.
- **Provenance manifest with deletion by source.** Keep a table of document hash, source, licence and
  collection date, so a takedown or a departing customer becomes a filter and a rebuild, not a forensic
  search. Monitor how many rows lack provenance.
- **Dedup before split, then split by group.** Deduplicate first, then split by conversation, customer or
  near-duplicate cluster (for example scikit-learn's `GroupShuffleSplit`) so near-copies cannot straddle
  train and validation.
- **Protected eval registry.** Every eval set, public or internal, is registered once and every training
  build is checked against it with n-gram and embedding overlap; the build fails on hits above a threshold.
  Track hits per eval set over time.
- **Datasets versioned like code.** Record a content hash of the final JSONL, the pipeline commit and the
  stage counts in the model's registry entry, so every model points to the exact data that made it.
- **Scale with existing tooling.** For web-scale corpora use a pipeline library built for it, such as
  Hugging Face's `datatrove`, which was used for FineWeb, instead of hand-rolled loops.

## Trade-offs
- **Quality vs volume and diversity.** Aggressive filtering raises average quality but shrinks volume and
  narrows diversity (dialects, code, niche domains). When data-constrained, a cleaner smaller set repeated
  a few times beats a larger noisy one; beyond a few repeats, fresh data wins again.
- **Dedup threshold: templates vs memorisation.** A low threshold removes legitimate templated text such as
  contracts and forms you may want learned; a high one keeps near-copies that drive memorisation. Tune per
  source.
- **Model scoring vs heuristics.** Far better at judging content, but it imports the labeller's
  preferences at scale, costs a classifier pass over everything, and can over-select benchmark-like prose.
- **Decontamination vs useful data.** It protects eval validity but deletes useful documents quoted in
  benchmarks, and cannot catch paraphrased leaks without an embedding pass.
- **Small curated SFT sets vs knowledge.** Cheap and effective for format, tone and task structure, but
  they cannot inject much new knowledge; for that, volume and pretraining-style data come back into play.

## Pitfalls
- **Deduplicating only exact matches.** Near-duplicate boilerplate survives. Symptom: the model
  regurgitates licence headers, cookie notices and canned openings, and validation scores are inflated.
- **Splitting train/validation before deduplicating.** Near-duplicates straddle the split. Symptom:
  excellent validation loss that production performance does not match.
- **Selecting by length or surface polish.** The model learns to be long and ornate. Symptom: responses
  grew after fine-tuning with no gain on task metrics.
- **Mixing templates.** Some examples with system prompts, some without, or two turn formats. Symptom:
  instruction adherence varies with the prompt's shape.
- **Decontaminating against public benchmarks only.** The internal golden set leaks in, so your own
  numbers are the ones you cannot trust.
- **Training on logs without PII scrubbing.** Symptom: the model emits real names, emails or account
  numbers, found by a customer or an auditor.
- **Discarding rejects without the reason.** You cannot audit a missing behaviour, tune a threshold, or
  explain to a reviewer what was excluded.

## When to use
Curation is not optional; what varies is the effort.
- **One-off fine-tune on a few thousand examples:** a human read of all of them, a dedup pass and template
  enforcement.
- **Production pipeline regenerating sets from logs:** the full staged pipeline with per-stage drop
  metrics, contamination checks against every eval set, PII scrubbing and dataset versioning.
- **Measure:** drop rate per stage, duplicate rate, contamination hits, inter-annotator agreement, and the
  downstream eval delta between dataset versions, the only number that matters in the end.
- **Day to day:** scripts over JSONL, a dashboard of stage counts and a habit of reading samples.
- **Interviews:** explain MinHash and LSH, why deduplication reduces memorisation, what LIMA showed, and
  how you would decontaminate.

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
