---
id: dense-vs-sparse-retrieval
module: rag-retrieval
name: Dense vs sparse retrieval
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - rag-retrieval/bm25-internals
  - rag-retrieval/inverted-indexes
  - rag-retrieval/hybrid-search
  - rag-retrieval/embedding-models
  - rag-retrieval/similarity-search
  - rag-retrieval/retrieval-failure-diagnosis
  - model-internals/tokenization
sources:
  - Karpukhin et al., 2020 — Dense Passage Retrieval for Open-Domain Question Answering
  - Thakur et al., 2021 — BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
  - Formal, Piwowarski & Clinchant, 2021 — SPLADE: Sparse Lexical and Expansion Model for First Stage Ranking
---

## TL;DR
Sparse retrieval represents text as a vocabulary-sized vector that is almost all zeros (which words
appear, weighted by BM25) and finds matches through an inverted index. Dense retrieval represents text as
a few hundred learned floats and finds matches by vector similarity. Sparse is exact about terms and blind
to meaning; dense is fluent about meaning and unreliable about exact terms. Neither dominates, which is
why production systems usually run both and fuse the results.

## Why it exists
For decades search meant term matching: a query word either appears in a document or it does not, and
ranking functions like BM25 weigh those appearances. This works astonishingly well and still sets the
baseline, but it has a hard ceiling called vocabulary mismatch. A user who types "stop billing me" never
reaches the page titled "cancel your subscription"; no amount of term weighting bridges words that do
not overlap. Synonym lists and query expansion patch individual cases without solving the class.

Dense retrieval attacks mismatch directly: train an encoder so that a query and the passage that answers
it map to nearby vectors regardless of shared words. Contrastive training on question–passage pairs
gives the model a notion of "aboutness" that survives paraphrase and even crosses languages.

The naive conclusion, "embeddings make keyword search obsolete", fails in practice for two reasons.
First, dense encoders compress a passage into a fixed vector; rare, high-information tokens like part
numbers, error codes, clause ids and names are exactly what gets lost, because the model saw them rarely
during training and its tokenizer splits them into meaningless pieces. Second, dense models are trained
on particular domains and degrade when the corpus looks different; zero-shot benchmarks across
heterogeneous domains have repeatedly shown BM25 beating many dense retrievers on out-of-domain data.
Understanding each family's failure mode is what makes the combination work.

## How it works
### Sparse: a vocabulary-dimensional vector
Take a vocabulary of V terms (tens of thousands to millions after tokenization and stemming). A document
becomes a vector in ℝ^V whose entry for term t is a weight such as its BM25 contribution, zero for the
vast majority of terms. A query becomes the same kind of vector. The score is their dot product, but
nobody materializes those vectors: because they are sparse, the dot product only touches terms present
in the query, and an inverted index maps each term to the documents containing it. Scoring a five-term
query means reading five posting lists, no matter how large the vocabulary. That is why sparse retrieval
runs comfortably on CPU with millisecond latency over tens of millions of documents.

The representation has three properties that follow from its construction. It is exact: a term either
matches or not, so "ERR-4012" finds "ERR-4012". It is interpretable: you can read off why a document
scored. And it is static: the weights depend only on counts (tf, df, length), never on word order or
meaning, so "bank account" and "river bank" share the term "bank".

### Dense: a learned low-dimensional vector
A bi-encoder (two transformer towers, or one shared tower) maps query and passage independently into
ℝ^d with d typically a few hundred to a few thousand. Training pulls each query toward its labeled
positive passage and pushes it away from negatives (in-batch and hard-mined), using cosine or dot
product as the similarity. After training, the geometry encodes meaning: passages about the same thing
cluster, and queries land near the passages that answer them even with zero lexical overlap.

The properties invert. Dense is fuzzy: it can match "stop billing me" to "cancel your subscription". It
is opaque: a score of 0.71 has no term-level explanation. And it is learned, which cuts both ways: it is
only as good as its training distribution, and every change of model means re-embedding the corpus.

### Where each one fails
Dense failure modes:
- Exact identifiers and rare terms. The tokenizer splits "PX-200" into subword pieces shared with
  unrelated strings; the encoder has no stable representation for it, so a query containing it may rank
  a passage about "PX-210" equally.
- Domain shift. A model trained on web QA sees medical, legal or internal-jargon text as noise; recall
  on such corpora can fall below BM25 without fine-tuning.
- Negation and precise constraints ("flights not via Frankfurt") average out in a single vector.
- Long passages. One vector per chunk means the embedding is a blend; a passage that answers the
  question in one sentence among twenty gets diluted.

Sparse failure modes:
- Vocabulary mismatch, including morphology the stemmer misses, synonyms and cross-language queries.
- Short, abstract queries ("why is this slow") that share only stopwords with relevant documents.
- No notion of relevance beyond term statistics: a document that mentions all the query words in
  passing can outrank the one that actually answers.

### The same score, different dimensions
Put the two side by side and the scoring operation is identical: both rank by an inner product between a
query vector and a document vector. What differs is who defines the dimensions. In sparse retrieval each
dimension is a vocabulary term fixed in advance by the analyzer, and the weights are count statistics;
two texts interact only through dimensions they literally share. In dense retrieval the d dimensions are
latent features the encoder invented during training, and every text has a nonzero value on all of them,
so any two texts interact. That one difference explains the rest: sparse can be served by reading only
the posting lists of the query's terms, while dense needs a vector index because every document is a
candidate; sparse is exact because a term either is a dimension or is not, while dense is fuzzy because
meaning is smeared across shared dimensions.

### Learned sparse: a middle ground
Learned sparse models (SPLADE is the reference) use a transformer to predict a weight for every
vocabulary term, including terms not present in the text (expansion), with a sparsity penalty so most
weights stay zero. The output is still a sparse vector served from an inverted index, so it keeps exact
matching and CPU serving, but the weights carry learned semantics and synonyms. It is a good option when
you want one index and cannot run a vector database, at the cost of a neural encoder at index and query
time.

### Multi-vector dense: another middle ground
Late-interaction models such as ColBERT keep one dense vector per token rather than one per passage and
score by matching each query token to its most similar passage token. Because a rare token keeps its own
vector instead of being averaged into the passage, exact-term behavior improves while paraphrase
matching survives. The price is an index many times larger than single-vector dense, which is why it
appears more often as a reranking stage than as first-stage retrieval (see the reranking lesson).

### Operational differences that decide the choice
| Concern | Sparse (BM25) | Dense (bi-encoder) |
|---|---|---|
| Index cost | CPU, tokenization only | GPU or API call to embed every chunk |
| Query cost | Sub-millisecond to milliseconds on CPU | Embed the query (model call) plus ANN search |
| Index update | Append postings; near-real-time (seconds) | Embed then insert; model change forces full re-index |
| Explainability | Term-level | None without extra tooling |
| Tuning | Analyzer, k1, b, field boosts | Model choice, fine-tuning, chunking |
| Exact tokens | Strong | Weak |
| Paraphrase | Weak | Strong |

## Example
Five support passages, two queries. The first query is an exact error code that dense retrieval tends to
blur; the second is a paraphrase that BM25 cannot bridge. Run both retrievers and compare the top hit.

```python
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

docs = [
    "Error E4012 on the PX-200 means the fuser is overheating; power-cycle the unit.",
    "Error E4021 on the PX-210 indicates a paper jam in tray 2.",
    "To cancel your subscription, open Billing and choose End plan.",
    "Wireless setup: hold the Wi-Fi button for five seconds until the light blinks.",
    "Replacing the toner cartridge on PX-series printers.",
]
queries = ["E4012 PX-200", "how do I stop being billed"]

tok = lambda s: s.lower().replace("-", " ").split()   # the analyzer, shared by index and query
bm25 = BM25Okapi([tok(d) for d in docs])
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
D = model.encode(docs, normalize_embeddings=True)

for q in queries:
    sparse = bm25.get_scores(tok(q))
    dense = D @ model.encode(q, normalize_embeddings=True)
    print(f"\nQ: {q}")
    print("  BM25  ->", docs[int(np.argmax(sparse))][:60], np.round(sparse.max(), 2))
    print("  dense ->", docs[int(np.argmax(dense))][:60], np.round(dense.max(), 2))
```

BM25 nails the error-code query, because "e4012", "px" and "200" are exact tokens, while the dense model
may confuse the PX-200 and PX-210 passages, whose vectors are nearly identical in shape. On the billing
paraphrase, BM25 scores every document at zero (no shared tokens at all; "billed" is not "billing"
without a stemmer), so `argmax` just returns the first passage with a score of 0.0, while the dense model ranks the cancellation passage first. The exact margins depend
on the model, which is itself the point: the behavior is structural, the numbers are not.

## Trade-offs
- Sparse retrieval is cheap, incremental and exact on identifiers, but it stalls on paraphrase; the
  balance flips toward dense as soon as your users phrase questions differently from how the documents
  are written.
- Dense retrieval generalizes across wording and languages, but costs an embedding pass per chunk and
  per query and a full re-index on every model change; it flips back toward sparse when the corpus is
  huge, churns constantly, or the GPU budget is tight.
- Learned sparse keeps one CPU-served inverted index while adding semantics, at the cost of a neural
  encoder at index time (and, for most variants, at query time) and some loss versus a strong dense model on pure paraphrase tasks.
- In-domain fine-tuning of a dense model can overtake BM25 decisively, but needs labeled query–passage
  pairs; without them an off-the-shelf model on jargon-heavy text may lose to BM25, which needs no labels
  at all.

## Pitfalls
- Shipping dense-only because the demo paraphrases worked; the symptom is support tickets like "I
  searched the exact ticket number and got unrelated results".
- Shipping sparse-only with the default analyzer; the symptom is zero-result or wrong-result queries
  whenever users use synonyms, plurals the stemmer misses, or another language.
- Evaluating a dense model on the vendor's benchmark rather than your corpus; the symptom is recall that
  collapses on internal jargon, acronyms and tables that look nothing like web text.
- Chunking long documents into large pieces for dense retrieval; the symptom is relevant passages ranked
  below shorter, vaguely related chunks because the long chunk's vector is an average of many topics.
- Forgetting that sparse and dense indexes must be refreshed together; the symptom is a document that
  appears in keyword results but not semantic results (or vice versa) for hours after an update.

## When to use
Start from the queries. If users search by identifiers, codes, names and exact phrases, or the corpus is
technical and churns fast, sparse is the backbone and dense is an add-on. If users ask natural-language
questions against prose and wording varies, dense is the backbone and sparse protects the exact-match
cases. Most real workloads contain both kinds of queries, which is why the practical default is hybrid
retrieval with fusion, covered in its own lesson. Decide with data: build a labeled query set, measure
recall@k for each retriever alone and in combination, and look at which queries each one loses. The
overlap is the decision number: if the queries BM25 wins are a subset of those dense wins, a second
arm adds cost and nothing else; if each wins queries the other loses, the union of their successes
bounds what hybrid can reach. In
interviews, be ready to name the structural failure modes of each family, explain why BM25 remains a
strong baseline, and say what learned sparse models add.

## Role lens
- **Engineer:** Builds both retrievers behind one interface, keeps tokenization consistent between index
  and query, and runs the per-retriever recall ablation on every model or analyzer change.
- **Architect:** Decides which index is the system of record, how both stay consistent on updates, and
  whether GPU embedding cost is justified by measured recall gains over BM25.
- **FDE:** Audits a customer's query logs to classify identifier-style versus natural-language queries,
  then sets expectations for which retriever carries which traffic.

## Check yourself
**Q:** A customer replaced their Elasticsearch keyword search with a vector database and reports that
overall satisfaction rose but a vocal group of power users says search "got dumber". What is happening,
and what would you do before adding anything back?
**A:** The power users are almost certainly issuing identifier-style queries, such as SKUs, error codes,
ticket ids and exact phrases, which dense encoders blur into similar-looking neighbors, while the
majority issue natural-language questions that benefited from semantic matching. I would pull a sample
of queries from both groups and label them, measure recall@k for the dense retriever and a BM25 baseline
on each slice, and expect to see dense winning on paraphrase and losing on exact tokens. The fix is not
to revert but to run both retrievers and fuse their rankings, possibly with a reranker, and to keep the
two indexes refreshed in lockstep so a document is never visible in one and missing from the other. I
would also check tokenization of identifiers in both systems, since hyphen handling alone can decide
those queries.

## Quiz
### Q: Why does a dense bi-encoder often fail on queries containing a rare product code such as "PX-200"?
- [ ] Because vector databases cannot index strings containing digits
- [x] Because the tokenizer splits the code into subword pieces the model saw rarely and in unrelated contexts, so the embedding carries little stable information about it
- [ ] Because cosine similarity ignores numeric tokens by design
- [ ] Because dense models are trained only on lowercase text and codes are uppercase
> A bi-encoder has no entry for "PX-200"; it sees fragments like "px", "-", "200" whose learned meanings
> are diffuse, so passages about PX-200 and PX-210 end up nearly indistinguishable. Sparse retrieval
> matches the literal token and does not have this problem.

### Q: Which statement about BM25 as a baseline is best supported?
- [ ] BM25 is obsolete once any pretrained embedding model is available
- [ ] BM25 beats dense retrieval on paraphrased natural-language questions
- [x] On out-of-domain corpora, off-the-shelf dense retrievers frequently fall below BM25, which needs no training data and matches exact terms
- [ ] BM25 and dense retrieval always return the same top result when both are tuned
> Zero-shot evaluations across heterogeneous domains show BM25 remaining competitive with or ahead of
> many dense models that were trained elsewhere. On paraphrase BM25 is weak, and the two retrievers
> disagree often enough that fusing them improves recall.

### Q: What does a learned sparse model like SPLADE change compared with BM25?
- [ ] It replaces the inverted index with an HNSW graph over sparse vectors
- [x] It uses a transformer to assign learned weights to vocabulary terms, including terms absent from the text, while still producing a sparse vector served from an inverted index
- [ ] It removes the need for a tokenizer by operating on raw characters
- [ ] It converts BM25 scores into probabilities calibrated across queries
> SPLADE keeps the sparse, vocabulary-dimensional representation and inverted-index serving, but learns
> the weights and expands documents with related terms, which addresses vocabulary mismatch without a
> vector database. The index structure is unchanged.
