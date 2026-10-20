---
id: pre-training
module: training-adaptation
name: Pre-training
kind: concept
difficulty: core
est_minutes: 16
related:
  - training-adaptation/post-training
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/data-curation
  - training-adaptation/distributed-training
  - model-internals/scaling-laws
  - model-internals/tokenization
  - model-internals/perplexity
  - ml-fundamentals/loss-functions
sources:
  - Radford et al., 2018 — Improving Language Understanding by Generative Pre-Training
  - Brown et al., 2020 — Language Models are Few-Shot Learners
  - Kaplan et al., 2020 — Scaling Laws for Neural Language Models
  - Hoffmann et al., 2022 — Training Compute-Optimal Large Language Models
---

## TL;DR
Pre-training turns a randomly initialised transformer into a model that knows language, facts and
reasoning patterns by making it predict the next token over trillions of tokens of text. It is
self-supervised because the text provides its own labels, which is why it can use the whole internet
instead of a hand-labelled set. It consumes almost all of the compute and produces a *base model* that
completes text but does not yet follow instructions; every later stage adapts that base.

## Why it exists
Before pre-training, each NLP task got its own model trained from scratch on its own labelled data. A
sentiment classifier learned English from a few thousand labelled reviews, which is far too little to
learn grammar, world knowledge and idiom. Labelled data is expensive, and the quantity needed to learn
language from zero would be impossible to annotate.

The way out is to find a task whose labels are free. Next-token prediction is that task: take any text,
hide the token after position *t*, and ask the model to predict it from tokens ≤ *t*. Every document on the
web becomes training data without a single annotator. To predict well, the model is forced to learn
syntax (which words can follow), semantics (which continuations make sense), facts (the capital of France
is ...), and even procedural patterns (how a proof or a function body continues). The objective is
simple; the data makes it hard.

The naive alternative of training the big model directly on your downstream task fails for two reasons.
First, no downstream dataset is large enough to set billions of parameters without overfitting. Second,
most of what a model needs for your task is general competence that is shared across tasks, so paying to
learn it once, then adapting, is cheaper than paying for it per task. Pre-training is that one-time
payment, and fine-tuning, RAG and prompting are all ways to spend its result.

## How it works
### The objective
For a token sequence x₁…x_T, the model with parameters θ is trained to maximise the log-likelihood of
each token given its prefix:

```
L(θ) = − Σ_t log p_θ(x_t | x_<t)
```

This is cross-entropy between the one-hot next token and the model's softmax output, averaged over every
position in every sequence. The causal mask lets one forward pass score all T positions at once, so a
sequence of 8k tokens yields 8k training signals. Perplexity is just exp of this loss, which is why
pre-training progress is reported in loss or perplexity rather than accuracy.

### The data
Tokens come from a curated mixture: web crawl, code, books, reference text, academic papers, and
increasingly multilingual and synthetic data. The pipeline matters as much as the model: language
identification, quality filtering (classifiers or heuristics), near-duplicate removal (duplicated text
makes the model memorise rather than generalise), and decontamination against evaluation sets. The
*mixture weights* (how much code versus web) are a design choice that shapes what the base model is good
at, because the model allocates capacity to what it sees most.

### The compute budget
Training cost scales with parameters N and tokens D. A standard approximation for a dense transformer
is roughly 6·N·D floating-point operations: 2·N·D for the forward pass and about twice that for
backward. The scaling-law work asked how to split a fixed budget between N and D. Kaplan et al. found
loss falls smoothly as a power law in compute; Hoffmann et al. (the Chinchilla result) showed that
earlier models were undertrained, and that for compute-optimal training N and D should grow together,
with roughly 20 tokens per parameter as a rule of thumb. Later open models deliberately train far past
that ratio because they optimise for *inference* cost: a smaller model trained on more tokens is cheaper
to serve for the same quality.

### The schedule
Practical recipes use AdamW with a linear warmup followed by a cosine or warmup-stable-decay schedule,
batch sizes in the millions of tokens, bf16 mixed precision and gradient clipping. Training runs in
stages: the main run on short sequences, then a shorter long-context stage that extends the context
window, and often a final *annealing* phase on high-quality data with the learning rate decayed to near
zero, which measurably lifts benchmark scores. Mid-training checkpoints are kept so a loss spike or
divergence can be rolled back.

### Why the result is a base model
Nothing in the objective says "be helpful". A base model prompted with a question may answer, continue
with more questions, or produce a forum-style thread, because all of those are plausible continuations
of web text. Instruction following, refusals and chat formatting are added by post-training. The base
model's job is to carry the knowledge and the latent skills; it is the asset that every downstream stage
starts from.

## Example
The whole pre-training objective fits in a few lines. Given a batch of token ids, the labels are the
same ids shifted left by one; the loss is cross-entropy at every position. This is exactly what
`AutoModelForCausalLM` does internally when you pass `labels=input_ids`.

```python
import torch, torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen2.5-0.5B"               # a small base model, not an -Instruct one
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.bfloat16).cuda()
opt = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=0.1)

text = "Pre-training predicts the next token. The loss is cross-entropy over the vocabulary."
ids = tok(text, return_tensors="pt").input_ids.cuda()        # [1, T]

logits = model(input_ids=ids).logits                          # [1, T, vocab]
pred, target = logits[:, :-1, :], ids[:, 1:]                  # position t predicts token t+1
loss = F.cross_entropy(pred.reshape(-1, pred.size(-1)).float(), target.reshape(-1))
print(f"loss={loss.item():.3f}  perplexity={loss.exp().item():.1f}")

loss.backward()                                               # one optimiser step of "pre-training"
torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
opt.step(); opt.zero_grad()
# Scale this loop to ~10^13 tokens, thousands of GPUs and a cosine LR schedule and you have a base model.
```

Back-of-envelope on the budget, as an illustrative order of magnitude: a 7B-parameter model trained on
1 trillion tokens needs about 6 × 7e9 × 1e12 ≈ 4e22 FLOPs. At an achieved 4e14 FLOP/s per GPU (roughly
40% utilisation of a modern accelerator's bf16 peak) that is 1e8 GPU-seconds, about 1,200 GPU-days.
Pre-training is the one stage where the arithmetic is in GPU-years, not GPU-hours.

## Trade-offs
- More tokens per parameter raise quality for a fixed model size and lower serving cost, but past the
  compute-optimal point each extra point of quality costs disproportionately more training compute.
- A broad data mixture gives general competence, while over-weighting one domain (code, a language)
  improves it at the expense of others; the right mix depends on what the model will be served for.
- Longer training sequences teach long-range structure but cost quadratic attention compute, which is
  why context extension is done in a short late stage rather than from the start.
- Aggressive quality filtering raises per-token value but can remove the dialects, formats and messy
  text the model will meet in production, hurting robustness.

## Pitfalls
- Judging a base model by chatting with it; it was never trained to answer, so a rambling or repetitive
  reply says nothing about its knowledge. Evaluate base models with few-shot prompts or log-likelihood
  tasks instead.
- Letting evaluation data leak into the corpus; the symptom is benchmark scores that do not transfer to
  held-out tasks of the same kind, which is why decontamination is part of the pipeline.
- Reading a single pre-training loss number as a quality comparison across models; loss depends on the
  tokenizer and the data mix, so two models' losses are only comparable with the same tokenizer and
  evaluation corpus.
- Treating "continued pre-training" on domain text as cheap; without mixing in general data at a sensible
  ratio the model forgets general skills, and the symptom is a jump in loss on everything outside the
  domain.

## When to use
Almost no application team pre-trains from scratch; the cost is in GPU-years and the data engineering
is a product in itself. What you decide is *which* pre-trained base to build on, and the knowledge of how
it was made is what lets you decide well: its token budget and data mix predict its strengths, its
tokenizer and context window are fixed at this stage, and its licence and training-data provenance are
set here. Continued pre-training on a large domain corpus (tens of millions of tokens or more, such as
legal or biomedical text) is the one case where an application team does run this objective, usually
on a smaller model. In interviews, expect to explain the objective, why it is self-supervised, what
scaling laws say about N versus D, and why a base model differs from a chat model.

## Role lens
- **Engineer:** Chooses a base checkpoint by its training tokens, data mix and context length, and runs
  continued pre-training only with a general-data replay mix to avoid forgetting.
- **Architect:** Owns the build-versus-adopt decision on foundation models, weighing training cost,
  data provenance and licence terms against the control a custom base would give.
- **FDE:** Explains to a customer why "train a model on our data" usually means fine-tuning or RAG on a
  pre-trained base, not pre-training, and what a corpus of their size can realistically teach.

## Check yourself
**Q:** A customer with 200 million tokens of proprietary manuals asks you to "pre-train a model on our
data so it knows our products". How do you respond, and what would you actually propose?
**A:** 200M tokens is four orders of magnitude short of a pre-training corpus, so training from scratch
would produce a model that cannot even write fluent English. The knowledge they want lives in two
places: facts, which are best served by retrieval over the manuals so answers stay current and citable,
and style or terminology, which a fine-tune on a pre-trained base can absorb. If the domain vocabulary is
genuinely far from web text, continued pre-training of an open-weight base on the 200M tokens, mixed with
general data to prevent forgetting, is a defensible middle option. I would propose RAG first, measure the
failure cases, and only then decide whether adaptation of the weights is justified.

## Quiz
### Q: Why can pre-training use the raw internet while earlier NLP models needed labelled datasets?
- [ ] Transformers are robust to label noise, so unlabelled text is acceptable
- [x] Next-token prediction uses the text itself as the label, so no annotation is needed
- [ ] Web text is pre-labelled by its HTML structure
- [ ] Pre-training uses clustering, which is unsupervised
> The target at each position is simply the next token already present in the document. That makes the
> objective self-supervised: supervision exists, but it comes for free from the data rather than from
> annotators.

### Q: According to the Chinchilla result, what was wrong with earlier large models like GPT-3?
- [ ] They had too few parameters for their training data
- [x] They were trained on too few tokens for their parameter count
- [ ] They used the wrong tokenizer
- [ ] They were trained with too high a learning rate
> Hoffmann et al. showed that for a fixed compute budget, parameters and tokens should scale together,
> roughly 20 tokens per parameter. GPT-3-era models had far fewer tokens than that, so a smaller model
> trained longer would have matched them for the same compute.

### Q: A freshly pre-trained base model is asked "What is the capital of France?" and replies with three more geography questions. What does this show?
- [ ] The model failed to learn the fact during pre-training
- [ ] The tokenizer split the question badly
- [x] The base model is continuing text in a plausible style; answering is a behaviour added in post-training
- [ ] The learning rate was too low in the annealing phase
> A list of questions is a perfectly likely continuation of a question in web text. The fact may well be
> encoded; eliciting it as an answer rather than a continuation is what instruction tuning and preference
> training provide.
