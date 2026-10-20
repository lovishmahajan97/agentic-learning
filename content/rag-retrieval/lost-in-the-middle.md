---
id: lost-in-the-middle
module: rag-retrieval
name: Lost-in-the-middle
kind: concept
difficulty: core
est_minutes: 13
related:
  - model-internals/context-window
  - model-internals/attention-mechanism
  - model-internals/positional-encoding
  - rag-retrieval/reranking
  - rag-retrieval/recall-vs-precision-trade-off
  - rag-retrieval/long-context-vs-rag
  - prompting/context-engineering
  - agents/context-compaction-summarization
sources:
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
  - Xiao et al., 2023 — Efficient Streaming Language Models with Attention Sinks
  - Official docs: Anthropic prompt engineering, long context tips
---

## TL;DR
Language models do not read a long prompt uniformly: information at the beginning and the end is used
far more reliably than information in the middle, the U-shaped curve measured by the lost-in-the-middle
experiments. For RAG, retrieving more chunks can lower answer quality even when the right chunk is
present, since every extra chunk is a distractor that pushes the evidence toward the middle. So retrieve
fewer and better, place the strongest evidence at the edges, and put the question last.

## Why it exists
The reflex when a RAG answer is wrong is to raise k. The retrieval eval says the gold chunk is in the
top-20 for 94% of questions, so why does the model miss it? The lost-in-the-middle experiments answered
that by controlling exactly one variable. Take a multi-document question-answering setup with k
retrieved passages, exactly one of which contains the answer, and move that passage from position 1 to
the middle to position k while holding everything else fixed. Accuracy is highest with the answer at the
start, next highest at the end, and lowest in the middle; in some configurations the middle placement
scored below answering with no documents at all. The effect grew with the number of passages, and the
extended-context variants of the models tested at the time did not escape it. A synthetic key-value
lookup task showed the same curve for several models, so it is not only about distractors that look like
the answer.

This matters because the naive mental model, "more context means more information means better
answers", treats the context window like a database. It is not. Attention is a competition over a fixed
budget of probability mass; retrieved chunks are by construction similar to each other and to the
question, so they split that mass among lookalikes, and the model's training has taught it where useful
things usually sit: instructions at the top, the question and answer at the bottom. A pipeline that
ignores position quietly converts retrieval wins into generation losses.

## How it works
### The shape of the curve
Plot accuracy against the position of the gold passage for a fixed k. The curve is U-shaped: a primacy
advantage at the first positions, a recency advantage at the last, a trough in between. The depth of the
trough is model-specific. Models trained since the original study, with long-context training data and
near-perfect single-fact needle-in-a-haystack scores, show a much shallower trough, but multi-fact
questions, reasoning over several passages and very long contexts still show positional sensitivity.
Treat it as a quantity to measure for your model and your chunk size, not as a constant.

### Proposed mechanisms
These are explanations offered in the literature, not settled facts.
1. **Primacy.** In a causal transformer every later token can attend to the first tokens, and the first
   positions tend to become attention sinks that absorb large attention mass regardless of content, so
   early material is repeatedly available and reinforced.
2. **Recency.** Rotary position encodings and the statistics of training text make nearby tokens attend
   to each other more strongly, and chat-format training puts the question right before the answer, so
   the model learns to look back a short distance for what matters.
3. **The middle has neither.** With many similar passages, the attention that would single out the gold
   passage is diluted across lookalikes, and nothing structural compensates.

### What it does to a RAG prompt
The natural layout is "concatenate chunks in rank order, then the question". Rank 1 lands at the start,
which is good; ranks 2 to 5, often the real evidence for multi-part questions, land in the early middle;
the last slot, the strongest position after the first, is occupied by the weakest chunk. Raising k makes
every one of these worse: more distractors, and the evidence deeper in the trough.

### Mitigations and why each works
1. **Retrieve fewer, better.** Rerank with a cross-encoder, then cut to a small k (5 to 8 is a common
   operating point; measure yours). Each removed distractor returns attention mass to the evidence and
   shortens the middle.
2. **Reorder for the edges.** Place rank 1 first, rank 2 last, rank 3 second, rank 4 second-to-last, and
   so on, so the best evidence sits at both ends of the U. LangChain ships this as a document
   transformer; it is ten lines to write yourself.
3. **Question last.** Put long documents first and the question and instructions after them, and repeat
   the key instruction at the end. Anthropic's long-context guidance recommends exactly this layout. The
   question then sits in the recency window immediately before generation, and the prefix of documents
   is stable, which is also what prompt caching wants.
4. **Structure the context.** Wrap each chunk in a tagged block with an id and source, and ask the model
   to quote the relevant sentences before answering. The quotes land at the end of the prompt, right
   before the answer, which converts a "find it in the middle" problem into a "read what you just wrote"
   problem.
5. **Compress.** Extract only the relevant sentences from each chunk (an LLM or a small extractive model)
   before the final prompt, so the middle is short.
6. **Map-reduce.** When a question genuinely needs many passages, answer per passage or per small group
   and combine, so no single prompt has a long middle.
7. **Measure.** Run a position sweep on your model with your chunk size before and after changes.

Worked numbers: 20 chunks of 400 tokens is an 8,000-token context with the gold chunk at rank 3 sitting
at position 3 of 20. Reranking and cutting to 6 chunks gives 2,400 tokens with the same chunk at
position 2 of 6, and after edge reordering it is the second block from the start.

## Example
The reorder function and a position-sweep harness: `position_sweep` builds k-document prompts with the
gold chunk at every position among random distractors and records accuracy per position, which is the
curve you tune against.

```python
import random

def reorder_for_edges(ranked):
    """rank1 first, rank2 last, rank3 second, rank4 second-to-last ... best evidence at both ends."""
    front, back = [], []
    for i, doc in enumerate(ranked):
        (front if i % 2 == 0 else back).append(doc)
    return front + back[::-1]

print(reorder_for_edges([1, 2, 3, 4, 5, 6, 7]))        # [1, 3, 5, 7, 6, 4, 2]

def build_prompt(docs, question):
    body = "\n\n".join(f'<doc id="{i + 1}">\n{d}\n</doc>' for i, d in enumerate(docs))
    return (f"{body}\n\nUsing only the documents above, quote the sentences that answer the question, "
            f"then answer.\nQuestion: {question}")             # documents first, question last

def position_sweep(llm, gold, distractors, question, grader, k=20, trials=30):
    """Accuracy as a function of where the gold chunk sits among k-1 distractors."""
    acc = {}
    for pos in range(k):
        correct = 0
        for _ in range(trials):
            ctx = random.sample(distractors, k - 1)
            ctx.insert(pos, gold)
            correct += grader(llm(build_prompt(ctx, question)))   # grader returns 1 or 0
        acc[pos] = correct / trials
    return acc
# Illustrative shape for a 20-chunk context: ~0.9 at position 0, ~0.65 around position 10, ~0.8 at 19.
# Run it per question type and per model; the trough depth is what tells you how much k you can afford.
```

## Trade-offs
- Cutting k after reranking removes distractors but costs recall; it flips when questions need many
  pieces of evidence (multi-hop, aggregation), where compression or map-reduce beats a smaller k.
- Edge reordering is free but destroys natural order; it flips when order carries meaning (log
  timelines, document sections), where you keep the order and label each chunk explicitly instead.
- Documents-first, question-last costs nothing and aligns with prompt caching, but in a long chat that
  re-sends a per-turn document set the whole context is paid every turn either way.
- Quote-then-answer prompting improves evidence use and gives you checkable quotes, at the price of
  output tokens and latency; vendor citation features can recover some of that cost.
- Relying on a newer long-context model shrinks the trough without engineering, but the effect is
  model-dependent and silently returns when a customer swaps models.

## Pitfalls
- Raising k to fix misses; the symptom is retrieval recall@k going up in the eval while end-to-end answer
  accuracy goes down.
- Evaluating only with the gold chunk at position 1, or only with a single-needle test; the symptom is a
  green eval and production answers that ignore facts sitting in the prompt.
- Putting the instructions and question before 20,000 tokens of documents; the symptom is the model
  answering a different question or dropping constraints stated at the top.
- Ordering chunks by document order or timestamp without a reason; the symptom is the best chunk buried
  in the middle behind weaker ones.
- Sending full parent sections at a high k; the symptom is context overflow and a quality collapse that
  looks like a model regression but is a layout problem.
- Treating the effect as fixed; the symptom is a pipeline tuned for one model regressing silently after
  a model upgrade or a customer's choice of a different deployment.

## When to use
Think about position whenever a prompt carries more than a handful of retrieved passages, and every time
someone proposes raising k. Measure the evidence-present-but-unused rate in error analysis (retrieval
found the gold chunk, the answer is still wrong) because that gap, not recall, is what this effect
produces; then run a position sweep and track answer accuracy against k, which usually peaks and then
declines. Day to day it shows up as the ordering in your prompt builder, the cut after the reranker, and
the question-last template. In interviews, "recall@20 is 95% but answers are wrong 30% of the time, what
do you check?" is the standard prompt, and the strong answer names position and distractors before
touching the embedding model.

## Role lens
- **Engineer:** Implements rerank, cut and edge reordering in the prompt builder, uses a documents-first
  question-last template, and adds a position-sweep test to the eval suite.
- **Architect:** Sets the context budget policy per model (k, chunk size, compression) and treats every
  "just add more context" request as a measured trade rather than a free improvement.
- **FDE:** When a customer insists on stuffing all their documents into the prompt, runs the position
  sweep on their data and uses the curve to justify retrieval, reranking and a small k.

## Check yourself
**Q:** Your retrieval eval shows the gold chunk is in the top-20 for 94% of questions, but end-to-end
accuracy is 61%. The team proposes raising k to 40. Argue for or against, and propose a plan.
**A:** Against. Recall is not the bottleneck; the gap between 94% evidence-present and 61% correct says
the generator is failing to use evidence it already has, which is the signature of distractors and
position. Raising k adds twenty more distractors and pushes the evidence deeper into the middle, so it
should make things worse. Plan: run error analysis on the failures and classify them as evidence present
but unused versus evidence missing; for the first class, add a cross-encoder reranker and cut to the top
5 to 8, reorder so the strongest chunks sit at the edges, move the question after the documents, and ask
for quotes before the answer; run a position sweep before and after; re-measure end to end. Only if the
second class dominates, which means multi-hop or aggregation questions, reach for query decomposition or
map-reduce rather than a bigger k.

## Quiz
### Q: What does the lost-in-the-middle finding say about how models use long contexts?
- [ ] Accuracy declines linearly with the position of the relevant passage
- [x] Accuracy is highest when the relevant passage is at the start or end and lowest when it is in the middle
- [ ] Only the first passage is ever used, so k should always be 1
- [ ] The effect appears only in models with context windows under 4,000 tokens
> The measured curve is U-shaped, with primacy and recency advantages and a trough in between. The
> trough depth varies by model and shrinks with long-context training, but it is not a linear decay.

### Q: Which prompt layout best mitigates the effect for a RAG answer?
- [ ] Question first, then chunks in retrieval-rank order, then a reminder of the question
- [x] Chunks first with the strongest at the beginning and end, then the question and instructions last
- [ ] Chunks sorted by document timestamp, then the question
- [ ] All chunks concatenated without separators so the model treats them as one document
> Edge placement exploits primacy and recency for the evidence, and question-last puts the task in the
> recency window right before generation. Rank order buries ranks 2 to 5 in the early middle.

### Q: Recall@20 is 94% but answer accuracy is 61%. What is the best first change?
- [ ] Raise k to 40 so more evidence is available
- [ ] Switch to a larger embedding model
- [x] Rerank, cut to a small k, reorder for the edges, and re-measure end-to-end accuracy
- [ ] Increase chunk overlap so the answer appears in more chunks
> The gap is evidence-present-but-unused, a generation-side problem caused by distractors and position.
> More k or more overlap adds distractors; a different embedding model targets recall, which is already
> high.
