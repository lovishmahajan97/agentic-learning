---
id: safetensors
module: python-for-ai
name: safetensors
kind: tool
difficulty: intro
est_minutes: 11
status: fact-checked
related:
  - python-for-ai/pytorch
  - python-for-ai/hugging-face
  - safety-security/supply-chain-security-for-models-and-datasets
  - training-adaptation/checkpointing
  - training-adaptation/quantization
  - serving-inference/gpu-memory-optimization
sources:
  - Official docs: safetensors
  - Official docs: PyTorch serialization semantics
---

## TL;DR
safetensors is a file format for storing tensors: a small JSON header describing each tensor's name,
dtype, shape and byte offsets, followed by the raw bytes. It replaced pickle-based PyTorch checkpoints
on the Hugging Face Hub because loading a pickle can execute arbitrary code, while loading safetensors
only reads numbers, and because its layout lets weights be memory-mapped and loaded lazily and fast.

## Why it exists
For years the default way to save PyTorch weights was `torch.save`, which uses Python's `pickle`. Pickle
is not a data format; it is a small program that the unpickler executes to rebuild objects, and that
program can call any importable function, including `os.system`. So a `.bin` or `.pt` checkpoint
downloaded from the internet is executable code. Once model hubs made it routine to pull weights from
strangers, "load a model" became "run an untrusted program", and malicious pickles were found in the
wild.

There was also a performance problem. Unpickling builds tensors by copying data into freshly allocated
memory, so loading a large model needs the bytes read and copied, sometimes twice when moving to GPU,
and you cannot read one tensor without parsing the whole file. Partial loading, needed to shard a model
across GPUs or to load one layer for inspection, was awkward.

The naive fix, `torch.load(..., weights_only=True)`, restricts the unpickler to an allow-list of types
and is the right default for legacy files (recent PyTorch releases made it the default). But it still
runs a pickle interpreter over untrusted input, and its safety rests on the allow-list having no holes.
(`torch.load(..., mmap=True)` on the newer zip-based checkpoint format narrows the speed gap, but not
the security one.)
safetensors removes the interpreter entirely: the format has no way to express code, so there is nothing
to restrict.

## How it works
### File layout
1. **8 bytes:** an unsigned little-endian 64-bit integer N, the size of the header.
2. **N bytes:** a UTF-8 JSON object. Each key is a tensor name mapping to
   `{"dtype": "BF16", "shape": [4096, 4096], "data_offsets": [begin, end]}`; an optional
   `"__metadata__"` key holds string-to-string metadata such as the format or training info.
3. **The rest:** one contiguous byte buffer; each tensor occupies `[begin, end)` relative to the start
   of this buffer, stored row-major with no padding inside a tensor.

Every design choice follows from "the loader should only ever read". JSON can only describe data. The
header is parsed first and validated (offsets must be in bounds and not overlap, sizes must match
`dtype × shape`) before any tensor bytes are touched, which blocks malformed files that try to trigger
out-of-bounds reads.

### Zero-copy and lazy loading
Because each tensor sits at a known offset in raw form, the file can be memory-mapped: the operating
system maps it into the address space and pages in only the bytes actually read. `safe_open` returns a
handle where `get_tensor(name)` materialises one tensor and `get_slice(name)[a:b]` reads only a slice,
which is how tensor-parallel loaders give each GPU just its shard of a weight matrix without reading the
rest.

### Sharding
Large models are split into several files (`model-00001-of-00004.safetensors`, ...) plus
`model.safetensors.index.json`, which maps each tensor name to its shard. Loaders read the index,
then open only the shards they need.

### Framework neutrality
The format stores dtype and raw bytes, not Python objects, so the same file loads into PyTorch, NumPy,
JAX/Flax, TensorFlow or Rust code (`safetensors.torch`, `safetensors.numpy`, ...).

### What it deliberately does not store
No optimizer state objects, no model class, no arbitrary Python. Tensors that share memory (tied
embedding weights) cannot be saved as aliases; `save_file` refuses them, and helpers such as
`save_model` deduplicate them and rely on the loading code to re-tie.

## Example
Save, load lazily, and read the header by hand to see that it is just JSON.

```python
import json, struct, torch
from safetensors.torch import save_file, load_file
from safetensors import safe_open

weights = {
    "embed.weight": torch.randn(32000, 512, dtype=torch.bfloat16),
    "head.weight": torch.randn(10, 512),
}
save_file(weights, "model.safetensors", metadata={"format": "pt", "run": "exp-42"})

with open("model.safetensors", "rb") as f:            # no library needed to inspect
    n = struct.unpack("<Q", f.read(8))[0]
    header = json.loads(f.read(n))
e = header["embed.weight"]      # {'dtype': 'BF16', 'shape': [32000, 512], 'data_offsets': [begin, end]}
print(e["data_offsets"][1] - e["data_offsets"][0])     # 32768000 bytes

with safe_open("model.safetensors", framework="pt", device="cpu") as f:
    rows = f.get_slice("embed.weight")[:1000]          # reads 1000 rows, not the whole matrix
    print(rows.shape, f.metadata())

full = load_file("model.safetensors")                  # dict[str, Tensor]
```

The embedding is 32,000 × 512 × 2 bytes = 32,768,000 bytes, exactly `end - begin` in its
`data_offsets`; where each tensor starts depends on the writer's ordering, so read offsets, never assume them.

## Trade-offs
- No code execution on load removes a whole class of supply-chain attacks, but the format can only
  hold tensors; tokenizer files, configs and training state need their own (also non-executable) formats.
- Memory-mapping makes loading fast and lazy on local disk, but on network filesystems or object
  storage mounts page faults can be slow; copying to local NVMe first often wins for large models.
- Framework-neutral raw bytes make files portable, but you lose the convenience of pickling an entire
  object graph; full training checkpoints with optimizer and scheduler state still often use `torch.save`.
- safetensors is the standard for Transformers-style checkpoints, but llama.cpp-style local inference
  uses GGUF, which also embeds tokenizer metadata and quantized types; conversion is a deployment step.

## Pitfalls
- Assuming safetensors makes a model trustworthy: it only guarantees loading runs no code; the weights
  can still be backdoored, and `trust_remote_code` model classes can still execute arbitrary Python.
- Loading legacy `.bin` files with `weights_only=False` because a load error appeared: this re-enables
  arbitrary code execution, and the symptom you were fixing was the safety check working.
- Saving a model with tied weights via `save_file` directly: it raises an error about shared tensors,
  and naive workarounds that clone the tensors store the bytes twice; the tie then survives only if the
  loading code re-ties the parameters, which `save_model` / `load_model` handle for you.
- Forgetting the index file when copying sharded checkpoints: loaders fail to find tensors or load
  only part of the model, and the error names a missing key rather than a missing file.

## When to use
Use safetensors for any weights you publish, download, mirror or move between teams, and convert legacy
pickle checkpoints when you take them in. Make "no pickle from outside" a policy: CI or the model
registry rejects `.bin`/`.pt`/`.pkl` artifacts from external sources unless they are converted in a
sandbox. Keep `torch.save` only for internal training-resume checkpoints that hold optimizer state, and
never load those from untrusted locations. When loading time matters, measure time to first token after
a cold start with memory-mapped local files versus network storage. In interviews, be ready to explain
why pickle is unsafe and what memory mapping buys.

## Role lens
- **Engineer:** Saves and loads weights with safetensors, converts legacy checkpoints, and uses
  `safe_open` slices to inspect or shard weights without loading the whole file.
- **Architect:** Sets the model-artifact policy: allowed formats, conversion and scanning in a sandbox,
  provenance metadata, and storage layout that keeps cold-start loading fast.
- **FDE:** Meets customers whose security teams block pickle files, and shows that safetensors
  artifacts can be audited as plain data before they enter the customer's network.

## Check yourself
**Q:** A security reviewer says, "We switched to safetensors, so models from the hub are now safe to
run." What is right and what is wrong about that statement?
**A:** It is right that loading a safetensors file cannot execute code: the format is a JSON header plus
raw bytes, so the pickle-based remote-code-execution path is gone. It is wrong to conclude the model is
safe. Weights can encode backdoors or poisoned behaviour that only an evaluation reveals; repositories
can still require `trust_remote_code`, which runs Python model code; tokenizers and configs come from the
same source; and other files in the repo may still be pickles. A sound policy combines safetensors-only
weights, pinned revisions, disallowing remote code unless reviewed, provenance checks, and behavioural
evaluation before deployment.

## Quiz
### Q: What makes loading a pickle-based PyTorch checkpoint dangerous?
- [ ] Pickle files are not compressed, so they can exhaust disk space
- [x] Unpickling executes instructions that can call arbitrary Python functions
- [ ] Pickle stores tensors in a format GPUs cannot read
- [ ] Pickle files cannot be checksummed
> Pickle is a small program run by the unpickler to rebuild objects, and it may invoke any importable
> callable. safetensors stores only a JSON header and raw bytes, so there is no program to run.

### Q: How does safetensors let each GPU load only its shard of a large weight matrix?
- [ ] It splits every tensor into one file per GPU at save time
- [ ] It compresses tensors so they decompress faster
- [x] Tensors are stored raw at known offsets, so a slice can be read from a memory-mapped file
- [ ] It stores the model class, which knows how to shard itself
> Known offsets plus row-major raw storage let a loader compute and read only the bytes for the requested
> rows. Sharded files exist too, but slicing works within a single tensor.

### Q: Why might you still use `torch.save` inside a training job?
- [ ] Because safetensors cannot store bf16 tensors
- [ ] Because safetensors files cannot exceed 2 GB
- [x] To checkpoint optimizer and scheduler state, which are Python objects rather than plain tensor dicts
- [ ] Because safetensors loads slower than pickle in every case
> safetensors deliberately stores only named tensors. Training-resume checkpoints often include non-tensor
> state, so `torch.save` remains common internally, as long as those files never come from untrusted sources.
