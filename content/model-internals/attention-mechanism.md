---
id: attention-mechanism
module: model-internals
name: Attention mechanism
kind: concept
difficulty: intro
est_minutes: 12
status: fact-checked
related:
  - model-internals/self-attention
  - model-internals/multi-head-attention
  - model-internals/transformer
  - model-internals/softmax
  - model-internals/embeddings
  - rag-retrieval/similarity-search
sources:
  - Bahdanau et al., 2014 — Neural Machine Translation by Jointly Learning to Align and Translate
  - Luong et al., 2015 — Effective Approaches to Attention-based Neural Machine Translation
  - Vaswani et al., 2017 — Attention Is All You Need
---

## TL;DR
Attention is a differentiable lookup. A query is compared against a set of keys, the similarity scores
are turned into weights that sum to one with a softmax, and the output is the weighted sum of the
matching values. It exists because squeezing a variable-length input into one fixed-size vector throws
information away; attention lets each step of a computation reach back and pull exactly the parts of
the input it needs, with the notion of "matching" learned end to end.

## Why it exists
The 2014 sequence-to-sequence translators read the source sentence with an encoder RNN into a single
final hidden state and generated the target from that vector. It worked for short sentences and
degraded sharply with length, for an obvious reason once stated: a fifty-word sentence had to pass
through the same few hundred numbers as a five-word one, and the decoder needed different parts of it at
different steps.

The naive fix, a bigger vector, does not help much. What the decoder needs at output step *t* depends on
*t*, so a static summary written before decoding starts must anticipate every future need at once. The
required capacity grows with the number of (input position, output step) combinations, not with any
fixed width.

Bahdanau et al. kept every encoder hidden state and let the decoder, at each step, score its current
state against each of them, normalize the scores, and build a context vector as the weighted sum. The
weights, an alignment between output and input words, were learned with no alignment supervision, and
translation quality on long sentences stopped collapsing. The general move is to replace "summarize
once, then read the summary" with "look up what you need, when you need it", and to make the lookup soft
so that gradients flow through it. That single move is the ancestor of self-attention, cross-attention
and, in hard top-k form, of vector retrieval.

## How it works
### Three roles
Every attention computation has a **query** (what am I looking for), a set of **keys** (what each item
advertises about itself) and a set of **values** (what each item hands over if selected). Keys and values
are separate because the thing you match on need not be the thing you retrieve, the same way a database
index differs from the row it points to. In neural attention all three are usually produced by learned
linear projections `W_Q`, `W_K`, `W_V` of some hidden states, so the network learns what to match on and
what to pass along.

### Score, normalize, mix
For one query **q** and keys **k₁…kₙ**:

```
score_i = f(q, k_i)
α       = softmax(score)              # α_i ≥ 0, Σ α_i = 1
output  = Σ_i α_i · v_i
```

The choice of *f* is the main design decision:
- **Additive** (Bahdanau): `vᵀ·tanh(W₁q + W₂k)`, a tiny MLP per (query, key) pair. Expressive, but it
  cannot be written as one matrix multiplication over all keys.
- **Dot-product** (Luong): `qᵀk`. All scores for a query come from a single matrix–vector product, and
  all queries at once from a matrix–matrix product, which is exactly what GPUs do fastest.
- **Scaled dot-product** (Vaswani): `qᵀk / √d_k`. If the components of **q** and **k** are roughly
  independent with zero mean and unit variance, `qᵀk` has variance d_k, so with d_k = 64 the scores have
  standard deviation 8. Softmax of numbers spread that widely is nearly one-hot, its gradient is close to
  zero almost everywhere, and learning stalls. Dividing by √d_k puts the scores back at unit variance.
  The original paper reports additive and dot-product attention performing similarly at small d_k and
  unscaled dot-product falling behind at larger d_k, which is what motivated the scaling.

Softmax is used rather than, say, dividing by the sum of raw scores, because it guarantees positive
weights that sum to one (so the output is a convex combination of values whose scale does not depend on
how many keys there are), it is smooth everywhere, and its exponential sharpens contrasts so the model can
approximate a hard selection while remaining trainable. Hard attention, which samples one key, needs
reinforcement-style gradient estimators and has largely been abandoned for language.

### Where the vectors come from decides the flavour
- **Cross-attention:** queries from one sequence, keys and values from another. The decoder-to-encoder
  attention in translation; also how vision-language models inject image features and how text
  conditions a diffusion model.
- **Self-attention:** queries, keys and values all from the same sequence, so each token builds its
  representation from its neighbours. This is the transformer's core and has its own lesson.

### Attention as a soft dictionary
A Python `dict` looks up one exact key and returns one value. Attention looks up every key a little and
returns a blend, which is what makes it differentiable. Read this way, a vector database performing
top-k dot-product search over stored embeddings is the hard, external, disk-backed version of the same
operation, and retrieval-augmented generation is attention over documents the model's weights never saw.

### Cost
One query against *n* keys costs O(n·d) for scores plus O(n·d) for the weighted sum. With *n* queries,
as in self-attention, that becomes O(n²·d), and the n×n score matrix is the quantity that drives every
later discussion of context length, memory and kernels.

## Example
A worked instance in NumPy, first with two-dimensional vectors you can check by hand, then a
demonstration of why the √d_k scaling matters.

```python
import numpy as np
np.random.seed(0)

def attention(q, K, V, scale=True):
    scores = K @ q                                     # one dot product per key
    if scale:
        scores = scores / np.sqrt(q.shape[-1])
    w = np.exp(scores - scores.max()); w /= w.sum()    # numerically stable softmax
    return w, w @ V

# Three memories; the query points along the first key, half-way toward the third.
K = np.array([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]])
V = np.array([[10.0, 0.0], [0.0, 10.0], [5.0, 5.0]])
q = np.array([1.0, 0.0])
w, out = attention(q, K, V, scale=False)
print(np.round(w, 3), np.round(out, 2))
# scores 1.0, 0.0, 0.7 -> weights [0.474 0.174 0.351] -> output [6.5 3.5]
# mostly the first value, a third of the third, a little of the second: a blend, never a pick.

# With d_k = 64 and unit-variance vectors, raw scores have std ~8 and softmax is nearly saturated.
d, n = 64, 16
K, q, V = np.random.randn(n, d), np.random.randn(d), np.eye(n)
for s in (False, True):
    w, _ = attention(q, K, V, scale=s)
    entropy = -(w * np.log(w + 1e-12)).sum()
    print("scaled" if s else "raw   ", f"max weight {w.max():.3f}  entropy {entropy:.2f} (uniform = {np.log(n):.2f})")
# raw    max weight 0.778  entropy 0.65 (uniform = 2.77)  <- almost all mass on 2-3 keys; near one-hot, gradients vanish
# scaled max weight 0.176  entropy 2.53 (uniform = 2.77)  <- a real distribution the model can learn to sharpen
# (exact values depend on the seed; the ratio of entropies is the point)
```

## Trade-offs
- Content-based lookup over the entire input, paid for with cost linear in the number of keys per
  query; once every position is a query the cost is quadratic, and past tens of thousands of positions
  you move work to sparse attention or external retrieval.
- Soft weights are differentiable and train end to end, but the output is always a blend; when several
  keys match, the result is blurred, so tasks that need exact provenance (citations) use hard top-k
  retrieval in front of the model.
- Learned projections let the model decide what "similar" means, at the price of parameters and of
  attention maps that look like alignments but are not reliable explanations.
- Additive scoring is slightly more expressive; dot-product scoring maps onto a single matmul and
  therefore onto tensor cores, which is why it won once d_k was scaled.

## Pitfalls
- Reading attention weights as feature importance; the symptom is explanations that change with the
  random seed or disagree with gradient-based attribution for the same prediction.
- Omitting the √d_k scaling in a from-scratch implementation; the symptom is attention maps that are
  nearly one-hot from the first steps and a training loss that plateaus early.
- Applying softmax along the wrong axis so weights sum to one across queries rather than across keys;
  the symptom is outputs whose magnitude changes with sequence length.
- Forgetting to mask padding positions; the symptom is the same input producing different outputs
  depending on how much padding its batch-mates needed.

## When to use
You rarely instantiate attention as a standalone primitive, but you meet the pattern constantly: in
every transformer layer, in cross-attention adapters that fuse modalities, and in the retrieval layer of
a RAG system, which is attention done externally with hard top-k. The decisions that follow from it are
practical: how to mask padding and future positions, whether a task needs the model to attend over a
long input or a retriever to select a short one, and how to budget the n² cost. In interviews the
expected baseline is to explain query, key and value with an example, say why softmax and why √d_k, and
distinguish self- from cross-attention; a strong answer connects the soft-dictionary view to vector
search and says when each is the right tool.

## Role lens
- **Engineer:** Builds and debugs attention masks for padding and causality, and recognizes the
  wrong-axis and missing-scale bugs from their symptoms in training curves.
- **Architect:** Decides where lookup happens: inside the model's context window at quadratic cost, or
  in a retrieval layer that pre-selects what enters the window.
- **FDE:** Uses the dictionary analogy to explain to customers why "the model attends to your whole
  document" is a blend, not a citation, and why grounding needs a retrieval step.

## Check yourself
**Q:** Explain why scaled dot-product attention divides scores by √d_k, and what you would observe in
training if a from-scratch implementation forgot to do it.
**A:** A dot product of two d_k-dimensional vectors with independent, zero-mean, unit-variance components
has variance d_k, so its standard deviation grows with √d_k; at d_k = 64 raw scores spread with standard
deviation around 8. Softmax over values that far apart is almost one-hot, and in that regime its gradient
with respect to the inputs is near zero, so the weights stop moving and the layers below receive almost
no signal. Dividing by √d_k restores unit variance regardless of head size. Without it you would see
attention distributions that are sharply peaked from initialization, a loss that drops briefly then
plateaus, and performance that gets worse, not better, as you increase the head dimension.

## Quiz
### Q: What does the softmax step provide that raw similarity scores do not?
- [ ] It makes attention run in linear time over the keys
- [x] Positive weights that sum to one, so the output is a convex combination independent of how many keys exist
- [ ] It removes the need for separate key and value projections
- [ ] It enforces that each position can only see earlier positions
> Softmax normalizes scores into a distribution, keeping the output's scale stable and the operation
> smooth and trainable. Causality comes from a mask, not from softmax, and the cost per query is
> unchanged by it.

### Q: In the cross-attention of an encoder–decoder translator, where do queries, keys and values come from?
- [ ] All three from the encoder outputs
- [ ] Queries from the encoder, keys and values from the decoder
- [x] Queries from the decoder state, keys and values from the encoder outputs
- [ ] All three from the decoder state
> The decoder asks, at each output step, which source positions are relevant; the source positions
> advertise themselves as keys and hand over their content as values. All-from-the-same-sequence would be
> self-attention.

### Q: Which problem did attention originally solve in neural machine translation?
- [ ] The quadratic cost of processing long sentences
- [x] The fixed-size context vector that forced the whole source sentence through one bottleneck
- [ ] The lack of positional information in the encoder
- [ ] The vanishing gradient inside a single LSTM cell
> Bahdanau et al. let the decoder look back at all encoder states instead of one summary vector, which
> fixed the collapse in quality on long sentences. Quadratic cost is a property attention introduced,
> not one it solved.
