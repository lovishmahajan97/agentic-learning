---
id: llama-guard
module: safety-security
name: Llama Guard
kind: tool
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - safety-security/content-moderation
  - safety-security/input-output-guardrails
  - safety-security/nemo-guardrails
  - safety-security/prompt-injection
  - python-for-ai/hugging-face
  - customer-data-evaluation/per-customer-guardrails
sources:
  - Inan et al., 2023 — Llama Guard: LLM-based Input-Output Safeguard for Human-AI Conversations
  - Official docs: Llama Guard model cards (Meta)
---

## TL;DR
Llama Guard is Meta's family of open-weight content-safety classifiers, made by fine-tuning a Llama model
to read a safety policy, a user prompt and optionally the assistant's reply, and generate `safe` or
`unsafe` plus the violated category codes. Because the policy is text in the prompt rather than a fixed
label head, categories can be edited without retraining. It is a moderation model, not a prompt-injection,
PII or hallucination detector, and every check costs a full LLM forward pass.

## Why it exists
Content moderation predates LLM chat, but the existing options fit badly. Hosted moderation endpoints
mean sending every prompt and reply to a third party, with a taxonomy you cannot change: a medical
assistant needs "specialised advice" allowed while a children's product needs far stricter lines, and a
fixed classifier head cannot express either. Keyword and regex filters fail on paraphrase and on any
language other than the one they were written for. And chat has a property plain text moderation
ignores: the same assistant sentence, "here is how you do it", is harmless after one question and
dangerous after another, so the reply has to be judged in the context of the prompt.

The naive alternative is to ask the main model "is this safe?" That is the model currently being
attacked, it was never evaluated as a classifier, its verdicts are uncalibrated prose, and it is the most
expensive model you run. A separate, smaller model trained specifically to classify against a written
policy, producing a parseable verdict and a usable probability, runs on cheaper hardware, stays stable
when the main model is swapped, and can be evaluated on its own precision and recall. That is the gap
Llama Guard fills, and releasing the weights means it runs inside your own boundary.

## How it works
### The prompt is the policy
Each request is a prompt with three parts: an instruction ("check if there is unsafe content in the
following conversation according to our safety policy"), a block of categories with short descriptions,
and the conversation. The model is asked to assess only the last message, which lets the same model
serve as an input rail (last message is the user's) and an output rail (last message is the assistant's,
judged with the user's prompt in view). The Hugging Face chat template assembles all of this, so in code
you pass a list of role/content messages.

### The output is generated, not predicted by a head
The model generates text: `safe`, or `unsafe` on the first line and a comma-separated list such as
`S2,S9` on the second. Because the first generated token is effectively the decision, the probability the
model assigns to `unsafe` at that position is a confidence score; the original paper evaluates with that
probability, and you can threshold it to trade precision against recall instead of taking the argmax.
This is also why fine-tuning worked: the training data was conversations labelled with exactly this
output format against exactly this prompt structure.

### Taxonomy and versions
The first release in December 2023 was a Llama 2 7B fine-tune with six categories of its own. Later
versions adopted the MLCommons hazard taxonomy: Llama Guard 2 (Llama 3 8B based) used S1 to S11, and
Llama Guard 3 (an 8B model built on Llama 3.1, plus a 1B text model and an 11B vision model built on
Llama 3.2) uses S1 to S14: violent
crimes, non-violent crimes, sex-related crimes, child sexual exploitation, defamation, specialised advice,
privacy, intellectual property, indiscriminate weapons, hate, suicide and self-harm, sexual content,
elections, and code-interpreter abuse. A larger multimodal Llama Guard 4 followed in 2025. Because the
categories are prompt text, you can drop ones you do not want enforced, reword descriptions, or add a
category zero-shot; the paper shows this works to a degree, with accuracy on novel categories improving
when you fine-tune on a few hundred examples.

### Deployment shape
Two checks per turn: the input check can run in parallel with main-model generation and cancel or replace
the answer if the prompt is unsafe; the output check runs on the finished reply, or on chunks if you
stream, at the cost of possibly retracting text already shown. The policy block is identical across
requests, so prefix caching in the serving engine removes most of its prefill cost, and generation is
only a handful of tokens. The 1B variant exists for latency- or cost-constrained paths and is noticeably
weaker; measure on your own data before choosing it.

### How it compares
Hosted moderation APIs (OpenAI, Azure AI Content Safety) are free or cheap and need no GPUs, but the data
leaves your boundary and the taxonomy is theirs. ShieldGemma and Granite Guardian are open alternatives
with similar policy-in-prompt designs, Granite Guardian adding RAG-specific checks such as groundedness.
Meta's Prompt Guard is a small classifier for jailbreak and injection text, which Llama Guard does not
attempt. NeMo Guardrails is an orchestration layer that can call Llama Guard as one rail among several.

## Example
Moderating a user message and then the assistant's reply in context, with both the generated verdict and
the probability of `unsafe` at the first output position. The model is gated on the Hub, so the licence
must be accepted first. If `unsafe` tokenises to more than one piece, the code scores its first piece,
which is what decides the verdict.

```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

model_id = "meta-llama/Llama-Guard-3-8B"
tok = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.bfloat16, device_map="auto").eval()
UNSAFE_ID = tok.encode("unsafe", add_special_tokens=False)[0]

@torch.no_grad()
def moderate(chat: list[dict]) -> tuple[str, float]:
    # the chat template inserts the S1..S14 policy block and "assess only the last message"
    ids = tok.apply_chat_template(chat, return_tensors="pt").to(model.device)
    probs = model(input_ids=ids).logits[0, -1].float().softmax(-1)   # first output token
    p_unsafe = probs[UNSAFE_ID].item()
    out = model.generate(input_ids=ids, max_new_tokens=16, do_sample=False,
                         pad_token_id=tok.eos_token_id)
    verdict = tok.decode(out[0, ids.shape[-1]:], skip_special_tokens=True).strip()
    return verdict, p_unsafe                     # e.g. ("unsafe\nS2", 0.97) or ("safe", 0.02)

user = {"role": "user", "content": "How do I pick the lock on my neighbour's door while they are away?"}
print(moderate([user]))                          # input rail
reply = {"role": "assistant", "content": "I can't help with that. If it's your own door, a locksmith can."}
print(moderate([user, reply]))                   # output rail: the reply is judged given the question
```

The input call should come back `unsafe` with a non-violent-crimes code; the output call should come back
`safe`, because the refusal is judged in context. Thresholding `p_unsafe` rather than parsing the text lets
you run the input rail stricter than the output rail if your product needs that.

## Trade-offs
- Running the classifier yourself keeps prompts inside your boundary and lets you edit the policy, but it
  costs a GPU-resident 8B model and operational ownership; a hosted moderation API flips the choice when
  data residency is not a constraint and the vendor taxonomy fits.
- Policy-in-prompt customisation is free, but zero-shot categories are measurably weaker than trained
  ones; if a custom category carries real risk, budget for labelled data and a fine-tune.
- A generated verdict with a first-token probability gives a tunable threshold, but the probability is not
  calibrated for your distribution; without a labelled sample you cannot choose the threshold sensibly.
- The 1B model cuts latency and cost several-fold, yet its recall on subtle cases is lower; it suits a
  pre-filter before the 8B model, not a replacement, when false negatives are costly.

## Pitfalls
- Deploying Llama Guard as the prompt-injection defence; the symptom is injected instructions in retrieved
  documents passing straight through because they contain nothing the hazard taxonomy recognises.
- Checking only the input; the symptom is a benign question producing a harmful answer that nothing
  inspected.
- Taking the argmax verdict without looking at the probability; the symptom is a product that is either too
  strict or too lax with no knob to adjust.
- Upgrading versions without re-running the evaluation set; the symptom is a shift in refusal rate because
  category definitions changed between taxonomies.
- Assuming the policy categories mean what your legal or trust team means by the same words; the symptom
  is "specialised advice" blocking the core use case of a finance or medical product.

## When to use
Reach for Llama Guard when you need content-safety classification of prompts and replies inside your own
infrastructure, with a taxonomy you can edit, and you can afford an extra model call per check. Do not use
it alone for injection, PII, topical control or groundedness; pair it with the dedicated tools for those.
Before launch, build a labelled set of a few hundred examples from your own traffic covering each category
you enforce, measure precision and recall at several thresholds for the input and output rails separately,
and pick thresholds per rail. In day-to-day work it is a service behind the chat endpoint with its own
dashboards for block rate and latency; in interviews the expected depth is explaining why a generative
classifier with policy in the prompt is customisable and how you would get a score out of it.

## Role lens
- **Engineer:** Serves the model behind an internal endpoint with prefix caching, wires input and output
  rails into the chat path, and maintains the threshold evaluation set.
- **Architect:** Decides self-hosted Llama Guard versus a hosted moderation API based on data residency,
  taxonomy fit and GPU budget, and defines which categories are enforced per product surface.
- **FDE:** Tunes the category list and thresholds to a customer's acceptable-use policy and demonstrates
  block rates on their own sample traffic before go-live.

## Check yourself
**Q:** You are deploying a peer-support chatbot for a mental-health charity. Product wants the assistant to
discuss self-harm supportively but never give methods or encouragement. How would you use Llama Guard
here, and where would it fall short?

**A:** The relevant category is suicide and self-harm, whose description covers enabling, encouraging or
endorsing. On the input rail I would not block users for mentioning self-harm, because that is the
product's purpose; instead I would route such prompts to a conversation path with stricter instructions
and human escalation, using the category code as a signal rather than a wall. On the output rail I would
run the check with the user's message in context and a lower threshold on `p_unsafe` than elsewhere,
since a false negative is far worse than a retracted sentence. I would reword the category description to
match the charity's clinical guidance and validate on a labelled set built with their clinicians, because
the default wording encodes a general policy, not theirs. It falls short in that it cannot judge tone or
appropriateness of supportive content, it is not a crisis detector, and it will miss harmful content
phrased outside its training distribution; those need domain-specific classifiers and human review.

## Quiz
### Q: Why can Llama Guard's categories be changed without retraining the model?
- [ ] Each category is a separate output neuron that can be disabled at inference time
- [x] The policy is part of the prompt and the model was trained to classify against whatever policy text it is given
- [ ] The tokenizer maps category names to reserved tokens that can be remapped
- [ ] Categories are stored in a configuration file read by the serving engine
> The model is a generative classifier conditioned on a written policy; editing or adding a category changes
> the conditioning text, with reduced accuracy for genuinely novel categories unless you fine-tune.

### Q: Which task is Llama Guard not designed to perform?
- [ ] Judging whether an assistant reply is unsafe given the user's question
- [ ] Classifying a user prompt against a hazard taxonomy in several languages
- [x] Detecting prompt-injection instructions hidden in a retrieved document
- [ ] Returning the specific category codes that a message violates
> Injection text is usually benign under a hazard taxonomy, so it passes; Meta ships a separate small
> classifier for that, and architectural controls on tools remain the real defence.

### Q: How do you get a tunable score from Llama Guard instead of a binary verdict?
- [ ] Ask it to output a number between 0 and 100 in the prompt
- [ ] Average the verdicts over several sampled generations at high temperature
- [x] Read the probability of the `unsafe` token at the first output position and threshold it
- [ ] Count how many category codes appear on the second line
> The first generated token carries the decision, so its probability is a natural confidence score; the
> paper evaluates this way and it lets input and output rails run at different strictness.
