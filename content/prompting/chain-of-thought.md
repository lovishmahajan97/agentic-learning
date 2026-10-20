---
id: chain-of-thought
module: prompting
name: Chain-of-thought prompting
kind: pattern
difficulty: core
est_minutes: 14
status: reviewed
related:
  - prompting/zero-shot
  - prompting/few-shot
  - prompting/react
  - prompting/structured-outputs-json-mode
  - model-internals/reasoning-models-test-time-compute
  - model-internals/sampling-strategies
  - evals/pass-k
  - agents/reflection-self-critique
sources:
  - Wei et al., 2022 — Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
  - Kojima et al., 2022 — Large Language Models are Zero-Shot Reasoners
  - Wang et al., 2022 — Self-Consistency Improves Chain of Thought Reasoning in Language Models
  - Turpin et al., 2023 — Language Models Don't Always Say What They Think
---

## TL;DR
Chain-of-thought (CoT) has the model write intermediate reasoning before its final answer. It works
because a transformer spends a fixed amount of computation per generated token, so a problem that needs
many dependent steps cannot be solved inside one token; writing the steps out buys more sequential
computation and lets the sequence carry state. Gains are large on arithmetic, logic and multi-step
planning, near zero on lookups, and the written chain is not guaranteed to be the real cause of the
answer.

## Why it exists
Ask a model for the final answer to a multi-step word problem and it often fails; ask it to show its work
first and it often succeeds, with the same weights and the same question. The explanation is
architectural. Generating one token runs the input through a fixed number of layers once. There is no
loop, no scratchpad, no way to do step three after step two inside that single pass. A problem with more
sequential dependencies than a given network's depth can absorb cannot be solved in one token by that
network; theoretical work on bounded-depth transformers shows that some inherently serial problems are out
of reach without intermediate tokens (under standard complexity assumptions) and become solvable with
them. Writing intermediate steps changes the computation: every emitted token is
another full pass, conditioned on all the previous ones, so serial compute grows with the length of the
chain and partial results become visible to later steps through attention.

The naive alternatives both fail. A bigger model still has fixed per-token compute; it pushes the ceiling
up at great cost without removing it. Asking for the answer and then an explanation produces post-hoc
rationalisation: the answer token was already sampled before any reasoning existed, so the explanation
can only justify it. CoT was first shown with few-shot demonstrations containing worked rationales, then
as a zero-shot trigger phrase, and the gains appeared only above a certain model scale; smaller models
write fluent chains that reach wrong answers.

## How it works
### Compute per token, and why order matters
Each generated token gets the same number of layers of compute and can attend to every earlier token.
Decoding is therefore a sequence of bounded-depth computations whose only memory is the text itself. A
chain of k reasoning tokens gives roughly k additional passes, each able to read the partial results
written so far. The consequence for prompt design is strict ordering: reasoning must come before the
answer. In a JSON output the "reasoning" field must precede the "answer" field, because generation is
left to right and a field cannot influence one already emitted. With tags, `<reasoning>` then
`<answer>`.

### Variants
- Few-shot CoT: demonstrations that include the rationale, so the model copies the reasoning style and
  granularity. Flawed rationales get copied too.
- Zero-shot CoT: a trigger such as "think step by step before answering". Cheaper to set up, less
  control over the chain's shape.
- Self-consistency: sample several chains for the same question and take the majority final answer. It
  works because different chains make different mistakes, so wrong answers are spread across many values
  while the right answer is reached repeatedly; it costs k times the tokens and needs a discrete answer
  to vote on.
- Built-in reasoning: current reasoning models are typically trained with reinforcement learning to
  produce long internal chains before answering, often hidden or summarised by the API, with a budget or
  effort control instead of a prompt trigger. On these models an explicit "think step by step" is usually
  redundant and can produce a second, visible chain that duplicates the hidden one; tune the effort
  setting and measure instead. The reasoning-models lesson covers the mechanism.

### Why voting works, in numbers
Suppose a single chain reaches the right answer with probability 0.6. With five independent chains and a
strict majority vote, the answer is right when at least three chains are: 10·0.6³·0.4² + 5·0.6⁴·0.4 +
0.6⁵ ≈ 0.35 + 0.26 + 0.08 = 0.68. Plurality voting does better still when wrong chains scatter across
different wrong numbers, because the correct answer can win with two votes against four singletons. The
same arithmetic shows when it backfires: if the model makes one *consistent* mistake more often than it
reaches the truth, voting amplifies the mistake, and if chains are not independent (identical sampling,
a shared misreading of the question), five votes carry little more information than one. Measure the
vote margin as well as the answer: a 5/5 majority and a 2/5 plurality deserve different confidence, and
the margin is a cheap uncertainty signal for routing low-agreement cases to a person.

### What the chain is, and is not
The chain is a computation, and a useful debugging artefact: reading failed chains tells you whether the
model misread the question, dropped a constraint or made an arithmetic slip. It is not a faithful account
of why the answer was produced. Experiments that bias the prompt, for example by making the correct
option always "A" in the demonstrations, show models producing chains that never mention the bias while
their answers follow it. Treat the chain as evidence to inspect, never as an audit trail.

### Where it does not help
Fact retrieval, short-text classification, sentiment and anything that is a lookup rather than a
derivation: there are no intermediate steps to write, so you pay latency for nothing. Surveys of CoT
across benchmarks report the gains concentrated in math and symbolic reasoning, with little or negative
effect elsewhere; on format-sensitive tasks the extra prose makes outputs harder to parse and
occasionally worse. CoT cannot recover knowledge the model lacks; a chain built on a missing fact is
confidently wrong.

### Operational details
Reasoning needs room: size `max_tokens` for the chain, not the answer, or the output truncates mid-chain
with no answer at all. On models with built-in reasoning the hidden thinking tokens typically count
against the same limit and are billed as output even when you never see them. Parse only the delimited answer. Decide whether end users should see the chain; it
can leak the contents of the context and it reads as authoritative even when unfaithful. Count the
tokens: output tokens are typically priced several times above input tokens, and a chain can multiply output length by an order
of magnitude.

## Example
Three disciplines in one script: the reasoning block is placed before the answer, only the delimited
answer is parsed, and self-consistency takes a majority over five sampled chains. The direct-answer
variant is run the same way so the comparison is like for like.

```python
import anthropic, re, collections

client, MODEL = anthropic.Anthropic(), "claude-opus-5-5"
QUESTION = ("A warehouse ships 3 pallets per truck. On Monday 4 trucks leave, on Tuesday twice as many, "
            "and on Wednesday 5 pallets are returned. How many pallets are out at the end of Wednesday?")

DIRECT = "Answer with just the number inside <answer></answer> tags."
COT = ("First work through the problem inside <reasoning></reasoning> tags, one step per line. "
       "Then give the final number inside <answer></answer> tags. Nothing after the answer tag.")

def ask(style):
    r = client.messages.create(model=MODEL, max_tokens=2048, system=style,     # room for the chain
                               messages=[{"role": "user", "content": QUESTION}])
    text = next((b.text for b in r.content if b.type == "text"), "")
    m = re.search(r"<answer>\s*(-?\d+)\s*</answer>", text)                    # parse only the answer
    return (m.group(1) if m else "PARSE_FAIL"), text

for style in (DIRECT, COT):
    votes = collections.Counter(ask(style)[0] for _ in range(5))             # self-consistency: 5 chains
    answer, n = votes.most_common(1)[0]
    print(f"{'direct' if style is DIRECT else 'cot':<7} majority={answer} ({n}/5) votes={dict(votes)}")
# Expected: 4*3 + 8*3 - 5 = 31. The model named here always reasons internally, so expect
# little gap: the hidden chain already supplies the computation. To see the classic CoT effect,
# run both styles on a model without built-in reasoning. The ordering and parsing discipline
# shown here applies to any model.
```

## Trade-offs
- Higher accuracy on multi-step problems against latency and output tokens, which cost more than input
  tokens; the trade becomes a net loss on lookups and short classifications, where there is nothing to
  derive.
- Self-consistency buys a further accuracy gain against k times the cost; it stops paying when
  single-chain accuracy is already high or when the answer is free text that cannot be voted on.
- A visible chain is a debugging aid against a leak and liability risk: it can expose context contents and
  reads as an explanation even when it is not one; hide it from end users unless the product is about
  showing work.
- On reasoning-native models an explicit CoT instruction duplicates work the model already does, so the
  lever becomes the effort or budget setting; on models without built-in reasoning the prompt trigger is
  still the main lever.

## Pitfalls
- Asking for the answer first and the reasoning after; the symptom is articulate justifications of wrong
  answers that never change the answer.
- Setting `max_tokens` for the answer length; the symptom is truncated chains and missing answer tags,
  showing up as parse failures.
- Parsing the whole response as the answer; the symptom is JSON decode errors because the chain is prose.
- Reading the chain as the cause of the answer; the symptom is answers that track a feature of the prompt
  the chain never mentions.
- Applying CoT to every call by default; the symptom is latency and cost tripling on a classification task
  with no accuracy change.
- Few-shot CoT with sloppy rationales; the symptom is the model copying the same shortcut and failing on
  exactly the cases the shortcut misses.

## When to use
Use CoT when the task has intermediate steps: arithmetic, unit conversion, date logic, multi-constraint
selection, planning, code reasoning, anything where a person would want scratch paper. Skip it for
lookups, sentiment, short classification and plain extraction, and add a reasoning field to structured
outputs only when the eval shows a gain. Always A/B against the direct-answer prompt on your eval set and
track accuracy, output tokens and latency together; add self-consistency only where the remaining errors
are inconsistent across samples. On models with built-in reasoning, treat the effort or budget setting as
the dial and measure before adding prompt triggers. In interviews, the expected depth is the
compute-per-token argument, the ordering rule, self-consistency and the faithfulness caveat.

## Role lens
- **Engineer:** Adds a delimited reasoning section before the answer, sizes `max_tokens` for the chain,
  parses only the answer, and reports accuracy against tokens for each variant.
- **Architect:** Decides which request classes get a reasoning budget, since it multiplies latency and
  output cost, and sets the policy on exposing chains to users.
- **FDE:** Uses failed chains from the customer's data to show their experts where the model misreads the
  domain, turning each recurring pattern into a rule or an example.

## Check yourself
**Q:** An extraction pipeline returns JSON. A teammate added "think step by step" to the instruction;
parsing now fails on a third of requests and accuracy on the other two thirds did not move. What happened
and what would you do?
**A:** Two things. The chain is prose, and it now appears before or around the JSON, so a parser expecting
a bare object fails; and extraction is largely a lookup task with few intermediate steps to compute, so the
chain added tokens without adding accuracy. I would first revert and confirm the baseline. If I believed
some fields need reasoning, say deriving a total from line items, I would put the reasoning in a delimited
block or in a "reasoning" field that precedes the answer fields, parse only the answer part, raise
`max_tokens` so the chain cannot truncate, and A/B it on the eval set measuring accuracy, parse-failure
rate, latency and output tokens. If the model has built-in reasoning I would tune its effort setting
instead of adding a trigger. Keeping CoT would require a measured gain, not a hunch.

## Quiz
### Q: Why does writing out intermediate steps improve a model's accuracy on multi-step arithmetic?
- [ ] The extra tokens raise the sampling temperature, which improves exploration
- [x] Each generated token is one bounded pass through the network, so a chain of tokens supplies the sequential computation a single token cannot
- [ ] The model retrieves similar solved problems when it sees the word "step"
- [ ] Longer outputs are weighted higher by the reward model at inference time
> Per-token compute is fixed by depth. A problem with more dependent steps than one pass can absorb needs
> the sequence itself to carry partial results, which is exactly what the chain does.

### Q: In a JSON output with both a reasoning field and an answer field, why must reasoning come first?
- [ ] JSON parsers require string fields before numeric fields
- [ ] The API discards fields that appear after the answer
- [x] Generation is left to right, so a field emitted later cannot influence one already produced
- [ ] The model's attention is limited to the first field
> If the answer is generated first it was chosen without the reasoning, and the reasoning becomes a
> rationalisation. Ordering reasoning before answer is what lets the computation affect the result.

### Q: Self-consistency improves CoT accuracy by sampling several chains and voting. Why does this work?
- [ ] Averaging token probabilities across chains produces a lower-perplexity answer
- [x] Different chains make different mistakes, so wrong answers scatter while the correct answer is reached repeatedly
- [ ] The longest chain is usually the correct one
- [ ] Repeated sampling triggers the API's caching layer, which stores verified answers
> Correct chains converge on the same answer while wrong chains tend to disagree with each other, so the
> majority is usually right when single-chain accuracy is reasonable. It fails when the model makes the
> same mistake consistently, costs k times the tokens and needs a discrete answer to vote on.
