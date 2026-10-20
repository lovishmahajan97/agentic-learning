---
id: sampling-strategies
module: model-internals
name: Sampling strategies (temperature, top-k / top-p, greedy vs beam)
kind: concept
difficulty: core
est_minutes: 15
related:
  - model-internals/logits
  - model-internals/softmax
  - model-internals/hallucination
  - model-internals/perplexity
  - agent-reliability/deterministic-replay-and-reproducibility
  - serving-inference/structured-constrained-generation
  - prompting/structured-outputs-json-mode
  - evals/model-evaluation
sources:
  - Holtzman et al., 2020 — The Curious Case of Neural Text Degeneration
  - Fan et al., 2018 — Hierarchical Neural Story Generation
  - Official docs: Hugging Face Transformers generation strategies
---

## TL;DR
A language model emits a probability distribution over the whole vocabulary at each step; a sampling
strategy decides which token to take. Greedy always takes the most likely token and tends to loop; beam
search keeps several candidate sequences and maximizes total likelihood, which suits translation but
produces bland, repetitive open-ended text. Sampling draws from the distribution after reshaping it with
temperature and truncating the tail with top-k or top-p, trading determinism for diversity. The right
setting depends on whether the task has one correct answer.

## Why it exists
The model does not output a token; it outputs a distribution, and a 100k-entry distribution has a long
tail of individually unlikely tokens. Something must collapse it to one choice, and every obvious way of
doing so fails somewhere. Greedy decoding picks the argmax each step. It is deterministic and cheap, but
a sequence of locally best tokens is not the globally best sequence, and in practice greedy text falls
into repetition loops: once a phrase has appeared, repeating it becomes the most likely continuation,
which makes repeating it again even more likely. Beam search fixes the local-versus-global problem by
keeping the *B* best partial sequences, yet Holtzman et al. (2020) showed that for open-ended text the
highest-likelihood continuations are exactly the bland, repetitive ones; human text is consistently
*less* likely than beam output under the model, because people say surprising things.

Pure sampling from the full distribution avoids blandness but has the opposite failure. Each step
places a few percent of mass on the tail collectively, so over a few hundred tokens the sampler will
draw a nonsensical token, and once a derailment is in the context, the model conditions on it and the
text degrades. Temperature, top-k and top-p exist to keep the diversity of sampling while cutting off
the tail that causes incoherence.

## How it works
### Reshaping: temperature
Logits `z` are divided by *T* before softmax. `T < 1` sharpens the distribution toward the top token,
`T > 1` flattens it, and `T = 0` is treated as greedy by every engine rather than as a division. Because
temperature rescales all logits, it does not change the ranking, only how much mass the leaders hold.
It is the single most used knob: near 0 for extraction, classification and code where consistency
matters; around 0.7–1.0 for drafting and brainstorming.

### Truncating: top-k and top-p
- **Top-k** keeps the *k* highest-probability tokens, sets the rest to zero and renormalizes. It is
  simple and bounded, but *k* is blind to the shape of the distribution: when the model is certain, 40
  candidates include junk; when it is uncertain among 200 reasonable words, 40 is too few.
- **Top-p (nucleus)** sorts tokens by probability and keeps the smallest set whose cumulative mass
  reaches *p*, then renormalizes. The set adapts: a peaked distribution yields one or two tokens, a flat
  one yields many. This is why nucleus sampling became the default over top-k.
- **Min-p** keeps tokens whose probability is at least a fraction of the top token's probability
  (`p_i ≥ min_p · p_max`). It adapts like top-p but scales with the leader, which makes it behave well at
  high temperature; it is supported in vLLM and llama.cpp among others.

Engines apply these as a pipeline over logits, typically temperature first, then top-k, then top-p, and
the order matters because truncation after temperature sees a reshaped distribution. Repetition,
frequency and presence penalties sit earlier in the same pipeline and lower the logits of tokens that
already appeared.

### Searching: greedy versus beam
Greedy is beam search with `B = 1`. Beam search expands each of *B* partial sequences by the vocabulary,
scores all candidates by cumulative log-probability, keeps the best *B*, and repeats until each beam
ends; a length penalty is usually applied because longer sequences accumulate more negative log-prob and
would otherwise lose unfairly. The cost is *B* forward passes per step and *B* KV caches, and results
cannot be streamed cleanly because the winning beam is only known at the end. Beam search is the right
tool when there is a single correct target and the metric rewards likelihood: machine translation,
speech recognition, grammar correction. For chat and creative generation it is rarely used.

### Determinism
`T = 0` makes the choice deterministic given the logits, but it does not make the logits bit-identical
across runs: batched kernels, different batch compositions and non-associative floating-point reductions
can change low-order bits, and when two tokens are nearly tied the argmax flips. A seed parameter makes
sampling reproducible under the same conditions, not across hardware or engine versions. Treat
"temperature 0" as "much more stable", not "reproducible".

### Interactions worth knowing
Constrained decoding (JSON schemas, grammars) masks logits *before* sampling, so it composes with any
strategy. Some reasoning-oriented APIs fix the temperature and reject the parameter, because their
training assumed a particular sampling regime. And vendor documentation generally advises changing
temperature *or* top-p, not both at once, because their effects overlap and tuning both obscures which
one moved the result.

## Example
A compact sampler that applies temperature, top-k and top-p in the usual order, run against a toy
seven-token distribution so the effect of each setting is visible. With `top_p = 0.9` the nucleus is
tokens 0–4 (cumulative mass 0.93 is the first to reach 0.9); `top_k = 3` keeps tokens 0–2; adding
`temperature = 0.5` sharpens the distribution so the same `top_p` nucleus shrinks to tokens 0–1.

```python
import torch

def sample_next(logits, temperature=1.0, top_k=0, top_p=1.0, generator=None):
    """logits: [vocab]. Order matches common engines: temperature, then top-k, then top-p."""
    if temperature == 0:                                   # "T = 0" means greedy, not a division
        return int(logits.argmax())
    z = logits.float() / temperature
    if top_k > 0:
        kth = torch.topk(z, top_k).values[-1]
        z = z.masked_fill(z < kth, float("-inf"))          # drop everything below the k-th score
    if top_p < 1.0:
        sz, idx = torch.sort(z, descending=True)
        p = torch.softmax(sz, -1)
        before = p.cumsum(-1) - p                          # mass strictly ahead of each token
        sz = sz.masked_fill(before >= top_p, float("-inf"))  # keep the smallest set reaching top_p
        z = torch.full_like(z, float("-inf")).scatter(0, idx, sz)
    probs = torch.softmax(z, -1)                           # renormalize over the survivors
    return int(torch.multinomial(probs, 1, generator=generator))

probs = torch.tensor([0.50, 0.20, 0.10, 0.08, 0.05, 0.04, 0.03])
logits = probs.log()
g = torch.Generator().manual_seed(0)
for cfg in [dict(temperature=0), dict(top_k=3), dict(top_p=0.9),
            dict(temperature=0.5, top_p=0.9), dict(temperature=1.5)]:
    draws = [sample_next(logits, generator=g, **cfg) for _ in range(2000)]
    print(f"{cfg}: tokens seen = {sorted(set(draws))}")
```

## Trade-offs
- Lower temperature buys consistency and higher per-token accuracy on single-answer tasks, but costs
  diversity; the trade flips when the task rewards variety, such as generating multiple candidates.
- Top-p adapts the candidate set to the model's confidence while top-k gives a hard bound on
  candidates; top-k is safer when a flat distribution must never admit hundreds of options.
- Beam search raises likelihood and translation-style accuracy, but multiplies compute and memory by
  the beam width and blocks streaming; it loses for open-ended text, where likelihood is the wrong
  objective.
- Sampling several diverse answers and voting or reranking improves accuracy on reasoning tasks, at a
  linear cost in tokens that only pays off when a verifier or majority signal exists.

## Pitfalls
- Setting temperature 0 and expecting identical outputs across runs or deployments; the symptom is
  flaky golden tests that fail on near-tied tokens after an engine or batch-size change.
- Using greedy or beam for long free-form generation; the symptom is paragraphs that repeat a sentence
  or phrase verbatim, sometimes indefinitely until the token limit.
- Raising temperature to "make the model more creative" on an extraction or code task; the symptom is a
  rise in malformed output and invented fields that evaluation then attributes to the prompt.
- Tuning temperature and top-p simultaneously; results move but it is unclear which knob did it, and
  settings copied between models behave differently because logit scales differ.
- Applying a strong repetition penalty to structured output; the symptom is JSON that avoids repeating
  a required key or closing bracket because the token was penalized.

## When to use
Pick the strategy from the task's error model. One right answer and a downstream parser: temperature at
or near 0, optionally with constrained decoding, and keep top-p at its default. Drafting, ideation or
multiple candidates for a reranker: temperature 0.7–1.0 with top-p around 0.9, or min-p if the engine
offers it. Translation or transcription scored by likelihood-correlated metrics: beam search with a
modest width and a length penalty. Measure task accuracy and format-failure rate across a small grid of
settings rather than copying numbers from a blog; the same temperature means different things for
different models. In day-to-day work the strategy appears as a few request parameters; in interviews,
expect to explain why greedy repeats, why nucleus beat top-k, and why beam search is wrong for chat.

## Role lens
- **Engineer:** Sets temperature, top-p and penalties per endpoint, pins them in config alongside the
  model version, and tests format-failure rate when either changes.
- **Architect:** Defines sampling policy per workload class, from deterministic-leaning extraction to
  diverse candidate generation, and how determinism claims are communicated to customers.
- **FDE:** Diagnoses a customer's "the model is inconsistent" or "it keeps repeating itself" by reading
  the sampling parameters before touching the prompt.

## Check yourself
**Q:** A team generating product descriptions complains that greedy decoding repeats phrases, but when
they switched to plain sampling at temperature 1 the text occasionally became nonsensical. What would you
recommend and why does each failure happen?
**A:** Greedy repeats because repetition is self-reinforcing under the model: once a phrase appears,
repeating it is the single most likely continuation, and taking the argmax every step never escapes.
Plain sampling fixes that but draws from the full tail, where a few percent of mass per step sits on
tokens that make no sense in context; over a long description one of them is eventually drawn, and the
model then conditions on its own mistake. The fix is to keep sampling but truncate the tail: top-p
around 0.9 (or min-p) so the candidate set adapts to confidence, temperature around 0.7–0.8 to favour
the leaders without flattening, and a mild frequency penalty if repeats persist. Validate with a
repetition metric and a human spot-check across a small parameter grid rather than one setting.

## Quiz
### Q: Why did nucleus (top-p) sampling largely replace top-k as the default truncation method?
- [ ] It is cheaper to compute than top-k
- [x] Its candidate set adapts to the shape of the distribution, keeping few tokens when the model is confident and many when it is not
- [ ] It guarantees deterministic output
- [ ] It removes the need for temperature
> A fixed k admits junk when the model is sure and excludes good options when it is unsure. A
> cumulative-mass threshold sizes the set to the model's confidence at each step.

### Q: Why is beam search a poor choice for open-ended chat responses?
- [ ] It cannot be combined with a KV cache
- [ ] It only works with encoder–decoder models
- [x] It maximizes sequence likelihood, and for open-ended text the most likely continuations are bland and repetitive
- [ ] It ignores the system prompt
> Human text is less likely than beam output under the model because it is surprising. Beam search finds
> high-likelihood text, which is what you want in translation and exactly what you do not want in chat.

### Q: With temperature set to 0, why can two identical requests still produce different outputs?
- [ ] The engine ignores temperature 0 and samples anyway
- [x] Floating-point reductions and batch composition can change logits slightly, flipping near-tied argmax choices
- [ ] The tokenizer is non-deterministic
- [ ] Top-p is still applied randomly
> Argmax is deterministic given the logits, but the logits themselves vary at the low-order bits with
> kernel choice and batching; when two tokens are almost tied, that is enough to change the winner.
