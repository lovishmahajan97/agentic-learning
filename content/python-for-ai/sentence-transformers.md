---
id: sentence-transformers
module: python-for-ai
name: sentence-transformers
kind: tool
difficulty: core
est_minutes: 15
related:
  - python-for-ai/hugging-face
  - python-for-ai/pytorch
  - rag-retrieval/embedding-models
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/reranking
  - rag-retrieval/similarity-search
  - rag-retrieval/embedding-pipelines
  - model-internals/embeddings
sources:
  - Reimers & Gurevych, 2019 — Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks
  - Official docs: Sentence Transformers
---

## TL;DR
sentence-transformers is the standard Python library for turning text into fixed-size vectors whose
cosine similarity tracks meaning, and for scoring query-document pairs with cross-encoders. It wraps a
Hugging Face encoder with the pooling, normalisation and training losses that make its outputs usable
as embeddings. It exists because a raw transformer's token outputs are not good sentence vectors, and
comparing every pair through a full model does not scale to search.

## Why it exists
A BERT-style encoder produces one vector per token, not per sentence. The obvious shortcuts, taking the
`[CLS]` vector or averaging token vectors from a model trained only on masked-language modelling, give
vectors whose cosine similarity correlates poorly with semantic similarity; the model was never trained
to place paraphrases close together. The accurate alternative, feeding both sentences into one model
and classifying the pair (a cross-encoder), works well but costs a full forward pass per pair: to find
the best match for one query among a million documents you would run a million forward passes per
query.

Sentence-BERT's insight was to fine-tune the encoder in a siamese setup, two copies with shared
weights, so that each sentence is encoded independently and a simple similarity between the two vectors
is trained to reflect relatedness. Independent encoding is the key property: document vectors can be
computed once, stored in a vector index, and each query costs one forward pass plus a nearest-neighbour
lookup. The library grew around that idea and now packages the model loading, pooling, batching,
multi-GPU encoding, contrastive training losses and the cross-encoder rerankers you put after
retrieval. Without it, every team re-implements pooling and normalisation, and small differences (mean
versus CLS pooling, forgetting to normalise, missing query prefixes) silently degrade retrieval.

## How it works
### Bi-encoder: `SentenceTransformer`
A model is a pipeline of modules saved together: a Transformer module (a Hugging Face encoder with its
tokenizer and max sequence length), a Pooling module (mean of token vectors masked by attention, CLS, or
last-token for decoder-based embedders), and optionally a Dense projection and a Normalize step.
`encode(texts, batch_size=..., normalize_embeddings=True)` tokenises, sorts inputs by length to reduce
padding waste, runs batches on the chosen device under no-grad, pools and returns a NumPy array or
tensor of shape `[n, dim]`. Normalising to unit length means dot product equals cosine similarity,
which is what most vector indexes compute fastest.

### Prompts and asymmetry
Many retrieval models are trained asymmetrically: queries and passages get different instruction
prefixes (for example E5's `"query: "` and `"passage: "`). The library stores such prompts in the model
config and applies them via `prompt_name`, because forgetting the prefix measurably hurts retrieval for
those models while raising no error.

### Cross-encoder: `CrossEncoder`
A cross-encoder takes the concatenated `(query, document)` pair and outputs one relevance score.
Attention runs across both texts, so it captures interactions a dot product cannot, and it is usually
more accurate, but it cannot precompute anything. The standard pattern is retrieve-then-rerank: a
bi-encoder fetches the top 50-200 candidates cheaply, then a cross-encoder rescores only those.

### Training
Fine-tuning uses contrastive losses. `MultipleNegativesRankingLoss` takes (anchor, positive) pairs and
treats every other positive in the batch as a negative, so larger batches give more negatives for free;
this is why batch size matters more here than in classification. Recent versions train through a
`SentenceTransformerTrainer` built on the Hugging Face Trainer, with evaluators that report retrieval
metrics such as recall@k and NDCG on a held-out set.

### Truncation
Each model has a `max_seq_length`; longer inputs are silently truncated. That limit, not the model's
theoretical context, decides your chunk size.

## Example
Retrieve-then-rerank over a small corpus. `all-MiniLM-L6-v2` produces 384-dimensional vectors.

```python
from sentence_transformers import SentenceTransformer, CrossEncoder

docs = [
    "Refunds are issued within 14 days of a returned item being received.",
    "Our warehouse ships orders Monday to Friday.",
    "To reset your password, use the link on the sign-in page.",
    "Returned items must be unused and in original packaging.",
]
bi = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
doc_emb = bi.encode(docs, normalize_embeddings=True, batch_size=64)   # shape (4, 384), computed once

query = "how long until I get my money back after returning something"
q_emb = bi.encode(query, normalize_embeddings=True)
scores = doc_emb @ q_emb                       # dot product == cosine because vectors are unit length
top = scores.argsort()[::-1][:3]               # cheap first stage: top-k candidates

ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
pairs = [(query, docs[i]) for i in top]
rerank = ce.predict(pairs)                     # one forward pass per pair, only for k candidates
for i, s in sorted(zip(top, rerank), key=lambda t: -t[1]):
    print(f"{s:7.3f}  {docs[i]}")
print(bi.max_seq_length)                       # inputs beyond this many tokens are truncated
```

Cost shape: with N documents and k candidates, a query costs 1 bi-encoder pass plus k cross-encoder
passes, instead of N cross-encoder passes.

## Trade-offs
- Bi-encoders allow precomputed document vectors and millisecond search, but compress each text to one
  vector and miss fine query-document interactions; add a cross-encoder when top-k precision matters.
- Running embeddings locally avoids per-token API cost and keeps data in-house, but you own GPU capacity
  and batching; hosted embedding APIs win when volume is small or ops capacity is scarce.
- Small models (MiniLM-class) are fast enough for CPU, but larger or instruction-tuned embedders usually
  retrieve better; the gap must be measured on your queries, not read from a public leaderboard.
- Fine-tuning on in-domain pairs often beats a bigger generic model, but requires re-embedding the
  whole corpus with the new model, since vectors from different models are not comparable.

## Pitfalls
- Forgetting `normalize_embeddings=True` when the index uses dot product: longer or unusual texts get
  inflated scores, and the symptom is retrieval favouring the same few verbose documents for every query.
- Omitting a model's required query or passage prefix: no error, just noticeably worse recall than the
  model card claims.
- Chunks longer than `max_seq_length`: the tail is silently dropped, so facts near the end of a chunk
  are never retrievable.
- Mixing vectors from two model versions in one index after an upgrade: similarity scores become
  meaningless, and the symptom is a sudden, unexplained retrieval quality drop.
- Encoding one text at a time in a loop: throughput collapses because the GPU processes batches of one;
  pass lists and let `encode` batch.

## When to use
Reach for sentence-transformers when you need embeddings or reranking from open models: self-hosted
RAG, data that cannot leave the network, high-volume offline embedding where API costs add up, semantic
deduplication, clustering, or fine-tuning an embedder on your domain. Prefer a hosted embedding API when
volume is modest and you do not want to run GPUs. Before choosing a model, build a small labelled set of
real queries with their correct passages and measure recall@k and MRR for two or three candidates;
choose on that, and record the model name and version alongside every stored vector. In interviews,
explain bi-encoder versus cross-encoder cost and why in-batch negatives make batch size matter.

## Role lens
- **Engineer:** Embeds corpora in batches, applies correct prefixes and normalisation, adds a
  cross-encoder reranker, and fine-tunes with contrastive loss when generic models miss domain terms.
- **Architect:** Owns the embedding model choice and versioning policy, the re-index plan for upgrades,
  and the decision between self-hosted encoders and a hosted embedding API.
- **FDE:** Runs local embedding inside a customer's network when documents cannot be sent to an API,
  and evaluates candidate models on the customer's own queries before committing.

## Check yourself
**Q:** Your RAG system uses a bi-encoder for top-5 retrieval. Users complain answers cite related but
wrong passages. Why might adding a cross-encoder fix this, and how would you size it?
**A:** A bi-encoder compresses each passage into one vector before seeing the query, so it ranks by
topical closeness and can miss which passage actually answers the specific question. A cross-encoder
reads query and passage together, so attention can match details such as the exact entity, date or
negation. I would retrieve a larger candidate set, say top 50, with the bi-encoder to protect recall,
rerank with a cross-encoder and keep the top 5. Latency grows with the candidate count, so I would
measure recall@50 to make sure the right passage is in the pool, then tune the pool size against p95
latency and a precision metric on labelled queries.

## Quiz
### Q: Why can a bi-encoder search a million documents quickly while a cross-encoder cannot?
- [ ] Bi-encoders use smaller transformer models than cross-encoders
- [x] Bi-encoders encode documents independently of the query, so document vectors are precomputed once
- [ ] Cross-encoders cannot run on GPUs
- [ ] Bi-encoders skip tokenisation entirely
> Independent encoding lets you store document vectors and answer queries with one forward pass plus a
> vector lookup. A cross-encoder needs the query and each document together, so nothing can be precomputed.

### Q: With `MultipleNegativesRankingLoss`, why does increasing batch size often improve the embedder?
- [ ] Larger batches reduce the learning rate automatically
- [ ] Larger batches prevent truncation of long inputs
- [x] Every other positive in the batch acts as a negative, so larger batches supply more negatives per example
- [ ] Larger batches make embeddings higher-dimensional
> The loss contrasts each anchor with its positive against all other in-batch positives. More items per
> batch means a harder, more informative contrast; dimension and truncation are fixed by the model.

### Q: Dot-product scores in your index favour long documents regardless of the query. What is the likely cause?
- [ ] The cross-encoder was trained on short texts
- [ ] The model's max sequence length is too large
- [x] Embeddings were not normalised, so vector magnitude influences the score
- [ ] The tokenizer is case-sensitive
> Without unit normalisation, dot product mixes direction with magnitude, and magnitude can correlate
> with text properties. Normalising makes dot product equal cosine similarity, which compares direction only.
