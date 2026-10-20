---
id: slm-vs-llm-trade-offs
module: system-design
name: SLM vs LLM trade-offs
kind: decision
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - model-internals/small-language-models
  - model-internals/scaling-laws
  - training-adaptation/knowledge-distillation
  - training-adaptation/quantization
  - system-design/model-routing-cascading
  - system-design/self-host-vs-api
  - infra-capacity-economics/edge-ai-deployment
  - system-design/model-selection
sources:
  - Kaplan et al., 2020 — Scaling Laws for Neural Language Models
  - Hoffmann et al., 2022 — Training Compute-Optimal Large Language Models
  - Hinton et al., 2015 — Distilling the Knowledge in a Neural Network
---

## TL;DR
Small language models (roughly up to the low tens of billions of parameters, runnable on one GPU or a
laptop; a fuzzy, moving boundary) trade breadth for cost, latency and deployability. On narrow,
in-distribution tasks, especially after fine-tuning, an SLM often matches a large model; on open-ended
reasoning, long-horizon tool use and broad world knowledge it does not. The decision turns on the
task's shape: how narrow, what a tail error costs, and where the data may go.

## Why it exists
The gap between a 7B-class model and a frontier model is not ten percent. It is an order of magnitude
or more in price per token, a similar factor in weight bytes read per generated token and therefore in
latency, and the difference between "fits in the customer's VPC on one GPU, or on the device" and
"requires an external API". A choice with that much leverage needs a reasoned basis rather than a
default.

Both defaults are wrong. "Always use the biggest model" overpays on the large fraction of traffic that
is easy, blows latency budgets for interactive and on-device use, and is simply unavailable wherever
data cannot leave the premises. "Small models are good enough now" fails on the tail: the hard five
percent of inputs where errors concentrate, which is also where incidents and compliance findings come
from. Small models follow long, complex instruction sets less reliably, hallucinate more on knowledge
questions because fewer facts fit in fewer parameters, and degrade more sharply in long contexts.

What makes the decision genuinely hard is that the gap is task-dependent. Pattern-following tasks
(classify, extract, reformat, answer from supplied text) saturate early: beyond some size, a bigger
model does not do them measurably better, and fine-tuning closes whatever gap remains. Broad reasoning
and knowledge tasks keep improving with scale. So the question is never "are SLMs good enough" in the
abstract; it is "is this task the kind that saturates", and that has to be measured.

## How it works
### What shrinks when a model shrinks
Scaling-law work established that loss falls smoothly with parameters and training tokens, and the
compute-optimal follow-up showed that smaller models trained on far more tokens recover much of the
gap, which is why current small models are much stronger than their parameter count once suggested.
But capabilities do not shrink uniformly. The first to go are **stored world knowledge** (facts live in
parameters; fewer parameters, fewer facts, more confident invention unless retrieval supplies the
facts), **multi-step reasoning and planning**, **robustness to long or intricate instruction sets**,
and **long-context use**. What holds up is in-distribution pattern behaviour, because a narrow task
occupies a small region of input space that fine-tuning can cover directly.

### The physical arithmetic
Weight memory is parameters × bytes per parameter: a 7B model is about 14 GB in fp16 and roughly 3.5
to 4 GB at 4-bit, which fits a consumer GPU or a laptop; a 70B model is about 140 GB in fp16 and
needs several GPUs or aggressive quantization. Decode is memory-bandwidth bound: every generated token
reads the model's active weights once, so single-stream tokens per second are approximately bandwidth
divided by weight bytes, and a model ten times smaller generates roughly ten times faster on the same
card (approximate; batching, KV-cache traffic and mixture-of-experts designs change the constant). The
KV cache is smaller too, since there are fewer layers and heads. This is why SLMs win on latency and
on edge deployment, not only on price.

### Closing the gap: fine-tuning and distillation
The standard path is to use the large model as a teacher: run it over your task's inputs to produce
labels or target outputs, verify a sample by hand, and fine-tune the small model on the result,
typically with a parameter-efficient method. The student learns the **task behaviour**, not the
teacher's general capability, so it matches in-distribution and falls off out of distribution. Narrow
tasks commonly need hundreds to a few thousand examples (an order of magnitude, not a rule). The
student also inherits the teacher's mistakes, which is why the test set must be human-labelled rather
than teacher-labelled.

### The decision dimensions
- **Task breadth:** closed schema and bounded inputs favour the SLM; open-ended generation favours the LLM.
- **Tail cost:** if one error in the hard cases is expensive, the LLM or a cascade; if errors are cheap
  and reviewable, the SLM.
- **Latency budget:** sub-second interactive or on-device targets usually force the SLM.
- **Volume:** self-hosted SLM cost is fixed per GPU-hour and amortises only at volume.
- **Residency and connectivity:** on-prem, air-gapped or offline rules out external APIs.
- **Knowledge need:** if answers depend on facts, pair the SLM with retrieval or choose the LLM.
- **Change rate:** a task whose definition shifts monthly makes retraining a treadmill.
- **Operational capacity:** a self-hosted model needs someone to own serving, monitoring and upgrades.

### The hybrid
A cascade runs the SLM first and escalates to the LLM on low confidence, failed validation, or a
trigger condition. It captures most of the saving while giving LLM quality on the tail, at the price of
a confidence signal that itself needs calibration. Routing and cascading have their own lesson; the
point here is that the choice is rarely binary.

## Example
A worked scenario with **illustrative figures**. A support platform classifies 5,000,000 inbound
messages a day into intents and PII categories; each call is about 300 input and 10 output tokens.

Evaluation on 1,000 human-labelled messages: the frontier API model scores 97%; a 7B open-weight model
zero-shot scores 90%; the same 7B model fine-tuned on 3,000 teacher-labelled examples (500 of them
human-verified) scores 96%, with a 95% interval of about ±1 point.

Cost per day. API at an illustrative 1 unit per million input tokens and 3 per million output:
5M × (300×1 + 10×3)/10⁶ = 1,650 units. Self-hosted SLM on a GPU node at an illustrative fixed 100 units
per day, capacity-planned separately. Naive saving: 1,550 units, about 94%.

Now price the quality gap. One point of accuracy on 5M messages is 50,000 misrouted messages a day. If
each costs 0.02 units of human rework, the SLM-only path's extra point of error costs 1,000 units a
day, so the real saving is 550 units, and the confidence interval means it could be close to zero.

Cascade. The fine-tuned SLM is confident (calibrated probability above threshold) on about 85% of
traffic, where its accuracy is close to the LLM's; the remaining 15% escalates. API cost falls to
0.15 × 1,650 ≈ 248 units, plus 100 for the GPU, total ≈ 348 units a day, with overall accuracy
indistinguishable from 97% on the test set. The saving is about 79% with no measurable quality loss.
The lesson: the error-cost term, quality gap × volume × cost per error, must appear in the comparison,
and once it does, the cascade usually dominates both pure options at this volume.

## Trade-offs
- An SLM gives roughly an order of magnitude lower cost and latency but a narrower competence; it flips
  as soon as the task needs world knowledge or multi-step reasoning that retrieval cannot supply.
- Fine-tuning an SLM closes the in-distribution gap but creates an artefact you must retrain when the
  input distribution shifts or the base model is retired; it flips when the task changes faster than
  you can label.
- Self-hosting an SLM delivers residency, offline operation and fixed cost, but needs operations
  capacity and only pays at volume; at low volume a small model behind a hosted API is the better middle.
- The LLM through an API gives breadth and zero infrastructure, at the price of variable cost, external
  data flow and exposure to the provider's deprecation schedule.
- A cascade captures most of the saving with LLM-level quality, but adds a confidence estimator that
  must be calibrated and monitored, and a second code path to operate.

## Pitfalls
- Judging the SLM on zero-shot results and discarding it. Symptom: the fine-tuned version would have
  matched the bar, but the comparison never got that far.
- Using an SLM for knowledge-heavy question answering without retrieval. Symptom: fluent, confident,
  wrong answers about facts the model never held.
- Handing an SLM a 3,000-token instruction set written for a frontier model. Symptom: rules late in the
  prompt are ignored and output format drifts.
- Comparing price per token only. Symptom: the business case omits the cost of errors and escalations,
  and the GPU's fixed cost at low utilisation.
- Assuming quantization is free. Symptom: the 4-bit artefact that looked fine on a general benchmark
  loses several points on your task; always evaluate the exact artefact you deploy.
- Distilling without a human-labelled test set. Symptom: 99% agreement with the teacher, and the same
  systematic errors the teacher makes.

## When to use
Reach for an SLM for narrow, high-volume, latency-sensitive work: classification, routing, schema-bound
extraction, guardrail checks, on-device or on-prem features, and anywhere data cannot leave. Reach for
the LLM for open-ended generation, complex tool use, low-volume high-value analysis, and for any task
still being discovered: prototype with the large model, and distil once the task definition is stable.
Measure accuracy with a confidence interval on your own set, cost per successful task including the
cost of errors, p95 latency, and the escalation rate if you cascade. In interviews, the expected depth
is naming which capabilities degrade with size, the bandwidth arithmetic behind the latency gain,
distillation with a human-labelled test set, and the cascade as the usual answer.

## Role lens
- **Engineer:** Builds the eval that compares the candidates on the real task, runs the distillation
  pipeline, and tests the quantized artefact rather than the fp16 original.
- **Architect:** Decides which task families are SLM-eligible, sets the quality bar and the error-cost
  model, and chooses between pure SLM, pure LLM and a cascade per family.
- **FDE:** Makes an SLM work inside a customer's VPC or air-gapped site when the API model cannot be
  used, and sets expectations about which capabilities will not carry over.

## Check yourself
**Q:** A customer requires that document extraction run entirely inside their VPC on a single GPU.
Your current pipeline uses a frontier API model. How do you assess whether a small model can replace
it, and what does the path look like if it can?
**A:** Characterise the task first: fixed-schema extraction over a known document population is narrow
and in-distribution, so it is a strong SLM candidate. Build a human-labelled golden set from the
customer's real documents, measure the frontier model on it as the ceiling, then measure candidate
open-weight models zero-shot. If there is a gap, generate training data by running the frontier model
over the customer's documents where terms permit, human-verify a sample, fine-tune with a
parameter-efficient method, and re-evaluate the exact quantized artefact on the human-labelled set.
Check the memory arithmetic: a 7B model at 4-bit is a few gigabytes of weights, but long documents
mean a large KV cache, so chunk pages and bound context. Plan operations: serving engine, monitoring,
a retraining trigger when templates change. Because there is no LLM to escalate to inside the VPC, add
a human review queue for low-confidence outputs, or budget a larger open-weight model for the tail.

## Quiz
### Q: Which capabilities degrade first as a language model gets smaller?
- [ ] The ability to produce valid JSON for a fixed schema
- [x] Stored world knowledge and multi-step reasoning
- [ ] Tokenization accuracy on common words
- [ ] The ability to follow a single short instruction
> Facts are stored in parameters and reasoning depth grows with scale, so both fall off first. Format
> compliance and simple instruction following are narrow pattern behaviours that small models retain,
> especially after fine-tuning.

### Q: Why does a 7B model generate tokens roughly ten times faster than a 70B model on the same GPU?
- [ ] It uses a smaller vocabulary, so each token is shorter
- [ ] It produces fewer output tokens for the same prompt
- [x] Decode is memory-bandwidth bound and each token reads all the weights once, so ten times fewer weight bytes means roughly ten times more tokens per second
- [ ] Smaller models skip the attention layers during decoding
> Per-token decode time is dominated by streaming the weights from GPU memory, not by arithmetic.
> Fewer bytes per token means proportionally more tokens per second; vocabulary and output length do
> not change the per-token cost.

### Q: A team reports 99% agreement between a distilled small model and its large teacher on a held-out set labelled by the teacher. What is missing before shipping?
- [ ] A larger teacher model to generate more labels
- [x] A human-labelled test set, because the student inherits the teacher's systematic errors and agreement with the teacher cannot reveal them
- [ ] A longer fine-tuning run to reach 100% agreement
- [ ] A quantized version of the teacher for comparison
> Agreement measures how well the student copies the teacher, including its mistakes. Only labels
> from an independent source show whether either model is actually correct on the task.
