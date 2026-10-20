---
id: supervised-vs-unsupervised-vs-self-supervised-learning
module: ml-fundamentals
name: Supervised vs unsupervised vs self-supervised learning
kind: concept
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - ml-fundamentals/classification
  - ml-fundamentals/regression
  - ml-fundamentals/clustering
  - ml-fundamentals/loss-functions
  - training-adaptation/pre-training
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/data-labeling
  - rag-retrieval/embedding-models
sources:
  - Radford et al., 2018 — Improving Language Understanding by Generative Pre-Training
  - Devlin et al., 2018 — BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding
  - Chen et al., 2020 — A Simple Framework for Contrastive Learning of Visual Representations
---

## TL;DR
The three paradigms differ in one thing: where the training signal comes from. Supervised learning takes
each label from outside the input, usually a paid human. Unsupervised learning has inputs only and
optimises a property of the data, such as how tightly points group. Self-supervised learning
manufactures labels from the input (the next token, the masked word, the matching caption), so the
signal is free and unlimited. Modern LLMs exist because self-supervision turned the internet into
training labels, with human supervision added at the end.

## Why it exists
Every learning algorithm minimises a loss, and a loss needs something to compare the model's output
against. The entire question is who supplies that target.

The first answer was: a human. Supervised learning works well when the task is narrow and labels are
cheap, but labels are the bottleneck. A routing label on a support ticket costs a few seconds of a
trained agent's time; a radiology label costs a specialist's time; and however cheap one label is, you
cannot buy billions of them. Meanwhile text, images, code and logs exist in enormous quantities with no
labels attached.

Unsupervised learning was the first attempt to use that data. With no target, you optimise structure
instead: compact clusters, directions of maximum variance, a density that assigns the data high
likelihood. The weakness is that the objective is not your task. Clustering tickets does not tell you
which ones to escalate, and there is no ground truth to score against, so you cannot say how good the
result is except by looking.

Self-supervised learning resolved the tension with a trick: hide part of the data and train the model
to predict it from the rest. The signal is now as plentiful as the data, and predicting the hidden part
well requires learning exactly the regularities you care about, because grammar, facts, visual
structure and code semantics are what make the hidden part predictable. This is what "pre-training"
means. Once that representation exists, a small amount of supervised data is enough to teach a
specific behaviour, which is why fine-tuning on a few thousand examples works where training from
scratch on them would not.

## How it works
### Supervised: the target is given
The dataset is pairs (x, y). The model is a function f_θ(x). The loss compares f_θ(x) with y:
cross-entropy when y is a class, squared error when y is a number (see the classification and
regression lessons). Gradient descent pushes θ toward reproducing the human judgements. Two
consequences follow directly. Generalisation depends on the labelled set covering the distribution you
will see in production, because the model has no signal about anything else. And the quality ceiling is
the label quality: if two annotators disagree on 15% of examples, a model cannot agree with both of them
on more than 85% of examples, so when scored against either one, up to 15 points of apparent "error"
can be annotator disagreement rather than model error. Measure agreement before blaming the model.

### Unsupervised: the target is a property of the data
Only x is available. The objective is defined on the data itself. k-means minimises the distance from
each point to its cluster centre; PCA keeps the directions of largest variance; a density model
maximises the likelihood it assigns to the observed points. The output is structure for a human to
interpret. Because there is no y, "accuracy" does not exist; evaluation is by internal metrics, by
stability, or by whether the structure helps a downstream decision. The clustering lesson covers the
main algorithms.

### Self-supervised: the target is manufactured from the data
Only x is available, but a *pretext task* turns each x into one or more (x', y') pairs automatically.
Three designs dominate:

- **Autoregressive (GPT-style).** Input: the tokens so far; label: the next token. A document of n
  tokens yields n − 1 training examples. The loss is ordinary cross-entropy over the vocabulary, so this
  is a classification problem with on the order of 100k classes whose labels cost nothing.
- **Masked (BERT-style).** Replace a fraction of tokens (15% in the original recipe) with a mask and
  predict the originals from both sides. It produces strong encoders for embeddings and classification
  but is awkward for generation, because the model never learned to produce text left to right.
- **Contrastive (SimCLR, CLIP, most embedding models).** Build pairs that should match (two
  augmentations of one image, an image and its caption, a query and the document that answered it) and
  train the model to pick the true partner out of a batch of impostors. The label "which one matches"
  is known because you constructed the pair. The loss-functions lesson derives the objective.

The reason this works is compression. To predict the next token of "the capital of France is", the model
must store a fact. To predict a masked verb, it must learn agreement. The pretext task is a proxy, but
the representation it forces into existence transfers to tasks nobody labelled.

### How they combine in a real system
A modern LLM pipeline uses all three in sequence. Pre-training is self-supervised on trillions of tokens.
Supervised fine-tuning teaches instruction following on thousands to millions of human-written
(prompt, response) pairs. Preference optimisation (RLHF, DPO) uses human *comparisons* between
responses, a form of supervision at the level of preferences rather than exact outputs. Underneath,
unsupervised methods run constantly: clustering embeddings of production traffic to discover what to
label next, deduplicating training data, finding outliers. Between supervised and unsupervised sit two
pragmatic hybrids: **semi-supervised** learning trains on a few labels plus many unlabelled inputs (for
example pseudo-labelling, where a model's confident predictions on unlabelled data are added as labels,
which works only if those confident predictions are mostly right), and **weak supervision**, where noisy
labels come cheaply from heuristics, metadata or an LLM and are cleaned or combined before training.
Reinforcement learning, where the signal is a
scalar reward from an environment rather than a label, is the fourth paradigm and underlies the RL part
of RLHF; it is covered in the training module.

### Where the boundary blurs
The labels are conventions, not laws, and an expert should know where they leak. Next-token prediction
is literally maximum-likelihood density estimation of p(text), which is the textbook definition of an
unsupervised objective; it is called self-supervised because the computation is a per-position
classification with a known target. Autoencoders, which reconstruct their own input, are filed under
either heading depending on the author. A useful working test: if the loss compares each example's
output with a specific target, whatever produced that target, the supervised toolkit (cross-entropy,
held-out accuracy, label-noise reasoning) applies; if the loss scores a global property such as
cluster compactness, it does not, and evaluation becomes a judgement call.

## Example
A support organisation has about 2,000,000 historical tickets and budget to hand-label roughly 5,000 of
them with one of 12 routing categories (illustrative numbers). The three paradigms use the same corpus
differently.

Supervised only: train a classifier on the 5,000 labelled tickets. It works, but categories with 40
examples are learned poorly, and the model knows nothing about the 1,995,000 unlabelled tickets.

Unsupervised: embed all 2,000,000 tickets with a pretrained sentence encoder and cluster them into 40
groups. Humans read the central examples of each cluster and discover three recurring problems that the
12-category taxonomy never had. Cluster ids are not routing labels, but they tell you what to label and
how to sample it so the 5,000 labels cover the real distribution.

Self-supervised: the sentence encoder used above was itself trained contrastively on hundreds of
millions of unlabelled text pairs, which is why its notion of "similar" is useful at all. If the tickets
average 60 tokens, continued pre-training on them with the next-token objective would create about
120,000,000 training targets from the same data, for free. Fine-tuning that adapted model on the 5,000
labels usually gives the best classifier of the three, because the supervised step only has to teach the
category boundaries, not the language of the domain.

The code shows the one operation that makes self-supervision different: the labels are produced by a
function of the input, not by a person.

```python
import random

supervised_example = ("card declined at checkout again", "billing")   # one (x, y) pair: paid for

def next_token_pairs(ids):
    """Autoregressive pretext task: every prefix is an input, the token after it is the label."""
    return [(ids[:i], ids[i]) for i in range(1, len(ids))]

def masked_pairs(ids, mask_id=0, p=0.15, seed=1):
    """Masked-LM pretext task: hide ~15% of tokens; the hidden originals are the labels."""
    rng = random.Random(seed)
    corrupted, targets = list(ids), {}
    for pos, tok in enumerate(ids):
        if rng.random() < p:
            corrupted[pos], targets[pos] = mask_id, tok
    return corrupted, targets

ids = [312, 7, 2201, 54, 887, 19]          # token ids for the same sentence (illustrative values)
print(len(next_token_pairs(ids)))          # 5 labelled examples from one unlabelled line
print(masked_pairs(ids))                   # ([0, 7, 2201, 54, 887, 19], {0: 312}) with seed=1; seed=0 happens to mask nothing
```

## Trade-offs
- Supervised learning gives the highest task accuracy per example and a directly measurable score, but
  every example costs human time and the model inherits the annotators' blind spots; the cost argument
  flips when labels fall out of the system for free (resolved-ticket categories, clicks, merged PRs).
- Unsupervised methods need no labels and surface structure nobody asked about, but produce no accuracy
  number and need a human to interpret the output; they win when the goal is exploration, not prediction.
- Self-supervised pre-training yields transferable representations from unlimited data, but needs large
  compute and the pretext task is only a proxy: a model excellent at next-token prediction is not
  automatically good at following instructions. For a narrow domain, fine-tuning a public checkpoint
  almost always beats pre-training your own.
- Combining paradigms (pre-train, cluster, label, fine-tune) gives the best result but multiplies the
  stages that must be evaluated, versioned and debugged.

## Pitfalls
- Treating cluster ids as labels and training a supervised model on them; the symptom is high
  "accuracy" against the pseudo-labels and poor agreement with human review, because the model learned
  the clustering's artefacts rather than the intended categories.
- Fine-tuning on a large narrow supervised set until the pre-trained knowledge is overwritten; the
  symptom is the task metric rising while general capability and out-of-distribution behaviour degrade.
- Assuming unlabelled data is neutral because nobody labelled it; pre-training text carries the biases
  and errors of its sources, and self-supervision reproduces them faithfully.
- Judging self-supervised pre-training by its own loss alone; the symptom is perplexity falling while
  the downstream task you care about does not move, because the proxy and the task diverged.
- Building a supervised label from a field that will not exist at inference time (the agent's final
  resolution note as a feature for routing); the symptom is near-perfect offline scores and a useless
  model in production. The train-test-leakage lesson covers this in depth.

## When to use
Start from the question "what signal do I have, and what does more of it cost?" If you have labels and a
narrow, stable target, supervised learning with a small model is usually the cheapest, fastest and most
measurable option. If you have a large unlabelled domain corpus, you almost never pre-train from
scratch; use a pre-trained model and either embed and cluster to understand the data, or label a
stratified few thousand examples and fine-tune. Measure supervised work with held-out accuracy at the
natural class distribution, unsupervised work by whether it changed a decision, and self-supervised work
by downstream task performance, never only by its own loss. In interviews, expect "why is next-token
prediction self-supervised rather than unsupervised?" The answer is that the loss is a normal supervised
loss against a target; the target just came from the data instead of a person.

## Role lens
- **Engineer:** Chooses the fine-tuning data format that matches the signal available, labelled pairs
  for a classifier or raw text for continued pre-training, and the loss that goes with it.
- **Architect:** Decides where labels come from at scale, annotation budget, implicit feedback capture or
  a pre-trained vendor model, because that choice fixes cost and iteration speed for years.
- **FDE:** Audits what the customer actually has, usually millions of unlabelled documents and a handful
  of experts, and designs the clustering pass and labelling session that make those experts efficient.

## Check yourself
**Q:** A customer wants 3 million internal documents sorted into their 40 categories, has no labels, and
says "just use unsupervised learning so we don't have to label anything". How do you respond, and what
pipeline do you propose?
**A:** Clustering alone cannot deliver their 40 categories. It groups by whatever similarity dominates the
embedding space, with no guarantee that matches their taxonomy, and it gives no way to measure accuracy
against the categories they care about. Propose a pipeline instead: embed all documents with a
pre-trained (self-supervised, contrastively trained) encoder; cluster to see the natural structure and
to sample diverse documents; have their experts label a stratified sample of a few thousand, measuring
inter-annotator agreement so the ceiling is known; train a supervised classifier on that sample and
evaluate on a held-out labelled set at the natural class distribution; and use the clusters afterwards
to find categories the taxonomy misses and to prioritise further labelling. The labels are what make the
categories theirs; the unsupervised step makes the labelling cheap.

## Quiz
### Q: Why is next-token prediction described as self-supervised rather than unsupervised?
- [ ] Because a human verifies each predicted token before it is used
- [x] Because it trains with a standard supervised loss on a target that is constructed from the data itself
- [ ] Because the model scores its own samples and trains on the best ones
- [ ] Because the tokens were labelled during tokenisation
> The objective is ordinary cross-entropy against a known correct token, exactly like supervised
> classification; the only difference from supervised learning is that the label came from the text
> rather than from an annotator. Classical unsupervised objectives such as k-means score a global
> property of the data instead of a per-example target.

### Q: A team receives about 200,000 labelled examples per day for free, because agents pick a final category when they close each ticket. Which paradigm fits a routing model best?
- [ ] Unsupervised, because labels produced by a system rather than annotators cannot be trusted
- [ ] Self-supervised, because the volume is large enough to pre-train a model from scratch
- [x] Supervised, because an abundant, cheap, task-aligned label stream removes the usual bottleneck
- [ ] Reinforcement learning, because the closing category acts as a reward
> Supervised learning is limited by label cost and coverage; here labels are free and directly
> aligned with the routing task. Agent labels can be noisy, but that is something to measure on a sample,
> not a reason to throw away the signal.

### Q: What makes contrastive training of an embedding model self-supervised?
- [ ] Annotators choose the negative examples for each anchor
- [ ] The loss contains no cross-entropy term, so no labels are involved
- [x] Positive pairs are produced automatically, so the target "which candidate is the match" is derived from the data
- [ ] The embeddings are evaluated without any labelled benchmark
> Two views of the same item, or a naturally paired query and document, give a known correct match; the
> model is trained to identify it among the other items in the batch. That target is a label, just one
> that cost nothing to create.
