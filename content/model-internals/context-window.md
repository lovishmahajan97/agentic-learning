---
id: context-window
module: model-internals
name: Context window
kind: concept
difficulty: intro
est_minutes: 13
status: reviewed
related:
  - model-internals/kv-cache
  - model-internals/positional-encoding
  - model-internals/tokenization
  - model-internals/self-attention
  - prompting/prompt-caching
  - prompting/context-engineering
  - rag-retrieval/long-context-vs-rag
  - rag-retrieval/lost-in-the-middle
sources:
  - Liu et al., 2023 — Lost in the Middle: How Language Models Use Long Contexts
  - Su et al., 2021 — RoFormer: Enhanced Transformer with Rotary Position Embedding
  - Peng et al., 2023 — YaRN: Efficient Context Window Extension of Large Language Models
---

## TL;DR
The context window is the maximum number of tokens a model can attend over in one request: system
prompt, conversation history, retrieved documents, tool results and the answer all share it. It is
finite because attention cost grows with length, the KV cache grows with length, and positional
encodings are only trained up to some length. The model has no memory beyond it, so everything the
model "knows" about this conversation has to fit, and managing that budget is a core engineering job.

## Why it exists
A transformer processes a fixed set of positions at once, and three costs scale with how many. Attention
compares every token with every earlier token, so prefill compute has a term quadratic in length.
The KV cache stores keys and values for every position in every layer, so memory grows linearly with
length and the cache of a single long request can exceed the model's own weights. Positional encodings
are learned or tuned for the lengths seen in training; feed a model positions it has never seen and
quality degrades sharply unless the encoding is deliberately extended. Vendors therefore publish a hard
limit, and serving engines enforce one (`max_model_len` in vLLM) because the memory for the worst case
must be reserved in advance.

The naive mental model, "the chatbot remembers our conversation", is wrong in a way that matters. The
model is stateless between calls. The client re-sends the entire transcript each turn, and the model
re-reads it. Memory is an illusion maintained by the application, paid for in tokens on every request,
and it ends abruptly at the window boundary: either the request is rejected or something has to be
dropped. Deciding what to drop, summarize or retrieve is the problem the context window forces on you.

## How it works
### Accounting
Every request must satisfy `input_tokens + max_output_tokens ≤ window`. Input is everything in the
prompt after chat templating: system prompt, each prior user and assistant turn, tool definitions, tool
outputs, retrieved chunks, and the template's own role markers. Many APIs cap output separately at a
figure well below the window, so the practical limit on a long answer is the output cap, not the window.
Tokens are counted by the model's own tokenizer, and the same text can differ in token count by tens of
percent across tokenizers (more for non-English text and code), so budgets must be computed with the
right one. Less visible consumers count too: images and audio are converted to tokens, and on reasoning
models the hidden thinking tokens are generated into the same window and usually billed and capped as
output, so a request that "only" asks for a short answer can still run out of room. APIs differ in what
happens at the edge: most reject an over-long request outright, some truncate silently.

### What a full window costs
Put numbers on the first two constraints with a GQA model such as Llama-3-8B (32 layers, 8 KV heads,
head dimension 128, fp16). The KV cache costs 2 × 32 × 8 × 128 × 2 bytes = 128 KiB per token, so a
single 128k-token request holds about 16 GiB of cache, roughly the size of the model's own fp16 weights.
A model without GQA (32 KV heads) would need four times that. Compute grows faster: per layer, the
projections and MLP cost on the order of 24·d² FLOPs per token regardless of length, while attention
scores and the weighted sum cost about 4·n·d for a token that attends over *n* positions. The attention
term overtakes the rest once *n* exceeds roughly 6·d, around 25k tokens at d = 4,096 (a rule of thumb
for orders of magnitude, not a measurement). Below that, long prompts cost roughly linearly; above it,
every doubling of the prompt costs more than double.

### Why the limit sits where it does
Three constraints set the number. **Training length**: models are pre-trained at some length and often
extended afterwards by continued training at longer lengths with the rotary base frequency adjusted
(RoPE scaling methods such as YaRN); the advertised window is where that extension was validated.
**Memory**: the serving engine must be able to hold the KV cache for the longest allowed request, so the
operator may configure a smaller window than the model supports to fit more concurrent users. **Compute
and latency**: prefill time grows faster than linearly in prompt length, so time-to-first-token on a
window-filling prompt can be seconds, and every turn re-pays it unless the prefix is cached.

Published windows have grown from about 2k tokens (GPT-3 era) through 4k, 8k and 32k to 128k, 200k and,
for some models, a million or more. Treat the exact figures as moving targets; the mechanisms are not.

### Nominal versus effective window
A model accepting 128k tokens does not use all of them equally. Liu et al. (2023) showed accuracy on a
retrieval-style task is highest when the relevant passage sits at the start or end of the context and
drops when it sits in the middle. Needle-in-a-haystack tests probe the same thing. The effective window,
the length over which the model reliably uses information, is task-dependent and usually shorter than the
nominal one, and it must be measured for your task rather than read off a spec sheet.

### Living within it
- **Sliding window**: drop the oldest turns. Simple, loses early instructions and facts.
- **Summarization / compaction**: replace old turns with a model-written summary. Keeps the gist, loses
  detail and costs a call.
- **Retrieval**: keep history or documents outside the prompt and pull in only what the current turn
  needs. The default for large corpora; the `long-context-vs-rag` topic covers the trade.
- **Prompt caching**: does not enlarge the window, but makes a long shared prefix cheap to re-send by
  reusing its KV cache across requests, which changes the economics of a big system prompt.
- **Sliding-window attention** in the model itself (Mistral 7B v0.1 attends over a 4,096-token window per
  layer) bounds per-layer cost while deeper layers still propagate information further back; it is an
  architectural choice, not something you configure per request.

## Example
A token budgeter for a support assistant on an 8,192-token window. The fixed parts (system prompt,
retrieved chunks, the new question) are paid first, the output reservation is held back, and history is
admitted newest-first until the budget runs out. Count with the deployed model's tokenizer; `tiktoken`
is used here only as a stand-in.

```python
import tiktoken

enc = tiktoken.get_encoding("cl100k_base")          # stand-in; use your model's tokenizer
count = lambda s: len(enc.encode(s))

WINDOW, MAX_OUT, TEMPLATE_OVERHEAD = 8_192, 800, 64  # overhead: role markers, illustrative

def fit_history(system, chunks, history, question):
    fixed = count(system) + sum(map(count, chunks)) + count(question) + TEMPLATE_OVERHEAD
    budget = WINDOW - MAX_OUT - fixed
    if budget < 0:
        raise ValueError(f"fixed parts exceed window by {-budget} tokens: trim chunks")
    kept = []
    for turn in reversed(history):                  # newest turns are worth the most
        n = count(turn)
        if n > budget:
            break
        kept.insert(0, turn); budget -= n
    return kept, budget

system = "You are a support assistant. " * 60                 # ~360 tokens
chunks = ["Policy section ... " * 120 for _ in range(6)]        # 6 chunks, ~360 tokens each
history = [f"turn {i}: " + "words " * 250 for i in range(20)]  # ~250 tokens per turn
kept, left = fit_history(system, chunks, history, "How do I get a refund?")
print(f"kept {len(kept)} of {len(history)} turns, {left} tokens spare")
# Roughly: fixed parts ~2,600 tokens, so ~4,800 remain for history and about 18 of the 20
# ~250-token turns survive. Dropping the oldest turns is the crude option; summarizing them is the
# usual upgrade, and it is what you would do before ever cutting the system prompt or chunks.
```

## Trade-offs
- A larger window lets you skip retrieval and paste everything in, but prefill latency and per-request
  cost grow with every token on every turn; retrieval wins once the corpus no longer fits, changes too
  often to keep cached, or costs more to re-read per request than an occasional retrieval miss costs you.
- Keeping full history maximizes fidelity, while summarizing it cuts tokens and latency at the price of
  lost detail; the flip happens when old turns stop being referenced.
- Reserving a large output budget protects long answers but shrinks the space for input; size it to the
  real distribution of answer lengths rather than the maximum.
- Operators can shrink the engine's window to fit more concurrent users in GPU memory, trading
  per-request capability for throughput; raise it only when traffic actually uses it.

## Pitfalls
- Counting tokens with the wrong tokenizer or forgetting template and tool-definition overhead; the
  symptom is intermittent "context length exceeded" errors on requests that looked within budget.
- Assuming the model will find a fact anywhere in a 100k-token prompt; the symptom is answers that ignore
  material placed in the middle while the same material at the end works.
- Letting an agent loop append tool outputs forever; latency climbs turn by turn, cost per turn grows
  linearly, and the run eventually fails at the boundary.
- Truncating from the front and cutting the system prompt or the schema of a tool; behaviour changes
  abruptly mid-conversation with no error.
- Setting `max_tokens` far above real answer lengths; engines whose schedulers budget KV memory against
  a request's worst-case length then admit fewer concurrent requests even though answers are short.
- Forgetting that reasoning models spend hidden thinking tokens from the same budget; the symptom is
  empty or cut-off answers with a "length" finish reason on hard prompts while easy ones work.

## When to use
There is no opting out; every request has a window. The decisions are how much of it to spend on
instructions, on examples, on retrieved material, on history, and on the answer. Measure the token
distribution of real requests, the rate of truncation events, and task accuracy as a function of where
key information sits in the prompt. Watch for the RoPE-scaling trap too: some model cards note that statically enabling a context-extension
method for all requests can slightly degrade quality on short inputs, so enable it only on deployments
that actually receive long prompts. Prefer retrieval when the corpus is large or changes, long context
when a single document must be reasoned over as a whole, and compaction for long agent runs. In
day-to-day work the window appears as a budgeting function, a truncation policy and a cost line; in
interviews, expect to be asked why windows are finite, what the KV cache has to do with it, and whether
a bigger window makes RAG unnecessary.

## Role lens
- **Engineer:** Writes the budgeting and truncation logic, counts tokens with the right tokenizer, and
  monitors truncation and context-exceeded errors as first-class metrics.
- **Architect:** Sets the context policy per workload, chooses between long-context and retrieval
  designs, and ties the window size to GPU memory and concurrency targets.
- **FDE:** Shows a customer why pasting their entire knowledge base into the prompt is slow and
  expensive, and measures effective context on their real documents.

## Check yourself
**Q:** A product manager argues that with a 200k-token window, the team can drop its RAG pipeline and
paste the whole product manual into every request. Make the engineering case for and against.
**A:** For: simpler system, no retrieval failures, and the model can cross-reference the whole manual.
Against: every request pays prefill over the full manual, so time-to-first-token and cost per turn rise
with manual size rather than with the question; effective use of information in the middle of a very
long prompt is measurably weaker than at the edges, so accuracy can fall even as the window is nominally
large; the KV cache for a 200k-token request is large enough to cut concurrency on a shared GPU or to
hit vendor rate limits. Prompt caching softens the cost if the manual is a stable prefix, but not the
attention-quality issue. The right test is accuracy, latency and cost on real questions with the manual
pasted versus retrieved, measured before the pipeline is deleted.

## Quiz
### Q: Which of these is NOT a reason the context window is finite?
- [ ] KV-cache memory grows with the number of positions
- [ ] Attention compute grows with sequence length
- [x] The tokenizer vocabulary has a fixed size
- [ ] Positional encodings are only trained up to a certain length
> Vocabulary size bounds how many distinct tokens exist, not how many positions a request may contain.
> Memory, compute and positional training length are the real constraints.

### Q: A chat assistant "forgets" instructions given early in a long session. What is the most likely mechanism?
- [ ] The model's weights were updated by the conversation
- [x] The application truncated or summarized early turns to fit the window, so the instructions are no longer in the prompt
- [ ] The KV cache was evicted by the GPU
- [ ] Temperature was too high
> The model has no state between calls; it only sees what the client re-sends. Once early turns are
> dropped to stay within the window, their content is gone for every later turn.

### Q: What does prompt caching change about a long shared system prompt?
- [ ] It raises the model's maximum context length
- [ ] It makes the model attend to the prompt more accurately
- [x] It reuses the prefix's KV cache across requests, cutting prefill cost and latency for that prefix
- [ ] It compresses the prompt into fewer tokens
> Caching stores the computed keys and values for a byte-identical prefix so later requests skip that
> prefill work. The window and the model's use of the content are unchanged.
