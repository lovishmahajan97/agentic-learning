---
id: checkpointing
module: training-adaptation
name: Checkpointing
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - training-adaptation/gradient-checkpointing
  - training-adaptation/distributed-training
  - training-adaptation/overfitting
  - training-adaptation/tooling
  - llmops-platform/experiment-tracking
  - llmops-platform/model-registry
  - llmops-platform/rollback
  - agent-reliability/durable-execution-and-checkpointing
sources:
  - Mohan et al., 2021 — CheckFreq: Frequent, Fine-Grained DNN Checkpointing
  - Rajbhandari et al., 2020 — ZeRO: Memory Optimizations Toward Training Trillion Parameter Models
  - Vaswani et al., 2017 — Attention Is All You Need
  - Official docs: PyTorch torch.save / Distributed Checkpoint; Hugging Face Trainer
---

## TL;DR
Checkpointing saves the complete training state (weights, optimizer moments, scheduler, RNG and data
position) to durable storage at intervals, so a run can resume after a failure, be rolled back, and be
selected from: the best checkpoint on validation is early stopping made concrete. Long runs fail, at
cluster scale they fail often, and a full training checkpoint of a 7B model is on the order of 100 GB,
so frequency, format, sharding and retention are engineering decisions. It is unrelated to gradient
checkpointing, which recomputes activations to save memory.

## Why it exists
Training is a long, stateful computation on hardware that fails. A single-GPU fine-tune runs for hours on
a spot instance that can be reclaimed at any minute; a pretraining run occupies thousands of accelerators
for weeks, and with that many parts, hardware faults are routine rather than exceptional. The Llama 3
report, for example, counts hundreds of job interruptions during a 54-day pretraining window, most of
them unexpected. Without checkpoints every failure restarts from zero; with independent failures the
expected time to finish grows exponentially in the ratio of run length to mean time between failures
(roughly `MTBF·(e^(L/MTBF) − 1)`), so a run many times longer than the MTBF effectively never finishes. With checkpoints every *T*
minutes, a failure costs on average *T/2* minutes of recomputation plus the restart time.

Resumability is the forcing reason; two more follow from having the artefacts. Model selection: the best
model is usually not the last one, because validation performance peaks and then overfitting sets in, so
you need the weights from the peak step, which only exist if you saved them. Rollback and reproducibility:
a deployed model that misbehaves needs a known-good predecessor, and a result nobody can reconstruct is
not a result. The naive alternative, saving only the weights at the end or at epoch boundaries, fails on
all three counts: it cannot resume correctly (the optimizer state is gone), misses the peak, and leaves
nothing to roll back to.

## How it works
### What a resumable checkpoint contains
Resuming means continuing the exact same trajectory, so everything that influences the next step must be
saved:
- **Model weights**, in the training precision plus fp32 master weights under mixed precision.
- **Optimizer state.** AdamW keeps two fp32 moments per parameter. In mixed precision a checkpoint holds
  about 14 bytes per parameter: 2 for bf16/fp16 weights plus 12 for the fp32 master copy and the two
  moments (ZeRO's often-quoted 16 bytes also counts the 2-byte gradients, which live only in memory). A 7B
  model's resumable state is therefore roughly 100 GB, most of it fp32 optimizer state. Dropping it and
  resuming with fresh moments produces a loss spike, because Adam's first steps after a reset are badly scaled.
- **Scheduler state and step counter**, so the learning rate continues from where it was instead of
  restarting warmup.
- **Mixed-precision loss-scaler state**, where used.
- **RNG states** (Python, NumPy, PyTorch CPU and CUDA) and the **data-loader position** (epoch, batches
  consumed, sampler seed), so dropout masks and example order after resume match an uninterrupted run and
  no example is skipped or seen twice.
- **Config and tokenizer**, so the artefact can be loaded without the training code.

### Frequency
The expected loss from a failure is half the interval, and each save costs write time during which the
GPUs may idle. Saving a 100 GB state over a 1 GB/s link is about 100 seconds; saving every 30 minutes
costs about 5% of throughput in exchange for losing at most 30 minutes. Two techniques shrink the cost:
asynchronous checkpointing snapshots the state to host memory (a stall of seconds or less per GPU, bounded
by device-to-host bandwidth) and writes to storage in the background while training continues; incremental
or differential schemes save only what changed, which pays off mainly when much of the state is static,
such as large sparse embedding tables. Pick the interval
from failure rate, save cost and the value of lost minutes, and save more often near the end of a run.

### Distributed layout
Under data parallelism every rank holds the same state and one rank writes it. Under ZeRO or FSDP the
optimizer state and weights are sharded, so each rank writes its own shard in parallel, which is fast but
ties the checkpoint to that world size and sharding scheme. Resuming on a different number of GPUs
requires a resharding step; libraries such as PyTorch Distributed Checkpoint and DeepSpeed provide
consolidation utilities. Keep a consolidated, weights-only export alongside the sharded training state for
evaluation and serving.

### Atomicity and retention
Write to a temporary path and rename on completion, so a crash mid-write never leaves a truncated file
masquerading as the latest checkpoint; a manifest with a hash catches silent corruption. Retention policy:
keep the last *k* for resume, the best on validation for selection, and periodic ones (every *n* steps or
every epoch) for analysis, and delete the rest, because 100 GB per save fills any disk quickly.

### Selection, averaging and formats
Record the validation metric with each checkpoint so "best" is a lookup, not a re-evaluation. Averaging
the weights of the last several checkpoints is a cheap, old trick that often improves the final model;
the original Transformer paper averaged the last 5 (base) or 20 (big) checkpoints. For export, prefer
`safetensors` over pickled `torch.save` files: a pickle can execute arbitrary code on load, which is why
`torch.load` defaults to `weights_only=True` since PyTorch 2.6 and why weights from untrusted sources should never be
unpickled. With adapters, the checkpoint is the adapter weights plus an exact reference (name and hash)
to the base model they were trained on; without that reference the adapter is unusable.

## Example
A compact save/load pair for a single-process PyTorch loop: full state, atomic rename, keep-last-*k*
retention, and RNG restoration. The Hugging Face `Trainer` does the same through `save_steps`,
`save_total_limit`, `load_best_model_at_end`, `metric_for_best_model` and `resume_from_checkpoint`.

```python
import os, random, numpy as np, torch

def save_checkpoint(dirpath, step, model, opt, sched, scaler, sampler_state, keep_last=3):
    state = {"step": step, "model": model.state_dict(), "optimizer": opt.state_dict(),
             "scheduler": sched.state_dict(), "scaler": scaler.state_dict() if scaler else None,
             "sampler": sampler_state,                                   # epoch, batches consumed, seed
             "rng": {"torch": torch.get_rng_state(), "cuda": torch.cuda.get_rng_state_all(),
                     "python": random.getstate(), "numpy": np.random.get_state()}}
    tmp, final = f"{dirpath}/ckpt-{step}.pt.tmp", f"{dirpath}/ckpt-{step}.pt"
    torch.save(state, tmp)
    os.replace(tmp, final)             # atomic rename: a crash mid-write never leaves a half file
    saved = sorted((int(f.split("-")[1].split(".")[0]), f) for f in os.listdir(dirpath) if f.endswith(".pt"))
    for _, old in saved[:-keep_last]:  # retention: keep the newest k; keep "best" elsewhere
        os.remove(f"{dirpath}/{old}")

def load_checkpoint(path, model, opt, sched, scaler):
    state = torch.load(path, map_location="cpu", weights_only=False)  # trusted file only: it is a pickle
    model.load_state_dict(state["model"]); opt.load_state_dict(state["optimizer"])
    sched.load_state_dict(state["scheduler"])
    if scaler and state["scaler"]:
        scaler.load_state_dict(state["scaler"])
    torch.set_rng_state(state["rng"]["torch"]); torch.cuda.set_rng_state_all(state["rng"]["cuda"])
    random.setstate(state["rng"]["python"]); np.random.set_state(state["rng"]["numpy"])
    return state["step"], state["sampler"]          # caller fast-forwards the data loader from these

# In the loop:  if step % SAVE_EVERY == 0: save_checkpoint(CKPT_DIR, step, model, opt, sched, scaler,
#                   {"epoch": epoch, "consumed": batches_this_epoch, "seed": 1234 + epoch})
# On startup:   step, sampler = load_checkpoint(latest, model, opt, sched, scaler); skip `consumed` batches.
```

The caller restores the data order by reseeding the sampler with the saved epoch seed and skipping the
consumed batches, which is the part most hand-written loops forget.

## Trade-offs
- Frequent saves bound the work lost to a failure but cost write time and storage; the balance moves toward
  more frequent saves as failure rates rise and toward fewer as the save itself becomes slow relative to
  the interval.
- Sharded checkpoints write in parallel and avoid gathering 100 GB onto one rank, but tie the artefact to a
  world size and need a resharding step to resume elsewhere.
- Weights-only exports are small and serve directly but cannot resume training; full state resumes
  exactly but is several times larger, so most teams keep both.
- Keeping many checkpoints enables rollback, averaging and post-hoc analysis, but at 100 GB each the
  storage bill becomes the constraint, which is what retention policies trade against.
- Asynchronous checkpointing hides the write latency but needs host-memory headroom for a full copy of
  the state and adds a failure mode in which the background write itself fails silently.

## Pitfalls
- Saving only weights and resuming with a fresh optimizer; the symptom is a loss spike immediately after
  resume and a resumed run that never rejoins the original curve.
- Not saving the data-loader position; the symptom is examples seen twice or never in the resumed epoch,
  and a loss curve after resume that differs from an uninterrupted run.
- Writing checkpoints in place without a temporary file and rename; the symptom is a truncated latest
  checkpoint after a crash and no resumable state at all.
- Letting the checkpoint directory fill the disk; the symptom is the run dying during a save, often
  taking the latest good checkpoint with it.
- A retention rule that deletes the best checkpoint because it is old; the symptom is a final model worse
  than one that existed an hour earlier. The Hugging Face `Trainer` retains the best checkpoint when
  `load_best_model_at_end` is set; hand-written loops must do the same explicitly.
- Saving adapters without recording the base model's exact version; the symptom is an adapter that loads
  without error onto a different base and produces garbage.
- Loading pickled checkpoints from untrusted sources; the symptom is arbitrary code running on load.

## When to use
Every training run longer than a few minutes should checkpoint, and every run whose output will be
deployed should keep the best checkpoint by validation metric. Decide the interval from failure rate and
save cost, decide the format from how the run is parallelised and who consumes the artefact, and decide
retention from storage and the need for rollback. Measure save time as a share of step time and the
recovery time after a simulated preemption. In day-to-day work this is a configuration task in the
training framework; in a platform role it becomes storage layout, model registry integration and
promotion rules. In interviews, expect to list what a resumable checkpoint must contain and to explain
why weights alone are insufficient.

## Role lens
- **Engineer:** Configures save interval, retention and best-checkpoint selection in the trainer, tests a
  resume after a kill, and exports weights-only `safetensors` for evaluation and serving.
- **Architect:** Owns checkpoint storage layout, sharding strategy, the registry that records which
  checkpoint became which deployed model, and the rollback path to a previous one.
- **FDE:** On a customer's cluster, verifies resume works under their preemption policy and that
  checkpoints and adapter references are stored where the customer's governance requires.

## Check yourself
**Q:** A fine-tuning job on spot GPUs was preempted at step 3,200 and resumed from a checkpoint that
contained only `model.state_dict()`. The loss jumped from 0.9 to 1.6 on resume and never recovered the
original curve. What went wrong and what should the checkpoint have contained?
**A:** The resumed run restarted AdamW with empty moment estimates, so its first steps were badly scaled
and large, and the learning-rate scheduler restarted from step zero, re-running warmup and resetting the
decay horizon, so the trajectory diverged from the original. The sampler also restarted, so the epoch's
data order and position were lost. A resumable checkpoint needs the model weights, the optimizer state
including both Adam moments, the scheduler state and global step, the mixed-precision scaler state if
used, the RNG states, and the data-loader position (epoch, batches consumed, seed), written atomically
via rename and retained under a policy that keeps the latest few and the best by validation metric.

## Quiz
### Q: Why does resuming from a weights-only checkpoint typically produce a loss spike?
- [ ] The weights are saved in lower precision and lose information
- [x] The optimizer's moment estimates and the scheduler step are reset, so the first updates are badly scaled and warmup restarts
- [ ] The data loader starts from the first example of the dataset again
- [ ] Gradient checkpointing must be re-enabled after a resume
> Adam's steps depend on accumulated moments and the schedule position; fresh moments and a restarted
> warmup change the step size abruptly. Data order matters for reproducibility but does not by itself
> cause a spike.

### Q: Which practice prevents a crash during a save from destroying the latest usable checkpoint?
- [ ] Saving checkpoints more frequently
- [ ] Using sharded checkpoints across ranks
- [x] Writing to a temporary file and atomically renaming it on completion
- [ ] Compressing the checkpoint before writing
> A crash mid-write leaves a truncated file; writing to a temporary path and renaming means the final
> name only ever points at a complete file. Frequency and sharding change cost, not atomicity.

### Q: How is checkpointing related to gradient checkpointing?
- [ ] Gradient checkpointing saves gradients to disk between steps
- [ ] They are the same feature enabled by one flag in most trainers
- [x] They are unrelated: checkpointing persists training state, gradient checkpointing recomputes activations to save memory
- [ ] Gradient checkpointing is the sharded form of checkpointing
> The shared name is historical. One is about durability and resume; the other trades compute for
> activation memory during backward and writes nothing to storage.
