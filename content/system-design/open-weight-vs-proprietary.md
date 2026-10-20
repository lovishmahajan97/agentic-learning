---
id: open-weight-vs-proprietary
module: system-design
name: Open-weight vs proprietary
kind: decision
difficulty: core
est_minutes: 16
related:
  - system-design/self-host-vs-api
  - system-design/build-vs-buy
  - security-governance-compliance/vendor-exit-strategy
  - security-governance-compliance/data-residency
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - training-adaptation/peft
  - safety-security/supply-chain-security-for-models-and-datasets
  - serving-inference/engines
sources:
  - Touvron et al., 2023 — Llama 2: Open Foundation and Fine-Tuned Chat Models
---

## TL;DR
Open-weight models publish their parameters so you can download, run, inspect, modify and fine-tune
them under a licence; proprietary models are reachable only through a provider's API. The choice
decides who controls the model's lifecycle, where data goes, what you can change, and how cost scales.
It is a control-and-licensing axis, not a deployment one: open weights can be rented through an API
and proprietary models can run in private endpoints, so decide it separately from self-host versus API.

## Why it exists
At any given moment the proprietary frontier usually leads on raw capability, and an API is the
lowest-effort way to use it. So the question is really why anyone runs open weights at all, and the
answer is that an API bundles conditions you may not be able to accept. Your data leaves your
environment, mitigable by contract but not by architecture. The provider retires versions on its own
schedule, and the behaviour your prompts and evals were tuned to disappears with them. You cannot see
or change the weights, the tokenizer, the sampling internals, or more than a sliver of the logits.
Fine-tuning is limited to what the provider offers. The price is per token, forever, and set by
someone else. And the core capability of your product is a dependency you cannot fork.

Open weights invert each point. The weights are a file you keep: the same bytes in five years. Data
never has to leave. You can fully fine-tune, quantize, distil, prune, inspect and constrain decoding.
The cost is hardware and people rather than tokens. In exchange you take on serving, safety tuning,
upgrades and supply-chain checks, and you usually accept a capability gap to the frontier.

Both naive positions fail. "Open means free" ignores GPUs, utilisation, serving engineering and
operations, and the fact that the strongest open models trail the frontier. "Proprietary is best, so
use it" breaks under residency rules, deprecation churn, scale economics and deep customisation
needs. A further trap is the word "open" itself: most open-weight releases are not open source in the
software sense. Training data and code are usually withheld, and licences range from permissive
(Apache-2.0, MIT) to custom community licences with acceptable-use policies and clauses that trigger
at scale or restrict using outputs to train competitors. The licence has to be read, not assumed.

## How it works
### The dimensions, and why each one matters
1. **Control and lifecycle.** An open-weight model is an immutable artefact you pin by content hash. A
   proprietary model is a versioned API with deprecations typically announced months ahead. This
   matters *because* an LLM system's behaviour is tuned to a specific model; when the model changes,
   the prompts, evals and downstream parsers were all written against something that no longer exists.
2. **Data governance.** Open weights in your own environment mean data physically never leaves.
   Proprietary access relies on data-processing agreements, zero-retention options and regional
   endpoints; a private endpoint inside your cloud tenant is the middle ground. Which suffices depends
   on whether the constraint is legal (a contract can satisfy it) or architectural (it cannot).
3. **Customisation depth.** Open weights allow full fine-tuning, parameter-efficient adapters,
   quantization, distillation, raw logit access, custom constrained decoding, speculative decoding
   with your own draft model, and tokenizer work. Proprietary models offer prompting, structured
   output features and sometimes a fine-tuning API. The difference matters most for narrow,
   high-volume tasks, which is where deep customisation pays.
4. **Capability.** The proprietary frontier generally leads; the best open models trail by a margin
   that varies by task and shifts with every release, so this is contested and must be measured on
   your own evaluation rather than read off a leaderboard.
5. **Cost structure.** Proprietary is per-token operating cost that scales with usage. Self-hosted
   open weights are fixed cost per GPU-hour, so the economics hinge on utilisation; the self-host
   versus API lesson works the break-even. Open weights served by a hosting provider turn back into
   per-token cost, often lower than frontier proprietary pricing, with less control than self-hosting
   and no operations.
6. **Licensing.** Permissive licences allow redistribution and commercial use with few conditions.
   Custom licences may add acceptable-use policies, attribution or naming rules, restrictions on
   training other models with outputs, and thresholds for very large deployments. The licence decides
   whether you can ship on-device, redistribute fine-tunes, or embed the model in a product you sell.
7. **Ecosystem and supply chain.** Open weights come with mature serving engines, quantization
   formats and fine-tuning tooling, which is a large part of their practical value. They also come
   with risk: weights downloaded from a hub can be tampered with, pickle-format checkpoints can execute
   code on load, and a popular fine-tune may carry behaviour nobody audited. Pin revisions, prefer
   safetensors, verify hashes, and read the model card.
8. **Safety and support.** A proprietary API includes the provider's safety tuning, moderation
   layers, SLAs and someone to call. With open weights you own safety behaviour and guardrails, and
   outages are your outages.

### Decoupling the axis from deployment
Four combinations exist and all are used. Open weights self-hosted: maximum control and isolation,
maximum operations. Open weights through a hosting API: portability and lower price without hardware,
but the host's lifecycle and shared infrastructure. Proprietary through the public API: frontier
capability, least effort, least control. Proprietary in a private endpoint within your cloud tenant:
frontier capability with contractual and network isolation, at a premium. Choosing "open" does not
force you to run GPUs, and choosing "proprietary" does not force data onto the public internet.

### How the decision runs
Apply constraints first: isolation requirements, licence compatibility with how you ship, and the
customisation the task needs. Then capability on your eval, then cost at your real utilisation, then
lifecycle risk and exit cost. The common outcome is a portfolio: proprietary frontier for the hardest
and lowest-volume work, open weights (hosted or self-hosted, often fine-tuned) for high-volume narrow
tasks, both behind a gateway so the split can move as the models do.

## Example
Three things an open-weight artefact lets you do that an API-only model does not: pin the exact bytes,
read the full next-token distribution, and constrain the decision to an allowed set without generating
text. The restricted softmax below yields a per-class score that a cascade can route on.

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen2.5-0.5B-Instruct"                 # any small open-weight chat model
revision = "<commit hash from the model page>"      # pin exact weights, never a moving branch
tok = AutoTokenizer.from_pretrained(name, revision=revision)
model = AutoModelForCausalLM.from_pretrained(
    name, revision=revision, torch_dtype=torch.bfloat16,
    use_safetensors=True,                           # refuse pickle checkpoints: they can run code on load
).eval()

labels = ["refund", "shipping", "other"]
# first token of each label; check they are distinct for your tokenizer before relying on this
label_ids = [tok(" " + l, add_special_tokens=False).input_ids[0] for l in labels]

msgs = [{"role": "user", "content": "Classify this message: 'My parcel never arrived'. Reply with one word."}]
ids = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")

with torch.no_grad():
    next_logits = model(ids).logits[0, -1]          # full-vocabulary distribution for the next token
probs = torch.softmax(next_logits[label_ids], dim=-1)   # renormalise over the allowed labels only
for label, p in zip(labels, probs.tolist()):
    print(f"{label:9s} {p:.3f}")
# No decoding loop, no parsing of free text, and the probabilities double as a confidence signal.
```

The same classification through a proprietary API would need a generated answer parsed from text,
and any confidence would have to be inferred from limited top-k log probabilities if the provider
exposes them at all.

## Trade-offs
- Open weights give lifecycle control and physical data isolation, but you take on serving, safety
  tuning, upgrades and supply-chain checks; it flips when the team lacks operations capacity or the
  volume cannot keep a GPU busy.
- Proprietary access gives frontier capability with no infrastructure, but the behaviour you tuned to
  can be retired on the provider's schedule; it flips when behaviour must stay fixed for years, as in
  regulated workflows or embedded devices.
- Deep customisation buys quality on narrow tasks but creates artefacts you must retrain and re-certify
  when the base changes; it flips when the task is broad and prompting suffices.
- Hosted open weights are the middle path, portable and cheaper per token than frontier pricing with no
  operations, but you inherit the host's lifecycle and share its infrastructure; it fits when you want
  exit optionality without hardware.
- The most capable open model may carry the most restrictive licence; permissiveness versus capability
  flips the moment you redistribute, embed on-device, or sell the model as part of a product.

## Pitfalls
- Reading "open weights" as "open source" and skipping legal review. Symptom: an acceptable-use or
  scale clause surfaces during a customer's due diligence or an acquisition.
- Loading weights from an unverified source or in pickle format. Symptom: code execution on load, or a
  fine-tune that behaves differently from its model card.
- Assuming open weights are cheaper without a utilisation model. Symptom: a GPU running at a small
  fraction of capacity costs more per task than the API would have.
- Pointing a proprietary integration at a floating "latest" alias. Symptom: outputs change overnight,
  evals fail, and there is nothing in the change log.
- Migrating from an API to open weights and expecting the provider's safety behaviour to come along.
  Symptom: jailbreak success and policy violations rise because the guardrails were implicit in the
  API model.
- Choosing by public benchmark instead of your own eval. Symptom: a migration that was "equivalent on
  benchmarks" loses several points on the task that matters.

## When to use
Choose open weights when data isolation must be architectural rather than contractual, when behaviour
must be frozen for years, for high-volume narrow tasks where fine-tuning pays, for on-device or edge
deployment, when you need logits or custom decoding, or when vendor exit is a board-level requirement.
Choose proprietary when you need frontier capability now, volume is low or spiky, the team is small,
and the provider's terms satisfy your governance. Most organisations run both through a portfolio.
Measure the eval delta between candidates, GPU utilisation if self-hosting, cost per successful task,
and time to migrate after a deprecation notice. In interviews, the expected depth is separating this
axis from deployment, naming the licence question, and making the lifecycle argument concretely.

## Role lens
- **Engineer:** Pins weights by revision, verifies safetensors and hashes, runs the same eval against
  the open and proprietary candidates, and keeps both behind one interface.
- **Architect:** Owns the decision per task family, the licence review with legal, the isolation
  requirement classification (legal versus architectural), and the exit plan.
- **FDE:** Translates a customer's "no data leaves" demand into the cheapest option that truly meets
  it, and sets expectations about capability, safety and operations when open weights are the answer.

## Check yourself
**Q:** A healthcare customer insists on open-weight models "so that no data leaves". Their platform
team is three engineers and expected volume is low. What do you advise, and how do you reason about it?
**A:** Separate the two axes first. The actual requirement is data isolation, and it can be met by
self-hosted open weights or by a proprietary model in a private endpoint inside their own cloud tenant
with contractual zero retention and regional guarantees. Which one depends on whether their constraint
is legal, where a contract satisfies the regulator, or physical, where the network boundary must. With
three engineers and low volume, a self-hosted GPU fleet is an operations burden with poor utilisation
economics, so if the constraint is legal, the private endpoint or a hosted open model in-region is
likely cheaper and safer. If the environment is genuinely air-gapped, open weights are the only
option: pick a permissively licensed model, pin by hash, verify safetensors, serve with a standard
engine, size the GPU from the memory arithmetic, and add explicit guardrails because the API's implicit
safety tuning is gone. Either way, build the eval on their data first and keep the model behind a
gateway so the decision stays reversible.

## Quiz
### Q: What does "open-weight" actually guarantee about a model?
- [ ] Its training data and training code are published
- [ ] It may be used for any purpose without restriction
- [x] Its parameters are published for download and use under a licence; training data, code and unrestricted use are not implied
- [ ] It is always cheaper to run than a proprietary model
> Open-weight releases publish the parameters, usually with a licence that may carry acceptable-use,
> attribution or scale conditions. Data and code are typically withheld, and cost depends entirely on
> how the weights are served and how busy the hardware is.

### Q: Which capability is available only with open weights, not through a proprietary API?
- [ ] Structured JSON output conforming to a schema
- [x] Pinning the exact model bytes by hash and keeping that behaviour indefinitely
- [ ] A service-level agreement on availability
- [ ] Lower latency on every request
> A provider can retire any API version; a downloaded artefact cannot be retired. Structured outputs
> and SLAs are things proprietary APIs commonly offer, and latency depends on serving, not on whether
> the weights are open.

### Q: A customer requires that data never leave their cloud tenant. Which option is not automatically excluded?
- [ ] A proprietary model through its public API with a zero-retention agreement
- [ ] An open-weight model served by a third-party hosting provider's multi-tenant API
- [x] A proprietary model deployed as a private endpoint inside the customer's own tenant
- [ ] None of these; only self-hosted open weights can satisfy the requirement
> Zero retention and hosted open weights both send data outside the tenant, so they fail the stated
> rule. A private endpoint keeps the request inside the customer's network boundary, which shows the
> isolation requirement is separate from the open-versus-proprietary choice.
