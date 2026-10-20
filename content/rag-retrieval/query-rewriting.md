---
id: query-rewriting
module: rag-retrieval
name: Query rewriting
kind: pattern
difficulty: core
est_minutes: 13
related:
  - rag-retrieval/query-decomposition
  - rag-retrieval/hyde
  - rag-retrieval/hybrid-search
  - rag-retrieval/bm25-internals
  - rag-retrieval/metadata-filtering
  - rag-retrieval/retrieval-failure-diagnosis
  - prompting/context-engineering
  - prompting/prompt-injection
sources:
  - Ma et al., 2023 — Query Rewriting for Retrieval-Augmented Large Language Models
  - Zheng et al., 2023 — Take a Step Back: Evoking Reasoning via Abstraction in Large Language Models
---

## TL;DR
The text a user types is written for a human, not for an index: it is short, refers to earlier turns,
uses different words from the documents, and often buries one useful identifier in a wall of paste.
Query rewriting inserts an LLM step between the user and the retriever that turns the raw input into
one or more retrieval-friendly queries, because fixing the key at query time is far cheaper than
re-indexing the corpus to tolerate every way a human might ask.

## Why it exists
Every retriever rests on one assumption: the query and the relevant passage look alike. BM25 needs
shared terms; dense retrieval needs nearby embeddings. Real queries break the assumption in predictable
ways. A follow-up turn like "and on Android?" contains nothing retrievable on its own, because the
subject lives three messages back and the retriever has no memory. A user writes "cancel my
subscription" while the documentation says "terminate your plan", so the sparse leg finds nothing and the
dense leg scores it as merely similar. A pasted stack trace of 200 lines swamps the embedding with
boilerplate and drowns the one line, an error code, that would have matched exactly. Product names are
abbreviated, misspelled or replaced by nicknames the documents never use.

The naive alternatives each fail somewhere. Embedding the entire conversation history as the query
dilutes the current intent with everything said before. Asking users to phrase questions better does not
survive contact with users. Expanding the index with synonyms helps vocabulary mismatch but does nothing
for conversational references or long pastes, and it bloats every document. The rewrite step sits at the
one point where all the information is available at once: the conversation so far, the user's current
message, and (via its prompt) the conventions of the corpus. An LLM is good at exactly this
transformation, and a small one is enough.

## How it works
### Where it sits
The user turn and the recent history go to a rewriter prompt. Its output is a small structured object:
one or more search strings, optionally a set of metadata filters, and optionally a flag saying no
retrieval is needed. Each search string goes to the retriever(s); the result lists are fused; the fused
context plus the *original* question go to the generator. The generator still sees the user's words,
because the rewrite is for the index, not for the answer.

### The forms a rewrite takes
1. **Condensing (conversational RAG).** Turn "and on Android?" into "How do I enable push notifications
   on Android?" using the history. This exists because retrievers are stateless; the standalone question
   is the only thing they can act on.
2. **Normalisation.** Fix typos, expand abbreviations, map aliases to canonical product names. This exists
   because BM25 matches exact terms and an embedding of a misspelled name is not reliably near the correct
   one.
3. **Multi-query expansion.** Generate several paraphrases, retrieve for each, and fuse with reciprocal
   rank fusion. A single embedding is one point; several points cover the neighbourhood where the
   relevant passage might sit. The cost is several retrievals instead of one.
4. **Keyword extraction.** From a long paste, pull the identifiers (error codes, function names, version
   strings) for the sparse leg and a one-line summary for the dense leg, because long inputs dilute both.
5. **Step-back abstraction.** From "why did key K-7 get HTTP 429 at 3pm Tuesday", also ask "what are the
   API rate-limit rules". Documents describe general rules, not specific incidents, so the abstract
   question is what the corpus can answer; retrieve for both.
6. **Routing and filtering.** Emit structured filters (product, date range, document type) alongside the
   query so the retriever can restrict the candidate pool, and emit "no retrieval" for greetings or
   requests that are about the conversation itself.

The research framing (Ma et al.) treats the rewriter as a trainable module optimised by feedback from
downstream answer quality, with the LLM reader frozen. In production the same shape is usually a
prompted small model, because the input is short and the output is shorter.

### Design choices and why
- **Always retrieve for the original query too.** A rewrite can drift; fusing results from the original
  and the rewrite(s) means a bad rewrite degrades gracefully instead of replacing a working path.
- **Protect literals.** Instruct the rewriter never to alter quoted strings, codes, numbers or version
  identifiers. Normalisation that "fixes" `ERR_4012` into `error 4012` breaks the one exact match you had.
- **Use a small, fast model and cache.** The rewrite adds a sequential LLM call to every request. Keying
  a cache on (hash of recent history, query) removes it for repeats.
- **Tell the rewriter about the corpus.** A sentence such as "documents are organised per product and use
  the terms plan, seat and workspace" produces rewrites in the index's vocabulary.
- **Log the pair.** Store the original and the rewrite with every request, because retrieval failures must
  be attributable to the rewrite or to the index.
- **Treat the input as untrusted.** The rewriter is an LLM reading user text; a query containing
  instructions can hijack it, so constrain the output format and validate it.

## Example
A condensing-plus-expansion rewriter with reciprocal rank fusion. `llm(prompt)` returns text and
`retrieve(query, k)` returns ranked chunk ids; any provider or vector store works.

```python
import json, hashlib

REWRITE_PROMPT = """Conversation so far:
{history}
Latest user message: {query}
Rewrite the latest message as 1-3 standalone search queries for a product documentation index.
Keep every code, number and quoted string exactly as written. Use the documentation's own terms
(plan, seat, workspace). Return JSON: {{"queries": [...], "needs_retrieval": true|false}}"""

_cache = {}

def rewrite(history, query):
    key = hashlib.sha256((history[-2000:] + "\n" + query).encode()).hexdigest()
    if key not in _cache:
        raw = llm(REWRITE_PROMPT.format(history=history[-2000:], query=query))
        _cache[key] = json.loads(raw)
    return _cache[key]

def rrf(ranked_lists, c=60):
    scores = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (c + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)

def search(history, query, k=8):
    plan = rewrite(history, query)
    if not plan["needs_retrieval"]:
        return []
    queries = [query] + plan["queries"]            # original always included
    return rrf([retrieve(q, 20) for q in queries])[:k]

ctx = search("user: how do I turn on push notifications?\nassistant: In Settings > Alerts...",
             "and on Android?")
# the rewrite becomes e.g. "enable push notifications on Android", which the index can actually match
```

## Trade-offs
- One extra sequential LLM call per request adds latency and cost; it is worth it for conversational or
  noisy input and not for well-formed single-turn queries from trained users.
- Multi-query expansion raises recall but multiplies retrieval cost and admits drifted paraphrases that
  lower precision; it pays off when retrieval is cheap relative to a wrong answer.
- Putting corpus conventions in the rewriter prompt improves vocabulary matching but couples the prompt
  to the corpus, so it goes stale when products are renamed or documents restructured.
- Condensing to a single standalone question loses nuance from long conversations, such as a constraint
  the user corrected earlier; including the last few turns verbatim in the rewriter prompt limits this.

## Pitfalls
- The rewriter invents specifics, turning "the API" into a named third-party API; the symptom is fluent
  answers about the wrong product that cite real documents.
- Normalisation rewrites exact identifiers; the symptom is that the BM25 leg stops matching the one
  document containing the error code, right after rewriting was introduced.
- Retrieving only for the rewrite and never the original; the symptom is a regression on simple queries
  that worked before the rewriter shipped.
- Rewriting and retrieving for every turn including "thanks"; the symptom is wasted calls and odd snippets
  injected into small talk.
- Judging the feature by reading rewrites that "look better" instead of measuring recall@k before and
  after on a golden set; the symptom is a launch with no metric that moved.
- Letting user text steer the rewriter; the symptom is rewrites containing instructions or filters the
  user should not be able to set, a prompt-injection path into the retrieval layer.

## When to use
Reach for condensing the moment the interface is conversational; without it, follow-up questions retrieve
garbage. Add normalisation and keyword extraction when failure analysis shows vocabulary mismatch or long
pasted inputs, and multi-query or step-back expansion when recall@k is the measured bottleneck and
retrieval is cheap. Skip it when queries are well-formed single turns in the corpus's own vocabulary or
when the latency budget has no room for a sequential call. Measure recall@k with and without the rewriter
on a golden set, the p95 latency it adds, and the fraction of queries where the rewrite differs from the
input. In interviews, "how do you handle follow-up questions in a RAG chatbot" and "what if users use
different terminology than the docs" are both query-rewriting questions.

## Role lens
- **Engineer:** Writes and versions the rewriter prompt, guards identifiers, fuses results with RRF, and
  logs original and rewritten queries so retrieval failures can be attributed.
- **Architect:** Decides where rewriting lives in the pipeline, which model runs it under the latency
  budget, and whether its output may set metadata filters that touch access control.
- **FDE:** Tunes the rewriter prompt to a customer's vocabulary and product names, and shows the before and
  after recall numbers that justify the extra call.

## Check yourself
**Q:** A support chatbot answers first questions well, but follow-ups such as "what about on Android?"
retrieve unrelated passages. Diagnose the cause, propose a fix, and say what could go wrong with it.
**A:** The retriever only sees the literal follow-up, which has no retrievable content; the subject is in
the earlier turns. Add a condensing step: a small LLM reads the recent history and the new message and
emits a standalone question, such as "how do I enable push notifications on Android", which is then
retrieved alongside the original text and fused. Risks: the rewriter can drift or hallucinate an entity,
so keep the original query in the fusion and log both; it adds a sequential call, so use a fast model and
cache on history plus query; it must not alter codes or quoted strings; and it reads untrusted text, so
constrain its output format. Validate with recall@k on a golden set of multi-turn conversations before
and after.

## Quiz
### Q: Why should the original user query still be retrieved for after rewriting?
- [ ] The generator needs the original query's embedding to answer
- [x] A rewrite can drift from the user's intent, and fusing results from both lets a bad rewrite degrade gracefully instead of replacing a working path
- [ ] Vector databases reject rewritten queries that differ from the logged input
- [ ] Rewrites are only used for the sparse BM25 leg
> Rewriters are LLMs and sometimes misread intent or invent specifics; keeping the original query in the
> fused result set preserves the baseline behaviour while the rewrite adds recall.

### Q: Which rewrite form directly addresses "why did key K-7 get HTTP 429 at 3pm on Tuesday?"
- [ ] Condensing the conversation into a standalone question
- [ ] Multi-query paraphrase expansion
- [x] Step-back abstraction to "what are the API rate-limit rules", retrieved alongside the original
- [ ] Spelling normalisation
> Documentation describes general rules, not the user's specific incident, so an abstracted question is
> what the corpus can match; the original is kept for the exact identifiers.

### Q: A team adds normalisation and the BM25 leg stops finding a document that contains error code ERR_4012. What most likely happened?
- [ ] The inverted index needs rebuilding after rewriting is enabled
- [ ] BM25 cannot match tokens containing underscores
- [x] The rewriter altered the literal identifier, so the exact term no longer appears in the query
- [ ] The embedding model truncated the rewritten query
> Normalisation that "fixes" codes, numbers or quoted strings destroys the exact-match signal BM25 depends
> on; the rewriter prompt must protect literals and the eval should include identifier-heavy queries.
