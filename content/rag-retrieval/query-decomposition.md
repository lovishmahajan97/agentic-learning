---
id: query-decomposition
module: rag-retrieval
name: Query decomposition
kind: pattern
difficulty: core
est_minutes: 13
related:
  - rag-retrieval/query-rewriting
  - rag-retrieval/multi-hop-retrieval
  - rag-retrieval/agentic-rag
  - rag-retrieval/recall-vs-precision-trade-off
  - agents/task-decomposition
  - agents/parallel-tool-calls
  - evals/retrieval-evaluation
sources:
  - Press et al., 2022 — Measuring and Narrowing the Compositionality Gap in Language Models
  - Zhou et al., 2022 — Least-to-Most Prompting Enables Complex Reasoning in Large Language Models
  - Official docs: LlamaIndex sub-question query engine
---

## TL;DR
A compound question ("compare the refund windows of the Standard and Enterprise plans for EU customers")
embeds as one blurry point and its top-k is dominated by whichever half the index matches best, so the
other half never reaches the prompt. Query decomposition has an LLM split the question into focused,
standalone sub-questions, retrieves for each (in parallel when they are independent), and answers from
the merged evidence or from per-sub-question answers. Independent parts fan out; dependent parts chain,
which is multi-hop retrieval.

## Why it exists
Retrieval returns one ranked list for one query, and both retrieval families reward documents that
match the query as a whole. A dense embedding of a two-part question is roughly a mixture of the two
intents, so it sits between the two regions of the space where the relevant passages live and is
nearest to neither. BM25 sums term matches, so the top of the list is whatever document mentions the
most query terms, which for a comparison question is usually a passage about one plan with a passing
mention of the other. Either way, the second half of the question is served by the tail of the ranking,
and the tail does not reach the prompt.

Widening top-k does not fix this: it trades precision for a small recall gain, and the passage about the
second plan may still rank below dozens of passages about the first. Larger chunks hoping to capture
both topics in one piece just blur the embeddings for every other query. Longer context so the model
can "reason over more" only helps if the evidence was retrieved at all.

There is a second, model-side reason. Press et al. describe a compositionality gap: models that answer
each component question correctly still fail the composed question at a noticeable rate. Making the
components explicit, retrieving evidence for each, and composing the answer in a separate step attacks
both the retrieval failure and the reasoning failure with one mechanism.

## How it works
### Step 1: decide whether to split, then split
A planner prompt receives the question and returns a list of sub-questions, or a signal that the
question is atomic. The "atomic" branch exists because decomposition is not free: every sub-question is
another retrieval, and splitting "what is the refund window?" into three parts adds noise and latency for
nothing. Each sub-question must be standalone and must carry the shared constraints of the original
("for EU customers", "on the annual plan"), because the retriever sees sub-questions one at a time and a
dropped qualifier changes the answer.

### Step 2: classify dependencies
Sub-questions are either independent (the refund window of plan A does not depend on plan B) or
dependent (the second question needs the first answer, such as "who manages the author of the report").
Independent ones fan out in parallel; dependent ones must run in sequence, and at that point the pattern
is multi-hop retrieval. A planner can emit this as a small DAG; in practice two levels, "parallel batch"
then "sequential follow-up", cover most cases.

### Step 3: retrieve per sub-question
Each sub-question gets its own top-k, often a smaller k than a single query would use, because each
sub-question is now a sharp key and the total context budget is shared across them. Metadata filters
derived from the sub-question (product = Enterprise) apply here too.

### Step 4: combine the evidence
Two options with different failure modes:

- **Merge contexts.** Deduplicate chunks by id across sub-questions, keep a note of which sub-question
  retrieved each chunk, and run one generation with the original question. One generation call, simplest
  citations, but the context budget is split n ways and the model must do the composing itself.
- **Answer per sub-question, then synthesise.** Generate a short, cited answer to each sub-question from
  its own evidence, then a final call composes them. This isolates reasoning, scales to larger evidence
  sets, and makes it visible when one sub-question had no evidence, at the cost of n + 1 generation calls.
  The sub-answer prompt must allow "insufficient evidence", because otherwise an empty retrieval turns into
  a confident parametric guess that the synthesis step cannot detect.

### Step 5: synthesise with provenance
The final prompt holds the original question, the sub-questions, and the evidence or sub-answers tagged by
sub-question, so citations stay attached to the facet they support. Keeping the original question in the
prompt matters because the decomposition may have lost tone or an implicit constraint that the model can
recover when it sees both.

### Why not let a single agent loop do all this?
It can, and agentic RAG is the generalisation. The explicit decomposition pattern is preferred when the
question shape is predictable, because fan-out is parallel and bounded (n retrievals, known latency),
whereas an agent loop is sequential and open-ended. Use decomposition for breadth, the agent loop for
depth.

## Example
A fan-out decomposition with parallel retrieval and merged evidence. `llm` and `retrieve` are stubs for
any provider and vector store; `retrieve` returns `(chunk_id, text)` pairs.

```python
import asyncio, json

PLAN = """Question: {q}
If this question has multiple independent parts, split it into 2-4 standalone sub-questions.
Each sub-question must repeat any shared constraint (region, plan, date). If the question is
atomic, return it unchanged. Return JSON: {{"sub_questions": [...]}}"""

async def retrieve_async(q, k):
    return await asyncio.to_thread(retrieve, q, k)   # wrap a sync retriever

async def decomposed_search(question, k_each=5):
    subs = json.loads(llm(PLAN.format(q=question)))["sub_questions"]
    results = await asyncio.gather(*(retrieve_async(s, k_each) for s in subs))
    evidence, seen = [], set()
    for sub, hits in zip(subs, results):
        for cid, text in hits:
            if cid not in seen:                       # dedupe across sub-questions
                seen.add(cid); evidence.append({"sub": sub, "id": cid, "text": text})
    return subs, evidence

ANSWER = """Original question: {q}
Sub-questions and evidence (each chunk is tagged with the sub-question that retrieved it):
{ev}
Answer the original question. Cite chunk ids. If a sub-question has no supporting chunk,
say so instead of guessing."""

async def answer(question):
    subs, evidence = await decomposed_search(question)
    ev = "\n".join(f"[{e['id']}] ({e['sub']}) {e['text']}" for e in evidence)
    return llm(ANSWER.format(q=question, ev=ev))

print(asyncio.run(answer(
    "Compare the refund windows of the Standard and Enterprise plans for EU customers.")))
# planner output, illustrative: ["What is the refund window of the Standard plan for EU customers?",
#                                "What is the refund window of the Enterprise plan for EU customers?"]
```

## Trade-offs
- n sub-questions mean n retrievals (and n extra generations if you answer per sub-question); parallel
  fan-out keeps wall-clock near one retrieval, but p95 latency becomes the slowest branch.
- A good split sharpens every facet's recall; a bad split is worse than none, so the gate that leaves
  atomic questions alone matters as much as the splitter.
- Per-sub-question answers give traceability and surface missing evidence, but they lose cross-evidence
  reasoning that a single merged context would allow; merge when facets interact, separate when they don't.
- Sharper sub-queries rank facet-specific passages higher but demote a single passage covering both facets;
  deduplicated merging makes this harmless, but it changes which chunk is "best".

## Pitfalls
- Over-decomposition of simple questions; the symptom is that lookups which used to be fast and right
  became slower and noisier after the feature shipped.
- Dropped shared constraints; the symptom is a synthesis that compares the right plans for the wrong
  region, because "for EU customers" fell off one sub-question.
- A sub-question with no evidence answered from parametric memory; the symptom is a comparison where one
  side has precise, cited numbers and the other has plausible, uncited ones.
- Exceeding the context budget with n × k chunks; the symptom is silent truncation of the evidence for
  whichever sub-question came last.
- No deduplication across sub-questions; the symptom is repeated passages in the prompt and a model that
  over-weights whatever was repeated.
- Treating a dependent chain as independent; the symptom is a second sub-question like "what is the
  manager's email?" that is vague on its own and retrieves nothing useful.

## When to use
Reach for decomposition when the question shape is "compare", "list all", "A and also B", or anything
that names several entities or facets; a cheap gate (the planner's atomic branch, or a classifier on
question length and conjunctions) keeps it away from simple lookups. Do not use it when latency is
tight and the corpus is small enough that a single wide retrieval plus a reranker already covers both
facets. Measure per-sub-question recall, final-answer accuracy on the compound-question slice of the
golden set, the fraction of queries decomposed, and the latency distribution. In interviews, the question
"a user asks to compare two products and the bot only talks about one" is a decomposition question; the
strong answer also says how dependent sub-questions turn into multi-hop retrieval.

## Role lens
- **Engineer:** Implements the planner prompt with an atomic branch, parallel retrieval, deduplicated
  merging, and a synthesis prompt that refuses to fill gaps without evidence.
- **Architect:** Decides whether decomposition is a fixed pipeline stage or a capability of an agent loop,
  and sets the per-sub-question budget so fan-out cannot blow the context or the cost model.
- **FDE:** Spots compound questions in a customer's query logs, builds the golden slice that proves the
  gain, and tunes the planner to the customer's entities and constraints.

## Check yourself
**Q:** Users ask "Compare the data-retention defaults of our Standard and Enterprise plans for EU
customers" and the bot answers only about Standard. Design the decomposition and name two ways the
design can go wrong.
**A:** Have a planner split the question into two standalone sub-questions that each repeat the EU
constraint: the retention default for Standard in the EU and the same for Enterprise. Retrieve for both
in parallel with a small k each, deduplicate the chunks, tag each with its sub-question, and run one
synthesis prompt with the original question and the tagged evidence, requiring citations and an explicit
"no evidence" statement per facet. Failure modes: the planner drops "EU" from one sub-question and the
synthesis compares different regions; or the Enterprise retrieval returns nothing and the model fills in
a plausible number from memory, which only an "insufficient evidence" rule and per-facet recall
measurement would catch. A third is cost: without a gate, every simple question pays for the planner.

## Quiz
### Q: Why does a single dense retrieval tend to miss half of a two-part comparison question?
- [ ] Embedding models cannot encode questions longer than one sentence
- [x] The question's embedding is a mixture of two intents and lands near neither region, so the ranking is dominated by whichever half matches best
- [ ] Vector databases return only one document per topic
- [ ] Comparison questions always require a knowledge graph
> One vector represents one point; a two-topic question sits between the two relevant clusters, and the
> top-k fills with passages about the stronger match while the other facet stays in the tail.

### Q: When should sub-questions run sequentially instead of in parallel?
- [ ] When there are more than three sub-questions
- [ ] When the retriever is a sparse BM25 index
- [x] When a sub-question can only be formed from the answer to a previous one
- [ ] When the context budget is small
> Dependent sub-questions ("who manages the author of the report?") need the earlier answer to exist as
> a query; that chain is multi-hop retrieval. Independent facets fan out in parallel for lower latency.

### Q: One side of a comparison comes back with precise cited numbers and the other with plausible uncited ones. What is the most likely cause?
- [ ] The reranker favoured one plan's documents
- [x] One sub-question retrieved no evidence and the model answered it from parametric memory
- [ ] Deduplication removed the second plan's chunks
- [ ] The embedding model was trained on one plan's documents
> An empty retrieval for a facet is invisible to the synthesis step unless the sub-answer prompt must
> state "insufficient evidence"; without that rule the model fills the gap with a confident guess.
