---
id: loss-functions
module: ml-fundamentals
name: Loss functions (cross-entropy, contrastive)
kind: concept
difficulty: core
est_minutes: 16
status: crisp
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
A loss is the differentiable stand-in for what you want; its gradient is the model's only signal.
Cross-entropy, −log p(correct answer), trains anything that predicts a distribution, including every LLM
token. Contrastive losses (InfoNCE) are cross-entropy over similarity scores.

## Why it exists
- **The real objective is not differentiable.** Accuracy, recall@10, user satisfaction and "did the agent
  complete the task" are piecewise constant in the parameters: nudge a weight and the metric does not
  move, so its gradient is zero almost everywhere. Gradient descent needs a scalar to minimise.
- **A surrogate must meet three requirements:** (1) be differentiable, (2) share its minimiser with the
  true objective, or come close, and (3) give useful gradient magnitudes everywhere, not just near the
  solution.
- **Squared error fails requirement 3 for classes.** With p = σ(z), its logit gradient is
  (p − y)·p·(1 − p). When confidently wrong, p is near 0 for the true class, p(1 − p) is near 0, and the
  gradient vanishes exactly when the correction should be largest. Cross-entropy removes that factor,
  which is why it replaced squared error for classification.
- **In numbers,** for a true label y = 1 (squared error as ½(p − y)²): at p = 0.5 the logit gradient is
  −0.125 under squared error and −0.5 under cross-entropy; at p = 0.01, confidently wrong, about −0.0098
  versus −0.99. The more wrong the model, the more the two diverge.
- **Embeddings need relations, not labels.** You know a query and the document that answered it belong
  together and most others do not. A loss on single outputs cannot express that; one on relative distances
  can. That is what contrastive losses are for, and needing no human labels is why most embedding models
  use them.

## How it works
### Cross-entropy from maximum likelihood
1. **Likelihood.** The model outputs a distribution q over K classes; the data gives the true class y. The
   likelihood of a dataset is Π q(y_i).
2. **Take the log and negate.** Maximising it is minimising −Σ log q(y_i), and −log q(y) is the
   cross-entropy H(p, q) = −Σ_k p_k log q_k with p the one-hot target.
3. **It is a KL divergence in disguise.** H(p, q) = H(p) + KL(p ‖ q), and H(p) does not depend on the
   model, so minimising cross-entropy is minimising KL(p_data ‖ q). It is not an arbitrary choice; it is
   the loss that makes "probability" mean something.

### The gradient is prediction minus target
```
q = softmax(z),  log q_y = z_y − log Σ_j exp(z_j)
∂L/∂z_k = q_k − 1[k = y]                 (softmax + cross-entropy)
∂L/∂z   = σ(z) − y                       (sigmoid + binary cross-entropy)
```

- **Bounded by 1, proportional to how wrong the model is,** never vanishing while it is wrong, and near
  zero once it is confidently right.
- **Why frameworks fuse softmax and cross-entropy** into one numerically careful operation: compute
  log-softmax with the max-subtraction trick, never log(softmax(z)) as two steps, or tiny probabilities
  underflow to log(0).

### Variants and LLM specifics
- **Label smoothing** replaces the one-hot target with (1 − ε)·onehot + ε/K. It stops logits growing
  without bound and often improves calibration, at the cost of never predicting with full confidence.
- **Class weights** scale each example's loss to counter imbalance.
- **LLM training loss** is cross-entropy averaged over token positions. **Perplexity** is exp of that
  average: a loss of 2.0 nats means perplexity ≈ 7.4, as uncertain as choosing uniformly among about
  seven tokens.
- **SFT loss masking:** in supervised fine-tuning the loss is usually masked so only assistant tokens
  count, because learning to predict the prompt teaches nothing you want.

### Regression losses in brief
- **Squared error** is cross-entropy's cousin: maximum likelihood under Gaussian noise; its minimiser is
  the conditional mean.
- **Absolute error** gives the median and resists outliers; **Huber** blends the two. The regression
  lesson covers when each applies.

### Contrastive losses
Setting: an encoder f maps items to vectors; you have pairs that should match (anchor, positive) plus
items that should not (negatives). The goal is a geometry where sim(anchor, positive) exceeds
sim(anchor, negative) for every negative.

| Loss | Formula | Behaviour |
|---|---|---|
| **Pairwise contrastive** (the original form) | y·d² + (1 − y)·max(0, m − d)² for a pair at distance d | pulls matches together; pushes non-matches apart only until a margin m, because pushing already-distant negatives further is wasted effort |
| **Triplet** | max(0, d(a, p) − d(a, n) + m) | needs hard-negative mining, since random triplets are mostly already satisfied and contribute zero gradient |
| **InfoNCE / NT-Xent** (the modern default) | L = −log [ exp(s(a, p)/τ) / Σ_j exp(s(a, x_j)/τ) ] | softmax over similarities, then −log of the positive's share |

**Read InfoNCE carefully.** It is cross-entropy for the classification task "which of these candidates is
the match", with the label known because you built the pair. Contrastive learning is classification with
a free label, which is why it counts as self-supervised.

### Three design choices that follow
- **In-batch negatives.** A batch of N pairs gives an N×N similarity matrix: the diagonal holds the
  positives and the off-diagonal entries are N(N − 1) negatives for free. Larger batches make the matching
  task harder and the gradient more informative, which is why SimCLR and CLIP use very large batches. CLIP
  makes the loss symmetric, averaging image-to-text and text-to-image cross-entropy over the same matrix.
- **Temperature τ** divides the similarities before the softmax. A small τ sharpens the distribution so
  gradient concentrates on the hardest negatives; too small makes training unstable and oversensitive to
  mislabelled negatives (CLIP learns τ, starting from 0.07).
- **Hard negatives.** In-batch negatives are mostly easy, so retrieval embeddings improve with mined
  negatives, such as documents BM25 ranks highly that are not the answer. The risk is false negatives,
  unlabelled true matches treated as impostors, which put a floor under the loss.
- **Cosine on normalised vectors** is the usual similarity, because unbounded dot products admit the
  trivial solution of making every vector longer.

### Matching the loss to the output
| Output | Loss |
|---|---|
| Distribution over discrete options | cross-entropy |
| A quantity | squared, absolute or Huber |
| Relations between items | contrastive or ranking losses |
| Preferences between two responses | pairwise logistic loss on the difference of scores: the Bradley–Terry form underlying reward models and DPO |

Multiple losses are summed with weights that are hyperparameters in their own right.

## Diagram
Both main losses end in the same place: a softmax over scores, then −log of the correct entry.

```mermaid
flowchart TD
    O{"What does the model output?"} -->|"class or next token"| LG["Logits z"]
    O -->|"embeddings"| EM["Normalised vectors"]
    EM --> SIM["Similarity matrix divided by tau"]
    LG --> SM["Log-softmax, fused"]
    SIM --> SM
    SM --> CE["Minus log of correct entry"]
    T["Target: true class or own pair"] --> CE
    CE --> G["Gradient: prediction minus target"]
    G --> U["Optimizer step"]
```

## Examples
### Cross-entropy gradient and InfoNCE in one script
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

### Worked numbers: why squared error stalls when confidently wrong
Logit gradient for a true label y = 1 as the predicted probability falls (pure Python below reproduces it).

| Predicted p | Squared error gradient | Cross-entropy gradient | Ratio |
|---|---|---|---|
| 0.9 | −0.009 | −0.1 | about 11× |
| 0.5 | −0.125 | −0.5 | 4× |
| 0.1 | −0.081 | −0.9 | about 11× |
| 0.01 | −0.0098 | −0.99 | about 100× |

```python
def logit_grads(p, y=1):
    se = (p - y) * p * (1 - p)        # squared error 0.5*(p - y)^2 through a sigmoid
    ce = p - y                        # cross-entropy through a sigmoid
    return round(se, 4), round(ce, 4)

for p in (0.9, 0.5, 0.1, 0.01):
    print(p, logit_grads(p))          # (-0.009, -0.1) ... (-0.0098, -0.99)
```

Reading the table: squared error's gradient peaks near p = 0.5 and shrinks again as the model gets more
wrong, so a confidently wrong example barely moves the weights. Cross-entropy's gradient grows
monotonically with the error.

## Production patterns
- **Pass logits, never probabilities, to the loss.** Use the fused op (PyTorch `F.cross_entropy` or
  `nn.BCEWithLogitsLoss`) and keep softmax out of the model's forward pass during training. Monitor for a
  loss that floors well above zero, the signature of a double softmax.
- **Mask non-target tokens in SFT.** Set label ids for prompt and padding positions to `-100`, the
  default `ignore_index` of PyTorch cross-entropy, so they contribute nothing. Monitor the count of
  supervised tokens per batch and log it with the loss.
- **Track loss and task metric together.** Log held-out cross-entropy (or perplexity) and the task metric
  on the same eval schedule; when the loss keeps falling and the metric stalls, stop and inspect outputs.
- **Contrastive fine-tuning with mined hard negatives.** Build (query, positive) pairs from search logs,
  add BM25-ranked non-answers as negatives, and deduplicate so no true match lands in the negative set.
  Monitor recall@k on a held-out retrieval set rather than the loss value.
- **Scale in-batch negatives within memory.** Use the largest batch that fits for InfoNCE; when it cannot
  grow, add mined negatives instead. Monitor recall@k as batch size changes.
- **Label smoothing for calibration.** Enable it via the `label_smoothing` argument of PyTorch
  `F.cross_entropy` (for example 0.1) for classifiers whose probabilities feed thresholds. Monitor
  expected calibration error; skip it for distillation teachers that need sharp distributions.

## Trade-offs
- **Cross-entropy: strong gradients vs overconfidence.** Non-vanishing gradient while wrong and proper
  likelihood semantics, but it rewards ever-larger logits and drives models toward overconfidence. Label
  smoothing trades peak confidence for calibration, worth it unless you need sharp distributions, for
  example as distillation targets.
- **In-batch negatives: free vs memory-bound.** InfoNCE with in-batch negatives supplies negatives for free
  and scales well, but embedding quality is coupled to batch size and therefore GPU memory. When the batch
  cannot be large, mined hard negatives recover most of the benefit at the cost of a mining pipeline.
- **Small temperature: sharp retrieval vs fragility.** It focuses learning on the hardest negatives but
  amplifies the damage from false negatives and can destabilise early training; a learned temperature is
  the usual compromise.
- **Surrogate vs real metric.** Any surrogate can decouple: the loss keeps falling while recall@10 or task
  accuracy stalls, so track both, and let the metric decide.

## Pitfalls
- **Softmax before a loss that applies log-softmax internally.** Probabilities get softmaxed twice; the
  loss floors well above zero and the model plateaus early.
- **log(softmax(z)) as two separate operations.** Symptom: NaN losses after a few steps when some
  probability underflows to zero.
- **Forgetting the loss mask in supervised fine-tuning.** Prompt and padding tokens count; the loss drops
  fast while outputs do not improve, because predicting the prompt is easy and useless.
- **Per-token versus per-sequence averaging left to accident.** Symptom: long documents dominating the
  gradient, or short ones, depending on the accident. Decide which you want.
- **Duplicates inside a contrastive batch treated as negatives.** Symptom: a loss that cannot fall below a
  floor and embeddings that stop improving; fix by deduplicating before batching.
- **Comparing cross-entropy across tokenisers or vocabularies.** Symptom: "our loss is lower" claims that
  mean nothing, because the loss depends on the vocabulary.

## When to use
Choosing a loss is choosing what the model estimates.
- **Cross-entropy** whenever the output is a distribution over discrete options: classification,
  next-token prediction, routing.
- **Contrastive** when you want geometry: retrieval embeddings, deduplication, the space a clustering will
  run in, cross-modal alignment. **Regression losses** for quantities.
- **Measure** the loss curve and the task metric together; for contrastive training, evaluate recall@k on
  a held-out retrieval set, because the loss value alone says little. Debugging a fine-tune that is not
  learning starts at the loss: masks, double softmax, scale.
- **Interviews:** derive the cross-entropy gradient, explain why InfoNCE is cross-entropy, say why batch
  size matters for contrastive learning, and relate perplexity to loss.

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

## Interview questions
### Why is cross-entropy the standard loss for classification and language modelling?
- **Maximum likelihood:** minimising −log q(y) maximises the data's likelihood and equals minimising
  KL(p_data ‖ q) up to a constant.
- **Good gradients:** through softmax the logit gradient is q − onehot(y), large while wrong and near zero
  once right.
- **Squared error on probabilities** carries a p(1 − p) factor that vanishes when the model is confidently
  wrong.

### Explain InfoNCE to someone who knows only classification.
- **It is classification:** for each anchor, the candidates are the classes, the scores are similarities
  divided by τ, and the correct class is the constructed positive.
- **In-batch:** a batch of N pairs gives N(N − 1) negatives from the similarity matrix's off-diagonal.
- **Free label:** because you built the pair, no annotation is needed, which makes it self-supervised.

### Your embedding fine-tune's loss stops falling at a floor and recall stalls. What do you check?
- **False negatives:** duplicates or near-duplicates in a batch, or mined "negatives" that are actually
  relevant, cap how low the loss can go.
- **Temperature:** too small amplifies those errors; try a learned τ.
- **Fix** by deduplicating before batching and filtering mined negatives, then re-measure recall@k.

### When would you not use label smoothing?
- **Use it** for classifiers whose probabilities feed thresholds, because it curbs overconfidence and often
  improves calibration.
- **Avoid it** when you need sharp distributions, such as a teacher whose outputs serve as distillation
  targets.
- **Measure** calibration error and accuracy with and without it on the same held-out set.

### An SFT loss of 0.3 nats: what perplexity is that, and does it mean the model is good?
- **Perplexity** is exp(0.3) ≈ 1.35; at the start of that run, exp(2.1) ≈ 8.2.
- **Not necessarily good:** if prompt tokens are unmasked, or the data is small and memorised, the training
  loss is low while held-out quality is flat.
- **Compare only within a tokeniser,** since the loss depends on the vocabulary.

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
