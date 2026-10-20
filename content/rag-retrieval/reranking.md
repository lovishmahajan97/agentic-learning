---
id: reranking
module: rag-retrieval
name: Reranking (cross-encoder)
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - rag-retrieval/hybrid-search
  - rag-retrieval/similarity-search
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/embedding-models
  - model-internals/self-attention
  - evals/retrieval-evaluation
  - infra-capacity-economics/latency-budgets
sources:
  - Nogueira & Cho, 2019 — Passage Re-ranking with BERT
  - Khattab & Zaharia, 2020 — ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT
  - Official docs: Sentence-Transformers cross-encoders
---

## TL;DR
A bi-encoder must compress each passage into one vector before it ever sees the query, so it cannot
model how specific query words interact with specific passage words. A cross-encoder reads query and
passage together in a single transformer pass and scores the pair directly, which is far more accurate
and far too slow to run over a corpus. Reranking resolves that: a cheap retriever pulls a few dozen
candidates, the cross-encoder reorders them, and only the top few reach the prompt.

## Why it exists
First-stage retrieval, whether BM25, dense or hybrid, is optimized for recall at scale, and the
architecture that makes it scale is also what limits its precision. A bi-encoder embeds the query and
each passage independently; the only interaction between them is one dot product at the end. Everything
the model could learn about "does this passage answer this question" had to be squeezed into a
fixed-size vector computed without knowing the question. Fine distinctions (the passage mentions the
right product but the wrong year, it discusses the topic without answering, it answers a negated
version of the question) are exactly what a single vector blurs.

The accurate alternative is to feed the query and the passage into the same transformer as one sequence
and let attention run across both. Every query token can attend to every passage token, so the model can
check the year, notice the negation and verify the answer is present. That model is a cross-encoder, and
the catch is cost: one full forward pass per (query, passage) pair, with nothing precomputable, since the
passage representation depends on the query. Scoring a million passages that way per query is impossible
in a serving path.

Two-stage retrieval is the resolution: spend the cheap, precomputable model on the whole corpus to find
candidates, then spend the expensive, accurate model on only those candidates. The reranker never has to
be fast over the corpus, only over fifty items.

## How it works
### The cross-encoder
Input is a single sequence: `[CLS] query [SEP] passage [SEP]`. The transformer encoder runs full
self-attention over the concatenation, so representations of passage tokens are conditioned on the query
and vice versa. A classification head on the `[CLS]` output (or a pooled representation) produces one
scalar: the relevance logit. Training uses labeled query–passage pairs (MS MARCO is the standard public
source) with a pointwise loss (relevant or not), a pairwise loss (the relevant passage must score above a
hard negative) or a listwise loss over a candidate set. The hard negatives matter: they are typically
passages a first-stage retriever ranked highly but which are wrong, which teaches the reranker precisely
the distinctions the retriever cannot make.

Contrast with the bi-encoder: there the passage tower runs once at index time and its output is stored;
the query tower runs once per query; the score is a dot product. Cross-encoder: nothing is stored; every
pair costs a forward pass over roughly |query| + |passage| tokens. For a 20-token query and 400-token
passages, reranking 50 candidates is 50 passes over about 420 tokens each, 21,000 tokens of encoder
compute per query, which with GPU batching is typically tens of milliseconds for a small model and more
for a large one (illustrative; measure yours).

### The pipeline
1. Retrieve the top-n candidates from the first stage (hybrid or dense), with n chosen so that recall@n
   is near its ceiling on your labeled set; 30 to 100 is typical.
2. Build n pairs and run the cross-encoder in one batch.
3. Sort by reranker score and keep the top k (3 to 10) for the prompt.
4. Optionally apply a score threshold to drop candidates the reranker considers irrelevant, so the LLM
   sees fewer distractors and a "no relevant context" path can trigger.

Step 1's n is the lever: too small and the reranker cannot recover what retrieval missed (it can only
reorder); too large and latency and cost grow for no gain, because recall@n has plateaued.

### Why it improves answers, not just rankings
Generation quality depends on what is at the top of the prompt. Rerankers push the passage that actually
answers the question into position 1 or 2, which matters because models use information at the start
(and end) of a long context more reliably than information buried in its middle, and they demote near-miss passages that share vocabulary but
not the answer, which are precisely the ones that induce confident wrong answers. Lowering k after
reranking also shortens prompts and cuts cost.

### Length and truncation
Cross-encoders have a maximum sequence length (512 tokens for BERT-derived models, longer for newer
ones). A passage longer than the limit minus the query is truncated silently, so the reranker scores
only its beginning. Keep chunks within the limit, or split long candidates into windows, score each
window and take the maximum.

### Alternatives on the cost–accuracy curve
- **Late interaction (ColBERT).** Stores one vector per passage token and scores by summing, over query
  tokens, the maximum similarity to any passage token. It keeps token-level matching while remaining
  precomputable, sitting between bi- and cross-encoders in both accuracy and cost, with a much larger
  index.
- **LLM rerankers.** Prompt a generative model with the query and candidate list and ask for an ordering
  or per-candidate relevance. Strongest on nuanced relevance, slowest and most expensive, and sensitive
  to candidate order in the prompt.
- **Hosted rerank APIs.** Several providers (Cohere, Voyage, Jina and others) expose a reranking model,
  typically cross-encoder style though architectures are not always disclosed, behind an endpoint; no GPU to run, but a network call per query and per-call pricing.

### Score semantics
A cross-encoder logit is comparable across candidates for the same query and, if the model was trained
pointwise with a sigmoid, loosely comparable across queries, which is what makes a "no good passage"
threshold possible. Calibrate it on labeled data; it is not a probability unless the training made it
one.

## Example
A two-stage pipeline with public Sentence-Transformers models: a small bi-encoder retrieves five
candidates by cosine, then an MS MARCO cross-encoder rescores each (query, passage) pair jointly. The
lexical distractors (passages about a different policy that share vocabulary with the query) tend to sit
high after stage one and drop after stage two.

```python
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder

passages = [
    "Returns are accepted within 30 days of delivery if the item is unopened.",
    "Laptops and tablets may be returned within 14 days; the box must be unopened.",
    "Opened software cannot be returned; unopened software follows the 30-day policy.",
    "Our 30-day price-match guarantee covers laptops bought in store or online.",
    "Return shipping labels can be printed from your order page.",
    "Warranty claims for laptops older than 30 days go through the manufacturer.",
]
query = "Can I return an unopened laptop after 20 days?"

bi = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

P = bi.encode(passages, normalize_embeddings=True)
q = bi.encode(query, normalize_embeddings=True)
stage1 = np.argsort(-(P @ q))[:5]                     # top-5 by cosine: cheap, precomputable
print("stage 1:", [passages[i][:40] for i in stage1])

pairs = [(query, passages[i]) for i in stage1]
scores = ce.predict(pairs)                            # one joint forward pass per pair
for i, s in sorted(zip(stage1, scores), key=lambda t: -t[1]):
    print(f"{s:+6.2f}  {passages[i][:60]}")
# Expect the 14-day laptop passage (the actual answer: no) at the top after reranking,
# and the generic 30-day and price-match passages, which share words but not the answer, lower.
```

## Trade-offs
- A cross-encoder adds precision the first stage cannot reach, but costs one transformer pass per
  candidate per query; the trade turns against it when the first stage is already precise at your k on
  labeled queries, or when the latency budget is single-digit milliseconds.
- Reranking more candidates raises the chance the right passage is reordered to the top, but only until
  recall@n plateaus; beyond that every extra candidate is pure latency and GPU cost.
- A larger reranker model scores more accurately, but the gain shrinks with candidate quality, so a small
  reranker on a good hybrid stage often beats a large reranker on a weak dense stage.
- Hosted rerank APIs remove the GPU and the model maintenance, but add a network hop, per-call cost and a
  data-egress question that some customers cannot accept; self-hosting wins when volume is high or data
  is sensitive.

## Pitfalls
- Reranking the top 5 and expecting better recall; the symptom is "the reranker did nothing" because the
  answer was never in the five. Rerankers reorder, they do not retrieve; widen n.
- Passing chunks longer than the reranker's maximum length; the symptom is long, relevant passages
  scoring low because only their first few hundred tokens were read.
- Using a reranker trained on web QA for a domain with its own notion of relevance (legal, clinical,
  code); the symptom is confident reordering that human reviewers disagree with. Validate with NDCG on
  your labels and fine-tune if needed.
- Letting reranker latency sit on the critical path unbatched; the symptom is p95 latency that scales
  with n because candidates were scored one at a time.
- Thresholding on raw logits copied from another model or dataset; the symptom is a "no relevant
  context" path that fires on everything or never, since score scales differ between rerankers.

## When to use
Add a reranker when retrieval evaluation shows the relevant chunk is usually in the top 30 to 50 but not
reliably in the top 5, which is the common state of a tuned hybrid system; it is often the cheapest
single improvement to answer quality. Skip it when first-stage precision is already high on your labels,
when the latency budget cannot absorb tens of milliseconds, or when the corpus is tiny enough that the
LLM can read everything. What to measure: NDCG@k or MRR before and after, recall@n at the chosen depth,
added p95 latency and per-query cost. Day to day, this is the n and k configuration, the batching code
and the length check; in interviews, the expected depth is explaining why a bi-encoder cannot do what a
cross-encoder does, and why that makes two stages necessary.

## Role lens
- **Engineer:** Wires the batched cross-encoder call between retrieval and prompt assembly, enforces the
  token-length limit on candidates, and tracks NDCG and p95 latency per deployment.
- **Architect:** Sets the candidate depth and the latency budget split between retrieval and reranking,
  and decides between a hosted rerank API and a self-hosted model on cost, volume and data residency.
- **FDE:** Shows a customer side-by-side top-5 lists before and after reranking on their own queries,
  which is usually the fastest way to earn the extra latency.

## Check yourself
**Q:** Your RAG system retrieves 50 hybrid candidates and reranks them, yet answer quality is unchanged
from the version without the reranker. Walk through how you would diagnose this before touching the
reranker model.
**A:** First check recall@50 on the labeled set: if the relevant chunk is missing from the 50 candidates
for most failing queries, the reranker has nothing to promote and the fix is first-stage retrieval or
chunking, not reranking. If recall@50 is high, look at the reranker's output directly: compute NDCG@5 or
MRR before and after reranking; no change suggests the reranker is not actually applied in the serving
path (wrong list passed, scores ignored, results re-sorted later by the original score), or that
candidates exceed its maximum length so it scores truncated text, or that the domain is far from the
reranker's training data. Then check prompt assembly: if all 50 chunks still go into the prompt rather
than the top 5, reranking only changed order inside a context the model cannot use well. Only after
those checks would I try a different or fine-tuned reranker.

## Quiz
### Q: Why is a cross-encoder more accurate than a bi-encoder at judging whether a passage answers a query?
- [ ] Because it uses a larger embedding dimension
- [x] Because attention runs jointly over query and passage tokens, so the model can compare specific words and constraints instead of two independently compressed vectors
- [ ] Because it is trained on more data by construction
- [ ] Because it computes cosine similarity with a learned temperature
> The bi-encoder's only interaction is a dot product between vectors computed without seeing each other;
> the cross-encoder conditions every token on the other side, which captures negation, exact entities
> and whether the answer is actually present. Model size and data are independent of the architecture.

### Q: Why can a cross-encoder not replace the first-stage retriever entirely?
- [ ] Its scores are not sortable because they are unnormalized logits
- [ ] It cannot process more than one passage per query
- [x] Its passage representations depend on the query, so nothing can be precomputed and every passage in the corpus would need a full forward pass per query
- [ ] It requires an inverted index that vector databases do not provide
> A bi-encoder's passage vectors are computed once and reused; a cross-encoder's are query-conditioned
> and must be recomputed for each query, which is linear in corpus size with a transformer pass per
> item. That is affordable for fifty candidates and impossible for millions.

### Q: A team increases the number of reranked candidates from 20 to 200 and sees no improvement in answer quality but a large latency increase. What does this most likely indicate?
- [ ] The reranker is overfitting to the larger candidate set
- [x] Recall of the first stage had already plateaued by 20 candidates, so the extra 180 contained almost no relevant passages to promote
- [ ] The reranker's maximum sequence length was exceeded by the larger batch
- [ ] Cross-encoders degrade as batch size grows because of attention dilution
> Reranking can only promote what retrieval returned; if the relevant chunk was already within the first
> 20 nearly every time, deeper candidate lists add cost without new relevant material. Batch size affects
> throughput, not per-pair scores, and sequence length is per pair, not per batch.
