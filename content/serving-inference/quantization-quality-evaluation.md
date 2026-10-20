---
id: quantization-quality-evaluation
module: serving-inference
name: Quantization quality evaluation
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - training-adaptation/quantization
  - model-internals/perplexity
  - evals/golden-dataset
  - evals/regression-tests
  - evals/llm-as-a-judge
  - evals/benchmarking
  - serving-inference/gpu-memory-optimization
  - llmops-platform/evaluation-gates-for-production-releases
sources:
  - Dettmers et al., 2022 — LLM.int8(): 8-bit Matrix Multiplication for Transformers at Scale
  - Frantar et al., 2022 — GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers
  - Lin et al., 2023 — AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration
  - Official docs: llama.cpp
---

## TL;DR
Quantization buys memory and speed with precision, and the only honest way to know what it cost is to
measure the quantized model against its own full-precision baseline on the work you actually serve.
Perplexity and per-token KL divergence are cheap, sensitive early signals; task-level evals on your golden
set, run with identical prompts and decoding, are the acceptance test. Degradation is uneven: long
context, multi-step math, code, strict output formats and low-resource languages break first, so
aggregate numbers hide the failures that matter.

## Why it exists
A 4-bit checkpoint lets a 70B model fit where a 16-bit one did not and roughly quarters the bytes decode
must stream, so the pressure to quantize is constant. The method lesson in Training & adaptation covers
*how* GPTQ, AWQ, int8 and fp8 work; this one is about *whether the result is still the model you tested*.
The card that ships with a quantized checkpoint says something like "perplexity 5.47 → 5.62 on
WikiText-2" (illustrative numbers),
and teams read that as "lossless enough" and deploy.

That inference is unsafe for several reasons. Perplexity averages log-loss over every token of generic
text, and most tokens are easy; a task usually hinges on a few decisive tokens (a digit, a function
name, a closing brace) whose probabilities can shift a lot while the average barely moves. The
calibration data used by GPTQ or AWQ, often a few hundred segments of web text, may not resemble your
domain, so the error is concentrated where you were not looking. Outlier activation channels, which
LLM.int8() showed emerge in models above a few billion parameters, make some layers far more fragile
than others. And evaluation sets of a few hundred items have noise larger than the effect you are
hunting, so a naive "accuracy dropped 1%" is often meaningless in either direction.

The naive alternative, trusting the vendor's benchmark or eyeballing a few chats, fails precisely on the
rare, high-stakes behaviours that production exposes at scale. What is needed is a layered measurement
that goes from cheap distribution fidelity to expensive task evidence, with the full-precision model as
the control.

## How it works
### Step 1: distribution fidelity with teacher forcing
Run the same fixed text through both models and compare their next-token distributions position by
position. Because the context is forced to be identical, this isolates the quantization error from
sampling and from divergence after a first wrong token. Three numbers come out of one pass:
- **Perplexity delta** on held-out text: WikiText-2 and C4 are the conventional sets, but add a sample of
  *your* domain text, because that is where calibration mismatch shows.
- **Mean KL divergence** `KL(P_fp16 || P_quant)` per token. KL catches cases where perplexity improves
  by accident while the distribution moves; it is the most sensitive cheap signal. Look at the tail as
  well as the mean (the 99th-percentile or maximum per-token KL), *because* damage concentrates on a few
  positions and a small mean can hide a handful of badly wrong ones. llama.cpp's perplexity tool can save
  full-precision logits and report KL statistics, including percentiles, against a quantized run.
- **Top-1 agreement**: the fraction of positions where both models' argmax token matches. Under greedy
  decoding this is a direct proxy for how often the quantized model would have gone a different way.
These are deterministic and take minutes, so they belong in CI for every new quantized artifact.

### Step 2: public benchmarks, run yourself
Re-run a standard harness (EleutherAI's lm-evaluation-harness is the usual one) on both models with
identical settings. Knowledge-style multiple choice (MMLU-type) tends to survive 4-bit quantization
well; multi-step arithmetic (GSM8K-type), code generation (HumanEval-type) and instruction-following
format checks (IFEval-type) are the categories that reveal damage first. Report the delta, not the
absolute score, and report it with a confidence interval.

### Step 3: your golden set, your graders
Take the golden dataset you already use for releases and run both models with the *same* prompts,
*same* system prompt, *same* decoding parameters. Use greedy decoding for the primary comparison to
remove sampling noise, then repeat with the production configuration over several seeds. Grade with the
same task graders; for open-ended outputs, use pairwise LLM-as-judge with the fp16 output as the
reference and randomised position. Because every prompt is scored under both models, use *paired*
statistics (paired bootstrap or a sign test on per-item wins) rather than comparing two independent
means. The arithmetic shows why. On 300 items at 80% accuracy, two independent runs give a standard error
on the difference of √(2 × 0.8 × 0.2 / 300) ≈ 3.3 points, a 95% interval of about ±6.4. If the models
disagree on only 15 items (5%), the paired standard error is roughly √(0.05 / 300) ≈ 1.3 points, an
interval near ±2.5: the same data now resolves effects less than half the size.

Count the *flips* as well as the net change: items that went correct → wrong and wrong → correct. A
quantized model can hold accuracy flat while 10 answers break and 10 others happen to improve, and the
users who owned those 10 broken answers see a regression the average hides. The flip count is also the
discordant count that drives the paired statistics, so it costs nothing extra to report.

### Step 4: stress the known weak spots
Add targeted slices *because* average scores mask them: long-context retrieval (needle-style prompts at
your maximum context, especially if the KV cache is quantized too), multilingual prompts, numbers and
dates, JSON or tool-call validity rates, and refusal or safety behaviour, which can shift in either
direction. Check speed as well: weight-only 4-bit helps bandwidth-bound decode but some kernels slow
prefill through dequantisation overhead, so a "faster" model can raise time to first token.

### Step 5: production confirmation
Ship behind a canary or shadow deployment and compare task metrics and user-facing signals against the
fp16 fleet before full rollout. Treat the quantized artifact as a new model version in the registry with
its own evaluation record, because that is what it is.

### Knowing where damage comes from
Weight-only schemes (W4A16: GPTQ, AWQ, NF4) keep activations in 16-bit and usually lose little on 7B-and-
larger models; smaller models lose more at the same bit width. Weight-and-activation schemes (W8A8 int8,
fp8) exploit faster tensor-core math for prefill and large batches but must handle activation outliers,
which is what SmoothQuant-style scaling and fp8's dynamic range address. Group size, per-channel versus
per-tensor scales and the calibration set all move the result, so two "4-bit" files of the same model can
differ more from each other than either differs from fp16. Quantizing the KV cache to fp8 is generally
reported as low-impact; 4-bit KV is where long-context retrieval tends to degrade.

## Example
A teacher-forced comparison of a full-precision model and its 4-bit counterpart on a sample of domain
text. It reports mean KL per token, top-1 agreement and perplexity under both models. Run this on your
own text, not only on WikiText, and gate on the deltas before investing in the slower task evals.

```python
import math, torch, torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

name = "Qwen/Qwen2.5-0.5B"                        # swap in the checkpoint you plan to ship
tok = AutoTokenizer.from_pretrained(name)
fp16 = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16, device_map="cuda").eval()
q4 = AutoModelForCausalLM.from_pretrained(
    name, device_map="cuda",
    quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                           bnb_4bit_compute_dtype=torch.float16)).eval()

texts = open("domain_sample.txt").read().split("\n\n")[:200]   # YOUR domain, same text for both
kl = agree = nll16 = nll4 = n = 0
with torch.no_grad():
    for t in texts:
        ids = tok(t, return_tensors="pt", truncation=True, max_length=1024).input_ids.cuda()
        lp = F.log_softmax(fp16(ids).logits[0, :-1].float(), -1)   # predictions for positions 1..T
        lq = F.log_softmax(q4(ids).logits[0, :-1].float(), -1)     # identical context: teacher forced
        tgt = ids[0, 1:]
        kl += (lp.exp() * (lp - lq)).sum(-1).sum().item()           # KL(P_fp16 || P_q4), summed
        agree += (lp.argmax(-1) == lq.argmax(-1)).sum().item()
        nll16 += -lp.gather(1, tgt[:, None]).sum().item()
        nll4 += -lq.gather(1, tgt[:, None]).sum().item()
        n += tgt.numel()
print(f"tokens={n}  mean KL/token={kl/n:.4f}  top-1 agreement={agree/n:.2%}")
print(f"ppl fp16={math.exp(nll16/n):.3f}  ppl 4bit={math.exp(nll4/n):.3f}")
# Next: run the golden set under both models with identical decoding and compare with paired bootstrap.
```

## Trade-offs
- Perplexity and KL are cheap and sensitive, but they measure distribution drift on fixed text, not task
  outcomes; a model can pass them and still fail a format-critical workflow.
- Task evals on your golden set are the real acceptance test, yet they cost GPU time and need enough
  items for paired statistics; for small sets, judge disagreement can exceed the quantization effect.
- Greedy decoding gives a clean comparison but is not how production runs; sampled comparisons are
  realistic but need several seeds to average out noise.
- Stricter acceptance thresholds protect quality but can block a quantization that would have cut
  serving cost in half; the right threshold depends on what the task's errors cost.

## Pitfalls
- Comparing against a published fp16 benchmark score instead of re-running the fp16 model yourself under
  identical prompts and settings; the symptom is a "regression" that is really a harness difference.
- Judging a quantized model by chat impressions. Fluency survives quantization long after arithmetic,
  code and long-context recall have degraded, so the demo looks fine and the eval does not.
- Ignoring the calibration set. A GPTQ or AWQ file calibrated on English web text can degrade noticeably
  on code or another language; the tell is a domain KL far above the WikiText KL.
- Quantizing weights and KV cache at once and attributing any loss to the weights; test them
  separately, since long-context failures usually trace to the cache.
- Declaring a 0.8-point accuracy drop "within noise" or "significant" without a confidence interval
  from paired per-item results.
- Reporting only net accuracy. Equal numbers of fixed and broken items cancel in the mean; the symptom
  is an eval that says "no change" followed by specific, reproducible complaints from production users.

## When to use
Run the full ladder whenever a quantized artifact is a candidate for production: a new checkpoint, a new
quantization method or group size, a new engine kernel, or KV-cache quantization. Make steps 1 and 3
mandatory gates in the model release pipeline and steps 2 and 4 part of the evaluation record. Skip the
expensive steps only for throwaway experiments. Watch for the categories that break first and weight
them by business cost: a support bot may tolerate what a code assistant cannot. In interviews, the
expected answer to "how do you know 4-bit is fine?" is this ladder plus the reason perplexity alone is
not enough, not a quoted benchmark number.

## Role lens
- **Engineer:** Owns the teacher-forced comparison script and the golden-set run, with paired statistics,
  wired into CI so every quantized artifact gets a delta report automatically.
- **Architect:** Sets the acceptance thresholds per use case and the policy that quantized models are
  registered, evaluated and rolled out as distinct model versions.
- **FDE:** Builds the domain text sample and golden set from the customer's own data, and shows them the
  measured quality-versus-cost curve before recommending a quantized deployment.

## Check yourself
**Q:** A team reports that their 4-bit model's WikiText perplexity rose by 2% and MMLU dropped by 0.3
points, and concludes it is safe for their contract-extraction product. What would you push back on?
**A:** Both signals are generic. WikiText perplexity averages over easy tokens of encyclopaedic prose,
and MMLU is multiple choice, which is the category that survives quantization best. Contract extraction
depends on long inputs, exact numbers and dates, and valid structured output, which are exactly the
categories that degrade first, and none were measured. I would ask for KL and top-1 agreement on a sample
of their contracts, a paired comparison of extraction accuracy and JSON validity on their golden set under
identical prompts and decoding, a long-context slice at their maximum document length, separate tests if
the KV cache is quantized, and confidence intervals on every delta, plus the list of flipped items
so someone reads what actually broke. A 0.3-point MMLU drop without an interval is not evidence either
way.

## Quiz
### Q: Why is per-token KL divergence a better early signal of quantization damage than perplexity alone?
- [ ] KL is cheaper to compute because it does not need the reference text
- [x] KL measures how far the whole next-token distribution moved, so it catches shifts that leave average log-loss almost unchanged
- [ ] Perplexity cannot be computed for quantized models
- [ ] KL directly predicts task accuracy on downstream benchmarks
> Perplexity only scores the probability of the actual next token. KL compares the full distributions,
> so it flags cases where alternative tokens gain or lose mass even when the target token's probability
> is similar.

### Q: Two evaluations of the same quantized model on a 300-item golden set use the same prompts for both models. Which analysis is most appropriate?
- [ ] Compare the two mean accuracies and call any drop above 1% a regression
- [ ] Run the quantized model five times and average, ignoring the fp16 run
- [x] A paired per-item comparison with a bootstrap confidence interval on the difference
- [ ] A chi-squared test on the total number of correct answers
> Because each item is scored under both models, pairing removes item difficulty from the variance and
> makes the test far more sensitive at the same sample size. Independent-mean comparisons waste that
> structure.

### Q: Which category is most likely to show quantization damage first?
- [ ] Short factual multiple-choice questions
- [ ] Casual conversational fluency
- [x] Multi-step arithmetic, code and long-context retrieval with strict output formats
- [ ] Translation between high-resource European languages
> Tasks that hinge on a few decisive tokens over long chains, or on recalling a detail far back in a
> quantized KV cache, amplify small distribution shifts. Fluency and recognition-style tasks are robust.
