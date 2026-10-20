---
id: supervised-fine-tuning
module: training-adaptation
name: Supervised fine-tuning (SFT)
kind: concept
difficulty: core
est_minutes: 17
status: reviewed
related:
  - training-adaptation/instruction-tuning
  - training-adaptation/post-training
  - training-adaptation/peft
  - training-adaptation/catastrophic-forgetting
  - training-adaptation/overfitting
  - training-adaptation/data-curation
  - training-adaptation/learning-rate
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
sources:
  - Ouyang et al., 2022 — Training language models to follow instructions with human feedback
  - Zhou et al., 2023 — LIMA: Less Is More for Alignment
  - Official docs: Hugging Face TRL
---

## TL;DR
SFT continues training a pre-trained model on prompt-response demonstrations with the same next-token
loss as pre-training, but computed only on the response tokens. It is the cheapest and most predictable
way to install a format, a persona or a domain style, because the model is told exactly what to say.
Its limit is that it can only imitate: it cannot express "this answer is better than that one", which
is why preference methods follow it.

## Why it exists
A pre-trained base model has the knowledge but not the behaviour. Prompting can coax behaviour out of
it, but the prompt has to be re-sent every call, it drifts on edge cases, and it cannot install anything
the base does not already do as an obvious pattern. If every response must open with a one-line
summary, cite a source id in a fixed format and never exceed 120 words, a prompt gets you most of the
way most of the time; a fine-tune gets you there every time with a shorter prompt.

The naive way to fine-tune is to keep pre-training on your domain text. That teaches the model the
*distribution* of your documents, not how to answer about them: a model continued-pre-trained on
support tickets will produce more support tickets. What you want is a conditional: given this prompt,
produce this response. SFT frames the data as (prompt, response) pairs and trains the model to produce
the response conditioned on the prompt, which is why the loss is masked so that the prompt is context
rather than target.

The other naive alternative is "train on a lot of mediocre examples". LIMA showed that about a thousand
carefully written demonstrations can be enough to turn a strong base model into a capable assistant,
and that the quality and diversity of the demonstrations matter more than their count. The base model
already has the skills; SFT is choosing which of its behaviours to surface, and noisy data surfaces
noisy behaviour.

## How it works
### Data format and the chat template
Each example is a conversation: an optional system message, one or more user turns, and the assistant
turns you want the model to produce. A chat template renders this into a single token sequence with
special tokens marking each role, for example `<|im_start|>assistant ... <|im_end|>`. The template is
part of the model's contract: train with one template and serve with another and the model will not
recognise where it is supposed to speak.

### Loss masking
The sequence is scored with next-token cross-entropy exactly as in pre-training, but the labels for
prompt and system tokens are set to an ignore value (−100 in PyTorch), so gradients flow only from the
assistant tokens. This matters *because* the model should not spend capacity learning to predict user
questions, and because in multi-turn data the user turns are often noisy or adversarial. In multi-turn
conversations every assistant turn is a target and every earlier turn is context.

The end-of-turn token (`<|im_end|>` above, or the model's EOS) must sit *inside* the supervised span.
It is the only thing that teaches the model to stop; mask it out with the prompt and the fine-tuned
model rambles past its answer into an invented next turn.

### How the loss is averaged
With masking, examples contribute very different numbers of supervised tokens: a 20-token answer and
a 600-token answer in the same batch. Averaging over all supervised tokens in the batch (the default
in most trainers) weights each *token* equally, so long responses dominate the gradient; averaging per
example first weights each *conversation* equally. Neither is wrong, but the choice changes what the
model learns when lengths are skewed: token-averaging over-trains the long-form style, and
gradient-accumulation code that averages per micro-batch and then across micro-batches silently mixes
the two. Check which one your trainer does before comparing runs.

### Where demonstrations come from
Human-written responses are the gold standard but scarce. Most practical SFT sets are partly
*distilled*: a stronger model drafts responses, humans or filters reject the bad ones, and the student
is trained on the survivors. A variant, rejection-sampling fine-tuning, samples several responses from
the model itself and keeps the ones a verifier or reward model scores best. Both are still SFT; only the
provenance of the targets changes, and the student inherits whatever errors survive the filter.

### Full fine-tuning versus parameter-efficient
Full SFT updates every weight. With AdamW in mixed precision that costs about 16 bytes per parameter
for weights, gradients and optimiser state, so a 7B model needs over 100 GB before activations.
Parameter-efficient methods (LoRA, QLoRA) freeze the base and train small adapters, fitting on a single
GPU at some cost in how far behaviour can move. Full fine-tuning is preferred when the target behaviour
is far from the base or the data is large; adapters when you need many variants or a small budget.

### Hyperparameters that actually matter
- **Learning rate.** Far smaller than pre-training: roughly 1e-5 to 2e-5 for full fine-tuning of a
  7B-class model, around ten times higher for LoRA adapters. Too high and the model forgets general
  capability within a few hundred steps.
- **Epochs.** Usually one to three. Demonstration sets are small, so the model can memorise them; a
  rising validation loss after epoch two is the usual signal to stop.
- **Sequence packing.** Short examples are concatenated to fill the context window so no compute is
  wasted on padding. Packing must either reset position ids and mask attention across example
  boundaries or accept a small cross-contamination; naive packing lets one example attend to its
  neighbour.
- **Mixing in general data.** A slice of broad instruction data alongside the domain examples keeps the
  model from losing general skills, the SFT form of catastrophic forgetting.

### What SFT can and cannot teach
SFT is excellent at format, register, length, persona and well-specified procedures, and at domain
terminology. It is weak at anything where the demonstration is only one of many acceptable answers:
it cannot tell the model that a confident, fluent, wrong answer is worse than a hedged right one,
because it never sees a worse answer. It can also teach hallucination: if demonstrations contain facts
the base model does not know, the model learns to produce facts in that style regardless of whether it
knows them. That is why demonstrations should be consistent with the base model's knowledge or
grounded in context provided in the prompt.

## Example
The core of an SFT step: render the conversation with the chat template, mask the prompt tokens, and
compute cross-entropy on the assistant tokens only. Trainers such as TRL's `SFTTrainer` wrap exactly
this with batching, packing and checkpointing.

```python
import torch, torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen2.5-0.5B"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.bfloat16).cuda()
opt = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=0.0)

example = [
    {"role": "system", "content": "You are a support assistant. Answer in at most two sentences."},
    {"role": "user", "content": "How do I rotate my API key?"},
    {"role": "assistant", "content": "Open Settings > API keys, click Rotate next to the key, and "
                                     "update any clients within 24 hours; the old key expires then."},
]
prompt = tok.apply_chat_template(example[:-1], tokenize=False, add_generation_prompt=True)
full = tok.apply_chat_template(example, tokenize=False)
ids = tok(full, return_tensors="pt").input_ids.cuda()            # [1, T]
n_prompt = len(tok(prompt).input_ids)   # valid because the prompt ends at a template boundary,
                                        # so its tokens are an exact prefix of the full sequence

labels = ids.clone()
labels[:, :n_prompt] = -100                                      # prompt tokens are context, not targets
logits = model(input_ids=ids).logits
loss = F.cross_entropy(logits[:, :-1].reshape(-1, logits.size(-1)).float(),
                       labels[:, 1:].reshape(-1), ignore_index=-100)
loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
opt.step(); opt.zero_grad()
print(f"trained on {(labels != -100).sum().item()} of {ids.numel()} tokens, loss {loss.item():.3f}")
```

With a few thousand such examples, two epochs and a cosine schedule, the model learns the two-sentence
support voice; the masked count printed on each batch is the quickest check that the template and
masking line up. Read it critically: here the system prompt, question and role markers (a few dozen
tokens) are context, and only the answer plus its end-of-turn marker are targets, so the count should be
roughly half the sequence. If the supervised count equals
the full length, masking is off; if it is zero, the prompt length was computed on a different template
and every label is −100, which trains nothing while the loop runs without error. Decoding
`ids[0][labels[0] != -100]` back to text and eyeballing it is the one-minute test that catches both.

## Trade-offs
- Demonstrations give precise control over format and style, but authoring them is the most expensive
  data per example in post-training, which flips in your favour only when a strong model can draft them
  for human review.
- Full fine-tuning moves behaviour furthest and handles large datasets, while LoRA fits one GPU and
  gives swappable adapters; the adapter route loses when the target distribution is far from the base.
- More epochs fit the demonstrations better but overfit sooner, so the gain reverses once validation
  loss turns upward, typically between epochs two and three on small sets.
- Training only on assistant tokens focuses capacity on the behaviour you want, but with very short
  responses the number of supervised tokens per example is tiny, so you need more examples or longer
  responses to get signal.

## Pitfalls
- Serving with a different chat template from the one used in training; the symptom is a model that
  ignores the system prompt, answers as the user, or never emits the end-of-turn token.
- Forgetting to mask the prompt; the symptom is a suspiciously low training loss (the model learns to
  predict easy prompt text) and a model that has learned to generate user questions.
- Fine-tuning on facts the base model does not know; the symptom is confident hallucination in the
  same style on unrelated questions, because the model learned "state facts fluently" rather than the
  facts.
- Skipping a general-data mix; the symptom is a model that is excellent on the domain evaluation and
  measurably worse on general reasoning or coding benchmarks after a single round.
- Judging success by training loss; a low loss on memorised examples says nothing about generalisation,
  so hold out prompts by topic, not just by row.
- Masking the end-of-turn token along with the prompt; the symptom is a model that answers correctly and
  then keeps going, generating a fake user turn or repeating itself until the token limit.

## When to use
Reach for SFT when the behaviour you want is consistent and describable by examples: a fixed output
schema, a tone, a domain vocabulary, a procedure, or a tool-calling convention that prompting keeps
getting slightly wrong. Do not reach for it to inject knowledge that changes weekly; retrieval does that
better and stays auditable. Before fine-tuning, measure how far prompting plus a few-shot examples
gets you, because that is the baseline the fine-tune must beat net of its maintenance cost. During the
work, track validation loss, a held-out task metric and a general capability benchmark together. In
interviews, expect to explain loss masking, why the learning rate is tiny, and why SFT alone cannot fix
"fluent but wrong".

## Role lens
- **Engineer:** Writes the data pipeline that renders conversations through the chat template, masks
  prompts, packs sequences and tracks the supervised-token count per batch as a sanity check.
- **Architect:** Decides whether a fine-tuned model is worth owning versus a prompt, including who
  re-trains it when the base model or the format changes.
- **FDE:** Turns a customer's best human responses into demonstrations, negotiates the review process
  that keeps them consistent, and sets expectations that a few thousand good examples beat a hundred
  thousand scraped ones.

## Check yourself
**Q:** After SFT on 20,000 customer-service transcripts, the model's training loss is very low but it
frequently makes up product features that do not exist, and it sometimes continues the conversation by
writing the customer's next message. What went wrong, and how would you fix each symptom?
**A:** Two separate bugs. Writing the customer's next turn means the loss was not masked to assistant
tokens, so the model learned to generate user turns too; fix by setting prompt and user labels to the
ignore index and checking the supervised-token count per batch. Invented features point to data
content: transcripts contain facts the base model never saw, so SFT taught it to assert product facts
fluently without grounding. Fix by grounding responses in retrieved documentation placed in the prompt
so the demonstrations show "answer from the provided context", filtering transcripts with unverifiable
claims, and following up with preference pairs that penalise invented details. The low training loss is
consistent with both problems, which is why a held-out task metric, not loss, should gate the release.

## Quiz
### Q: Why are the prompt tokens masked out of the SFT loss?
- [ ] To reduce the memory used by the backward pass
- [x] So the model learns to produce the response conditioned on the prompt rather than to predict the prompt itself
- [ ] Because prompt tokens are already encoded in the KV cache
- [ ] To prevent the model from seeing the system prompt
> The prompt is context, not a target. Training on it wastes capacity on predicting user text and, in
> multi-turn data, teaches the model to generate user turns, which shows up as the model continuing the
> conversation on the user's behalf.

### Q: Which learning rate is a sensible starting point for full fine-tuning a 7B model with SFT?
- [ ] 3e-4, the usual pre-training peak
- [ ] 1e-2, because the dataset is small and needs fast learning
- [x] About 1e-5 to 2e-5, far below pre-training
- [ ] 1e-7, to avoid any change to the base weights
> Fine-tuning starts from a good solution and must not destroy it. Learning rates near pre-training
> levels cause rapid forgetting of general capability; rates that are too low fail to move the behaviour
> in the few thousand steps available.

### Q: A team wants the model to stop giving confident wrong answers and prefer hedged correct ones. Why is SFT the wrong tool on its own?
- [ ] SFT cannot change the tone of responses
- [ ] SFT requires more data than preference methods
- [x] SFT only ever sees the demonstrated answer, so it cannot learn that one fluent answer is worse than another
- [ ] Hedging requires a larger context window
> Imitation learning has no notion of a worse alternative. Expressing "A is better than B" needs
> comparison data consumed by RLHF or DPO; SFT can set the format, but the quality ranking comes from
> preference optimisation.
