---
id: mixture-of-experts
module: model-internals
name: Mixture of experts (MoE)
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - model-internals/transformer
  - model-internals/llm-architecture
  - model-internals/scaling-laws
  - model-internals/kv-cache
  - serving-inference/gpu-memory-optimization
  - training-adaptation/distributed-training
  - infra-capacity-economics/gpu-sizing
sources:
  - Shazeer et al., 2017 — Outrageously Large Neural Networks: The Sparsely-Gated Mixture-of-Experts Layer
  - Fedus, Zoph and Shazeer, 2021 — Switch Transformers: Scaling to Trillion Parameter Models with Simple and Efficient Sparsity
  - Jiang et al., 2024 — Mixtral of Experts
  - DeepSeek-AI, 2024 — DeepSeek-V3 Technical Report
---

## TL;DR
A mixture-of-experts transformer replaces the dense feed-forward block in each layer with several
parallel "expert" FFNs plus a small router that sends each token to only one or two of them. The model
therefore holds many more parameters than it uses for any given token: quality tracks total parameters
while compute per token tracks active parameters. The price is that every expert must sit in memory,
routing must be kept balanced during training, and the serving arithmetic changes with batch size.

## Why it exists
Scaling laws say loss keeps falling as parameters grow, but in a dense transformer every parameter is
touched by every token, so FLOPs per token grow in lockstep with parameter count. Doubling the model
doubles training compute per token and doubles the weight bytes read on every decode step. The
feed-forward block is the obvious place to attack: with the usual 4× expansion it holds roughly two thirds
of the per-layer non-embedding parameters (8d² for the FFN versus 4d² for the Q, K, V and O projections),
yet for any single token most of that capacity is irrelevant. The circuits that handle Python indentation
are not needed while the model is writing French prose.

The naive alternative, "make the dense FFN bigger and pay for it", fails on the inference side as much as
the training side. Decode is memory-bandwidth bound (see the KV cache lesson), so a model that reads twice
the weights per token runs about twice as slowly at small batch no matter how good the kernels are. A
second naive option, training separate specialist models and choosing one per request, fails because the
useful choice is per token and per layer rather than per request, and because separate models cannot share
the attention layers and embeddings that carry most of the general language ability.

Conditional computation resolves the tension: keep a large pool of parameters but let a learned router
activate a small subset for each token. Shazeer et al. made this trainable at scale in 2017, Switch
Transformer simplified routing to a single expert per token and showed better loss per training FLOP than
dense models, and open models such as Mixtral and DeepSeek-V3 made it the default architecture for the
largest open-weight systems.

## How it works
### The layer
Attention is unchanged and shared by all tokens. The FFN in a layer is replaced by N expert FFNs
E₁ … E_N, each shaped like an ordinary FFN (or narrower), plus a router: one linear map W_r from the hidden
state **x** to N logits. The router scores become a distribution `p = softmax(x·W_r)`, the top-k experts
are selected, and the layer output is a weighted sum of their outputs:

```
y = Σ_{i ∈ topk(p)}  p_i · E_i(x)        (the k weights usually renormalised to sum to 1)
```

k is 1 in Switch Transformer and 2 in Mixtral. DeepSeek-V3 routes each token to 8 of 256 small routed
experts plus one always-on shared expert, which gives fine-grained specialisation while keeping a common
pathway every token passes through.

Routing is per token and per layer: the same token can go to expert 3 in layer 10 and expert 7 in layer
11, and adjacent tokens in a sentence usually take different paths. The router is trained end to end with
the rest of the network. Because top-k selection is not differentiable, gradient reaches the router only
through the p_i weights that multiply the chosen experts' outputs. That is why the output is scaled by p_i
rather than simply summed: without the multiplication the router would receive no learning signal.

### Active versus total parameters
Count Mixtral 8x7B: 8 experts per layer, top-2 routing. The total is about 47B parameters, not 8 × 7 =
56B, because attention, embeddings and norms are shared across experts. Per token only 2 of the 8 FFNs run,
so roughly 13B parameters are active. Training FLOPs per token and the weight bytes read per decode step at
batch 1 both track the 13B figure; loss tracks something much closer to the 47B figure. That gap is the
whole point of the architecture.

### Load balancing
Left alone, routers collapse. A few experts that happen to be slightly better early in training receive
more tokens, learn faster, and attract still more tokens, until most experts are dead weight. Two
mechanisms counter this:

- **An auxiliary load-balancing loss** (Switch style). For each expert, multiply the fraction of tokens
  routed to it, f_i, by the mean router probability it received, P_i; sum over experts and scale by N. The
  sum is minimised when both are uniform, and because P_i is differentiable it pushes the router toward
  spreading tokens. DeepSeek-V3 instead adds a per-expert bias to the routing scores and nudges it after each
  step, which balances load without the small quality penalty an auxiliary loss costs.
- **Expert capacity.** Each expert accepts at most `capacity_factor × tokens / N` tokens per batch.
  Overflow tokens are dropped from that expert (they pass through the residual connection only) or sent to
  their next choice. This exists because experts run as fixed-size batched matrix multiplications, often
  on different devices, and one overloaded expert would stall all the others.

### Why memory and parallelism change
Every expert's weights must be resident, because the next token might be routed anywhere. Mixtral 8x7B
therefore needs about 47B × 2 bytes ≈ 94 GB in fp16 for weights alone, more than one 80 GB card, even though
it computes like a 13B model. At larger scale the experts of each layer are spread across devices (expert
parallelism), and an all-to-all exchange moves each token's hidden state to the devices hosting its chosen
experts and back again. That communication, twice per MoE layer, is the dominant engineering cost of
training and serving MoE models.

### The batch-size effect at inference
At batch 1 a decode step reads only the k active experts per layer, so an MoE is far faster than a dense
model of equal total size. As the batch grows, different tokens pick different experts until every expert
is touched on every step; weight traffic then approaches that of the dense total and the advantage shifts
from bandwidth to FLOPs. Capacity planning for an MoE must be done at the concurrency you will actually run.

## Example
A minimal top-k MoE layer with a Switch-style balancing loss. With `d_model=512, d_ff=2048` one FFN has
2 × 512 × 2048 ≈ 2.1M parameters; eight experts hold ≈ 16.8M in total, but each token runs through two,
so ≈ 4.2M are active per token. The `aux` value is smallest when tokens are spread evenly.

```python
import torch, torch.nn as nn, torch.nn.functional as F

class MoELayer(nn.Module):
    def __init__(self, d_model=512, d_ff=2048, n_experts=8, k=2):
        super().__init__()
        self.k, self.n = k, n_experts
        self.router = nn.Linear(d_model, n_experts, bias=False)
        self.experts = nn.ModuleList(
            nn.Sequential(nn.Linear(d_model, d_ff), nn.SiLU(), nn.Linear(d_ff, d_model))
            for _ in range(n_experts))

    def forward(self, x):                                  # x: [tokens, d_model]
        probs = self.router(x).softmax(-1)                 # [tokens, n_experts]
        topv, topi = probs.topk(self.k, dim=-1)            # [tokens, k]
        topv = topv / topv.sum(-1, keepdim=True)           # renormalise over the chosen k
        out = torch.zeros_like(x)
        for e in range(self.n):                            # one batched matmul per expert
            hit = (topi == e)                              # [tokens, k] bool
            rows = hit.any(-1).nonzero(as_tuple=True)[0]
            if rows.numel() == 0:
                continue
            w = (topv * hit)[rows].sum(-1, keepdim=True)   # router weight for expert e
            out[rows] += w * self.experts[e](x[rows])      # gradient reaches the router via w
        frac = F.one_hot(topi, self.n).float().sum(1).mean(0)   # f_i: share of tokens sent to each expert
        mean_p = probs.mean(0)                                  # P_i: mean router probability per expert
        aux = self.n * (frac * mean_p).sum()                    # balancing loss, add lambda * aux to LM loss
        return out, aux

layer = MoELayer()
y, aux = layer(torch.randn(4096, 512))
print(y.shape, round(aux.item(), 3))
print(sum(p.numel() for p in layer.experts.parameters()) / 1e6, "M expert params total")
```

Production kernels do not loop over experts in Python; they sort tokens by expert and run grouped matrix
multiplications, which is why engines such as vLLM and SGLang ship fused MoE kernels.

## Trade-offs
- Quality per training FLOP improves because capacity grows without proportional compute, but weight
  memory grows with total parameters; when a single GPU's VRAM is the binding constraint, a dense model with
  the same memory footprint can be the better choice.
- Decode at small batch is faster than a dense model of equal total size because only k experts are read
  per layer, but the gain shrinks as concurrency rises and every expert is touched on every step.
- Expert parallelism lets total parameters exceed any one device, at the cost of an all-to-all exchange in
  every MoE layer; on slow interconnects that communication can cancel the compute savings.
- Fine-tuning an MoE on a small dataset is reported to be less stable than fine-tuning a dense model,
  because the router can drift and overfit; freezing the router or using more data helps, but a workflow
  built around frequent per-customer fine-tunes is simpler with dense models.
- Many narrow experts plus a shared expert (the DeepSeek design) specialise more finely than a few wide
  ones, but produce more small matrix multiplications and more routing overhead, which GPUs handle
  efficiently only with fused kernels.

## Pitfalls
- Sizing serving hardware from active parameters; the symptom is an out-of-memory error at model load,
  because all experts must be resident regardless of how few run per token.
- Reading "8x7B" as a 56B model, or benchmarking it against dense 56B models; shared attention makes it
  about 47B total and 13B active, and the two numbers answer different questions.
- Expecting experts to be human-interpretable specialists ("the code expert", "the legal expert"); the
  Mixtral authors found routing correlates with token type and syntax far more than with topic, so you
  cannot prune "the experts you don't need" by domain.
- Setting the capacity factor too low to save memory; the symptom is quality that degrades on inputs with
  an unusual token mix (code in a chat model, a rare language) because overflow tokens are silently dropped.
- Router collapse during training or continued pre-training; the symptom is an expert-utilisation histogram
  with a few hot experts and many near zero, plus a loss plateau that the parameter count does not explain.
- Benchmarking throughput at batch 1 and extrapolating to production concurrency; weight traffic per step
  rises with batch until all experts are read, so the dense-versus-MoE comparison can invert.

## When to use
You rarely decide to train an MoE; you decide whether to deploy one. Reach for an MoE when you need
top-tier quality from open weights, you have a multi-GPU node or enough host memory, and your traffic
pattern leaves a bandwidth advantage at the batch sizes you run. Prefer a dense model when you are on a
single card, when you fine-tune often on small data, or when your serving stack lacks fused MoE kernels and
expert-parallel support (a homegrown PyTorch loop will be slow). Measure tokens per second at your real
concurrency, peak memory including the KV cache, and quality on your own eval set rather than the model
card. In interviews expect "why is Mixtral 47B and not 56B", "what do active versus total parameters
govern", "why is load balancing needed and how does it work", and "when can an MoE be slower than a dense
model".

## Role lens
- **Engineer:** Reads the model card for total and active parameters, picks an engine with fused MoE
  kernels, and verifies memory from weights plus KV cache before promising a GPU count.
- **Architect:** Chooses between MoE and dense for the open-weight tier from VRAM, interconnect, concurrency
  profile and fine-tuning frequency, and writes the batch-size effect into the capacity plan.
- **FDE:** Explains to a customer why the "13B-speed" model on the datasheet still needs two 80 GB GPUs in
  their data centre, and why single-user latency numbers will not hold under shared load.

## Check yourself
**Q:** A customer wants to self-host a hypothetical open MoE listed as "about 140B total, 17B active" on
their existing single 80 GB GPU because "it runs like a 17B model". What do you tell them, and what would
change your answer?
**A:** Active parameters describe compute and bandwidth per token, not memory. All 140B parameters must be
resident because any token can be routed to any expert, so fp16 weights alone are about 280 GB, and even
4-bit quantisation (roughly 70 GB) leaves almost no room for the KV cache on an 80 GB card. Their options
are a multi-GPU node with expert or tensor parallelism, aggressive quantisation plus expert offloading to
CPU memory with a large latency penalty, or a dense model in the 13–30B range that fits the card. The speed
claim also holds only at low concurrency; as batch grows, every expert is read each step. I would change the
recommendation if they have a multi-GPU box with fast interconnect, or if their concurrency is near one and
they accept quantised weights.

## Quiz
### Q: Why does an MoE layer multiply each chosen expert's output by its router probability instead of simply summing the chosen outputs?
- [ ] To keep the output magnitude equal to that of a dense FFN
- [x] Because top-k selection is not differentiable, so the multiplication is the only path through which the router receives gradient
- [ ] To penalise experts that receive too many tokens in a batch
- [ ] Because experts have different hidden sizes and must be rescaled
> The hard top-k choice has no gradient. Scaling each expert's output by its softmax probability makes
> the router's parameters part of the differentiable graph, so it can learn which expert helps. Load
> balancing is handled separately by the auxiliary loss or bias terms, not by this scaling.

### Q: Mixtral 8x7B has about 47B parameters but computes like a roughly 13B model. Which statement about serving it is correct?
- [ ] It fits wherever a 13B fp16 model fits, since only 13B parameters are used per token
- [ ] Its KV cache is eight times larger than that of a 13B dense model
- [x] All 47B parameters must be in memory, but at small batch each decode step reads only the two active experts per layer
- [ ] It needs memory for 56B parameters because there are eight 7B experts
> Memory is set by total parameters because routing can pick any expert; compute and weight traffic per
> token at low batch are set by the active parameters. The total is 47B rather than 56B because attention
> and embeddings are shared, and the KV cache depends on attention shape, which the experts do not change.

### Q: What failure does the load-balancing auxiliary loss prevent during training, and what is the observable symptom without it?
- [ ] Vanishing gradients; the symptom is a loss that never decreases
- [x] Router collapse onto a few experts; the symptom is a skewed expert-utilisation histogram and a loss plateau despite the parameter count
- [ ] Overfitting of the attention layers; the symptom is a train/validation gap
- [ ] Token dropping from capacity overflow; the symptom is NaN losses
> Without a balancing term, early winners attract more tokens and the rest of the experts stop learning,
> so the model behaves like a much smaller dense network. Capacity overflow is a related but separate
> mechanism handled by the capacity factor, and it drops tokens rather than producing NaNs.
