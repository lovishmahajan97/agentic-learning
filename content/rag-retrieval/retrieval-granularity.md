---
id: retrieval-granularity
module: rag-retrieval
name: Retrieval granularity
kind: decision
difficulty: core
est_minutes: 12
status: fact-checked
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
Granularity is the size of the unit you retrieve: sentence, proposition, passage, section or whole
document. Small units make sharp vectors and precise matches but starve the generator of context;
large units carry context but blur the vector and crowd the prompt. The resolving move is to separate
the unit you *match on* from the unit you *return*: index small, return the enclosing parent. The
right pair depends on the question type and the context budget, and should be measured, not assumed.

## Why it exists
Chunking decides how a document is cut; granularity asks a different question: which cut should be the
unit of retrieval, scoring and return? The tension is built into dense retrieval. A vector is a fixed
size summary of its input. Embed a single sentence and the vector says exactly one thing, so a question
asking that thing matches it sharply. Embed a whole section and the vector says ten things at once, so
the same question matches it only moderately and may lose to a shorter chunk that mentions the topic
in passing. Precision at match time therefore favours small units.

The generator wants the opposite. A matched sentence such as "the cap is 12 months of fees" is useless
without the surrounding clause that says which cap, under which contract, with which exceptions.
Returning only the sentence produces confident, wrong answers. Returning the whole document fixes the
context but wastes most of the prompt budget on irrelevant text, and long contexts make models worse at
using facts buried in the middle. Context at generation time therefore favours large units.

The naive solution, one chunk size chosen as a compromise for both jobs, is exactly that: a compromise
that is mediocre at both. The insight behind retrieval granularity is that nothing forces the match
unit and the return unit to be the same object. Match on sentences, return sections, and the compromise
disappears; what remains is a set of engineering choices about how units point to their parents.

## How it works
### The ladder of units
From fine to coarse: **propositions** (atomic factual statements, often rewritten by an LLM so each
stands alone), **sentences**, **windows** (a sentence plus a few neighbours), **passages or chunks**
(a few hundred tokens), **sections** (heading-delimited), **documents**, and **document summaries**.
Each level can serve as the match unit, the return unit, or both. Going finer sharpens matching and
raises the number of vectors (cost, index size); going coarser does the reverse.

### Decoupling match from return
Index the small unit with a pointer to its parent: `{unit_id, text, parent_id, position}`. At query
time, retrieve the top-N small units, map each to its parent, deduplicate parents (several sentences
from one section are one hit), score each parent by its best child (or by a sum that rewards sections
with several matching sentences), and return the top-k parents to the generator. This works *because*
the sharpness of sentence vectors is preserved for matching while the generator gets the section that
makes the sentence interpretable. It is the mechanism behind sentence-window retrieval, parent-document
retrieval and auto-merging retrievers in common frameworks.

### Variants
- **Sentence window:** match on a sentence, return that sentence plus `w` neighbours on each side.
  Cheap and local; good for factoids where the answer lives within a few sentences.
- **Parent chunk:** match on small chunks (say 128 tokens), return the enclosing larger chunk (say
  1,024 tokens) or section. The standard small-to-big setup.
- **Auto-merging:** if enough children of one parent are retrieved, replace them with the parent;
  otherwise keep the children. Adapts the returned size to how concentrated the evidence is.
- **Summary-then-chunk:** match on per-document summaries to pick documents, then on chunks within
  the chosen documents. A two-level index that scales to large corpora where many documents share
  vocabulary.
- **Proposition retrieval:** rewrite text into self-contained statements before embedding, so each
  unit carries its own subject and no dangling references. Research on this (Chen et al., 2023)
  reports gains on question answering at the cost of an LLM pass over the whole corpus at indexing
  time.

### Scoring parents
Max-child scoring is simple and favours sections with one strongly matching sentence; sum or
count-based scoring favours sections where the topic is discussed at length. Which is right depends on
the question: "what is the default timeout" wants max; "explain the retry policy" wants breadth. Many
systems retrieve more parents than needed and let a cross-encoder reranker, which reads query and
parent text together, settle the order.

### Interaction with the context budget
With a budget of, say, 4,000 tokens of evidence, you can fit forty 100-token sentence windows, eight
500-token passages or two 2,000-token sections. The generator's ability to use the evidence degrades as the
prompt grows and as relevant facts move away from the beginning and end, so returning fewer, larger,
well-chosen parents is usually better than filling the budget with many small fragments. Granularity
is thus also a lever on lost-in-the-middle effects.

## Example
Small-to-big retrieval with sentence-level matching and section-level return, using a local embedding
model. Each sentence carries a `parent` pointer; parents are deduplicated and scored by their best
sentence.

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
# Expect "billing/caps" first: the sentence about twelve months matches sharply, and the generator
# receives the whole section, including the exclusions it needs to answer correctly.
```

## Trade-offs
- Finer match units sharpen retrieval but multiply the number of vectors; a sentence index holds
  roughly (passage length / sentence length) times as many vectors as a passage index, often an order
  of magnitude more for few-hundred-token passages, which matters at tens of millions of units.
- Larger return units give the generator context but spend the budget faster and push facts toward
  the middle of the prompt, where models use them least reliably; the flip point is your context budget.
- Max-child parent scoring is simple and good for pointed factual questions, while sum-based scoring
  rewards depth and suits explanatory questions; choose per query type or let a reranker decide.
- Proposition rewriting yields self-contained units and reported retrieval and QA gains (largest for
  retrievers not trained on the target domain), but costs an LLM pass over
  the whole corpus at indexing time and again on every update; it flips to "not worth it" on large,
  fast-changing corpora.
- Two-level summary-then-chunk retrieval scales well on large corpora with repeated vocabulary, but
  adds a stage whose failure (wrong document chosen) cannot be recovered by the second stage.

## Pitfalls
- Returning the matched sentence alone; the symptom is answers that quote a fact correctly but attach
  it to the wrong entity or ignore an exception stated in the next sentence.
- Not deduplicating parents, so the top-k is three sentences from the same section; the symptom is
  one source dominating every answer and other relevant sections never appearing.
- Choosing one chunk size for matching and returning and tuning it endlessly; the symptom is a
  recall-versus-answer-quality seesaw that no single value settles.
- Making parents so large that two of them fill the context; the symptom is long prompts, slow and
  costly generations, and facts in the middle of the prompt being ignored.
- Treating granularity as fixed across question types; the symptom is factoid queries doing well while
  "explain" or "compare" queries get fragments that never add up to a complete picture.

## When to use
Decouple matching from returning as soon as you see either precision problems (large chunks scoring
middling for pointed questions) or context problems (small chunks producing answers that lack
qualifiers). Start with sentence- or small-chunk matching and section-level return, dedupe parents,
and score by max child; move to auto-merging or sum scoring when explanatory queries dominate. Keep
whole-document retrieval for short documents such as FAQs or tickets, where the document is already
the right unit. Measure recall@k at the parent level, answer faithfulness, and prompt token usage
together, because granularity moves all three. In interviews, the expected answer to "what chunk size
should we use" is "which size for matching and which for returning, and why they need not be equal".

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
