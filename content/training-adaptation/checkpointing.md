---
id: checkpointing
module: training-adaptation
name: Checkpointing
kind: practice
difficulty: core
est_minutes: 14
status: agentic-verified
agent_relevance: useful
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
Checkpointing saves full training state (weights, optimizer, scheduler, RNG, data position) so a
fine-tune of your agent's model can resume, roll back and ship its best step, not its last. A 7B
checkpoint is ~100 GB. Not gradient checkpointing, nor agent-state checkpointers.

## For agent builders
- **Why it matters for agents.** The checkpoint you deploy behind an agent should be the one that wins on
  agent evals (tool-call validity, task success, refusals), which is often not the final step. Rollback
  after a bad model release only works if that earlier checkpoint was kept and registered.
- **Where it shows up in your stack.** `save_steps`, `save_total_limit` and `load_best_model_at_end` in
  Hugging Face and TRL trainers, intermediate checkpoints of hosted fine-tuning jobs, `safetensors`
  adapter files, and the model registry your agent's serving config points at.
- **What you control.** Which checkpoints are kept, which agent evals run on each, what metadata is
  stored (base model hash, data version, scores), and whether resume after a spot preemption is tested.
- **What you can skip.** Young–Daly derivations and sharded-checkpoint internals unless you run
  multi-GPU training. Do not confuse this with agent-run checkpointing (for example LangGraph
  checkpointers), which persists conversation state; that is `agent-reliability/durable-execution-and-checkpointing`.

## Why it exists
- **Long runs on failing hardware.** A single-GPU fine-tune runs for hours on a spot instance that can be
  reclaimed at any minute; a pretraining run occupies thousands of accelerators for weeks, where hardware
  faults are routine. The Llama 3 report counts hundreds of job interruptions in a 54-day pretraining
  window, most of them unexpected.
- **Without checkpoints, cost explodes.** Every failure restarts from zero. With independent failures the
  expected time to finish grows exponentially in run length over mean time between failures (roughly
  `MTBF·(e^(L/MTBF) − 1)`), so a run many times longer than the MTBF effectively never finishes.
- **With checkpoints, cost is bounded.** Saving every *T* minutes, a failure costs on average *T/2*
  minutes of recomputation plus the restart time.
- **Model selection and rollback.** The best model is usually not the last, because validation peaks and
  then overfitting sets in, so the peak's weights exist only if saved. A misbehaving deployed model needs
  a known-good predecessor, and a result nobody can reconstruct is not a result.
- **Weights at the end, or per epoch, fails all three.** It cannot resume correctly (optimizer state is
  gone), misses the peak, and leaves nothing to roll back to.

## How it works
### Two things called checkpoints in an agent stack
| | Training checkpoint (this lesson) | Agent-run checkpoint |
|---|---|---|
| What is saved | model weights, optimizer, scheduler, RNG, data position | the agent's state per step: messages, tool results, graph position |
| Keyed by | training step | thread or run id |
| Used for | resume a fine-tune, pick the best step, roll back a model | resume a long agent run, human-in-the-loop pauses, replay |
| Example | Hugging Face `Trainer` output directories | LangGraph checkpointers backed by memory, SQLite or Postgres |

- **The same rules carry over.** Save everything the next step depends on, write atomically, and test a
  resume on purpose before you rely on it.

### What a resumable training checkpoint contains
Resuming means continuing the exact same trajectory, so everything that influences the next step must be
saved:

| Component | Why it is needed |
|---|---|
| **Model weights** (training precision, plus fp32 master weights under mixed precision) | the parameters themselves |
| **Optimizer state** (AdamW: two fp32 moments per parameter) | fresh moments after resume cause a loss spike, because Adam's first steps after a reset are badly scaled |
| **Scheduler state and step counter** | the learning rate continues instead of restarting warmup |
| **Mixed-precision loss-scaler state**, where used | keeps the scale where it was |
| **RNG states** (Python, NumPy, PyTorch CPU and CUDA) and **data-loader position** (epoch, batches consumed, sampler seed) | dropout masks and example order match an uninterrupted run; no example skipped or seen twice |
| **Config and tokenizer** | the artefact loads without the training code |

- **Size arithmetic.** In mixed precision a checkpoint holds about 14 bytes per parameter: 2 for
  bf16/fp16 weights plus 12 for the fp32 master copy and the two moments.
- **ZeRO's often-quoted 16 bytes** also counts the 2-byte gradients, which live only in memory.
- **So a 7B model's resumable state is roughly 100 GB,** most of it fp32 optimizer state. A LoRA run's
  trainable state is far smaller, because only the adapter has optimizer moments.

### Selection, averaging and formats
- **Record the validation metric with each checkpoint,** so "best" is a lookup, not a re-evaluation. For
  an agent model, record agent eval scores too.
- **Average the last several checkpoints.** A cheap, old trick that often improves the final model; the
  original Transformer paper averaged the last 5 (base) or 20 (big) checkpoints.
- **Export as `safetensors`, not pickled `torch.save` files.** A pickle can execute arbitrary code on
  load, which is why `torch.load` defaults to `weights_only=True` since PyTorch 2.6 and why weights from
  untrusted sources should never be unpickled.
- **Adapters need their base.** The checkpoint is the adapter weights plus an exact reference (name and
  hash) to the base model they were trained on; without it the adapter is unusable.

### Frequency
- **The basic trade.** Expected loss from a failure is half the interval; each save costs write time
  during which GPUs may idle.
- **Example.** Saving 100 GB over a 1 GB/s link is about 100 seconds; saving every 30 minutes costs about
  5% of throughput in exchange for losing at most 30 minutes.
- **Asynchronous checkpointing** snapshots the state to host memory (a stall of seconds or less per GPU,
  bounded by device-to-host bandwidth) and writes to storage in the background while training continues.
- **Incremental or differential schemes** save only what changed; they pay off mainly when much of the
  state is static, such as large sparse embedding tables.

### Background: the Young–Daly interval
If a save stalls training for *C* seconds and failures arrive with mean time *M* between them, overhead
per unit time is roughly `C/T` (saving) plus `T/(2M)` (expected recomputation). Minimising the sum gives:

```
T_opt ≈ sqrt(2 · C · M)
```

| Save stall C | M | T_opt | Total overhead |
|---|---|---|---|
| 100 s (synchronous) | 10,800 s (a failure every three hours) | ≈ 1,470 s, about 25 minutes | roughly 14% |
| 5 s (asynchronous snapshot) | 10,800 s | about 5.5 minutes | near 3% |

- **Cheaper saves buy both** more frequent saves and less waste.
- **The square root** says save cost matters as much as failure rate.
- **Caveats.** The formula ignores restart time and assumes memoryless failures. Treat it as the start,
  then save more often near the end of a run, where losing work costs the most.

### Background: distributed layout
- **Data parallelism:** every rank holds the same state and one rank writes it.
- **ZeRO or FSDP:** optimizer state and weights are sharded, so each rank writes its own shard in
  parallel. Fast, but it ties the checkpoint to that world size and sharding scheme.
- **Resuming on a different GPU count** requires a resharding step; PyTorch Distributed Checkpoint and
  DeepSpeed provide consolidation utilities.
- **Keep a consolidated, weights-only export** alongside the sharded training state for evaluation and
  serving.

### Atomicity and retention
1. **Temp path, then rename.** A crash mid-write never leaves a truncated file masquerading as the latest
   checkpoint.
2. **Manifest with a hash** catches silent corruption.
3. **Flush before rename** on a local or network filesystem, or a power loss can leave the new name
   pointing at incomplete data.
4. **Object stores have no atomic directory rename.** Upload every shard first and write a small manifest
   or "latest" pointer last; readers trust only checkpoints whose manifest exists.
5. **Retention:** keep the last *k* for resume, the best on validation for selection, every checkpoint
   that was ever deployed for rollback, and periodic ones (every *n* steps or every epoch) for analysis.
   Delete the rest, because 100 GB per save fills any disk quickly.

## Diagram
One save and one resume: the GPU stalls only for the snapshot, the manifest is written last so a
half-written checkpoint is never trusted, and resume restores every piece of state, not just weights.

```mermaid
sequenceDiagram
    participant T as Training job
    participant H as Host memory
    participant S as Durable storage
    T->>H: Snapshot weights, optimizer, scheduler, RNG, data position
    Note over T: GPU stall is only this copy
    T->>T: Continue training
    H->>S: Write shards to temp paths in the background
    H->>S: Write manifest with hashes, then update latest pointer
    S->>S: Retention keeps last k, best on agent evals, deployed ones
    Note over T: Preemption or hardware fault
    T->>S: Read latest pointer, verify manifest hashes
    S-->>T: Full training state
    T->>T: Restore RNG, reseed sampler, skip consumed batches, resume
```

## Examples
### Picking the checkpoint to put behind the agent
Every saved checkpoint of a LoRA fine-tune is scored by the agent eval harness on a frozen suite. The
release takes the best task success among checkpoints that do not regress tool-call validity or refusals
beyond 2 points versus the base, and registers it with the metadata a rollback needs.

```python
import json

base = {"task_success": 0.70, "tool_call_validity": 0.95, "refusal_rate": 0.96}
results = {  # checkpoint -> scores from the agent eval harness on the frozen suite
    "ckpt-200": {"task_success": 0.74, "tool_call_validity": 0.95, "refusal_rate": 0.96},
    "ckpt-400": {"task_success": 0.79, "tool_call_validity": 0.94, "refusal_rate": 0.95},
    "ckpt-600": {"task_success": 0.81, "tool_call_validity": 0.89, "refusal_rate": 0.90},
}

def passes_gate(r, max_drop=0.02, guarded=("tool_call_validity", "refusal_rate")):
    return all(r[k] >= base[k] - max_drop for k in guarded)

eligible = {c: r for c, r in results.items() if passes_gate(r)}
best = max(eligible, key=lambda c: eligible[c]["task_success"])
record = {"checkpoint": best, "scores": eligible[best],
          "base_model": "org/base-8b-instruct", "base_sha256": "<hash of base weights>",
          "data_version": "agent-traces-v7", "format": "safetensors (adapter only)"}
print(json.dumps(record, indent=1))      # ckpt-400: the final step is not the one to ship
```

- **Reading it.** `ckpt-600` has the best task success but tool-call validity fell 6 points, so the
  release is `ckpt-400`. A keep-last-one retention rule would already have deleted it, so retention must
  protect the best gate-passing checkpoint until the release decision is made.

### Save and load the full state in PyTorch
A compact pair for a single-process loop: full state, atomic rename, keep-last-*k* retention, and RNG
restoration. The Hugging Face `Trainer` does the same through `save_steps`, `save_total_limit`,
`load_best_model_at_end`, `metric_for_best_model` and `resume_from_checkpoint`.

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

- **The part most hand-written loops forget:** the caller restores data order by reseeding the sampler
  with the saved epoch seed and skipping the consumed batches.
- **What the sketch leaves out: the flush.** `os.replace` makes the rename atomic but not durable against
  power loss. On a local or network disk, save through an open file and call `f.flush()` and
  `os.fsync(f.fileno())` before the rename.
- **Retention here is keep-last-*k* only.** A best-on-validation checkpoint must be copied to a separate
  name, or this loop will eventually delete it.

### Choosing the save interval
A runnable Young–Daly calculator. The third scenario is a spot-GPU fine-tune with a three-minute
synchronous write, preempted about every four hours.

```python
from math import sqrt

def young_daly(save_stall_s, mtbf_s):
    t = sqrt(2 * save_stall_s * mtbf_s)                  # optimal interval between saves
    overhead = save_stall_s / t + t / (2 * mtbf_s)        # saving + expected recomputation
    return t, overhead

scenarios = {                                             # (stall per save, mean time between failures)
    "sync save, failure every 3 h": (100, 10_800),
    "async snapshot, failure every 3 h": (5, 10_800),
    "spot GPUs: 3-min sync save, preempted every 4 h": (180, 14_400),
}
for name, (c, m) in scenarios.items():
    t, ov = young_daly(c, m)
    print(f"{name:48s} save every {t / 60:5.1f} min  overhead ~{ov:.0%}")
# sync save, failure every 3 h          save every  24.5 min  overhead ~14%
# async snapshot, failure every 3 h     save every   5.5 min  overhead ~3%
# spot GPUs: 3-min sync save, ...       save every  37.9 min  overhead ~16%
```

- **Reading it.** Saving once per three-hour epoch on the spot fleet would lose about 1.5 hours per
  preemption on average; cutting the stall is worth more than tuning the interval.

## Production patterns
- **Agent evals on every kept checkpoint.** Run the agent eval harness (tool-call validity, sandbox task
  success, refusals) on each saved checkpoint, store the scores with it, and promote the best that passes
  the regression gate. Monitor how often the promoted checkpoint is not the final step.
- **Registry entry per deployed model.** Register every checkpoint that goes behind an agent with step,
  agent eval scores, data version and, for adapters, the base model's name and hash; point the serving
  config at the registry version. Rollback is then a config change. Monitor time to roll back in drills.
- **Trainer-managed checkpoints for fine-tunes.** In the Hugging Face `Trainer`, set `save_steps` from the
  interval calculation, `save_total_limit` for retention, and `load_best_model_at_end=True` with
  `metric_for_best_model` so the best checkpoint is retained. Resume with
  `trainer.train(resume_from_checkpoint=True)`. Monitor save time per checkpoint and free disk.
- **Two artefacts per milestone.** Keep the full sharded training state for resume and a consolidated,
  weights-only `safetensors` export for evaluation and serving.
- **Manifest-last on object storage.** Upload all shards, then write a manifest listing files and hashes,
  then update a "latest" pointer. The resume logic reads only checkpoints with a valid manifest. Monitor
  for orphaned shard directories without a manifest.
- **Kill-and-resume drill.** Before a long run, kill the job on purpose mid-epoch and check that the
  resumed loss curve overlays the uninterrupted one. For FSDP or ZeRO jobs, also check asynchronous
  sharded saves through PyTorch Distributed Checkpoint or DeepSpeed. Monitor recovery time after the
  simulated preemption.

## Trade-offs
- **Frequent saves vs write time and storage.** They bound the work lost to a failure; the balance moves
  toward more frequent saves as failure rates rise, and toward fewer as the save becomes slow relative to
  the interval.
- **Evaluating every checkpoint vs eval cost.** Running agent tasks on each checkpoint finds the best one
  but costs sandbox time and tokens; evaluate a cheap tool-call suite on all and the full agent suite on
  the top few.
- **Sharded vs consolidated.** Sharded checkpoints write in parallel and avoid gathering 100 GB onto one
  rank, but tie the artefact to a world size and need a resharding step to resume elsewhere.
- **Weights-only vs full state.** Weights-only exports are small and serve directly but cannot resume
  training; full state resumes exactly but is several times larger, so most teams keep both.
- **Many checkpoints vs storage bill.** Keeping many enables rollback, averaging and post-hoc analysis,
  but at 100 GB each storage becomes the constraint, which is what retention policies trade against.
- **Asynchronous vs complexity.** It hides write latency but needs host-memory headroom for a full copy
  of the state and adds a failure mode in which the background write itself fails silently.

## Pitfalls
- **Shipping the last checkpoint by default.** Symptom: the new agent model has better task loss but more
  malformed tool calls than an earlier step would have. Fix: select on agent evals across checkpoints.
- **Saving only weights and resuming with a fresh optimizer.** Symptom: a loss spike immediately after
  resume and a resumed run that never rejoins the original curve. Fix: save optimizer, scheduler and
  scaler state in every checkpoint.
- **Not saving the data-loader position.** Symptom: examples seen twice or never in the resumed epoch, and
  a loss curve after resume that differs from an uninterrupted run. Fix: save epoch, batches consumed and
  sampler seed.
- **Writing checkpoints in place without a temporary file and rename.** Symptom: a truncated latest
  checkpoint after a crash and no resumable state at all. Fix: temp path, flush, rename (or manifest last).
- **Letting the checkpoint directory fill the disk.** Symptom: the run dies during a save, often taking
  the latest good checkpoint with it. Fix: a retention policy and a free-disk alert.
- **A retention rule that deletes the best or the deployed checkpoint because it is old.** Symptom: a
  final model worse than one that existed an hour earlier, or no rollback target. The Hugging Face
  `Trainer` retains the best checkpoint when `load_best_model_at_end` is set; hand-written loops must do
  the same explicitly.
- **Saving adapters without the base model's exact version.** Symptom: an adapter that loads without error
  onto a different base and produces garbage. Fix: store the base model's name and hash with the adapter.
- **Loading pickled checkpoints from untrusted sources.** Symptom: arbitrary code running on load. Fix:
  `safetensors`, and `weights_only=True`.

## When to use
- **Always checkpoint** any training run longer than a few minutes; keep the best checkpoint by
  validation and agent eval metrics for every run whose output will be deployed behind an agent.
- **Decide:** the interval from failure rate and save cost, the format from how the run is parallelised
  and who consumes the artefact, and retention from storage and the need for rollback.
- **Measure:** save time as a share of step time, and recovery time after a simulated preemption.
- **Day to day** it is a configuration task in the training framework; in a platform role it becomes
  storage layout, model registry integration and promotion rules.
- **Interviews:** list what a resumable checkpoint must contain and explain why weights alone are
  insufficient.

## Role lens
- **Engineer:** Configures save interval, retention and best-checkpoint selection in the trainer, tests a
  resume after a kill, and exports weights-only `safetensors` for evaluation and serving.
- **Architect:** Owns checkpoint storage layout, sharding strategy, the registry that records which
  checkpoint became which deployed model, and the rollback path to a previous one.
- **FDE:** On a customer's cluster, verifies resume works under their preemption policy and that
  checkpoints and adapter references are stored where the customer's governance requires.

## Check yourself
**Q:** A full fine-tune of a 7B model runs on spot GPUs that are preempted about every four hours. The job
saves `model.state_dict()` once per epoch (every three hours) with a three-minute synchronous write. After
a preemption at step 3,200 the resumed loss jumps from 0.9 to 1.6 and never rejoins the original curve.
Diagnose both problems and redesign the scheme.
**A:** Two separate failures. Correctness: a weights-only checkpoint restarts AdamW with empty moments,
so the first steps are badly scaled; the scheduler restarts warmup and resets its decay horizon; and the
sampler restarts, so the epoch's order is lost. The checkpoint must also hold both Adam moments and fp32
master weights, scheduler state and global step, scaler state if used, RNG states and the data position,
roughly 100 GB in total, written atomically with a manifest. Frequency: with failures every four hours,
a three-hour interval loses about 1.5 hours per preemption on average, and often the run is killed before
its first save. Young–Daly gives sqrt(2 × 180 s × 14,400 s) ≈ 2,280 s, about 38 minutes, at roughly 16%
total overhead; moving to an asynchronous snapshot with a stall of ten seconds or so brings the interval
under ten minutes and the overhead to a few percent. Finally, test the design by killing the job on
purpose and checking that the resumed loss curve overlays the uninterrupted one.

## Interview questions
### What must a resumable training checkpoint contain, and why is a weights file not enough?
- **Everything that influences the next step:** model weights (plus fp32 master weights under mixed
  precision), optimizer moments, scheduler state and step counter, loss-scaler state, RNG states and
  data-loader position, plus config and tokenizer.
- **Why weights alone fail:** fresh Adam moments make the first steps badly scaled, the scheduler
  restarts warmup, and the sampler loses the epoch's order, so the run never rejoins its original curve.

### A new fine-tuned model behind your agent starts producing bad tool calls in production. How does your checkpoint setup let you recover?
- **Rollback first:** the previously deployed checkpoint is in the registry with its base model hash and
  scores, so pointing serving back at it is a config change, not a retrain.
- **Then diagnose:** compare agent eval scores across the new run's kept checkpoints; an earlier step that
  passes the tool-call gate may be shippable without retraining.
- **Prevent repeats:** gate promotion on tool-call validity across checkpoints and never let retention
  delete a deployed checkpoint.

### How is a training checkpoint different from a LangGraph checkpointer, and what carries over?
- **Different state:** a training checkpoint holds weights, optimizer, scheduler, RNG and data position
  keyed by step; an agent checkpointer holds the run's messages, tool results and graph position keyed by
  thread, so a long agent run can pause and resume.
- **Same discipline:** save everything the next step depends on, write atomically to durable storage, and
  test resume on purpose.
- **Different consumers:** one is read by the trainer and registry, the other by the agent runtime on each
  step.

### How big is the checkpoint for a 7B full fine-tune, and how often would you save it?
- **Size:** about 14 bytes per parameter in mixed precision (2 for bf16 weights plus 12 for the fp32
  master copy and two moments), so roughly 100 GB.
- **Interval:** Young–Daly, `T_opt ≈ sqrt(2 · C · M)`. A 100 s synchronous save with a failure every three
  hours gives about 25 minutes and roughly 14% overhead.
- **Better lever:** an asynchronous snapshot with a 5 s stall cuts the interval to about 5.5 minutes and
  overhead to near 3%.

### A crash during a save left your latest checkpoint truncated. How do you design so this cannot happen?
- **Local or network filesystem:** write to a temp path, flush, then atomically rename, so the final name
  only ever points at a complete file.
- **Object stores have no atomic directory rename:** upload every shard, then write a manifest with hashes,
  then update a "latest" pointer. Resume trusts only checkpoints with a valid manifest.
- **Monitor** for orphaned shard directories and failed background writes; and keep a consolidated
  weights-only export, since sharded state is tied to one world size.

### Why should you avoid loading `.pt` checkpoints from an untrusted source?
- **They are pickles:** a pickle can execute arbitrary code on load.
- **That is why** `torch.load` defaults to `weights_only=True` since PyTorch 2.6, and why weights from
  untrusted sources should be exported and loaded as `safetensors`.
- **Only your own trusted training state** should be loaded with `weights_only=False`, as in the resume
  example.

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

## Use cases & projects
### Use cases
- **Shipping the best step, not the last.** Running agent evals on each saved checkpoint picks the model
  that wins on tool-call validity and task success, which is often mid-run.
- **Fast rollback after a bad release.** A registered earlier checkpoint lets the serving config point
  back to the known-good model in minutes instead of retraining.
- **Fine-tuning on spot or preemptible GPUs.** Full training state lets a preempted run resume where it
  stopped instead of starting over.
- **Reproducible model lineage.** Storing base model hash, data version and eval scores with each
  checkpoint answers which data produced the model behind an agent incident.

### Mini project
**Checkpoint, resume and select for an agent fine-tune (1-2 hours).**
- **Goal:** prove that a fine-tune can survive a kill and that you ship the best checkpoint by agent evals.
- **Steps:** run a small LoRA fine-tune on tool-calling trajectories with periodic saves and a cap on kept
  checkpoints; kill the process mid-run and resume from the latest checkpoint; confirm the loss curve
  continues smoothly; evaluate every kept checkpoint on held-out tool-call validity; write a metadata file
  per checkpoint.
- **Done when:** you have a resumed run whose loss matches an uninterrupted run, a table of eval scores
  per checkpoint, and the chosen adapter with its metadata (base model, data version, step, scores).
