---
id: hyde
module: rag-retrieval
name: HyDE (Hypothetical Document Embeddings)
kind: pattern
difficulty: core
est_minutes: 12
status: reviewed
related:
  - rag-retrieval/query-rewriting
  - rag-retrieval/dense-vs-sparse-retrieval
  - rag-retrieval/embedding-models
  - rag-retrieval/similarity-search
  - rag-retrieval/hybrid-search
  - rag-retrieval/reranking
  - model-internals/hallucination
  - model-internals/embeddings
sources:
  - Gao et al., 2022 — Precise Zero-Shot Dense Retrieval without Relevance Labels
  - Wang, Yang and Wei, 2023 — Query2doc: Query Expansion with Large Language Models
  - Nogueira et al., 2019 — Document Expansion by Query Prediction
---

## TL;DR
HyDE asks an LLM to write a plausible answer passage first, embeds that hypothetical instead of (or
alongside) the question, and retrieves the real passages nearest to it. It exists because a short
question and a long answer are different kinds of text in different regions of embedding space; a
hypothetical answer lands in the answer region even when its facts are wrong, because the encoder keeps
topic and vocabulary and washes out detail. It costs one generation per query.

## Why it exists
Dense retrieval compares two vectors, but the two texts being compared are not symmetric. A query is
"how do I rotate an API key?": short, interrogative, carrying almost no topical content beyond three
words. The passage that answers it reads "To generate a new key, open Settings, then Keys, and choose
Regenerate; the previous key remains valid for 24 hours." It is declarative, longer, and full of the
vocabulary the question never used. Encoders trained on large sets of (query, passage) pairs learn to map
the two shapes near each other, but an encoder used zero-shot, out of its training domain, or trained
without such pairs (the original paper's setting used an unsupervised encoder) does not close the gap
well. The question's vector sits in a "questions about keys" region, the passage's in a "procedures
about keys" region, and the nearest neighbours of the question are other questions, which the corpus
does not contain.

The direct fix is to train the retriever on in-domain labelled pairs, which needs labels you usually
do not have. Instruction-prefixed embedding models ("query:" versus "passage:") are the cheap modern
version of that fix and should be tried first. HyDE takes a different route that needs no labels and no
training: if the problem is that the query is the wrong shape, generate something of the right shape.
An LLM can write a passage that looks like an answer, and even a partly wrong answer is closer to the
real one than the question was.

## How it works
### The steps
1. **Generate.** Prompt the LLM with "write a passage that answers: {question}". The original work
   sampled several hypothetical documents per query rather than one; multiple samples cost linearly and
   reduce the variance introduced by any single hallucinated detail.
2. **Encode with the document encoder.** Each hypothetical passage is embedded with the same model that
   embedded the corpus, because the comparison must be passage-to-passage in the same space. Using a
   different encoder for the hypothetical than for the index defeats the whole point.
3. **Aggregate.** Average the hypothetical embeddings; optionally include the embedding of the original
   query as one more vector in the average. Averaging exists because it keeps what the samples agree on
   (topic, entities, structure) and suppresses what they disagree on (invented numbers); including the
   query anchors the result to the user's actual words.
4. **Search.** Run nearest-neighbour search with the aggregated vector and return the real passages.
5. **Answer from real passages only.** The hypothetical never reaches the generation prompt. It is a
   search key, not evidence, and treating it as evidence is how hallucinations get cited.

### Why hallucinated details mostly do not matter
An embedding model is a lossy compressor. It preserves the gist (what the passage is about, which
entities and terms appear, how it is structured) and gives little weight to a specific number or date.
So a hypothetical that says keys stay valid for 48 hours when the truth is 24 still lands next to the
passage about key rotation, because everything except the number matched. The paper frames this as the
encoder acting as a filter that grounds the generated text in the real corpus. The exception is
entity-level error: if the model writes about the wrong product or confuses two similarly named
services, the vector lands in the wrong neighbourhood and no amount of averaging rescues it.

### Where it fits with neighbouring techniques
- It is a special case of query rewriting: rewriting the query into document-shaped text. Query2doc is
  a close relative that concatenates the original query with the generated pseudo-document (repeating
  the query so it is not drowned out) and was shown to help sparse BM25 retrieval as well as dense.
- The mirror image works at index time. Doc2query-style expansion has a model write the questions each
  chunk answers and indexes them with (or as keys for) the chunk, so the corpus side becomes
  question-shaped instead. The generation cost moves from every query to ingestion, which suits high
  query volume and stable corpora; HyDE suits the reverse, or a corpus you cannot re-index.
- It can feed the sparse leg of hybrid search too, since the hypothetical contains answer vocabulary the
  question lacked; but it also contains invented terms, so keep the original query for BM25 as well.
- It is query-side; contextual retrieval and parent-document retrieval are index-side, and they combine.
- A final reranker should score the real candidates against the original question, not the hypothetical.

### When the gap is already closed
Modern embedding models trained with instructions and large query-passage corpora have narrowed the
question-to-passage gap substantially. On such a model HyDE's gain can be small, zero or negative, and
it always adds a generation call. The honest position is model- and corpus-dependent: measure recall@k
on your golden set with and without it.

## Example
HyDE with multiple samples, aggregation with the query embedding, and a search over the real index.
`llm`, `embed` and `index.search` are stubs for any provider, encoder and vector store.

```python
import numpy as np

def hyde_search(question, n_samples=3, k=10, include_query=True):
    prompt = f"Write a short documentation passage that answers the question.\nQuestion: {question}\nPassage:"
    hypos = [llm(prompt, temperature=0.7) for _ in range(n_samples)]   # parallelise in production
    vecs = [embed(h, kind="passage") for h in hypos]          # same encoder as the index
    if include_query:
        vecs.append(embed(question, kind="query"))             # anchor to the user's words
    v = np.mean(np.stack(vecs), axis=0)
    v = v / np.linalg.norm(v)                                  # cosine-normalised index assumed
    return index.search(v, k), hypos

hits, hypos = hyde_search("How do I rotate an API key without downtime?")
# Illustrative hypothetical: "To rotate an API key, create a new key in Settings > API Keys, update
# your clients to use it, then revoke the old key. Old keys stay valid for 48 hours after a new one
# is created so that clients can migrate." The 48 is invented; the real passage says 24 hours, but
# the topic, entities and structure match, so the real passage is retrieved and the generator answers
# from it, never from the hypothetical.
```

## Trade-offs
- The largest gains appear with zero-shot or out-of-domain encoders and question-shaped queries over
  passage-shaped corpora; with an instruction-tuned embedder trained on your query type the gain shrinks
  or reverses.
- One generation per query adds latency on the order of several hundred milliseconds to a few seconds
  (a whole passage must be decoded before search can start) and a per-query cost; applying it only
  as a fallback when first-pass scores are weak keeps the cost off the common path.
- Robust to small factual errors in the hypothetical but fragile to entity confusion; on private or novel
  domains the model writes a generic passage and retrieval becomes generic with it.
- Several samples stabilise the aggregated vector at linear cost; one sample is cheaper and noisier, and
  the right number depends on how much the hypotheticals disagree.

## Pitfalls
- Feeding the hypothetical into the answer prompt as if it were retrieved; the symptom is fluent answers
  containing facts that appear in no retrieved passage.
- Encoding the hypothetical with a different model than the index; the symptom is retrieval worse than
  the plain-query baseline.
- Dropping the original query entirely; the symptom is regressions on queries with exact identifiers,
  such as ticket numbers, that the hypothetical paraphrased away.
- Using HyDE on time-sensitive questions (current prices, latest version); the model writes stale
  details and the vector drifts toward old passages, so the symptom is retrieval of outdated versions.
- Adopting it because the paper reported gains, without measuring; the symptom is added latency and a
  flat recall@k.
- Running the reranker against the hypothetical instead of the question; the symptom is a top result
  that matches the invented details (the "48 hours" passage from an old version) over the true answer.
- Letting the hypothetical exceed the encoder's input limit; the symptom is silent truncation and a
  vector that represents only the opening sentences.

## When to use
Reach for HyDE when queries are question-shaped, the corpus is passage-shaped, and the embedding model
was not trained for that asymmetry; in practice that means a generic or older encoder, or a domain far
from its training data. If the corpus is itself question-shaped (an FAQ indexed on its questions, or
chunks expanded with generated questions), the shapes already match and HyDE has little left to fix. If
query volume is high and the corpus stable, compare against index-time question generation, which pays
the generation cost once per chunk rather than once per query. Try instruction-prefixed embeddings and hybrid search first, since they are
free at query time. Avoid it for identifier lookups, for time-sensitive facts, and for domains the LLM
knows nothing about. Measure recall@k and MRR on a golden set with and without it, and the latency it
adds at p95. In interviews, HyDE is the standard answer to "how would you improve dense retrieval
without labelled data", and the strong candidate also explains why hallucinations in the hypothetical
are mostly harmless and when they are not.

## Role lens
- **Engineer:** Implements the generate, embed, average, search path, keeps the hypothetical out of the
  answer prompt, and A/B tests recall@k against the plain-query baseline.
- **Architect:** Decides whether the extra generation fits the latency and cost budget, and whether a
  better embedding model would remove the need for it altogether.
- **FDE:** Checks whether the customer's domain is one the model can write plausibly about before
  recommending HyDE, and demonstrates the gain or lack of it on their own queries.

## Check yourself
**Q:** An internal wiki search uses a generic off-the-shelf embedding model; questions are phrased as
"how do I..." and recall@10 is about 0.6. Would HyDE help, how would you test it, and what are the risks?
**A:** Probably yes: the queries are question-shaped, the wiki is passage-shaped, and a generic encoder
is exactly the zero-shot setting where the asymmetry hurts. Test by generating two or three hypothetical
passages per golden-set question, embedding them with the same encoder as the index, averaging with the
query embedding, and comparing recall@10 and MRR against the baseline; also test an instruction-prefixed
encoder and hybrid search, which may close the gap for free. Risks: the wiki covers private systems the
model cannot write about, giving generic hypotheticals; identifier-heavy queries regress if the original
query is dropped; latency rises by one generation; and if the hypothetical ever leaks into the answer
prompt, invented details get cited. Keep it as a search key only.

## Quiz
### Q: Why does a hypothetical answer with a wrong number still retrieve the right passage?
- [ ] The LLM is usually right about numbers in technical domains
- [x] The embedding preserves topic, entities and structure and gives little weight to a specific detail, so the vector still lands in the right neighbourhood
- [ ] The vector index ignores numeric tokens
- [ ] The reranker corrects the number before searching
> Encoders are lossy compressors that keep the gist; the paper describes the encoder as a filter that
> grounds the generated text in the corpus. Entity-level errors, unlike numeric ones, do move the vector.

### Q: Which mistake most directly defeats HyDE's mechanism?
- [ ] Sampling three hypotheticals instead of one
- [ ] Including the original query embedding in the average
- [x] Embedding the hypothetical with a different model than the one that embedded the corpus
- [ ] Using a temperature above zero for generation
> The whole idea is passage-to-passage comparison in one space; a different encoder places the
> hypothetical in an unrelated space, so nearest neighbours are meaningless.

### Q: When is HyDE most likely to add latency without improving recall?
- [ ] When the corpus is passage-shaped technical documentation
- [x] When the embedding model is instruction-tuned on query-passage pairs matching your query type
- [ ] When queries are phrased as natural-language questions
- [ ] When no labelled relevance data exists
> Such models already map questions near their answers, which is the gap HyDE was designed to close;
> the remaining benefit is small or negative while the generation cost stays.
