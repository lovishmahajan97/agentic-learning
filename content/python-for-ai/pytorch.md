---
id: pytorch
module: python-for-ai
name: PyTorch (tensors, autograd, nn.Module, DataLoader, device)
kind: tool
difficulty: core
est_minutes: 20
related:
  - python-for-ai/numpy
  - python-for-ai/hugging-face
  - python-for-ai/safetensors
  - ml-fundamentals/backpropagation
  - ml-fundamentals/gradient-descent
  - ml-fundamentals/mixed-precision
  - serving-inference/gpu-memory-optimization
  - training-adaptation/gradient-checkpointing
sources:
  - Paszke et al., 2019 — PyTorch: An Imperative Style, High-Performance Deep Learning Library
  - Official docs: PyTorch
---

## TL;DR
PyTorch is NumPy-style arrays (tensors) that can live on a GPU and that record the operations applied
to them, so `loss.backward()` computes every gradient automatically. `nn.Module` packages parameters and
forward logic, `DataLoader` feeds batches in parallel, and `device` decides where memory lives. It won
because the graph is built by running ordinary Python, so you debug models with print statements and
`pdb` rather than a separate graph compiler.

## Why it exists
Training a neural network needs three things NumPy does not give you: the derivative of a scalar loss
with respect to millions or billions of parameters, execution on accelerators whose memory is separate
from the CPU's, and a way to keep a GPU busy while the next batch is being loaded and decoded.

Deriving gradients by hand is error-prone past a few layers, and finite differences are hopeless: one
extra forward pass per parameter means a billion forward passes per step for a 1B-parameter model.
Reverse-mode automatic differentiation computes all of them in roughly the cost of one extra backward
pass, but it needs a record of what was computed. Earlier frameworks such as TensorFlow 1.x asked you to
declare that record as a static graph first and then execute it in a session; this made optimisation easy
and debugging miserable, because errors surfaced inside a runtime you could not step through, and data-
dependent control flow (a loop that runs a variable number of times) needed special graph operators.

PyTorch's answer is "define-by-run": the graph is recorded as your Python executes, every iteration,
so control flow is just Python. For an AI engineer the payoff today is less about writing training loops
from scratch and more about literacy: every Hugging Face model, every LoRA adapter, every embedding
model and most local inference stacks are PyTorch modules, and when one OOMs, produces NaNs or runs on
the CPU by accident, you need to know which of these five pieces is responsible.

## How it works
### Tensors
A tensor is a typed, strided n-dimensional array: a pointer to storage plus `shape`, `stride`, `dtype`
and `device`. Views (`.view`, `.transpose`, slicing) share storage and only change strides, which is why
they are free, and why `.view` fails on a non-contiguous tensor and you need `.reshape` or
`.contiguous()`. Broadcasting follows NumPy rules. `dtype` matters more than in NumPy: `float32` is the
default, `bfloat16` and `float16` halve memory and run on tensor cores, and mixing dtypes in one op
raises or silently upcasts depending on the op.

### Autograd
Any tensor with `requires_grad=True` (all `nn.Parameter`s) makes subsequent ops record a node in a
directed acyclic graph: each output tensor holds a `grad_fn` pointing to the function that made it and
to its inputs. `loss.backward()` walks that graph in reverse, applying the chain rule, and *accumulates*
results into each leaf's `.grad`. Accumulation, not assignment, is a deliberate choice: it lets
gradient accumulation across micro-batches work for free, and it is why you must call
`optimizer.zero_grad()` every step. The graph is freed after `backward()` because holding saved
activations is the main memory cost of training; `torch.no_grad()` (or `torch.inference_mode()`) stops
recording entirely, which is why inference must run inside it.

### nn.Module
A module is a class whose attributes that are `nn.Parameter`s or other modules get registered
automatically. That registration is what makes `model.parameters()` (for the optimizer),
`model.to(device)`, `model.state_dict()` (for saving) and `model.train()` / `model.eval()` work across an
arbitrarily nested tree. `train()`/`eval()` flip behaviour of layers such as dropout and batch norm; they
do not disable gradients. You write `forward`, and call the module as `model(x)` so hooks run.

### DataLoader
A `Dataset` answers `len()` and `__getitem__(i)`. A `DataLoader` wraps it with batching, shuffling, a
`collate_fn` that stacks samples (or pads variable-length sequences), and `num_workers` subprocesses that
prefetch batches so tokenisation or image decoding overlaps GPU compute. Worker processes, not threads,
are used because of the GIL. `pin_memory=True` puts batches in page-locked host memory so the copy to
GPU can be asynchronous.

### device
Tensors live on exactly one device (`cpu`, `cuda:0`, `mps`). Ops require all inputs on the same device;
moving is explicit (`.to(device)`) because a host-to-GPU copy crosses PCIe and is slow relative to GPU
compute, and PyTorch refuses to hide that cost. CUDA calls are asynchronous: Python returns before the
kernel finishes, so naive timing measures launch time unless you call `torch.cuda.synchronize()`.

## Example
A complete minimal training loop; every line maps to one of the five pieces.

```python
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

device = "cuda" if torch.cuda.is_available() else "cpu"
X = torch.randn(1024, 16)                       # 1024 samples, 16 features
y = (X.sum(dim=1, keepdim=True) > 0).float()    # toy binary label
loader = DataLoader(TensorDataset(X, y), batch_size=64, shuffle=True)

class MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(16, 32), nn.ReLU(), nn.Linear(32, 1))
    def forward(self, x):
        return self.net(x)                       # logits, shape [batch, 1]

model = MLP().to(device)                         # moves every registered parameter
opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
loss_fn = nn.BCEWithLogitsLoss()

for epoch in range(5):
    model.train()
    for xb, yb in loader:
        xb, yb = xb.to(device), yb.to(device)    # data must be on the model's device
        opt.zero_grad()                          # grads accumulate otherwise
        loss = loss_fn(model(xb), yb)
        loss.backward()                          # fills p.grad for every parameter
        opt.step()
    model.eval()
    with torch.no_grad():                        # no graph, less memory
        acc = ((model(X.to(device)) > 0).float() == y.to(device)).float().mean()
    print(epoch, round(loss.item(), 4), round(acc.item(), 3))
```

The model has 16×32+32 + 32×1+1 = 577 parameters; `loss.item()` copies one scalar back to the CPU,
which forces a sync, so in a real loop you log it every N steps rather than every step.

## Trade-offs
- Eager define-by-run execution makes debugging trivial, but per-op Python overhead hurts small models;
  `torch.compile` recovers speed by tracing to fused kernels, at the cost of compile time and graph breaks.
- Explicit device placement makes data movement visible and predictable, but every boundary is a place
  to forget a `.to(device)`; frameworks like Accelerate hide it once you run multi-GPU.
- Lower precision (bf16/fp16) halves memory and speeds tensor-core math, but fp16 needs loss scaling to
  avoid gradient underflow; bf16 does not, so prefer it whenever the hardware supports it.
- Storing activations for backward is what makes autograd cheap in compute, but it dominates training
  memory; gradient checkpointing trades roughly one extra forward pass for much lower activation memory.

## Pitfalls
- Forgetting `optimizer.zero_grad()`: gradients from every step add up, and the symptom is a loss that
  diverges or oscillates wildly after a few steps.
- Running inference without `torch.no_grad()` or `inference_mode()`: memory climbs per request until a
  CUDA out-of-memory error, because each forward pass keeps its graph alive.
- Forgetting `model.eval()` before evaluation: dropout stays active, so the same input gives different
  outputs and validation metrics look noisy and worse than expected.
- Accumulating `total += loss` instead of `loss.item()`: you keep every step's graph referenced, and the
  symptom is steadily rising GPU memory across an epoch.
- Timing GPU code without `torch.cuda.synchronize()`: kernels are asynchronous, so measurements look
  impossibly fast and then the cost appears at the next `.item()` or `.cpu()` call.

## When to use
Reach for raw PyTorch when you need control a higher-level library hides: a custom loss, a non-standard
training step, inspecting activations, or debugging why a Hugging Face model misbehaves. For fine-tuning
standard LLMs, use Transformers, PEFT and TRL on top of it rather than hand-writing loops; for serving
LLMs at scale, use an inference engine (vLLM, SGLang, TGI) rather than a PyTorch `generate` loop. What
to measure: GPU utilisation, peak memory (`torch.cuda.max_memory_allocated()`), and samples per second;
a GPU idling at low utilisation usually means the DataLoader is the bottleneck. In interviews, expect to
explain why gradients accumulate, what `no_grad` saves, and the difference between `eval()` and
`no_grad()`.

## Role lens
- **Engineer:** Writes and debugs training or inference loops, fixes device mismatches, dtype errors
  and OOMs, and reads model code in Transformers to understand what a checkpoint actually does.
- **Architect:** Decides when the team owns PyTorch-level code versus buying an API, and sets the
  GPU, precision and framework standards that training and serving pipelines inherit.
- **FDE:** Gets a customer's model running on their hardware, often diagnosing CUDA/driver mismatches,
  CPU fallbacks, or memory limits on whatever GPU the customer actually has.

## Check yourself
**Q:** A colleague's inference service leaks GPU memory: every request adds a little until it crashes.
The code calls `model.eval()` at startup. What is the likely cause, and why doesn't `eval()` fix it?
**A:** `eval()` only changes layer behaviour such as dropout and batch norm; it does not stop autograd
from recording. If the forward pass runs without `torch.no_grad()` or `torch.inference_mode()`, each
output tensor carries a graph with saved activations, and if any output (or a running total of them) is
kept referenced, for example appended to a list or stored in a cache, that memory is never released.
The fix is to wrap inference in `inference_mode()`, convert results to Python values or detached CPU
tensors before storing them, and check `torch.cuda.memory_allocated()` stays flat across requests.

## Quiz
### Q: Why does PyTorch accumulate gradients into `.grad` instead of overwriting them?
- [ ] Because overwriting would break the chain rule inside a single backward pass
- [x] So that gradients from several backward passes can be summed, enabling gradient accumulation
- [ ] Because GPU memory cannot be overwritten in place
- [ ] So the optimizer can compute momentum without storing extra state
> Accumulation lets several micro-batches contribute to one update without extra code. The price is
> that you must zero gradients each step; momentum is stored separately by the optimizer, not in `.grad`.

### Q: What does `model.eval()` change?
- [ ] It disables gradient tracking for all parameters
- [ ] It moves the model to the CPU for deterministic evaluation
- [x] It switches layers like dropout and batch norm to inference behaviour
- [ ] It freezes parameters so the optimizer cannot update them
> `eval()` sets the training flag that dropout and normalisation layers read. Gradient recording is
> controlled separately by `no_grad`/`inference_mode`, which is why production code needs both.

### Q: A GPU sits at low utilisation during training while CPU cores are busy. What is the most likely fix?
- [ ] Increase the learning rate so each step does more work
- [ ] Switch the model from bf16 to fp32
- [x] Raise DataLoader `num_workers` and enable `pin_memory` so batches are prepared in parallel
- [ ] Call `torch.cuda.synchronize()` after every step
> The GPU is waiting for data. Worker processes prefetch and collate batches while the GPU computes, and
> pinned memory allows asynchronous copies; synchronising more often would make the stall worse.
