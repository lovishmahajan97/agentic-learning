---
id: transformer
module: model-internals
name: Transformer
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - model-internals/attention-mechanism
  - model-internals/self-attention
  - model-internals/multi-head-attention
  - model-internals/positional-encoding
  - model-internals/llm-architecture
  - model-internals/embeddings
  - ml-fundamentals/residual-connections
  - ml-fundamentals/normalization
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Radford et al., 2019 — Language Models are Unsupervised Multitask Learners
  - Touvron et al., 2023 — Llama 2: Open Foundation and Fine-Tuned Chat Models
---

## TL;DR
A transformer is a stack of identical blocks. Each block first lets every position pull information from
other positions through attention, then transforms each position on its own with a feed-forward network,
with residual connections and normalization holding the stack together. It replaced recurrence because
all positions of a sequence are processed in parallel as one matrix multiplication and any two positions
are a single attention step apart, which made training on web-scale text affordable. Nearly every
current LLM is a decoder-only variant of this design.

## Why it exists
Before 2017 sequence models were recurrent. An LSTM reads token *t* only after it has finished token
*t−1*, so a 1,000-token document is 1,000 dependent steps. That has two costs. First, you cannot
parallelize across positions inside a sequence, so GPUs sit mostly idle and wall-clock time grows with
length. Second, information from position 1 reaches position 1,000 only by surviving 999 updates of a
fixed-size hidden vector; gradients through that path shrink, and long-range dependencies are learned
badly or not at all.

Attention had already been bolted onto RNNs for translation to fix the second problem: the decoder could
look back at every encoder state instead of one summary vector. But the recurrent backbone kept the
first problem. Convolutional alternatives (ByteNet, ConvS2S) parallelize, yet connecting two positions
*n* apart needs O(n/k) stacked layers of kernel width *k*, or O(log n) with dilation, so distance still
costs depth.

The transformer's bet was to drop recurrence and convolution entirely and let attention do all the
mixing between positions. Then every position in a layer is computed at once, and the path between any
two positions is one layer long. The price is attention cost that grows with the square of sequence
length and a model with no built-in sense of order, which positional encodings must supply. That trade
was right because the binding constraint on quality turned out to be how much data and compute you can
push through training, and a matmul-parallel architecture scales that far better than a sequential one.

## How it works
### The block
Token ids go through an embedding table to give an `[n, d_model]` matrix, with position information
added or injected into attention. Then *L* identical blocks run in sequence. Each block has two
sub-layers:

1. **Attention sub-layer.** Every position builds a query, scores it against the keys of all positions it
   is allowed to see, and takes the softmax-weighted sum of their values. This is the *only* place in the
   network where tokens exchange information. Multi-head attention runs several of these in parallel in
   different subspaces.
2. **Feed-forward sub-layer (MLP).** Applied to every position independently with the same weights:
   `FFN(x) = W₂·act(W₁·x)`, with an inner width of about 4×d_model (or roughly 2.7× when a gated SwiGLU
   with three matrices is used, as in Llama). Attention's only nonlinearity is the softmax over scores,
   and its output is a weighted average of linearly projected values, so it can route and blend
   information but cannot compute a new feature of a single token. That per-position computation, and
   most of the parameters, live in the MLP; it holds roughly two thirds of a layer's weights.

Each sub-layer is wrapped as `x = x + SubLayer(Norm(x))`. The residual connection exists because it gives
gradients an identity path through 30–100 layers; the useful picture is a *residual stream* of width
d_model that each sub-layer reads from and writes a small update into. The original paper normalized
*after* the addition (post-norm); GPT-2 and nearly everything since normalize *before* the sub-layer
(pre-norm) because it keeps the identity path clean and trains deep stacks without delicate warmup
schedules. Llama-style models replace LayerNorm with RMSNorm, which drops the mean-centering step
because it is cheaper and works as well.

### Data flow with shapes
For one sequence of *n* tokens: ids `[n]` → embedding `[n, d]` → *L* blocks, each mapping `[n, d]` to
`[n, d]` (attention internally builds `[h, n, n]` scores; the MLP briefly widens to `[n, d_ff]`) →
final norm `[n, d]` → LM head `[n, V]`. Because every block preserves the shape, depth is just a loop,
and every block can read and write the same residual stream.

### The head
After the last block: a final norm, then a linear map from d_model to the vocabulary size (the LM head,
often weight-tied to the input embedding), giving logits that a softmax turns into a next-token
distribution. Training predicts token *t+1* at every position *t* in the same forward pass under a
causal mask, so one pass over a 4k-token sequence yields 4k training signals. That density of signal per
FLOP is a large part of why the architecture scales.

### Three layouts
- **Encoder–decoder** (original, T5): the encoder uses bidirectional self-attention over the input; the
  decoder uses causal self-attention plus cross-attention into the encoder output. Natural for
  translation where input and output are distinct sequences.
- **Encoder-only** (BERT): bidirectional, every token sees every other. Strong for classification and
  embeddings; cannot generate text autoregressively.
- **Decoder-only** (GPT, Llama, most chat models): one causal stack. It won for LLMs because it is a
  single component to scale, it trains on raw unlabeled text with one objective, prompt and completion
  live in the same stream so any task becomes conditioning, and causality is what makes the KV cache
  possible at inference.

### Where parameters and FLOPs go
Per layer with hidden size *d*: the four attention projections cost 4d² parameters; a 4× MLP costs 8d²;
a SwiGLU MLP costs 3·d·d_ff. For Llama-2-7B (d = 4096, L = 32, d_ff = 11008, vocab 32,000): attention
67.1M and MLP 135.3M per layer, 202.4M × 32 = 6.48B, plus two untied 131M embedding matrices, ≈ 6.74B.
Forward compute is about 2 FLOPs per parameter per token (each weight takes part in one
multiply-accumulate) plus an attention-score term of roughly 4·n·d per layer per token (QKᵀ and AV
against *n* earlier positions). Against a standard block's 12d² weights, that is 24d² versus 4·n·d, so
the attention term reaches a sixth of the weight FLOPs at n = d and equals them at n ≈ 6d, about 24k
tokens for d = 4096. Hence the rules of thumb: inference ≈ 2N FLOPs per token, training ≈ 6N
per token (forward plus a backward that costs about twice the forward). These two numbers let you
estimate cost before touching a GPU.

### What the pieces learn
Interpretability work consistently finds attention heads that implement routing (attend to the previous
token, find an earlier occurrence of the current token and copy what followed it) and MLP layers that
act like key–value memories storing factual and lexical associations. Treat this as a useful picture,
not a complete theory.

## Example
A minimal pre-norm decoder-only transformer in PyTorch. The block is the whole architecture; the rest is
embedding, stacking and the tied LM head. `nn.RMSNorm` needs PyTorch 2.4+; swap in `nn.LayerNorm` otherwise.

```python
import torch, torch.nn as nn, torch.nn.functional as F

class Block(nn.Module):
    def __init__(self, d=512, n_heads=8, d_ff=2048):
        super().__init__()
        self.h, self.dk = n_heads, d // n_heads
        self.n1, self.n2 = nn.RMSNorm(d), nn.RMSNorm(d)
        self.qkv, self.o = nn.Linear(d, 3 * d, bias=False), nn.Linear(d, d, bias=False)
        self.up, self.down = nn.Linear(d, d_ff, bias=False), nn.Linear(d_ff, d, bias=False)

    def forward(self, x):                                   # x: [B, n, d]
        B, n, d = x.shape
        q, k, v = self.qkv(self.n1(x)).split(d, dim=-1)
        q, k, v = (t.view(B, n, self.h, self.dk).transpose(1, 2) for t in (q, k, v))  # [B, h, n, dk]
        a = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        x = x + self.o(a.transpose(1, 2).reshape(B, n, d))  # residual 1: tokens have now mixed
        return x + self.down(F.gelu(self.up(self.n2(x))))   # residual 2: per-position transform

class TinyLM(nn.Module):
    def __init__(self, vocab=32000, d=512, L=6, max_len=1024):
        super().__init__()
        self.emb, self.pos = nn.Embedding(vocab, d), nn.Embedding(max_len, d)
        self.blocks = nn.ModuleList(Block(d) for _ in range(L))
        self.norm, self.head = nn.RMSNorm(d), nn.Linear(d, vocab, bias=False)
        self.head.weight = self.emb.weight                  # weight tying: one matrix, two jobs

    def forward(self, ids):                                 # ids: [B, n] -> logits [B, n, vocab]
        x = self.emb(ids) + self.pos(torch.arange(ids.shape[1], device=ids.device))
        for b in self.blocks:
            x = b(x)
        return self.head(self.norm(x))

m = TinyLM(); ids = torch.randint(0, 32000, (2, 128))
print(m(ids).shape, f"{sum(p.numel() for p in m.parameters())/1e6:.1f}M params")
# torch.Size([2, 128, 32000]) 35.8M params  (16.4M of them are the shared embedding)
```

## Trade-offs
- Full parallelism across positions and O(1) path length between any two tokens, paid for with
  attention cost quadratic in sequence length; the trade flips against plain attention when contexts
  reach hundreds of thousands of tokens, which is where sliding windows, retrieval and state-space
  hybrids enter.
- No built-in locality or order bias means the model needs positional encodings and more data than a
  CNN or RNN to learn simple structure, but the same lack of bias is why one architecture handles
  prose, code, audio tokens and protein sequences.
- Decoder-only gives one objective, one stack and a KV cache, but every token sees only its left
  context; for classification and embedding tasks an encoder that sees the whole input both ways is
  often better per parameter.
- Pre-norm trains deep stacks reliably but lets the residual stream's magnitude grow with depth, which
  is why final norms and careful initialization matter at scale; post-norm has the opposite profile.

## Pitfalls
- Estimating serving cost from parameter count alone and ignoring the attention term; the symptom is
  a cost model that is accurate at 2k context and badly wrong at 64k.
- Treating the architecture diagram as the whole story: tokenizer, positional scheme, norm placement,
  activation and head layout differ between model families, and loading weights with the wrong variant
  produces a model that runs and emits fluent garbage.
- Assuming an encoder-only model can be prompted to generate, or a decoder-only model gives good
  bidirectional embeddings out of the box; the symptom is puzzlingly weak results on a task the
  architecture was never trained to do.
- Reading attention maps as explanations of a prediction; many heads over many layers plus the MLP
  path mean a single map rarely tells you why the model answered as it did.

## When to use
You rarely choose *whether* to use a transformer; you choose which layout and size, and you need the
block structure in your head to reason about everything downstream: why the KV cache exists, why GQA
and FlashAttention target the attention sub-layer, why quantization targets the MLP matrices, why
context length is expensive, and how to turn a model config (`hidden_size`, `num_hidden_layers`,
`intermediate_size`, `num_attention_heads`, `num_key_value_heads`) into parameter, FLOP and memory
numbers. Reach for an encoder when the task is classify-or-embed with the whole input visible; a
decoder when it is generate; an encoder–decoder when inputs are long and fixed and outputs short
(translation, some summarization). In interviews, be ready to draw the block, name the two sub-layers
and their roles, and derive the 2N-FLOPs-per-token estimate from the shapes.

## Role lens
- **Engineer:** Reads a model's config to compute memory and FLOPs, picks encoder vs decoder models for
  embeddings vs generation, and knows which sub-layer a given optimization touches.
- **Architect:** Owns the cost and capability model that follows from layout and size: context policy,
  GPU tier and whether a small encoder can replace an LLM call for classification.
- **FDE:** Explains to a customer why a 70B model costs what it does, why their long documents change the
  bill non-linearly, and why "just fine-tune BERT to write emails" does not work.

## Check yourself
**Q:** Decoder-only transformers won for general-purpose LLMs over encoder–decoder designs. Explain the
reasons, and name what was given up in the process.
**A:** A single causal stack is one component to scale and one objective, next-token prediction, that
trains on unlabeled text. Because prompt and completion share the stream, any task becomes conditioning
rather than a separate head, which is what makes in-context learning and instruction following natural.
Causality also means earlier tokens' keys and values never change, so inference can cache them and
generate in linear time per token. What was given up: the input is read only left-to-right, so per-token
representations of the prompt are weaker than an encoder's bidirectional ones, which still matters for
embeddings and classification; and prompt plus output share one quadratic attention budget, whereas an
encoder–decoder processes the input once and cross-attends to it cheaply from each output step.

## Quiz
### Q: In a transformer block, which component is the only one that moves information between positions?
- [ ] The feed-forward (MLP) sub-layer
- [x] The attention sub-layer
- [ ] The layer normalization
- [ ] The residual connection
> The MLP, norms and residuals all operate on each position independently with shared weights. Only
> attention computes a weighted sum over other positions, so it is the sole channel for cross-token
> information flow.

### Q: Why did pre-norm (`x + f(Norm(x))`) replace the original post-norm in deep decoder stacks?
- [ ] It reduces the parameter count of each block
- [ ] It is what makes the KV cache possible
- [x] It keeps the identity residual path clean, so very deep stacks train stably without delicate warmup
- [ ] It lowers attention cost from quadratic to linear
> Post-norm puts a normalization on the main residual path, which makes gradients through deep stacks
> fragile. Pre-norm leaves the residual stream untouched between blocks. It has nothing to do with the
> cache or attention complexity, and it changes no parameter count.

### Q: For a dense transformer with N parameters at modest context, roughly how many FLOPs does one forward token cost?
- [ ] About N
- [x] About 2N
- [ ] About 6N
- [ ] About N²
> Every weight participates in one multiply-accumulate per token, which is two FLOPs, giving ≈2N; the
> attention-score term (about 4·n·d per layer) stays minor until context is several times the hidden
> size. 6N is the training estimate, which includes the backward pass.
