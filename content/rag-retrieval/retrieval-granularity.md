---
id: retrieval-granularity
module: rag-retrieval
name: Retrieval granularity
kind: decision
difficulty: core
est_minutes: 12
status: mastery
agent_relevance: core
mastery: evaluate
code_tested: yes
prerequisites:
  - rag-retrieval/chunking-strategies
  - model-internals/context-window
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/chunk-overlap
  - rag-retrieval/parent-document-retrieval
  - rag-retrieval/contextual-retrieval
  - rag-retrieval/lost-in-the-middle
  - rag-retrieval/reranking
  - rag-retrieval/recall-vs-precision-trade-off
  - model-internals/context-window
sources:
  - Chen et al., 2023 — Dense X Retrieval: What Retrieval Granularity Should We Use?
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
  - Official docs: LlamaIndex node parsers and retrievers
---

## TL;DR
Granularity is what one search hit hands your agent: a sentence, a section or a document. Small units
match sharply but starve the agent of context; large ones blur vectors and eat its window. Match small,
return the parent, or let the agent expand hits.

## For agent builders
- **Why it matters for agents.** An agent reads every tool result into a context it shares with tool
  schemas, history and reasoning. The return unit decides whether one search answers the step or
  whether the agent burns more calls hunting for the qualifier in the next sentence.
- **Where it shows up in your stack.** LangChain `ParentDocumentRetriever`, LlamaIndex
  `SentenceWindowNodeParser` and auto-merging retrievers, and in agent design as a two-tool pattern:
  a search tool that returns short snippets with ids plus a read tool that fetches the parent section.
- **What you control.** Match unit, return unit, parent scoring (max or sum), the evidence token budget
  per tool call, and whether expansion is automatic or a decision the agent makes.
- **What you can skip.** Proposition rewriting and late-interaction models until a golden set shows
  small-to-big with parent dedupe is not enough.

## Why it exists
- **It is a different question from chunking.** Chunking decides how a document is cut; granularity
  decides which cut is the unit of retrieval, scoring and return.
- **Matching favours small units.** A vector is a fixed-size summary of its input. A sentence's vector
  says one thing and matches that question sharply; a section's vector says ten things and may lose to a
  shorter chunk that mentions the topic in passing.
- **Generation favours large units.** "The cap is 12 months of fees" is useless without the clause that
  says which cap, under which contract, with which exceptions; alone it produces confident, wrong
  answers. A whole document wastes the budget, and long contexts make models worse at using facts buried
  in the middle.
- **One compromise size is mediocre at both jobs.** Nothing forces the match unit and the return unit to
  be the same object. Match on sentences, return sections, and the compromise disappears; what remains
  is how units point to their parents.

## How it works
### The ladder of units
| Unit (fine to coarse) | What it is |
|---|---|
| **Propositions** | atomic factual statements, often rewritten by an LLM so each stands alone |
| **Sentences** | one sentence |
| **Windows** | a sentence plus a few neighbours |
| **Passages or chunks** | a few hundred tokens |
| **Sections** | heading-delimited |
| **Documents** | the whole file |
| **Document summaries** | a generated digest per document |

- Each level can be the match unit, the return unit, or both.
- Finer sharpens matching and raises the number of vectors (cost, index size); coarser does the reverse.

### Decoupling match from return
Index the small unit with a pointer to its parent: `{unit_id, text, parent_id, position}`. At query time:
1. Retrieve the top-N small units.
2. Map each to its parent.
3. Deduplicate parents (several sentences from one section are one hit).
4. Score each parent by its best child, or by a sum that rewards sections with several matching sentences.
5. Return the top-k parents to the agent.

This works *because* sentence vectors stay sharp for matching while the agent gets the section that
makes the sentence interpretable. It is the mechanism behind sentence-window retrieval, parent-document
retrieval and auto-merging retrievers in common frameworks.

### Agent-driven expansion
- **The agent can be the parent lookup.** The search tool returns short snippets with `parent_id`; a
  second tool returns the parent on request. The agent reads parents only for hits it judges relevant.
- **Why it helps:** most snippets are rejected cheaply, so the budget goes to the one or two parents
  that matter. Coding agents already work this way: search, then open the file.
- **What it costs:** an extra tool call, and therefore an extra model step, per expanded hit, plus the
  risk the agent answers from the snippet without expanding. Prompt it to expand before quoting numbers.

### Variants
- **Sentence window:** match a sentence, return it plus `w` neighbours on each side. Cheap and local;
  good for factoids whose answer lives within a few sentences.
- **Parent chunk:** match small chunks (say 128 tokens), return the enclosing larger chunk (say 1,024
  tokens) or section. The standard small-to-big setup.
- **Auto-merging:** if enough children of one parent are retrieved, replace them with the parent;
  otherwise keep the children. Adapts the returned size to how concentrated the evidence is.
- **Summary-then-chunk:** match per-document summaries to pick documents, then chunks within them. A
  two-level index that scales to large corpora where many documents share vocabulary.
- **Proposition retrieval:** rewrite text into self-contained statements before embedding, so each unit
  carries its subject and no dangling references. Chen et al., 2023 report question-answering gains, at
  the cost of an LLM pass over the whole corpus at indexing time.
- **Multi-vector (late interaction):** the finest match unit is the token. ColBERT-style models keep one
  vector per token and score a passage by summing, over query tokens, each one's best match among
  passage tokens. The return unit stays the passage.
- **Why late interaction is decoupling at its limit:** matching sees fine detail without a separate
  parent lookup, paid for with one vector per token instead of one per chunk.

### Making the small unit self-sufficient
- **"The cap is twelve months of fees" embeds well only if the vector knows *which* cap.**
- Carry context into the embedded text: the heading path, the document title, or a short generated
  description of where the span sits (contextual retrieval).
- **The finer the match unit, the more it depends on this**, because a sentence has less of its own
  context to lean on than a 500-token passage. Snippets shown to an agent need it too, or the agent
  cannot judge which hit to expand.

### Scoring parents
| Scoring | Favours | Question type |
|---|---|---|
| Max child | one strongly matching sentence | "what is the default timeout" |
| Sum or count | topic discussed at length | "explain the retry policy" |

Many systems retrieve more parents than needed and let a cross-encoder reranker, which reads query and
parent text together, settle the order.

### Interaction with the context budget
- **A budget of, say, 4,000 tokens of evidence** fits forty 100-token sentence windows, eight 500-token
  passages or two 2,000-token sections.
- **Usage degrades** as the prompt grows and as relevant facts move away from the beginning and end. In
  an agent, earlier tool results pile up, so each new result lands further into a long context.
- **So fewer, larger, well-chosen parents** usually beat filling the budget with small fragments.
  Granularity is also a lever on lost-in-the-middle effects.

## Diagram
Follow the small-to-big path: small units are matched, mapped to parents, deduplicated and scored, and
only bounded parents reach the agent's evidence budget.

```mermaid
flowchart TD
    Q["Agent search query"] --> V[("Small units, each with parent_id")]
    V -->|"top-N units"| P["Map each unit to its parent"]
    P --> D["Deduplicate parents"]
    D --> S{"Question type?"}
    S -->|"pointed factoid"| MX["Score parent by max child"]
    S -->|"explain or compare"| SM["Sum, count or auto-merge"]
    MX --> R["Optional cross-encoder rerank"]
    SM --> R
    R --> B["Pack top-k parents into budget"]
    B --> G["Tool result to agent"]
```

## Examples
### Small-to-big: match sentences, return sections
Sentence-level matching and section-level return with a local embedding model. Each sentence carries a
`parent` pointer; parents are deduplicated and scored by their best sentence.

```python
import re, numpy as np
from sentence_transformers import SentenceTransformer

sections = {
    "billing/caps": "Fees are capped per contract year. The cap is twelve months of fees. "
                    "Caps exclude taxes and third-party pass-through costs.",
    "billing/invoicing": "Invoices are issued monthly. Payment is due within thirty days. "
                         "Late payments accrue interest at the statutory rate.",
    "support/hours": "Support operates on weekdays. Enterprise plans include weekend coverage.",
}
model = SentenceTransformer("BAAI/bge-small-en-v1.5")

units, parent = [], []
for sid, text in sections.items():
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        units.append(sent); parent.append(sid)
E = model.encode(units, normalize_embeddings=True)          # one vector per sentence

def retrieve(query: str, k_units: int = 6, k_parents: int = 2):
    q = model.encode([query], normalize_embeddings=True)[0]
    scores = E @ q                                             # cosine on unit vectors
    best = {}
    for i in np.argsort(-scores)[:k_units]:                   # match small ...
        p = parent[i]
        best[p] = max(best.get(p, -1.0), float(scores[i]))     # ... dedupe and score parents by max child
    ranked = sorted(best.items(), key=lambda kv: -kv[1])[:k_parents]
    return [(p, round(s, 3), sections[p]) for p, s in ranked]  # ... return big

for hit in retrieve("how much can fees be capped at?"):
    print(hit)
# Ran with sentence-transformers 6.2.0: "billing/caps" first (0.841), "billing/invoicing" second.
# The best child is "Fees are capped per contract year", not the twelve-months sentence, which is why
# returning the section matters: the generator still gets the figure and the tax exclusion.
```

### Agent scenario: search snippets plus a read tool
Two tools built on the index above. The agent decides which hits deserve the full parent.

```python
TOOLS = [
    {"name": "search", "description": "Find candidate passages. Returns one-sentence snippets "
     "with a section_id. Snippets omit exceptions: call read_section before quoting a number.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]}},
    {"name": "read_section", "description": "Return the full text of one section by section_id.",
     "input_schema": {"type": "object", "properties": {"section_id": {"type": "string"}},
                      "required": ["section_id"]}},
]

def run_tool(name, args):
    if name == "search":
        q = model.encode([args["query"]], normalize_embeddings=True)[0]
        top = np.argsort(-(E @ q))[:5]
        return "\n".join(f"{parent[i]}: {units[i]}" for i in top)
    if name == "read_section":
        return sections.get(args["section_id"], "Unknown section_id.")
```

- **Typical trace:** search returns "billing/caps: The cap is twelve months of fees." The agent calls
  `read_section("billing/caps")`, sees the tax exclusion, and answers with the qualifier.
- **The tool description does real work:** it tells the model when expansion is required.

### Auto-merging in a few lines
Return the parent when evidence is concentrated, the children when it is scattered. Pure Python.

```python
def auto_merge(child_hits, children_of, ratio=0.5):
    """Replace a parent's retrieved children with the parent when enough of them matched."""
    out = {}
    for pid, kids in children_of.items():
        hit = [c for c in kids if c in child_hits]
        if not hit:
            continue
        if len(hit) / len(kids) >= ratio:                     # evidence concentrated: return parent
            out[pid] = max(child_hits[c] for c in hit)
        else:                                                 # scattered: keep the small units
            out.update({c: child_hits[c] for c in hit})
    return sorted(out.items(), key=lambda kv: -kv[1])

children_of = {"retry-policy": ["r1", "r2", "r3", "r4"], "timeouts": ["t1", "t2", "t3", "t4"]}
child_hits = {"r1": 0.71, "r2": 0.74, "r3": 0.69, "t2": 0.66}
print(auto_merge(child_hits, children_of))
# [('retry-policy', 0.74), ('t2', 0.66)]: 3 of 4 retry children matched, so return the section
```

### Worked scenario: spending a 4,000-token evidence budget
| Return unit | Units that fit | Typical outcome |
|---|---|---|
| 100-token sentence windows | 40 | sharp but fragmented; "explain" questions never add up |
| 500-token passages | 8 | balanced default for mixed question types |
| 2,000-token sections | 2 | full context; one wrong parent wastes half the budget |

- **Applied to a legal-search case (illustrative):** on 1,024-token chunks the clause holding the
  liability cap ranks fourth, behind chunks about liability in general.
- **The expected fix:** sentence matching with clause-level return sharpens the match on the cap, while
  prompts stay bounded because parents are clauses, not whole contracts. Confirm the gain with
  parent-level recall@k on a golden set rather than assuming it.
- **Agent variant (illustrative):** five 30-token snippets cost about 150 tokens; expanding the one
  relevant 500-token clause brings the step to about 650 tokens, against 2,500 for five passages.

## Production patterns
- **Parent-document retriever** — when pointed questions rank diluted chunks above the precise one, use
  LangChain's `ParentDocumentRetriever` with a small `child_splitter` and a larger `parent_splitter`
  (children in the vector store, parents in a docstore). In LangChain 1.x it is imported from
  `langchain_classic.retrievers` (checked against langchain-classic 1.0.8). Monitor parent-level
  recall@k and prompt tokens.
- **Sentence window with metadata replacement** — for factoid-heavy corpora, LlamaIndex's
  `SentenceWindowNodeParser` stores neighbours in metadata and `MetadataReplacementPostProcessor` swaps
  them in after retrieval. Monitor answers that miss a qualifier from just outside the window.
- **Search plus read tools** — for agents, return snippets with ids from search and parents from a
  separate read tool. Monitor in traces how often the agent expands before quoting, and tool calls per
  task.
- **Over-retrieve parents, then rerank** — fetch more parents than you will send (for example 20 for a
  final 5) and let a cross-encoder order them. Monitor reranker latency p95 and the rank of the gold
  parent before and after reranking.
- **Hard evidence budget** — pack parents in rank order until a fixed evidence token cap per tool call,
  truncating or dropping the rest rather than growing the prompt. Monitor prompt tokens p95 and
  truncation rate.
- **Granularity per question type** — route factoid queries to max-child scoring and explanatory or
  comparison queries to sum scoring or auto-merging. Monitor eval scores split by query type, not one
  blended number.

## Trade-offs
- **Fine match units vs vector count.** A sentence index holds roughly (passage length / sentence
  length) times as many vectors as a passage index, often an order of magnitude more for few-hundred-
  token passages, which matters at tens of millions of units.
- **Large return units vs budget and position.** They give context but spend the budget faster and push
  facts toward the middle of the prompt, where models use them least reliably; the flip point is your
  context budget.
- **Automatic vs agent-driven expansion.** Automatic parents cost tokens on every hit; agent-driven
  expansion saves tokens but adds steps and latency, and fails when the model skips the read.
- **Max-child vs sum scoring.** Max is simple and good for pointed factual questions; sum rewards depth
  and suits explanatory ones. Choose per query type or let a reranker decide.
- **Propositions vs indexing cost.** Self-contained units brought reported retrieval and QA gains
  (largest for retrievers not trained on the target domain), but cost an LLM pass over the corpus at
  indexing time and on every update; not worth it on large, fast-changing corpora.
- **Summary-then-chunk scale vs unrecoverable misses.** It scales on large corpora with repeated
  vocabulary, but if the first stage picks the wrong document, the second stage cannot recover.

## Pitfalls
- **Returning the matched sentence alone.** Answers quote a fact correctly but attach it to the wrong
  entity or ignore an exception in the next sentence. Diagnose: run faithfulness checks on golden
  questions whose answers carry exceptions. Fix: return a window or the parent.
- **Not deduplicating parents.** The top-k is three sentences from one section; one source dominates
  and other relevant sections never appear. Diagnose: log distinct parents per result. Fix: collapse
  children into one parent hit.
- **One size for matching and returning.** Tuning it endlessly gives a recall-versus-answer-quality
  seesaw no single value settles. Fix: decouple match and return units.
- **Parents so large that two fill the context.** Prompts get long, generations slow and costly, middle
  facts are ignored, and earlier agent results get truncated. Diagnose: prompt tokens p95 and
  truncation rate per step. Fix: bound parents and pack to an evidence budget.
- **Snippets the agent cannot judge.** Bare sentences with no section name, so the agent expands the
  wrong hit or none. Diagnose: in traces, compare expanded ids with the gold parent. Fix: put the
  heading path or title in each snippet.
- **Fixed granularity across question types.** Factoid queries do well while "explain" or "compare"
  queries get fragments that never add up. Diagnose: split eval scores by query type. Fix: route scoring
  by type.

## When to use
- **Decouple when:** you see precision problems (large chunks scoring middling for pointed questions) or
  context problems (small chunks producing answers that lack qualifiers).
- **Start with:** sentence- or small-chunk matching, section-level return, parent dedupe and max-child
  scoring; move to auto-merging or sum scoring when explanatory queries dominate.
- **Agent-driven expansion when:** the agent searches often and most hits are irrelevant, so paying for
  parents only on chosen hits saves real context.
- **Keep whole-document retrieval:** for short documents such as FAQs or tickets, where the document is
  already the right unit.
- **Measure:** parent-level recall@k, answer faithfulness and prompt token usage together, because
  granularity moves all three.

## Exercise
**Goal:** decide, with evidence, which granularity set-up to ship for one corpus. No solution is given.
- **Inputs:** a document set with headings (a product manual or policy set), 30 golden questions
  labelled factoid or explanatory with their gold section, and an evidence budget of 4,000 tokens.
- **Deliver:** three retrievers over one corpus (passage-only, sentence match with section return and
  max-child scoring, and auto-merging) and a table of parent-level recall@5, evidence tokens per call
  and answer correctness, split by question type.
- **Acceptance:** no call exceeds the budget; duplicate parents never appear in one result; you
  recommend one set-up per question type and cite the failing questions that drove it.

## Role lens
- **Engineer:** Stores parent pointers on every unit, implements parent dedupe and scoring in the
  retriever, and tracks parent-level recall rather than chunk-level recall in the eval harness.
- **Architect:** Decides the index hierarchy (units, parents, summaries) and its storage cost, and sets
  the evidence token budget that bounds how large returned units may be.
- **FDE:** Maps a customer's question mix (factoid versus explanatory) to a granularity choice and
  shows side-by-side answers from fragment-only and parent-return setups to make the case.

## Check yourself
**Q:** A legal-search product indexes 1,024-token chunks. Users report that pointed questions ("what is
the liability cap?") return chunks that discuss liability generally, while the clause with the number
ranks fourth. How would you restructure retrieval, and what would you watch for?
**A:** The 1,024-token vectors are diluted: a chunk about liability in general matches the query about
as well as the chunk holding the specific cap. I would index at sentence or small-chunk level, keep a
pointer from each unit to its enclosing clause or section, retrieve the top units, deduplicate by
parent and score parents by their best child, then return the parent clause so the generator sees the
cap together with its exceptions. I would watch three things: index size, which grows with finer
units; parent-level recall@k on a golden set of clause questions, which should rise; and prompt length,
which should not balloon because parents are bounded clauses rather than whole contracts. If
explanatory queries regress, I would add sum-based parent scoring or a reranker over the parents.

## Interview questions
### Design small-to-big retrieval for a product manual. What do you store and what happens at query time?
- **Store** small units (sentences or about 128-token chunks) with `{unit_id, text, parent_id, position}`,
  and parents (sections or larger chunks) in a docstore.
- **Query:** retrieve top-N units, map to parents, deduplicate, score by best child, return the top-k
  parents.
- **Make units self-sufficient** by embedding the heading path or title with each small unit.

### Would you give an agent automatic parent expansion or a separate read tool? Defend the choice.
- **Read tool** when the agent searches many times per task and most hits are noise: snippets are cheap,
  and only chosen parents spend context.
- **Automatic expansion** when tasks are single-shot or latency-sensitive, because each read costs a full
  model step, and a weaker model may skip the read and answer from a fragment.
- **Decide on evidence:** compare task success, tokens and steps per task on the same golden set.

### When would you not use sentence-level matching?
- **Short documents** such as FAQs or tickets are already the right unit, so whole-document retrieval is
  simpler.
- **Very large corpora:** a sentence index can hold an order of magnitude more vectors than a passage
  index, which costs storage and latency at tens of millions of units.
- **Explanatory-heavy traffic** may be better served by passage units with sum scoring or auto-merging.

### Your agent quotes the right number but misses an exception stated in the next sentence. What is wrong?
- **The return unit is too small:** the tool is sending the matched sentence alone, or the agent did not
  call the read tool before answering.
- **Fix:** return a sentence window or the parent clause, so the qualifier travels with the fact, or make
  the tool description require reading the section before quoting figures.
- **Verify** with faithfulness checks on a golden set of questions whose answers carry exceptions.

## Quiz
### Q: Why does indexing small units and returning larger parents outperform a single compromise chunk size?
- [ ] Because small units are cheaper to embed, which frees budget for a bigger model
- [x] Because sharp small-unit vectors improve matching while the returned parent restores the context the generator needs
- [ ] Because vector databases search faster over shorter texts
- [ ] Because larger parents always contain the answer to any question about the document
> Matching and generation want different sizes. Decoupling lets each get its ideal: precise matching on
> focused vectors, and complete context from the parent. Search speed depends on vector count and
> dimension, not text length, and parents do not guarantee answers.

### Q: Several sentences from the same section rank in the top-N. What should the retriever do before returning results?
- [ ] Return each sentence as a separate result so the generator sees all of them
- [ ] Discard all but the lowest-scoring one to reduce bias
- [x] Collapse them into one parent hit, scored by the best (or combined) child score
- [ ] Re-embed the section and search again with the new vector
> Parent deduplication turns several child matches into a single section result, freeing top-k slots
> for other sections and letting the generator read the section once. Re-embedding at query time adds
> cost without changing the outcome.

### Q: Which question type most benefits from sum- or count-based parent scoring rather than max-child scoring?
- [ ] "What is the default connection timeout?"
- [x] "Explain how the retry policy works across services"
- [ ] "Which page mentions the product's launch date?"
- [ ] "Is the API key rotated automatically?"
> Explanatory questions are answered by sections that discuss the topic at length, which several
> matching children indicate. Pointed factual questions want the single best sentence, where max-child
> scoring is the right signal.

## Mastery test
**Problem:** an HR-policy agent matches 64-token chunks and returns whole policy documents (about 6,000
tokens each). Accuracy is high on the first search, but on multi-step tasks later answers ignore
facts the agent already retrieved, and cost per task tripled. What is happening and what do you change?

**A strong answer:**
- sees that two or three whole-document returns fill the window, so earlier tool results drift into
  the middle or get truncated;
- bounds the return unit to the policy section, dedupes parents and packs to a fixed evidence budget,
  or switches to snippets plus a `read_section` tool;
- keeps the small match unit, since matching was not the problem;
- measures tokens per step, truncation rate, and accuracy on later steps of multi-step tasks.

## Use cases & projects
### Use cases
- **Legal and policy agents.** Matching on sentences but returning the parent clause keeps the exceptions
  and scope that make a cap or limit meaningful.
- **Research agents with a token budget.** A search tool that returns short snippets with ids, plus a read
  tool that fetches the parent section, lets the agent decide when the extra tokens are worth it.
- **FAQ and help-centre agents.** Pointed factual questions are served well by small units with max-child
  scoring, so the agent gets one precise answer per call.
- **Long-document assistants.** Small-to-big with parent dedupe stops five sibling hits from returning the
  same section five times.

### Mini project
**Build a two-tool search and read agent (2-3 hours).**
- **Goal:** compare fixed-granularity retrieval with an agent that expands hits on demand.
- **Steps:** split documents into sections and sentences with parent ids; build `search(query)` returning
  sentence snippets with ids and `read_section(id)` returning the parent; run 20 questions through an agent
  with only search, with auto-parent return, and with both tools; log accuracy, tool calls and tokens.
- **Done when:** a table shows accuracy, calls per question and tokens for the three set-ups, and you can say
  which question types needed the read tool.
