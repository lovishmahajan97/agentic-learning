---
id: embedding-models
module: rag-retrieval
name: Embedding models
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - model-internals/embeddings
  - rag-retrieval/embedding-dimension-and-normalization
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/similarity-search
  - rag-retrieval/dense-vs-sparse-retrieval
  - rag-retrieval/reranking
  - python-for-ai/sentence-transformers
  - evals/retrieval-evaluation
sources:
  - Reimers & Gurevych, 2019 — Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks
  - Karpukhin et al., 2020 — Dense Passage Retrieval for Open-Domain Question Answering
  - Wang et al., 2022 — Text Embeddings by Weakly-Supervised Contrastive Pre-training
  - Muennighoff et al., 2022 — MTEB: Massive Text Embedding Benchmark
---

## TL;DR
An embedding model is an encoder trained so that texts with similar meaning land close together in a
fixed-size vector space. It exists because keyword search cannot see that "money back" and "refund"
mean the same thing, and because scoring every document against the query with a full cross-attention
model is too slow. A bi-encoder embeds documents once, ahead of time, so retrieval becomes a
nearest-neighbour lookup. The price is that the model must be chosen, prefixed and versioned
consistently, or the index is silently wrong.

## Why it exists
Retrieval is a matching problem: given a query, find the passages that answer it. Lexical search (BM25)
matches tokens, so it fails whenever the asker and the author chose different words: "cancel my plan"
against "terminate the subscription", or a German question against English documentation. This
vocabulary mismatch is the most common reason a keyword system returns nothing useful.

The first fix people try is averaging word vectors (word2vec, GloVe). It captures some synonymy but
throws away order and context, so "the dog bit the man" equals "the man bit the dog", and frequent,
meaningless words dominate the average. The second try is a pretrained transformer such as BERT, taking
the [CLS] output or the mean of the token outputs. That is surprisingly bad for similarity: BERT was
trained to predict masked tokens, not to make sentence vectors comparable, so its raw outputs sit in a
narrow cone where everything is cosine-similar to everything. Sentence-BERT showed that fine-tuning
with a contrastive objective on sentence pairs fixes this, and every modern embedding model descends
from that recipe.

The remaining question is cost. A cross-encoder that reads the query and a candidate passage together
is the most accurate matcher available, but scoring a million passages means a million forward passes
per query. The bi-encoder embedding model is the design that gives up a little accuracy for the ability
to precompute the document side once and reuse it for every query.

## How it works
### Architecture: the bi-encoder
A text embedding model is a transformer encoder (BERT, RoBERTa or XLM-R style) or, increasingly, a
decoder-only LLM repurposed as an encoder. The text is tokenized, run through the network, and the
per-token outputs are pooled into a single vector: mean pooling over tokens, the [CLS] token, or the
last token for decoder-based models. An optional linear projection and an L2 normalization follow.
Query and document pass through the same network independently, which is the whole point: a document's
vector never depends on the query, so it can be computed once, stored, and compared with a dot product.

### Training: contrastive learning
Training data is (query, positive passage) pairs. The loss (InfoNCE and relatives) pulls the positive's
vector toward the query and pushes negatives away. Negatives are cheap because the other passages in the
batch serve as in-batch negatives, which is why embedding models are trained with very large batches.
Hard negatives matter more: passages that share topic and vocabulary with the query but do not answer
it, mined from BM25 or from an earlier version of the model. They teach the model the difference between
"about the same thing" and "answers the question", which is exactly what retrieval needs; DPR made this
explicit. Modern recipes such as E5 and BGE first pretrain on hundreds of millions of weakly supervised
pairs (titles and bodies, questions and accepted answers, near-duplicates) and then fine-tune on curated
retrieval datasets with hard negatives.

### Asymmetric models and prefixes
Queries are short and question-shaped; passages are long and declarative. Many models are trained with a
marker so they can treat the two sides differently: E5 expects `query: ` and `passage: ` prefixes, and
BGE and instruction-tuned models accept a task instruction prepended to the query. Omit the prefix and
the model still returns vectors, just worse ones. There is no error, only a quiet recall drop.

### Capacity limits
Every model has a maximum input length, typically 512 tokens for BERT-derived models and around 8,000
for many newer long-context ones. Longer inputs are truncated silently by most local libraries (some
hosted APIs reject them with an error instead), which is one of the reasons chunking exists. Larger models generally produce better vectors but embed more slowly and emit more dimensions,
which cost storage and comparison time for every chunk forever; the dimension lesson covers that
arithmetic.

### Where it sits against the alternatives
Cross-encoders stay more accurate because attention can run across query and passage tokens together.
The standard pattern is therefore a bi-encoder for first-stage retrieval over millions of chunks and a
cross-encoder reranker over the top few dozen. Late-interaction models (ColBERT) keep one vector per
document token and score with a max-similarity sum, a middle ground in both accuracy and storage.
Sparse retrieval (BM25, learned sparse) remains complementary, which is why hybrid search exists.

### Choosing a model
The MTEB leaderboard ranks models across retrieval, clustering and classification tasks and is the
usual starting point, with two caveats: the rank is averaged over tasks that may not resemble yours, and
popular models are increasingly trained on data close to the benchmark. What decides in practice is
domain (legal, medical, code and non-English text all reshuffle the ranking), input length, multilingual
needs, embedding latency and cost, and whether chunk text may leave your environment (hosted API model
versus self-hosted open weights). Measure recall@k on a few hundred labelled query–passage pairs from
your own corpus before committing, because changing the model later means re-embedding everything.

## Example
Two queries against three passages with an asymmetric open model. Neither query shares a content word
with its correct passage ("money back" versus "refunds", "end my membership" versus "cancel a
subscription"), which is the case BM25 cannot solve. The prefixes are part of the model's contract, and `normalize_embeddings=True` makes
the matrix product a cosine similarity.

```python
from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer("intfloat/e5-base-v2")   # asymmetric: needs "query: " / "passage: "
docs = [
    "passage: Refunds are issued to the original payment method within 5-7 business days.",
    "passage: Our office is closed on public holidays.",
    "passage: To cancel a subscription, open Billing and click Cancel plan.",
]
queries = ["query: how long does it take to get my money back?",
           "query: end my monthly membership"]

D = model.encode(docs, normalize_embeddings=True)     # shape (3, 768), unit length rows
Q = model.encode(queries, normalize_embeddings=True)  # shape (2, 768)
scores = Q @ D.T                                      # cosine similarity because both sides are unit vectors

for q, row in zip(queries, scores):
    best = int(np.argmax(row))
    print(f"{q!r} -> doc {best}  scores={np.round(row, 3)}")
# Expected: query 0 -> doc 0, query 1 -> doc 2. Dropping the prefixes usually does not flip a
# three-passage toy; their cost shows up as lost recall@k over a real corpus, so measure it there.
```

## Trade-offs
- A bi-encoder turns retrieval into a precomputed nearest-neighbour lookup but loses the token-level
  query–document interaction a cross-encoder has; once you can afford to rerank the top 50, most of
  that accuracy gap closes.
- Larger models give better vectors but embed slower and produce bigger vectors; the gain flips to a
  cost when the corpus is small, the workload is latency-bound, or a reranker already corrects
  first-stage mistakes.
- Hosted API models (OpenAI, Cohere, Voyage, Google) remove serving work and are often strong, but every
  chunk's text leaves your network and a vendor deprecation forces a full re-embed; self-hosted open
  models (BGE, E5, GTE, Nomic) reverse both sides of that trade.
- Multilingual models handle cross-language queries but tend to be a little weaker on English-only
  tasks than an English-specialised model of the same size, so the corpus decides.
- Long-context embedding models reduce the need for chunking, but each vector becomes a blurrier average
  of more content, which lowers precision for specific questions.

## Pitfalls
- Embedding the index with one model and queries with another, or with a different version of the
  "same" model; symptom: every similarity is mediocre and the top results look random. Vectors from two
  models are not comparable even when their dimensions match.
- Dropping the `query:`/`passage:` prefix or the instruction on an asymmetric model; symptom: a
  measurable recall drop with no error anywhere in the logs.
- Silent truncation of long chunks; symptom: questions about the tail of long documents never retrieve,
  while the same content pasted as a short chunk works.
- Choosing by leaderboard rank alone; symptom: the model that scores a point higher on MTEB
  underperforms the old one on your own labelled queries.
- Treating cosine scores as absolute confidence and copying a threshold from a blog post; symptom:
  after a model swap, either nothing or everything passes the threshold. Scores are model-specific.
- Upgrading the embedding model in place without a plan to re-embed; symptom: old and new vectors mixed
  in one index, with quality degrading as new content arrives.

## When to use
Reach for an embedding model whenever users describe what they want in their own words and the corpus
says it differently: support search, document question answering, code search, deduplication, clustering
and recommendations all qualify. Do not expect it to replace lexical search for exact identifiers, part
numbers, error codes or rare names; pair the two. Measure recall@k and MRR on labelled pairs from your
data, embedding time per thousand chunks, and vector bytes per chunk. Day to day the work is picking a
model, pinning its version, applying its prefixes, and re-evaluating whenever the corpus or model
changes. In interviews, expect to explain bi-encoder versus cross-encoder, contrastive training with hard
negatives, and why two models can never share one index.

## Role lens
- **Engineer:** Wraps the model behind one `embed(texts)` function with the prefix, normalization and
  version baked in, so the index path and the query path cannot drift apart.
- **Architect:** Owns the model choice and its exit cost: domain fit, the hosted-versus-self-hosted data
  boundary, and the re-embedding budget a future upgrade will require.
- **FDE:** Runs a quick recall@k bake-off on the customer's own documents and queries, because their
  domain terminology decides which model wins, not the public leaderboard.

## Check yourself
**Q:** A customer's support search returns good results for English queries but poor ones for the same
questions in Spanish, although half the knowledge base is in Spanish. You suspect the embedding model.
How do you diagnose it, and what do you change?
**A:** First rule out the pipeline: embed a Spanish query and its known-correct Spanish passage directly
and compare the similarity against unrelated passages, and confirm the same model, prefixes and
normalization are used at index time and query time. If the model is English-centric, Spanish texts
cluster poorly and cross-language pairs worse still, which shows up as low recall@k on a small labelled
Spanish set. The fix is a multilingual model (multilingual E5, BGE-M3 or a multilingual API model),
which means re-embedding the whole corpus, so plan it as a versioned re-index rather than an in-place
swap. Validate with recall@k in both languages, and keep BM25 in a hybrid setup for product names and
error codes, which embeddings handle badly in any language.

## Quiz
### Q: Why are documents embedded independently of the query in a bi-encoder, even though a cross-encoder reading both together is more accurate?
- [ ] Cross-encoders cannot process inputs longer than a short query
- [x] Independence lets document vectors be computed once and indexed, so a query costs one encoder pass plus a nearest-neighbour lookup instead of one pass per document
- [ ] Contrastive training only converges when the two inputs are encoded separately
- [ ] Cross-encoders produce vectors that cannot be normalized
> The cross-encoder is more accurate precisely because it conditions on both texts, but that means every
> query–document pair needs its own forward pass. Precomputing document vectors is what makes retrieval
> over millions of chunks affordable, which is why cross-encoders are used only to rerank a short list.

### Q: A team switches from one vendor's 768-dimensional model to another vendor's 768-dimensional model and re-embeds only new documents. What happens?
- [ ] Nothing, because matching dimensions make the vectors compatible
- [ ] Old documents become slightly less accurate but remain searchable
- [x] Old and new vectors live in unrelated spaces, so similarities between them are meaningless and results degrade as the mix grows
- [ ] The vector database rejects the new vectors as malformed
> Dimension is just the length of the array; the meaning of each axis is specific to the model that
> produced it. A query embedded with the new model has no principled relationship to old vectors, so a
> mixed index returns essentially random matches for old content.

### Q: What do hard negatives add to contrastive training that in-batch negatives do not?
- [ ] They increase the effective batch size without extra memory
- [ ] They let the model handle inputs longer than its context limit
- [x] They force the model to separate passages that are topically similar but do not answer the query, rather than just separating unrelated texts
- [ ] They remove the need for labelled positive pairs
> In-batch negatives are mostly random, unrelated passages and are easy to push away. Hard negatives
> share vocabulary and topic with the query, so the model must learn finer distinctions, which is what
> separates a retriever from a topic classifier.
