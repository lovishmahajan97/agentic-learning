---
id: perplexity
module: model-internals
name: Perplexity
kind: metric
difficulty: core
est_minutes: 12
related:
  - ml-fundamentals/loss-functions
  - model-internals/tokenization
  - model-internals/logits
  - model-internals/softmax
  - model-internals/scaling-laws
  - evals/model-evaluation
  - evals/dataset-contamination
  - serving-inference/quantization-quality-evaluation
sources:
  - Jelinek et al., 1977 — Perplexity: a measure of the difficulty of speech recognition tasks
  - Official docs: Hugging Face Transformers (perplexity of fixed-length models)
---

## TL;DR
Perplexity is the exponential of a language model's average per-token cross-entropy on a text:
`exp(mean of −log p(token))`. It reads as the effective number of equally likely choices the model faces at
each token, so lower is better. It measures the pre-training objective directly and is interpretable, which
makes it the standard tool for tracking training, comparing checkpoints, checking quantisation and filtering
data. It is tokenizer-dependent and says nothing about instruction-following or factual correctness.

## Why it exists
The language-modelling objective is to assign high probability to real text, so the natural score is the
probability a model gives a held-out corpus. That number is useless on its own: it is the product of
thousands of probabilities, astronomically small, and it shrinks with text length. Taking the log turns the
product into a sum, dividing by token count removes the length dependence, and negating gives the
cross-entropy loss the model was trained on. Exponentiating that loss gives perplexity. A loss of 2.0 nats
is a perplexity of e² ≈ 7.4, which you can read as "at each step the model is as uncertain as if it were
choosing uniformly among 7.4 options". That interpretability is the reason the metric has outlived the
speech-recognition systems it was invented for.

Why not next-token accuracy? Because many continuations are valid. A model that assigns 30% to the actual
next token and spreads the rest over sensible alternatives is better than one that assigns 31% and then
garbage, and accuracy cannot tell them apart. Perplexity scores the whole distribution, it is differentiable
(it is the training loss in disguise), and it needs no labels: any text is an evaluation set. At pre-training
scale it is the only dense, cheap signal available. Downstream benchmarks are expensive, noisy and arrive
late; perplexity is computed on every batch.

## How it works
### Definition
For a token sequence x₁ … x_T under a model p:

```
PPL = exp( −(1/T) · Σ_t log p(x_t | x_<t) )
```

If the cross-entropy H is measured in nats, PPL = e^H; if in bits, PPL = 2^H. Perplexity 1 means the model
predicted every token with certainty; perplexity equal to the vocabulary size means uniform guessing.

The computation uses teacher forcing: feed the real text, take the logits at every position, apply softmax,
and read off the probability of the token that actually came next. One forward pass yields all T
probabilities, and no sampling is involved, so temperature plays no role unless you deliberately rescale
the logits.

### Reading the number
Perplexity is a geometric mean of inverse probabilities, which means a handful of very surprising tokens
dominate it. A rare proper noun, a typo or a number the model could not have known contributes far more
than hundreds of easy function words. That sensitivity is useful when you want to detect distribution shift
and a nuisance when you want to compare two similar models, so always evaluate on enough text that the
estimate is stable. As an order of magnitude, modern models on clean English prose sit in the single digits
to low teens depending on tokenizer and domain, code is lower, and random strings are far higher; treat
these as illustrative, not as targets.

### Why the tokenizer matters
Per-token loss depends on how much text each token carries. A tokenizer with a larger vocabulary produces
longer tokens: each one is harder to predict, so per-token loss rises, but there are fewer of them, so total
loss on the document can be identical. Two models with different tokenizers therefore cannot be compared on
per-token perplexity. The tokenizer-independent alternatives are bits per byte (total negative log-likelihood
in bits divided by the UTF-8 byte count of the text) or bits per character. Older literature also reports
per-word perplexity, which is different again. Whenever you see a perplexity, ask which unit it is per.

### Context length and striding
The probability of token t conditions on everything before it, so a model evaluated with a 1,024-token
window sees less context than with 8,192 and scores worse on the same text. For documents longer than the
window you must slide. Chopping the text into non-overlapping chunks is cheap but pessimistic, because the
first tokens of every chunk have almost no context. The standard recipe is a sliding window with a stride:
move the window forward by `stride` tokens, feed the whole window, and score only the last `stride` tokens.
A smaller stride gives each token more context at the cost of more forward passes. Report window and stride
with every number.

### What it does not measure
- Instruction-tuned and RLHF models are optimised to produce preferred responses, not to model the
  distribution of web text, so their perplexity on raw text often rises while their usefulness improves.
  Low perplexity does not mean a good assistant.
- Factuality: a fluent false sentence can have low perplexity.
- Generation quality: the perplexity of a model's own samples under itself is low by construction.

### What it is used for in practice
1. **Training.** The loss curve is log-perplexity; plateaus, spikes and divergence show up there first.
2. **Quantisation and pruning.** Measure perplexity on a fixed text (WikiText-2 is the common choice in
   llama.cpp-style tooling) before and after. A small rise is the usual price of 8-bit or 4-bit weights; a
   large rise means the compression damaged the model, and the task evals will confirm it later.
3. **Long-context checks.** Perplexity as a function of position: if it climbs past the training length,
   the model is not using the extended window.
4. **Data work.** Filtering documents whose perplexity under a reference model is far outside the normal
   range, flagging anomalous or adversarial prompts, and checking contamination: a suspiciously low
   perplexity on a benchmark's test set suggests the model has seen it.
5. **Domain fit.** Among candidate base models, the one with the lowest bits per byte on a sample of your
   corpus is a reasonable first filter before expensive task evals.

## Example
A sliding-window perplexity evaluation with Hugging Face Transformers. Each window of `max_len` tokens is
fed to the model, but only the final `trg_len` tokens are scored; earlier tokens are masked with `-100` so
they serve as context only. The script also reports bits per byte, which is comparable across tokenizers.

```python
import math, torch
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen2.5-0.5B"                              # any causal LM
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16).cuda().eval()

text = open("eval.txt", encoding="utf-8").read()
ids = tok(text, return_tensors="pt").input_ids.cuda()    # [1, T]
T, max_len, stride = ids.size(1), 2048, 512
nll_sum, n_scored, prev_end = 0.0, 0, 0

with torch.no_grad():
    for begin in range(0, T, stride):
        end = min(begin + max_len, T)
        trg_len = end - prev_end                         # tokens not yet scored
        window = ids[:, begin:end]
        labels = window.clone()
        labels[:, :-trg_len] = -100                      # context only; HF shifts labels internally
        out = model(window, labels=labels)               # out.loss = mean NLL over scored tokens
        nll_sum += out.loss.item() * trg_len             # first token of the first window is a tiny approximation
        n_scored += trg_len
        prev_end = end
        if end == T:
            break

ppl = math.exp(nll_sum / n_scored)
bits_per_byte = nll_sum / math.log(2) / len(text.encode("utf-8"))
print(f"tokens={n_scored} window={max_len} stride={stride} ppl={ppl:.2f} bpb={bits_per_byte:.3f}")
```

Run it before and after quantising the same model with the same `eval.txt`, window and stride, and the
difference is a meaningful regression signal. Run it across two models with different tokenizers and only
the `bpb` line is comparable.

## Trade-offs
- A dense, cheap, label-free signal at any scale, but blind to correctness, helpfulness and safety; it
  flips to task metrics the moment you leave pre-training.
- Comparable across checkpoints of one model and tokenizer, but not across tokenizers or vocabularies; bits
  per byte restores comparability at the cost of the intuitive "number of choices" reading.
- Sensitive to a few high-surprise tokens, which makes it a good detector of distribution shift and a noisy
  judge of small quality differences; average over enough text and report the evaluation size.
- Sliding-window evaluation gives the fairest number but costs up to `window / stride` times more forward
  passes than chunking; chunking is cheap but systematically pessimistic.

## Pitfalls
- Comparing per-token perplexity across models with different tokenizers; the symptom is a model with a
  32k vocabulary that "beats" one with 128k on the same text purely because its tokens are shorter.
- Reporting perplexity without the dataset, tokenizer, window and stride; the symptom is numbers that
  nobody can reproduce and that drift by double-digit percentages between evaluations.
- Using perplexity to choose a chat model; the symptom is a base model that "wins" and then cannot follow
  instructions.
- Evaluating on text that was in the training set; the symptom is an implausibly low number that does not
  predict generalisation, which is also the contamination test seen from the other side.
- Mixing nats and bits; the symptom is a "loss of 2.0" reported as perplexity 4 (2²) rather than 7.39 (e²),
  or the reverse.
- Scoring padding tokens or forgetting that labels must be shifted by one position; the symptom is a
  perplexity near 1 (padding predicted perfectly) or an absurdly high one.

## When to use
Use perplexity when the question is "how well does this model fit this distribution of text": during
pre-training and continued pre-training, when checking a quantised or pruned model against the original,
when choosing a base model for domain adaptation, and when detecting shifted or anomalous inputs. Do not use
it as the acceptance metric for a product feature; use task accuracy, faithfulness and human preference
there. Measure it with a fixed recipe, dataset, tokenizer, window and stride, checked into the repository so
every number is reproducible. In interviews, derive it from cross-entropy, explain why it is not comparable
across tokenizers, explain why RLHF models score worse on it, and name two production uses beyond training
curves.

## Role lens
- **Engineer:** Runs a fixed perplexity script on a standard text before and after quantisation and puts the
  delta in the pull request next to the task evals.
- **Architect:** Sets the rule that perplexity is a gate for model-level changes such as quantisation,
  pruning and continued pre-training, but never a substitute for task-level acceptance criteria.
- **FDE:** Uses bits per byte on a sample of the customer's documents to show whether a candidate open model
  "speaks their language" before investing in a full evaluation.

## Check yourself
**Q:** Two open models report WikiText perplexities of 5.2 and 6.8. A colleague says the first is clearly
better for your legal-contract summarisation product. What is wrong with that reasoning, and what would you
do instead?
**A:** Three things. The numbers are comparable only if the tokenizers, window and stride match, and a
larger vocabulary mechanically raises per-token perplexity. WikiText is encyclopaedia prose, not contracts,
so fit to the actual domain is unmeasured. And summarisation quality depends on instruction-following and
faithfulness, which perplexity does not capture; an RLHF model can have worse perplexity and better
summaries. Instead, compute bits per byte on a sample of the firm's contracts as a quick domain-fit filter,
then run a task evaluation with faithfulness, coverage and human preference on one or two hundred
summaries. Bring perplexity back later only as a regression check when quantising the chosen model.

## Quiz
### Q: A model's average cross-entropy on a test set is 1.5 nats per token. What is its perplexity, and what does that number mean?
- [ ] 1.5; the model is 1.5 times better than chance
- [ ] 2^1.5 ≈ 2.8; the model needs 2.8 bits per token
- [x] e^1.5 ≈ 4.5; on average the model is as uncertain as choosing uniformly among about 4.5 tokens
- [ ] 1/1.5 ≈ 0.67; the average probability of the correct token
> Perplexity is the exponential of the cross-entropy in the same base. With a loss in nats that is e^1.5
> ≈ 4.5. The 2^1.5 option is the right formula for a loss measured in bits, which is the classic unit
> confusion, and the others are not transformations of the loss at all.

### Q: Why can you not directly compare per-token perplexity between a model with a 32k-token vocabulary and one with a 128k vocabulary?
- [ ] Larger vocabularies make the softmax numerically unstable
- [x] Tokens carry different amounts of text, so per-token loss differs even when the two models assign identical probability to the document; compare bits per byte instead
- [ ] The 128k model always has lower perplexity because it has more parameters
- [ ] Perplexity is only defined for byte-level tokenizers
> Total log-probability of a document is tokenizer-independent, but dividing it by a different token count
> gives a different per-token average. Normalising by bytes or characters removes the tokenizer from the
> comparison.

### Q: After RLHF, a model's perplexity on raw web text increased while users rate its answers higher. What is the best interpretation?
- [ ] The RLHF run damaged the model and should be rolled back
- [ ] Perplexity was computed incorrectly
- [x] Perplexity measures fit to the web-text distribution, which RLHF deliberately moves the model away from toward preferred responses; use task and preference metrics for the chat model
- [ ] Users are wrong; perplexity is the ground truth
> Preference tuning changes the output distribution on purpose, so fit to raw text getting worse is
> expected and not a defect. The right evaluation for an assistant is the task it is meant to perform, which
> perplexity on web text does not measure.
