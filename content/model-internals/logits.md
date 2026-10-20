---
id: logits
module: model-internals
name: Logits
kind: concept
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - model-internals/softmax
  - model-internals/sampling-strategies
  - model-internals/embeddings
  - ml-fundamentals/loss-functions
  - ml-fundamentals/calibration
  - serving-inference/structured-constrained-generation
  - prompting/structured-outputs-json-mode
sources:
  - Goodfellow, Bengio & Courville, 2016 — Deep Learning
  - nostalgebraist, 2020 — interpreting GPT: the logit lens
---

## TL;DR
Logits are the raw, unnormalized scores a language model produces for every vocabulary entry at a
position, one real number per token, before softmax turns them into probabilities. They are the model's
last honest output: everything you can do to steer generation without retraining (temperature, logit
bias, banning tokens, grammar-constrained decoding) is an edit to this vector, and everything you can
measure about confidence (logprobs, perplexity) is read from it.

## Why it exists
A classifier over *V* classes needs a score per class, and a language model is a classifier over the
vocabulary at every position. The last layer is linear, and a linear map produces arbitrary reals, not
numbers that are non-negative and sum to one. You could clamp and rescale them, or train the outputs to
match one-hot targets with squared error, but both train badly: clamping kills gradients for every
negative score, and squared error on probabilities gives almost no gradient to a confidently wrong
prediction. The working design separates the jobs. The linear layer emits an unconstrained score per
token (the logit), softmax normalizes, and cross-entropy is computed straight from the scores in a
fused, numerically stable form. The payoff is the gradient: for target token *y*,
`∂L/∂z = softmax(z) − onehot(y)`, so every wrong token is pushed down in proportion to the probability it
stole and the right one is pushed up by its shortfall, with no saturation however wrong the model is.

Keeping scores and probabilities distinct is also what makes inference controllable. Probabilities are
locked to the simplex; scores are not. Add a constant to a token's logit and its probability rises
smoothly; set a logit to negative infinity and that token can never be sampled; divide all logits by a
temperature and the distribution sharpens or flattens without touching the model. Grammar-constrained
decoding, JSON mode, stop sequences, repetition penalties and the API's `logit_bias` parameter all work
by editing logits between the final layer and the sampler. The name is older than deep learning: a
logit is a log-odds, and in the multi-class case the logits are log-probabilities up to an additive
constant.

## How it works
### Where they come from
After the last transformer block, each position's hidden state `h` (a `d_model` vector) is multiplied
by the unembedding matrix `W_U` of shape `[V, d_model]`, giving `z = W_U · h`, a vector of *V* scores.
During training this is done for every position, because the loss is computed everywhere. During
decoding only the last position's logits are needed, since the next token depends on nothing else, and
engines compute just that row. The tensor an inference call returns is `[batch, seq_len, vocab]`; the one
you sample from is `logits[:, -1, :]`.

### Relationship to probabilities
`p_i = exp(z_i) / Σ_j exp(z_j)`, so `log p_i = z_i − logsumexp(z)`. Two consequences are used daily:
- **Shift invariance.** Adding the same constant to every logit leaves the probabilities unchanged, so
  the absolute value of a logit means nothing; only differences between logits matter. Implementations
  exploit this by subtracting the maximum before exponentiating to avoid overflow.
- **Logprobs.** What an API returns under `logprobs` is `log_softmax(z)` for the chosen token and a few
  alternatives. A logprob of −0.05 means the model put about 95% on that token; −2.3 means about 10%.
  Summing per-token logprobs gives the sequence log-likelihood; exponentiating the negative average gives
  perplexity.

### Editing logits
The sampler sees logits, not text, so steering means arithmetic:
- **Temperature** divides the whole vector by *T* before softmax.
- **Logit bias** adds a per-token offset *b*. Because `p_i / p_j = exp(z_i − z_j)`, a bias of *b*
  multiplies that token's odds against every other token by `e^b`: +1 is about 2.7×, +5 about 150×.
  With logits `[2.0, 1.0, 0.1]` the probabilities are `[0.66, 0.24, 0.10]`; a +1 bias on the third token
  gives `[0.56, 0.21, 0.23]`. OpenAI's API accepts values from −100 to 100; at −100 the factor `e^−100`
  effectively bans a token, and a large positive value effectively forces it.
- **Masking** sets disallowed tokens to −∞, giving them exactly zero probability. Constrained decoding
  builds this mask each step from a grammar or JSON schema, which is why structured output can be
  guaranteed rather than merely requested.
- **Repetition and frequency penalties** lower the logits of tokens already present in the context.

The order of these edits matters because they are not commutative; engines apply them in a fixed
pipeline, and the `sampling-strategies` topic covers the consequences.

### Numerical care and interpretability
Logits are produced in the model's compute dtype, but softmax and cross-entropy should be taken in
fp32, *because* `exp` of a half-precision score overflows easily and small probability differences
vanish in 16-bit. Some architectures bound the logits explicitly (Gemma 2 applies a `tanh` soft cap to
its final logits) to keep training stable. Finally, since `W_U` can be applied to *any* hidden state,
applying it (after the final normalization layer) to intermediate layers shows what the model would
predict if it stopped early; this "logit lens" is a standard interpretability tool and a reminder that logits are just a dot product
between a residual-stream vector and each token's output embedding.

## Example
The script pulls the next-token logits from GPT-2, shows their range and shape, converts them to
logprobs the way an API does, demonstrates shift invariance, and then does the two edits every serving
stack performs: banning a token and biasing another.

```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

tok = AutoTokenizer.from_pretrained("gpt2")
m = AutoModelForCausalLM.from_pretrained("gpt2").eval()
ids = tok("The capital of France is", return_tensors="pt").input_ids
with torch.no_grad():
    z = m(ids).logits[0, -1]                 # [50257]: one raw score per vocabulary entry
print(z.shape, round(z.min().item(), 1), round(z.max().item(), 1))   # unbounded reals

logp = torch.log_softmax(z.float(), -1)      # what APIs return as "logprobs"
top = torch.topk(logp, 5)
for lp, i in zip(top.values, top.indices):
    print(f"{tok.decode(i)!r:12} logit={z[i]:7.2f} logprob={lp:6.2f} p={lp.exp():.3f}")

# Shift invariance: a constant added to every logit changes nothing after softmax.
assert torch.allclose(torch.softmax(z + 123.0, -1), torch.softmax(z, -1), atol=1e-6)

# Steering happens here, before the sampler ever sees a probability.
z2 = z.clone()
z2[top.indices[0]] = float("-inf")           # ban the top token: probability exactly 0
z2[tok.encode(" Berlin")[0]] += 10.0         # logit bias: push an alternative up
print("after edits:", repr(tok.decode(torch.softmax(z2, -1).argmax())))
```

## Trade-offs
- Returning logprobs gives callers calibration signals and cheap confidence filters, but exposes
  information about the model and adds payload, which is why vendors cap how many alternatives you get.
- Logit bias is instant and needs no retraining, while a fine-tune changes behaviour more robustly; bias
  wins for a handful of tokens, loses when the desired change is semantic rather than lexical.
- Hard masking guarantees format compliance at zero probability leakage, but a mask that excludes the
  token the model wanted can push it into low-probability continuations and hurt content quality.
- A larger vocabulary gives finer-grained tokens but a larger logits tensor; in training, one
  8,192-token sequence with a 128,256-entry vocabulary produces about 1.05 billion logits, ~4.2 GB in
  fp32, which is why chunked or fused cross-entropy kernels that never materialize the full tensor matter
  once vocabularies and sequences grow. At decode time only the last row is computed, so the cost
  vanishes there.

## Pitfalls
- Reading logits as probabilities or comparing their absolute values across prompts; the symptom is
  "confidence" numbers that make no sense, since only differences within one vector are meaningful.
- Taking softmax or computing cross-entropy in fp16; the symptom is NaN losses or probabilities that
  collapse to exactly 0 and 1, especially for long sequences.
- Applying a logit bias to a word that tokenizes into several pieces and biasing only the first; the
  symptom is the model starting the word and then veering off.
- Using top-1 logprob as a hallucination detector without calibration; instruction-tuned models are
  often confidently wrong, so high probability does not imply correctness.
- Forgetting that the token you bias must exist in the vocabulary of *this* tokenizer; the id you copied
  from another model points at an unrelated token.

## When to use
You touch logits whenever you need control or measurement that prompting alone cannot give: forcing a
classification into a fixed label set, guaranteeing JSON, banning a competitor's name, computing
perplexity on an evaluation set, or scoring candidate answers by likelihood. Do not reach for logit
edits to fix what is really a prompt or data problem, and do not over-read logprobs as truth signals
without a calibration study. Measure the effect of any edit on task accuracy, not just on format
compliance. In interviews, the expected depth is to explain the path from hidden state to logits to
softmax to sampler, why shift invariance makes masking with −∞ clean, and how constrained decoding
plugs in.

## Role lens
- **Engineer:** Uses `logit_bias`, `logprobs` and logits processors in generation code, and knows to
  compute scores in fp32 and only for the last position.
- **Architect:** Decides whether structured output is enforced at the logit level or validated after
  the fact, and whether logprobs are exposed to downstream services.
- **FDE:** Uses per-token logprobs to show a customer where the model was unsure in a bad answer, and
  bans or biases tokens to meet a hard output constraint on site.

## Check yourself
**Q:** You need a model to answer only "yes" or "no". Compare doing it by prompt, by logit bias and by
hard masking, including what each guarantees and what can go wrong.
**A:** A prompt asks politely; most of the time it works, but nothing prevents "Yes, because..." or an
empty answer, so you need parsing and retries. Logit bias adds a large positive offset to the two tokens
and a negative one to others; it strongly favours them but is probabilistic, and you must bias the exact
token ids of this tokenizer, including leading-space variants. Hard masking sets every other logit to −∞
so only the two tokens have non-zero probability; the output is guaranteed, and you can read the two
logprobs as a calibrated-ish score. The risk with masking is that the model's preferred continuation was
something else, so the forced choice can be lower quality than a free answer followed by classification.
Any of the three should be evaluated on labelled data rather than trusted on format alone.

## Quiz
### Q: Why does adding the same constant to every logit leave the sampled distribution unchanged?
- [ ] Because logits are clipped to a fixed range before sampling
- [x] Because softmax divides each exponentiated logit by their sum, and the common factor cancels
- [ ] Because the model is trained with weight decay
- [ ] Because the tokenizer normalizes scores
> exp(z + c) = exp(c)·exp(z), so the constant multiplies numerator and denominator alike. Only
> differences between logits carry information, which is why absolute logit values are meaningless.

### Q: What does setting a token's logit to negative infinity achieve that a large negative bias does not?
- [ ] It removes the token from the vocabulary permanently
- [ ] It makes generation faster
- [x] Its probability becomes exactly zero, so it can never be sampled regardless of temperature
- [ ] It forces the model to pick the next-highest token deterministically
> A finite bias leaves a tiny but non-zero probability that temperature or long sampling runs can hit;
> −∞ becomes exactly 0 after softmax, which is what constrained decoding relies on.

### Q: An API returns a logprob of −0.69 for the chosen token. Roughly what probability did the model assign?
- [ ] About 69%
- [ ] About 7%
- [x] About 50%
- [ ] About 99%
> Probability is exp(logprob); exp(−0.69) ≈ 0.50. Logprobs are natural logs of probabilities, not
> percentages, and −0.69 is ln(0.5).
