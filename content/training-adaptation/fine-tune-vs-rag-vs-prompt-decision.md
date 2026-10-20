---
id: fine-tune-vs-rag-vs-prompt-decision
module: training-adaptation
name: Fine-tune vs RAG vs prompt decision
kind: decision
difficulty: core
est_minutes: 15
related:
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/peft
  - training-adaptation/catastrophic-forgetting
  - training-adaptation/knowledge-distillation
  - rag-retrieval/long-context-vs-rag
  - prompting/few-shot
  - prompting/prompt-caching
  - evals/golden-dataset
sources:
  - Lewis et al., 2020 — Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks
  - Ovadia et al., 2023 — Fine-Tuning or Retrieval? Comparing Knowledge Injection in LLMs
  - Zhang et al., 2024 — RAFT: Adapting Language Model to Domain Specific RAG
---

## TL;DR
The three levers change different objects. A prompt changes the input of one request and costs nothing
to try. RAG puts knowledge the model does not have into the input at request time, with freshness,
access control and citations. Fine-tuning changes the weights, which is the right tool for behaviour,
format, skill and per-request cost, and a poor tool for injecting facts. Decide by naming the gap
(knowledge, behaviour or economics) and climb prompt → RAG → fine-tune only when evals show the cheaper
rung is not enough.

## Why it exists
"Make the model know our stuff", "make it sound like us", "make it stop doing X" arrive every week, and
the reflex answer is "fine-tune it", because training feels like the real engineering. That reflex is
expensive and usually wrong for the first request. Fine-tuning on a pile of documents does not reliably
make a model recall their contents: the loss is next-token prediction over a handful of phrasings, which
is nothing like the massive repetition and paraphrase that got facts into the model during pretraining.
Ovadia et al. compared knowledge injection methods and found retrieval consistently beat unsupervised
fine-tuning on new facts. Even when fine-tuned knowledge does stick, it is frozen at training time,
cannot be scoped per tenant or deleted, carries no citation, and has to be redone when the base model is
upgraded.

The opposite reflex, "just prompt harder", has its own ceilings. Prompts grow to thousands of tokens of
instructions and examples, which costs money and latency on every call; few-shot examples cannot pin
down a precise output distribution; and some behaviours (strict schema compliance in a small model,
a domain's tone, a tool-calling idiom) never stabilise by instruction alone.

RAG sits between them and is often the answer, but only for one kind of gap: it helps when the model is
*missing* information that exists in retrievable text. It does nothing when the model has the
information and still does the wrong thing with it, and it introduces retrieval quality as a new failure
mode.

So the decision needs a framework that maps the symptom to the lever, and a measurement loop that proves
the cheaper lever insufficient before paying for the next one. Without it, teams spend two weeks on a
LoRA to fix what a 200-token system prompt edit would have fixed, or build a retrieval stack to fix a
formatting problem.

## How it works
### Three levers, three different objects
| Lever | What it changes | Good at | Weak at | Cost shape |
|---|---|---|---|---|
| Prompt (system prompt, few-shot, structured output) | The input of this request | Instructions, format, small behaviour shifts, per-deployment config | Precise distributions, very long rule sets, small-model compliance | Tokens × requests, partly offset by prompt caching |
| RAG | The input, with retrieved evidence | Facts, freshness, per-user/tenant scoping, citations, deletion | Behaviour the model already gets wrong with the facts in hand | Index, embeddings, retrieval quality, ops |
| Fine-tune (SFT/DPO, usually LoRA) | The weights | Default style, format, task skill, tool idiom, compressing a long prompt, distilling into a smaller model | Facts that change, facts needing attribution, anything still being redesigned | Data, GPU time, evals, re-doing on base-model upgrades, forgetting risk |

### The decision procedure
1. **Name the gap with failing examples.** Collect 50–200 real failures and sort them: the model lacked
   information (knowledge gap); it had the information and still answered wrongly, in the wrong form or
   tone (behaviour gap); it was right but too slow or expensive (economics gap). Most requests contain
   two gaps tangled together, and the lever is different for each.
2. **Knowledge gap → RAG.** Or long context, if the corpus is small, static and the token cost is
   acceptable. Do not fine-tune to inject the content. If the model handles retrieved passages badly
   (ignores them, cannot say "not in the documents"), that is a behaviour gap: fine-tune it to *use*
   retrieved context (the RAFT idea), still keeping the facts in the index.
3. **Behaviour gap → prompt first.** System prompt, a few curated examples, structured-output mode. Build
   the eval set from the failures and score it. If the score plateaus below target, or the prompt needed
   to reach it is so long that it dominates per-request cost, move to a LoRA SFT on the same reviewed
   examples; use DPO when the failures are preference-shaped (two answers, one better) rather than
   right/wrong.
4. **Economics gap → fine-tune.** Distil the big model's outputs into a smaller model, or train the
   few-shot block into the weights so the prompt shrinks; verify on the same eval set that quality held.
5. **Combine.** The usual end state of a production domain assistant is RAG for facts, a fine-tuned or
   well-prompted model for behaviour, and a prompt for per-deployment configuration. The levers are not
   exclusive; the mistake is using the wrong one for a given gap.

### Why fine-tuning is weak at facts
SFT shifts probability mass toward the sequences it sees. A fact seen a few times in one phrasing does
not become reliably retrievable under other phrasings, which is what question answering requires. RAG
puts the fact in the context window, where attention can copy it directly, so the model needs no
memorisation at all. This is also why fine-tuned "knowledge" fails silently: the model produces
plausible details in the style of the training data rather than refusing.

### Reversibility and the upgrade tax
A prompt change is one deploy with instant rollback and transfers largely unchanged to a newer base
model. A RAG index is updated incrementally and supports deletion, and it transfers to any model.
A fine-tune is a new artifact that must be re-evaluated, rolled back by swapping adapters, and
re-trained for every base-model release; with proprietary APIs it may also lock you to one vendor's
fine-tuning product. That tax is why "prompt until proven insufficient" is the default ordering, not
a statement that fine-tuning is bad.

## Example
A support assistant for a SaaS product (all numbers illustrative). Three complaints: it does not know
the product docs, which change weekly; it should answer in brand voice as a fixed JSON object; and it
runs 2 million requests a month with a 5,000-token prompt of instructions and twelve examples.

Sort the gaps. The docs are a knowledge gap: build RAG over the docs with weekly re-indexing, so new
pages are answerable the day they ship and retired pages stop being cited. Voice and JSON are behaviour
gaps: tighten the system prompt, enable structured-output mode, and measure on 200 reviewed cases.
Suppose that reaches 97% valid JSON and a tone score of 4.1/5 on a rubric, against targets of 99.5% and
4.5. The prompt has plateaued, so fine-tune: a LoRA SFT on 1,500 reviewed answers reaches 99.7% and 4.6,
and the twelve examples can leave the prompt, which shrinks to 800 tokens.

Now the economics. Input tokens drop from 5,000 × 2M = 10 billion to 800 × 2M = 1.6 billion per month,
about 6× fewer; with prompt caching the cached share is discounted, so the realised saving is smaller
but still material. The fine-tune costs a few GPU-hours per iteration plus the eval harness, repeated at
each base-model upgrade. Knowledge stayed in the index; the weights only learned behaviour; the system
prompt still carries per-customer configuration.

## Trade-offs
- Prompting costs nothing to try and rolls back instantly, but every request pays for the prompt and
  quality plateaus; it flips to fine-tuning when a multi-thousand-token prompt runs at high volume or
  evals stall below target.
- RAG gives freshness, citations, access control and deletion, but you now own a retrieval system whose
  recall becomes the dominant failure mode; it flips to plain long context when the corpus is small and
  rarely changes, and it never flips to fine-tuning for facts.
- Fine-tuning gives behaviour you cannot prompt into existence and a cheaper per-request path, but costs
  data, evals, forgetting risk and re-training at every base-model release; it flips back to prompting
  when the task is still changing or the model is upgraded often.
- Combining RAG with a fine-tuned model is the strongest configuration, but doubles the moving parts; it
  only pays off when the team can run the same eval set across both layers after every change.

## Pitfalls
- Fine-tuning to inject facts; the symptom is confident, plausible details that match the training data's
  style but not its content, and no way to answer questions about last week's changes.
- Skipping the eval set; the symptom is "the fine-tuned model feels better" with no number attached, and
  regressions on other tasks discovered by users rather than by the team.
- Building RAG for a behaviour gap; the symptom is retrieval returning the right passage while the model
  still answers in the wrong format or ignores the passage.
- Fine-tuning before the prompt has been seriously tried; the symptom is a two-week training project that
  a short system-prompt edit would have resolved.
- Ignoring the upgrade tax; the symptom is a fine-tuned model blocking a migration to a stronger base
  model for months because nobody budgeted to re-train and re-evaluate.
- Treating long context as free RAG; the symptom is per-request cost blowing up and lost-in-the-middle
  errors on the documents that matter most.

## When to use
Use the procedure every time someone asks for a model to "know", "sound like" or "stop doing" something.
Start by classifying failures, then prompt, then RAG for knowledge, then LoRA for behaviour or cost, and
measure the same eval set at every rung. Reach for fine-tuning when the behaviour is stable and the prompt
has plateaued or become the dominant per-request cost, when a smaller model must match a larger one on a
narrow task, or when the output distribution must be precise. Avoid it while the task is still being
designed, when facts must be fresh or attributable, or when you cannot afford to re-train at the next
base-model release. In interviews, the expected answer is never "fine-tune" or "RAG" alone; it is the
gap classification, the ordering, and the measurement loop that justifies each step.

## Role lens
- **Engineer:** Builds the eval set from real failures first, ships the prompt, then the retrieval
  layer, then a LoRA, scoring the same set at every rung and owning the prompt-token budget.
- **Architect:** Owns the lever policy: which knowledge must stay in retrieval for freshness, access
  control or compliance, which behaviours may move into weights, and the plan for base-model upgrades.
- **FDE:** Translates the customer's "it doesn't know our business" into the knowledge/behaviour split in
  week one, and declines the request to fine-tune on the company wiki with the reasoning to back it.

## Check yourself
**Q:** A customer says: "The model doesn't know our product catalogue and keeps answering in the wrong
tone, so let's fine-tune it on our docs." How do you respond, and what plan do you propose?
**A:** Separate the two gaps. The catalogue is knowledge that changes, must be scoped and must be
attributable, so it belongs in retrieval: fine-tuning on the docs will not make SKUs reliably recallable,
cannot remove discontinued items, and gives no citations. Tone is behaviour: tighten the system prompt
with a few curated examples first and score it on a rubric over 100–200 real conversations. If that
plateaus below target, train a LoRA on reviewed answers for tone only, keeping facts in the index. Later,
if the model mishandles retrieved passages, fine-tune it to use context rather than to contain it.
Propose the measurement plan up front so each step is justified by a number, not a feeling.

## Quiz
### Q: Which situation is RAG the right lever for?
- [ ] The model has the facts but formats answers inconsistently
- [x] The model lacks information that exists in documents and changes over time
- [ ] The model is accurate but too slow and expensive at current volume
- [ ] The model refuses too many legitimate requests
> RAG supplies missing, changing, attributable information at request time. Formatting and refusal are
> behaviour gaps for prompting or fine-tuning; cost is an economics gap for distillation or prompt compression.

### Q: Why is supervised fine-tuning a poor way to inject facts?
- [ ] Fine-tuning cannot modify attention-layer weights, only the output head
- [x] Next-token training on a few phrasings does not make facts reliably retrievable, and it offers no freshness or citation
- [ ] LoRA's low rank is too small to store any factual content
- [ ] Fine-tuning only changes the tokenizer vocabulary, not the model
> SFT shifts probabilities toward seen sequences; it does not build the robust, paraphrase-invariant recall
> that pretraining's massive repetition produces. Retrieval puts the fact in context, where attention can copy it.

### Q: When does fine-tuning beat prompting on cost?
- [ ] Always, because fine-tuned models are inherently smaller
- [ ] When the task definition changes every week
- [x] When a long few-shot prompt at high volume dominates per-request cost and the behaviour is stable
- [ ] When the base model is upgraded every month
> Training the examples into the weights removes thousands of tokens from every request, which pays off at
> volume; frequent task or base-model changes mean re-training repeatedly, which erases that saving.
