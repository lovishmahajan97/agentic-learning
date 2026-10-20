---
id: multi-hop-retrieval
module: rag-retrieval
name: Multi-hop retrieval
kind: pattern
difficulty: advanced
est_minutes: 15
related:
  - rag-retrieval/query-decomposition
  - rag-retrieval/agentic-rag
  - rag-retrieval/graphrag
  - rag-retrieval/knowledge-graphs
  - rag-retrieval/reranking
  - prompting/react
  - agents/agent-loop
  - evals/retrieval-evaluation
sources:
  - Yang et al., 2018 — HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering
  - Press et al., 2022 — Measuring and Narrowing the Compositionality Gap in Language Models
  - Trivedi et al., 2023 — Interleaving Retrieval with Chain-of-Thought Reasoning for Knowledge-Intensive Multi-Step Questions
---

## TL;DR
Some questions cannot be answered from any single passage because the second fact is only findable once
you know the first: "who manages the author of the report for incident 4812?" needs the author's name
before "manager of Alice" is even a query. One-shot retrieval finds the first hop at best, since the
terms that would retrieve the second do not exist yet. Multi-hop retrieval interleaves retrieving and
reading: retrieve, extract the bridging fact, form the next query, repeat under a hop limit, then answer.

## Why it exists
The failure here is not a ranking problem, so none of the usual ranking fixes apply. The document that
says "Alice reports to Bob" shares no terms and no topic with "incident 4812"; its embedding is nowhere
near the question's, and its BM25 score is zero. Raising k does not help, because the passage is not in
the tail of the ranking, it is outside the ranking altogether. A reranker cannot promote a candidate
that was never retrieved. Query rewriting cannot invent the name Alice before anyone has read the
incident report. The only way to reach the second document is to read the first, pull out the bridging
entity, and search again with it.

Benchmarks such as HotpotQA were built precisely to expose this: "bridge" questions, where an
intermediate entity links two documents, and "comparison" questions, where two entities must be
looked up and compared. Comparison questions fan out in parallel (that is query decomposition); bridge
questions are inherently sequential. Press et al. add the model-side observation that even when a model
can answer each hop, composing them fails at a measurable rate, and that making the hops explicit
narrows that gap.

The alternatives have their place. A knowledge graph answers "manager of the author of X" in one
traversal, but someone must have extracted the entities and relations offline and kept them fresh.
Stuffing the whole corpus into a long context works for small corpora and fails on cost and size for the
rest. Multi-hop retrieval needs neither preparation nor a giant window; it pays per question instead.

## How it works
### The loop
The state is the original question, the evidence gathered so far, and a hop counter.

1. **Plan the next query.** An LLM reads the question and the evidence collected so far and emits either
   the next search query or a signal that the evidence is sufficient. The next query is generated from
   the evidence, not from the question, because the bridging entity lives in the evidence.
2. **Retrieve.** Run the query through the normal retriever (hybrid plus reranker if you have one).
   Precision matters more here than in single-shot RAG, because a wrong top passage sends the next hop in
   the wrong direction.
3. **Accumulate.** Append new passages to the evidence, deduplicating by id. The evidence accumulates
   rather than being replaced, because the final answer needs both hops.
4. **Terminate.** Stop when the planner says "enough", when the hop cap is reached, or when a hop adds no
   new passages. All three conditions exist because each alone fails: the model alone may loop or stop
   early; a cap alone wastes hops on questions answered at hop one; no-progress detection catches the
   case where the planner keeps issuing the same query.
5. **Answer.** A final prompt with the question and all evidence produces the answer with citations per
   hop.

### Three named formulations
- **Self-ask** (Press et al.) has the model write "Follow up: ..." questions, answers each with a search
  call ("Intermediate answer: ..."), and finishes with "So the final answer is". The scaffold makes the
  hops legible and easy to log.
- **IRCoT** (Trivedi et al.) interleaves chain-of-thought with retrieval: each reasoning sentence the
  model writes becomes the next query, and retrieved passages feed the next sentence. It exploits the fact
  that a reasoning step naturally names the thing to look up next.
- **ReAct-style agent loops** generalise both: thought, action (search), observation, repeat. Agentic RAG
  is this with the retriever exposed as a tool; the discipline of hop caps and progress checks is the
  same.

### Forming a good second-hop query
The planner must extract the bridge entity and ask something specific: "Alice Chen manager" or "who does
Alice Chen report to", not "who is the manager?". Few-shot examples in the planner prompt showing the
extraction step are the cheapest way to get this right. Where entities are ambiguous (two Alices), the
planner should carry a disambiguating attribute (team, email) from hop one.

### Compounding error
If hop one retrieves the right passage with probability r1 and hop two, given a correct hop one, with
probability r2, the chain succeeds at roughly r1 × r2 (treating hops as independent, which is a
simplification). Illustratively, 0.9 × 0.9 ≈ 0.81 for two hops and ≈ 0.73 for three. This is why
per-hop precision, usually via a reranker, buys more in multi-hop than in single-shot RAG, and why hop
count should be the smallest that answers the question.

### Evaluating
Score each hop's supporting-fact recall (HotpotQA labels supporting sentences for exactly this), the
final answer, the hop-count distribution, and cost per question. Final-answer accuracy alone hides where
chains die.

## Example
A bounded multi-hop loop. `llm` returns text, `retrieve` returns `(id, text)` pairs; the planner emits
JSON so termination is explicit and loggable.

```python
import json

PLAN = """Question: {q}
Evidence so far:
{ev}
If the evidence answers the question, return {{"done": true}}.
Otherwise extract the entity or fact you still need, and return
{{"done": false, "query": "<a specific search query naming that entity>"}}"""

def multi_hop(question, max_hops=3, k=5):
    evidence, seen, trace = [], set(), []
    for hop in range(max_hops):
        ev = "\n".join(f"[{i}] {t}" for i, t in evidence) or "(none)"
        plan = json.loads(llm(PLAN.format(q=question, ev=ev)))
        if plan.get("done"):
            break
        hits = retrieve(plan["query"], k)
        new = [(i, t) for i, t in hits if i not in seen]
        trace.append({"hop": hop, "query": plan["query"], "new": len(new)})
        if not new:                      # no progress: stop instead of looping
            break
        seen.update(i for i, _ in new); evidence.extend(new)
    ev = "\n".join(f"[{i}] {t}" for i, t in evidence)
    answer = llm(f"Question: {question}\nEvidence:\n{ev}\nAnswer with citations, "
                 f"or say the evidence is insufficient.")
    return answer, trace

ans, trace = multi_hop("Who manages the engineer who wrote the report for incident INC-4812?")
# Illustrative trace:
#   hop 0  query="INC-4812 incident report author"      -> report: "Author: Alice Chen (Payments)"
#   hop 1  query="Alice Chen Payments team manager"     -> org page: "Alice Chen reports to Bob Ruiz"
#   hop 2  planner returns done=true; answer cites both passages
```

## Trade-offs
- Latency grows with hop count and hops are strictly sequential; gate with a classifier or let hop one
  decide, so single-hop questions never pay for a loop.
- The model can recover from a weak retriever by reformulating, but a wrong bridge entity poisons every
  later hop; reranking each hop raises per-hop precision and pays for itself here.
- Versus a knowledge graph there is no offline extraction and fresh data works immediately, but each
  question pays runtime cost; when the same relations are queried constantly, build the graph.
- Versus long-context stuffing the cost is bounded per hop rather than per corpus; for a corpus that fits
  comfortably in the window with caching, stuffing is simpler and often better.

## Pitfalls
- No hop cap or no-progress check; the symptom is runaway cost and timeouts on questions the corpus
  cannot answer.
- The model answers hop two from parametric memory and skips the search; the symptom is a confident
  final answer whose second fact cites no retrieved passage and cannot be reproduced for private entities.
- A generic second-hop query because the bridge entity was not extracted; the symptom is the same
  passages retrieved twice and a chain that stalls at hop two.
- Evidence overflowing the context across hops; the symptom is hop-one evidence truncated away by the time
  the answer is generated.
- Scoring only the final answer; the symptom is a dashboard that cannot show that most failures die at the
  second hop.
- Running comparison questions as sequential chains; the symptom is latency double what a parallel fan-out
  would cost.

## When to use
Use multi-hop when questions chain through entities the question itself does not name: org charts and
ownership (incident → service → owner), dependency graphs, legal cross-references ("as defined in section
3"), and "the author of X's other work". If the corpus already has a maintained knowledge graph, traverse
it instead. If questions are overwhelmingly single-hop, the loop should be gated so it only engages when
hop one leaves the question unanswered. Measure hop-level recall, hop-count distribution, final accuracy
on a multi-hop golden slice, and cost per question. In interviews, "what if the answer needs information
from two documents that share no terms" is the multi-hop question; the strong answer names the bridge
entity, the loop, its termination rules and compounding error.

## Role lens
- **Engineer:** Implements the planner prompt, the bounded loop with deduplication and progress checks, and
  per-hop logging so a failed chain shows exactly where it died.
- **Architect:** Decides between runtime multi-hop, an offline knowledge graph, or long-context stuffing
  for a given corpus and query mix, and sets the hop and cost ceilings.
- **FDE:** Finds the chained questions in a customer's logs ("who owns the service that broke"), builds the
  golden slice, and tunes the planner to the customer's entity types and naming conventions.

## Check yourself
**Q:** "Which on-call engineer owns the service that caused incident INC-4812?" Explain why single-shot
retrieval fails, design the hops, and name what could go wrong at each.
**A:** The incident report names the causing service but not its owner; the ownership record names the
service and engineer but never the incident, so no passage matches the whole question and no k rescues
the second document. Hop one: retrieve "INC-4812 root cause service", read the report and extract the
service name. Hop two: retrieve "<service> on-call owner" against the ownership or runbook pages. Stop
when the planner says the owner is found, with a cap of three hops and a no-progress check. Hop-one risks:
a stale duplicate report names a different service, so rerank for precision. Hop-two risks: a generic
query ("service owner") retrieves the wrong service, or the model fills in a name from memory, so require
that the final answer cite a retrieved passage for the owner and evaluate per-hop recall on a golden
slice.

## Quiz
### Q: Why does increasing top-k not help a bridge-type multi-hop question?
- [ ] Larger k slows the reranker too much
- [x] The second document shares no terms or topic with the question, so it is not in the ranking at all, not merely low in it
- [ ] Vector indexes cap results at a fixed number
- [ ] Multi-hop questions require sparse retrieval
> Ranking fixes only reorder candidates that scored at all; a passage about "Alice reports to Bob" has
> no similarity to "incident 4812" until the name Alice is known, which requires reading the first hop.

### Q: Which termination condition is sufficient on its own for a multi-hop loop?
- [ ] The model's "done" signal
- [ ] A fixed hop cap
- [ ] A check that the last hop added no new passages
- [x] None alone; the three are combined because each fails in a different way
> The model alone can loop or stop early, a cap alone wastes hops on simple questions, and no-progress
> detection alone never stops a planner that keeps finding marginally new passages.

### Q: Per-hop retrieval succeeds 90% of the time. Roughly how often does a three-hop chain succeed, treating hops as independent?
- [ ] About 90%, since each hop is independent
- [ ] About 30%, since errors add up linearly
- [x] About 73%, since the probabilities multiply
- [ ] About 97%, since later hops can correct earlier ones
> Each hop must succeed for the chain to succeed, so 0.9 × 0.9 × 0.9 ≈ 0.73; this is why per-hop precision
> via reranking and minimal hop counts matter more in multi-hop than in single-shot RAG.
