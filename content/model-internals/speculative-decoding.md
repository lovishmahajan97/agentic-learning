---
id: speculative-decoding
module: model-internals
name: Speculative decoding
kind: concept
difficulty: advanced
est_minutes: 16
status: reviewed
related:
  - model-internals/kv-cache
  - model-internals/sampling-strategies
  - model-internals/small-language-models
  - model-internals/logits
  - serving-inference/prefill-vs-decode
  - serving-inference/inference-optimization
  - serving-inference/tpot
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
sources:
  - Leviathan et al., 2023 — Fast Inference from Transformers via Speculative Decoding
  - Chen et al., 2023 — Accelerating Large Language Model Decoding with Speculative Sampling
  - Cai et al., 2024 — Medusa: Simple LLM Inference Acceleration Framework with Multiple Decoding Heads
  - Li et al., 2024 — EAGLE: Speculative Sampling Requires Rethinking Feature Uncertainty
---

## TL;DR
Decoding is memory-bandwidth bound: each step reads all the weights to produce one token while the
GPU's arithmetic units sit mostly idle. Speculative decoding uses that idle compute: a cheap draft model
guesses several tokens ahead and the large model verifies all of them in one forward pass. A
rejection-sampling rule keeps the output distribution exactly the large model's, so it is a pure
latency optimization whose gain depends on how often the draft is right.

## Why it exists
During decode the large model produces one token per forward pass, and that pass is dominated by moving
weights (and the KV cache) from GPU memory into the compute units, not by arithmetic. Processing two
tokens in the same pass costs almost the same as processing one, because the weights are read once
either way; the compute to multiply a second row is nearly free until the batch grows large enough to
become compute-bound. Single-request latency therefore has a floor set by bandwidth, and buying a GPU
with more FLOPs does little.

The obstacle to using that slack is the autoregressive dependency: token *t+1* is unknown until token *t*
is chosen, so you cannot simply ask for two tokens at once. The naive workaround, "use a smaller model",
trades quality for speed, which is usually unacceptable. The insight behind speculative decoding is that
you do not need to *know* the next tokens, only to *guess* them. If something cheap proposes a
continuation, the large model can score every proposed position in one pass, because given a candidate
prefix the model's distributions at all positions are computed in parallel, exactly as in prefill. Guess
right and you advanced several tokens for the price of one pass; guess wrong and you lost only the cheap
draft work.

## How it works
### The loop
1. **Draft.** A small model (or a cheaper heuristic) generates γ candidate tokens autoregressively from
   the current prefix, recording its probability `q(x)` for each. γ is typically a handful.
2. **Verify.** The target model runs one forward pass over `prefix + γ candidates`. Because a causal
   model computes a distribution at every position in parallel, this yields the target's `p(·)` at all
   γ draft positions plus one more position after the last candidate, with the same per-pass cost
   profile as a short prefill.
3. **Accept or reject, left to right.** For each candidate token *x* in turn, accept it with probability
   `min(1, p(x) / q(x))`. On the first rejection, sample a replacement from the residual distribution
   `normalize(max(0, p − q))` and stop. If all γ are accepted, sample one bonus token from the target's
   distribution at the extra position.
4. **Roll back.** KV-cache entries for rejected positions, in both models, are discarded; the accepted
   tokens are appended and the loop repeats.

### Why the output is exactly the target's
The accept rule is a form of rejection sampling. Where the draft puts too little mass on a token
(`q < p`), it is always accepted when drafted and the shortfall is made up by the residual on
rejections; where the draft puts too much (`q > p`), it is accepted only a fraction `p/q` of the time.
Summed over cases, the probability of emitting each token equals `p(x)` exactly, which is what makes the
method lossless rather than an approximation. The one-line proof: the chance a drafted token is accepted
is `α = Σ_x q(x)·min(1, p(x)/q(x)) = Σ_x min(p(x), q(x))`, and the residual's normalizer is
`Σ_x max(0, p(x) − q(x)) = 1 − α`, the rejection probability. So

```
P(emit x) = q(x)·min(1, p(x)/q(x)) + (1 − α) · max(0, p(x) − q(x)) / (1 − α)
          = min(p(x), q(x)) + max(0, p(x) − q(x)) = p(x)
```

The same algebra tells you what acceptance rate to expect: `α = Σ min(p, q) = 1 − TV(p, q)`, one minus
the total-variation distance between the two distributions. Acceptance is a direct measure of how much
the drafter's distribution overlaps the target's, not of whether its single top guess is right. Under greedy decoding the rule collapses to "accept while
the draft matches the target's argmax", which is the easiest version to reason about.

### What the speedup depends on
Let α be the probability a draft token is accepted (a measure of draft–target agreement). Treating each
position as an independent trial, the expected number of tokens produced per target pass is
`(1 − α^(γ+1)) / (1 − α)`. With α = 0.8 and γ = 5 that is
about 3.7 tokens per pass; with α = 0.5 it is about 2. The wall-clock gain is that figure divided by the
relative cost of the cycle (γ draft steps plus one slightly longer target pass). Writing *c* for the
cost of one draft step relative to one target pass, and treating verification as one target pass:

```
speedup ≈ (1 − α^(γ+1)) / ((1 − α) · (γ·c + 1))
```

So the draft must be much cheaper than the target *and* agree with it often; either alone is not
enough. Agreement is highest on predictable text, such
as code, boilerplate, structured output and anything that copies from the prompt, and lowest on creative
text at high temperature. Increasing γ raises the ceiling but lowers the average acceptance of later
positions, and past a point the extra verification compute costs more than it saves.

### Where the draft comes from
- **A separate small model** with the same tokenizer (needed so draft token ids are meaningful to the
  target). A model from the same family, an order of magnitude or more smaller, is the usual choice.
- **N-gram or prompt lookup**: propose the continuation that followed the same recent tokens earlier in
  the context. No second model, near-zero cost, strong on RAG answers that quote sources and on code
  edits that reproduce input, weak elsewhere.
- **Extra heads on the target** (Medusa): small heads attached to the last hidden state predict tokens
  at offsets +2, +3, ..., so drafting costs one target pass's worth of extra heads rather than a second
  model. Candidates are verified as a tree in one pass. Note that Medusa also offers a relaxed "typical
  acceptance" rule that trades exactness for higher acceptance; only the rejection-sampling rule above is
  lossless, so check which mode a deployment uses before claiming identical outputs.
- **A feature-level draft head** (EAGLE): a light autoregressive module that predicts the next hidden
  state rather than the next token, reusing the target's own representations for higher acceptance.

### Why it stops helping
At high concurrency the server is already feeding the GPU many tokens per pass, and the arithmetic units
are busy; verifying γ extra tokens per request then costs real compute and can lower total throughput.
Speculative decoding is a latency tool for workloads with few concurrent requests, or a throughput tool
only while the batch is still bandwidth-bound. Engines such as vLLM expose draft-model and n-gram modes
with a configurable number of speculative tokens, and the right setting is found by measuring
tokens/second per request and per GPU at the real batch size.

## Example
First an illustrative cost model (every number here is invented for the arithmetic, not a benchmark),
then a runnable verification step on toy distributions that exercises the accept, reject and bonus
paths.

Suppose a target pass takes 25 ms and a draft pass 2 ms, with γ = 5 and a verification pass that is
slightly longer than a single-token pass, say 27 ms. A cycle costs 5 × 2 + 27 = 37 ms. At α = 0.8 it
yields `(1 − 0.8⁶) / 0.2 ≈ 3.69` tokens, so about 10 ms per token against 25 ms baseline: a 2.5× gain.
At α = 0.5 it yields about 1.97 tokens, 18.8 ms per token, a 1.3× gain. At α = 0.3 it yields about
1.43 tokens, 25.9 ms per token: *slower* than not speculating. The same arithmetic with a draft that
costs 8 ms instead of 2 turns the α = 0.8 case into 67 / 3.69 ≈ 18 ms per token. Acceptance rate and
draft cost are the two numbers to measure before enabling it.

```python
import torch

def speculative_step(p_target, q_draft, draft_tokens, g=None):
    """p_target: [gamma+1, V] target probs at each verified position (ONE target pass);
    q_draft: [gamma, V] draft probs; draft_tokens: [gamma]. Returns the emitted tokens,
    which are distributed exactly as if sampled from the target alone."""
    out = []
    for i, x in enumerate(draft_tokens):
        p, q = p_target[i], q_draft[i]
        if torch.rand((), generator=g) < min(1.0, (p[x] / q[x]).item()):
            out.append(int(x))                                 # accept the draft token
        else:
            resid = torch.clamp(p - q, min=0)                  # residual corrects the distribution
            out.append(int(torch.multinomial(resid / resid.sum(), 1, generator=g)))
            return out                                         # stop at the first rejection
    out.append(int(torch.multinomial(p_target[-1], 1, generator=g)))  # all accepted: bonus token
    return out

V, gamma = 50, 5
g = torch.Generator().manual_seed(0)
target = torch.softmax(2 * torch.randn(V, generator=g), -1)
draft = torch.softmax(target.log() + 0.5 * torch.randn(V, generator=g), -1)  # similar, not identical
lengths = []
for _ in range(2000):
    toks = torch.multinomial(draft, gamma, replacement=True, generator=g)
    lengths.append(len(speculative_step(target.expand(gamma + 1, V), draft.expand(gamma, V), toks, g)))
alpha = torch.minimum(target, draft).sum().item()                   # = 1 - TV(target, draft)
print(f"alpha={alpha:.2f} predicted={(1 - alpha**(gamma + 1)) / (1 - alpha):.2f}",
      "measured:", sum(lengths) / len(lengths))   # agree within sampling noise on this i.i.d. toy
# A stationary toy: real positions have different distributions, but the accept/reject logic is identical.
```

## Trade-offs
- Fewer target passes per token cut single-request latency, but the draft model's weights and KV cache
  take GPU memory and its passes take time; the gain flips negative when acceptance is low or the draft
  is not much cheaper than the target.
- Lossless by construction with the rejection rule, whereas simply trusting the draft would be faster
  still but changes outputs; the guarantee costs the residual-sampling bookkeeping and cache rollback.
- A separate draft model is flexible and reusable across targets in a family, while attached heads
  (Medusa, EAGLE) avoid a second model and achieve higher acceptance, at the cost of training heads per
  target model.
- Larger γ raises the best-case tokens per pass but spends more verification compute on positions that
  are increasingly likely to be rejected; the optimum shifts lower as batch size grows.

## Pitfalls
- Enabling speculation on a heavily batched server and watching throughput fall; verification tokens
  compete for compute that was no longer idle, so per-GPU tokens/second drops even as a lone request
  speeds up.
- Pairing a draft model with a different tokenizer; the symptom is near-zero acceptance or outright
  errors because draft token ids do not mean the same thing to the target.
- Measuring the speedup on greedy code completion and assuming it holds for creative writing at
  temperature 1; acceptance falls with entropy and the gain shrinks or inverts.
- Forgetting to roll back KV-cache entries for rejected draft tokens; the symptom is corrupted
  continuations that reference text the model never emitted.
- Treating a reported speedup as a property of the method rather than of the draft–target pair and the
  workload; the same engine setting can help one customer and hurt another.

## When to use
Reach for it when per-request latency (time per output token) matters more than aggregate throughput:
interactive assistants, coding tools, agents making many sequential calls, or any deployment with few
concurrent requests per GPU. Expect the most from predictable outputs: code, structured data, RAG
answers that quote context, and templated text; n-gram drafting is nearly free to try there. Skip it or
measure carefully when the server runs large batches or outputs are high-entropy. Measure acceptance
rate, tokens per target pass, p50/p95 time per output token and per-GPU throughput before and after,
at production concurrency. In interviews, the expected depth is the memory-bandwidth argument, the
accept/reject rule and why it is lossless, the expected-tokens formula, and the conditions under which
the technique backfires.

## Role lens
- **Engineer:** Enables draft-model or n-gram speculation in the serving engine, tunes γ, and reads
  acceptance-rate metrics to decide whether it is paying off.
- **Architect:** Decides where speculation belongs in the fleet: latency-sensitive, low-concurrency
  pools get it, high-throughput batch pools do not, and the draft model becomes a managed artifact.
- **FDE:** Demonstrates the latency gain on a customer's own prompts and warns when their workload's
  acceptance rate makes it a loss rather than a win.

## Check yourself
**Q:** A team enabled speculative decoding with a small draft model and saw a large speedup in their
single-user demo, then deployed it to a shared endpoint and total throughput fell. Explain both results
and what you would change.
**A:** In the demo the batch size was one, so each target pass was memory-bandwidth bound and the
arithmetic units were idle; verifying γ draft tokens per pass used that free compute, and with a good
acceptance rate they got several tokens per target pass. On the shared endpoint the engine already
batches many requests, so the GPU is close to compute-bound; the extra γ verification tokens per
request now consume real compute, and the draft model's passes and memory add further cost, so
aggregate tokens per second drops even though a lone request would still be faster. I would measure
acceptance rate and per-GPU throughput at production concurrency, lower γ or switch to a cheaper
drafter such as n-gram lookup, and most likely restrict speculation to a latency-sensitive pool with
low concurrency while leaving the high-throughput pool without it.

## Quiz
### Q: Why can the target model verify several draft tokens for roughly the cost of generating one?
- [ ] The draft tokens are already in the KV cache
- [x] Decode is memory-bandwidth bound, so the weights are read once per pass and scoring extra positions in that pass is nearly free until compute saturates
- [ ] The target model skips its attention layers during verification
- [ ] Verification only runs the last transformer layer
> A causal model computes distributions at every input position in parallel. When the pass is dominated
> by reading weights rather than arithmetic, adding a few positions barely changes its duration.

### Q: What does the accept-with-probability min(1, p/q) rule guarantee?
- [ ] That at least one draft token is always accepted
- [ ] That the draft model's output distribution is used when it is confident
- [x] That the emitted tokens follow exactly the target model's distribution, making speculation lossless
- [ ] That greedy decoding is always used for verification
> Combined with sampling from the residual max(0, p − q) on rejection, the rule is rejection sampling
> from the target distribution using the draft as a proposal, so outputs match the target exactly.

### Q: Under which condition is speculative decoding most likely to slow a server down?
- [ ] Low temperature on code completion
- [ ] A draft model from the same family as the target
- [x] High concurrency where each target pass is already compute-bound
- [ ] Prompts that quote long passages from retrieved documents
> Speculation converts idle compute into extra tokens. When batching has already saturated compute,
> the verification tokens and draft passes cost real time and total throughput can fall.
