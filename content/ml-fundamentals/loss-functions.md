---
id: loss-functions
module: ml-fundamentals
name: Loss functions (cross-entropy, contrastive)
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - ml-fundamentals/classification
  - ml-fundamentals/regression
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/backpropagation
  - model-internals/softmax
  - model-internals/perplexity
  - rag-retrieval/embedding-models
  - multimodal/clip
sources:
  - Goodfellow, Bengio & Courville, 2016 — Deep Learning
  - van den Oord et al., 2018 — Representation Learning with Contrastive Predictive Coding
  - Chen et al., 2020 — A Simple Framework for Contrastive Learning of Visual Representations
  - Radford et al., 2021 — Learning Transferable Visual Models From Natural Language Supervision
---

## TL;DR
A loss function is the differentiable stand-in for what you actually want; its gradient is the only
signal the model ever receives. Cross-entropy, −log p(correct answer), is the loss for anything that
predicts a probability distribution, including every token an LLM emits, because it is maximum
likelihood and its gradient through softmax is simply prediction minus target. Contrastive losses teach
embeddings by asking "which candidate is the match", and the modern form, InfoNCE, is cross-entropy over
similarity scores.

## Why it exists
Gradient descent needs a scalar to minimise, and the thing you care about is almost never
differentiable. Accuracy, recall@10, user satisfaction and "did the agent complete the task" are
piecewise constant in the parameters: nudge a weight slightly and the metric does not move at all, so its
gradient is zero almost everywhere. A loss is a surrogate that must (1) be differentiable, (2) share its
minimiser with the true objective, or come close, and (3) give useful gradient magnitudes everywhere,
not just near the solution.

The third requirement is where naive choices fail. Suppose you train a classifier with squared error on
its output probability, p = σ(z). The gradient with respect to the logit is (p − y)·p·(1 − p). When the
model is confidently wrong, p is near 0 for the true class, the factor p(1 − p) is near 0, and the
gradient vanishes exactly when the correction should be largest. The model is stuck precisely because it
is wrong. Cross-entropy removes that factor, which is the mathematical reason it replaced squared error
for classification.

Embeddings pose a different problem: there is no label to compare against, only relations. You know
that a query and the document that answered it belong together and that most other documents do not.
A loss defined on single outputs cannot express that; one defined on relative distances can. That is what
contrastive losses are for, and the fact that they need no human labels is why most embedding models are
trained with them.

## How it works
### Cross-entropy from maximum likelihood
The model outputs a distribution q over K classes; the data gives the true class y. The likelihood of a
dataset is Π q(y_i). Maximising its log is minimising −Σ log q(y_i), and −log q(y) is the cross-entropy
H(p, q) = −Σ_k p_k log q_k with p the one-hot target. Since H(p, q) = H(p) + KL(p ‖ q) and H(p) does not
depend on the model, minimising cross-entropy is minimising the KL divergence from the data distribution
to the model's. It is not an arbitrary choice; it is the loss that makes "probability" mean something.

The gradient is the useful part. With q = softmax(z), log q_y = z_y − log Σ_j exp(z_j), and
differentiating with respect to z_k gives ∂L/∂z_k = q_k − 1[k = y]. The gradient on each logit is the
model's probability minus the target: bounded by 1, proportional to how wrong the model is, never
vanishing while it is wrong, and near zero once it is confidently right. Binary cross-entropy with a
sigmoid has the same form, ∂L/∂z = σ(z) − y. This is why softmax plus cross-entropy trains stably and why
every framework fuses them into one numerically careful operation: compute log-softmax with the
max-subtraction trick, never log(softmax(z)) as two steps, or tiny probabilities underflow to log(0).

Variants adjust what the target means. Label smoothing replaces the one-hot target with
(1 − ε)·onehot + ε/K, which stops logits growing without bound and often improves calibration at the
cost of never predicting with full confidence. Class weights scale each example's loss to counter
imbalance. For LLMs, the training loss is cross-entropy averaged over token positions, and perplexity is
exp of that average: a loss of 2.0 nats means perplexity ≈ 7.4, as uncertain as choosing uniformly among
about seven tokens. In supervised fine-tuning the loss is usually masked so only assistant tokens count,
because learning to predict the prompt teaches nothing you want.

### Regression losses in one paragraph
Squared error is cross-entropy's cousin: it is maximum likelihood under Gaussian noise and its minimiser
is the conditional mean. Absolute error gives the median and resists outliers; Huber blends the two. The
regression lesson covers when each applies.

### Contrastive losses
The setting: an encoder f maps items to vectors, and you have pairs that should match (anchor, positive)
plus items that should not (negatives). The goal is a geometry where sim(anchor, positive) exceeds
sim(anchor, negative) for every negative.

- **Pairwise contrastive** (the original form): y·d² + (1 − y)·max(0, m − d)² for a pair at distance d.
  Matching pairs are pulled together; non-matching pairs are pushed apart only until they are a margin m
  away, because pushing already-distant negatives further is wasted effort.
- **Triplet**: max(0, d(a, p) − d(a, n) + m). Needs hard-negative mining, since random triplets are
  mostly already satisfied and contribute zero gradient.
- **InfoNCE / NT-Xent** (the modern default): for anchor a with positive p and a set of candidates x_j,
  L = −log [ exp(s(a, p)/τ) / Σ_j exp(s(a, x_j)/τ) ]. Read it carefully: it is a softmax over
  similarities followed by −log of the positive's share. That is cross-entropy for the classification
  task "which of these candidates is the match", with the label known because you built the pair.
  Contrastive learning is classification with a free label, which is why it counts as self-supervised.

Three design choices follow from that reading. **In-batch negatives**: a batch of N pairs gives an N×N
similarity matrix whose diagonal holds the positives and whose off-diagonal entries are N(N − 1)
negatives for free; larger batches make the matching task harder and the gradient more informative,
which is why contrastive recipes such as SimCLR and CLIP use very large batches. CLIP makes the loss
symmetric, averaging image-to-text and text-to-image cross-entropy over the same matrix. **Temperature
τ** divides the similarities before the softmax; a small τ sharpens the distribution so gradient
concentrates on the hardest negatives, while too small a τ makes training unstable and oversensitive to
mislabelled negatives (CLIP learns τ, starting from 0.07). **Hard negatives**: in-batch negatives are
mostly easy, so retrieval embeddings improve when you add mined negatives, such as documents BM25 ranks
highly that are not the answer; the risk is false negatives, unlabelled true matches treated as
impostors, which put a floor under the loss. Similarities are usually cosine on normalised vectors
because unbounded dot products admit the trivial solution of making every vector longer.

### Matching the loss to the output
Distribution over discrete options: cross-entropy. A quantity: squared, absolute or Huber. Relations
between items: contrastive or ranking losses. Preferences between two responses: a pairwise logistic
loss on the difference of scores, which is the Bradley–Terry form underlying reward models and DPO.
Multiple losses are summed with weights that are hyperparameters in their own right.

## Example
Two things in one script: cross-entropy's gradient really is prediction minus target, and InfoNCE really
is cross-entropy over a similarity matrix.

```python
import torch, torch.nn.functional as F

# 1) Cross-entropy on logits and its gradient (softmax(z) - onehot(y))
z = torch.tensor([[2.0, 0.5, -1.0]], requires_grad=True)   # one example, three classes
y = torch.tensor([0])
loss = F.cross_entropy(z, y)                               # takes logits; applies log-softmax inside
loss.backward()
print(round(loss.item(), 4))                               # 0.2413  (softmax = [0.786, 0.175, 0.039])
print(z.grad)                                              # [[-0.2144, 0.1753, 0.0391]]
print(z.softmax(-1).detach() - F.one_hot(y, 3))            # identical: the gradient is the error

# 2) InfoNCE, CLIP-style symmetric version, over a batch of (anchor, positive) embeddings
def info_nce(a, b, tau=0.07):
    a, b = F.normalize(a, dim=-1), F.normalize(b, dim=-1)  # cosine similarity
    sim = a @ b.T / tau                                    # [N, N]; sim[i, i] is the true pair
    target = torch.arange(a.size(0))                       # "row i's correct class is column i"
    return (F.cross_entropy(sim, target) + F.cross_entropy(sim.T, target)) / 2

N, d = 256, 64
q = torch.randn(N, d); pos = q + 0.3 * torch.randn(N, d)   # positives: noisy copies of the anchors
print(round(info_nce(q, pos).item(), 3))                   # near 0: each anchor finds its own positive
print(round(info_nce(q, torch.randn(N, d)).item(), 3))     # ~7 here, above log(256) ≈ 5.5: no better than chance
```

## Trade-offs
- Cross-entropy gives non-vanishing gradient while the model is wrong and proper likelihood semantics,
  but it rewards ever-larger logits and drives models toward overconfidence; label smoothing trades peak
  confidence for calibration, and the trade is worth it unless you need sharp distributions, for example
  as distillation targets.
- InfoNCE with in-batch negatives supplies negatives for free and scales well, but embedding quality is
  coupled to batch size and therefore GPU memory; when the batch cannot be large, mined hard negatives
  recover most of the benefit at the cost of a mining pipeline.
- A small temperature focuses learning on the hardest negatives and sharpens retrieval, but amplifies
  the damage from false negatives and can destabilise early training; a learned temperature is the usual
  compromise.
- Any surrogate can decouple from the real metric: the loss keeps falling while recall@10 or task
  accuracy stalls, so both must be tracked, and the metric decides.

## Pitfalls
- Applying softmax before a loss that applies log-softmax internally, so probabilities get
  softmaxed twice; the symptom is a loss that floors well above zero and a model that plateaus early.
- Computing log(softmax(z)) as two separate operations; the symptom is NaN losses after a few steps
  when some probability underflows to zero.
- Forgetting the loss mask in supervised fine-tuning, so prompt and padding tokens count; the symptom is
  a loss that drops fast while outputs do not improve, because predicting the prompt is easy and useless.
- Averaging cross-entropy per token versus per sequence without deciding which you want; the symptom is
  long documents dominating the gradient, or short ones, depending on the accident.
- Duplicates inside a contrastive batch treated as negatives; the symptom is a loss that cannot fall
  below a floor and embeddings that stop improving, fixed by deduplicating before batching.
- Comparing cross-entropy values across models with different tokenisers or vocabularies; the symptom
  is "our loss is lower" claims that mean nothing, because the loss depends on the vocabulary.

## When to use
Choosing a loss is choosing what the model estimates. Use cross-entropy whenever the output is a
distribution over discrete options: classification, next-token prediction, routing. Use a contrastive
loss when you want geometry: retrieval embeddings, deduplication, the space a clustering will run in,
cross-modal alignment. Use regression losses for quantities. Measure the loss curve and the task metric
together; for contrastive training, evaluate recall@k on a held-out retrieval set, because the loss value
alone says little. Day to day, debugging a fine-tune that is not learning starts at the loss: masks,
double softmax, scale. In interviews expect to derive the cross-entropy gradient, explain why InfoNCE is
cross-entropy, say why batch size matters for contrastive learning, and relate perplexity to loss.

## Role lens
- **Engineer:** Owns the training loop details: logits rather than probabilities into cross-entropy,
  masks that cover prompt and padding, deduplicated contrastive batches and a sane temperature.
- **Architect:** Decides what objective each learned component optimises, a contrastively trained
  retrieval embedding versus a classifier versus a scalar reward, because the objective fixes what
  "similar" or "confident" will mean across the platform.
- **FDE:** When fine-tuning an embedding model on a customer's data, assembles (query, relevant document)
  pairs and hard negatives from their search logs, and explains why forty positive pairs are not enough.

## Check yourself
**Q:** A supervised fine-tuning run's cross-entropy falls from 2.1 to 0.3 within one epoch, but held-out
task accuracy does not move. List the causes you would check, in order, and the evidence for each.
**A:** First, the loss is computed on the wrong tokens: if prompt or padding tokens are included, the
model learns to predict easy text and the loss collapses; check the label mask and the ignore index.
Second, the number is a training loss; look at held-out cross-entropy, which will be flat if the model
is memorising a small dataset. Third, label alignment: an off-by-one in the next-token shift or a
double softmax produces a loss that falls quickly and means nothing. Fourth, the model learned the
format but not the content, which shows as outputs with the right template and wrong answers. Fifth,
evaluation decoding differs from training: sampling settings, stop tokens or a system prompt that was
absent from the training data. Only after these would I suspect the data itself.

## Quiz
### Q: With softmax output q and true class y, what is the gradient of cross-entropy with respect to logit k?
- [ ] q_k·(1 − q_k)
- [x] q_k − 1[k = y]
- [ ] −1 / q_k
- [ ] log q_k − log q_y
> Differentiating z_y − log Σ exp(z_j) with respect to z_k gives 1[k = y] − q_k, so the loss gradient is
> q_k − 1[k = y]: the predicted probability minus the target. The q(1 − q) factor belongs to squared error
> through a sigmoid, which is exactly the term that makes it vanish when confidently wrong.

### Q: Why is InfoNCE described as a cross-entropy loss?
- [ ] It adds the entropy of the embedding distribution as a regulariser
- [x] It applies a softmax over similarity scores to a set of candidates and takes −log of the positive's share, which is cross-entropy for "which candidate is the match"
- [ ] It minimises the KL divergence between the anchor's and the positive's output distributions
- [ ] It requires one-hot labels supplied by annotators for each pair
> The denominator sums exp(similarity/τ) over all candidates and the numerator is the positive's term,
> so the loss is −log softmax at the correct index. The label is the index of the constructed positive,
> which is why no annotation is needed.

### Q: Why do contrastive training recipes favour very large batches?
- [ ] Larger batches reduce the memory needed per optimisation step
- [x] In-batch negatives grow with batch size, so the matching task gets harder and the gradient more informative
- [ ] Cross-entropy is only defined for at least 1,024 classes
- [ ] The temperature parameter only works correctly with large batches
> Each anchor is contrasted against every other item in the batch, so N pairs provide N(N − 1) negatives
> at no extra labelling cost. With few negatives the task is trivially easy and the embedding learns
> little; this coupling to batch size is the main practical constraint of InfoNCE.
