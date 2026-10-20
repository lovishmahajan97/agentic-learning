---
id: experiment-tracking
module: llmops-platform
name: Experiment tracking (MLflow, W&B)
kind: tool
difficulty: core
est_minutes: 14
status: reviewed
related:
  - llmops-platform/model-registry
  - llmops-platform/reproducible-evaluations
  - training-adaptation/supervised-fine-tuning
  - data-architecture/model-and-dataset-provenance
  - observability/langfuse
  - llmops-platform/evaluation-frameworks
  - evals/error-analysis
sources:
  - Official docs: MLflow
  - Official docs: Weights & Biases
  - Pineau et al., 2021 — Improving Reproducibility in Machine Learning Research
---

## TL;DR
Experiment tracking records every run of a training or evaluation job — fixed parameters, code and data
versions, step-indexed metrics and output artifacts — in a queryable store so runs can be compared,
reproduced and promoted. MLflow and Weights & Biases are the two common tools; they share the same data
model and differ mainly in hosting (self-hosted open source versus SaaS-first), UI depth, and ecosystem
(MLflow's registry and model packaging; W&B's sweeps, artifact lineage graph and Weave for LLM traces).

## Why it exists
Any team that trains, fine-tunes or evaluates models produces runs faster than it can remember them.
The first week it is a spreadsheet of learning rates and scores; by the third week the spreadsheet is
missing the run everyone is talking about, nobody knows which checkpoint produced the 0.91, and the
config that got it was edited in place since. Fine-tuning makes this combinatorial: LoRA rank, learning
rate, data mix, epochs and base model revision interact, and a result without its full configuration
cannot be reproduced or trusted.

LLM application work has the same shape without the training loop. Every evaluation run is an
experiment: a prompt version, a model snapshot, a dataset version and a judge configuration go in, and
scores come out. Teams that keep those results in notebook cells and Slack screenshots cannot answer
"did the last three prompt changes help on the refund slice" without re-running everything.

The naive alternative, printing to logs and encoding settings in file names, fails because comparison
and lineage are the whole job: you need to filter a thousand runs by parameter, overlay metric curves,
and follow a model version back to the run, data and commit that produced it. That is a database with
a specific schema and a UI on top, which is what a tracker is.

## How it works
### The data model both tools share
An **experiment** (MLflow) or **project** (W&B) groups **runs**. A run has **parameters** fixed at start
(treated as immutable, because changing them mid-run would make comparisons meaningless; MLflow rejects
re-logging a param with a different value, while W&B lets `config` be updated but that is an escape hatch,
not the model), **metrics** logged as time
series against a step or epoch (so you can see divergence or over-fitting at step *k*, not just the
final number), **artifacts** which are files (checkpoints, per-item eval outputs, plots, configs),
**tags** for free-form filtering, and automatically captured context: git commit, command line, Python
environment and, where enabled, system metrics such as GPU utilisation (W&B records them by default;
MLflow does once system-metrics logging is switched on). Runs can be nested for hyperparameter sweeps.
Everything is queryable, so "all runs where `lora_r=16` sorted by `eval_acc`" is one call.

### What happens when you call `log`
Both clients are thin writers to a store, and their transport explains several sharp edges. MLflow's
client sends each logging call to the tracking server over REST (or writes to a local `mlruns`
directory with no server), and the server writes rows into its SQL backend: params as key/value
*strings*, metrics as `(key, value, timestamp, step)` rows, plus a "latest value" per metric that the
run table and search use. Batch calls (`log_metrics`, `log_params`) exist *because* one HTTP round trip
per scalar at every training step is slow; recent versions also offer asynchronous logging. W&B's
`init` starts a background process that buffers everything to a local run directory and streams it to
the server, *because* a training loop must never block on the network; the same design gives offline
mode (`WANDB_MODE=offline`, uploaded later with `wandb sync`) for air-gapped clusters. Artifacts take a
separate path: large files go to the artifact store (bucket or W&B storage) and only a reference lands
in the run.

Three consequences follow. MLflow params are strings, so a filter such as `params.lr > 0.001` is not a
numeric comparison; put numbers you will want to range-query into metrics or tags you control. MLflow's
search and run table sort on a metric's *last* logged value, not its best, so log a separate
`best_eval_acc` if that is what you will rank by. W&B requires the `step` passed to `log` to increase
monotonically; a call with an earlier step is dropped with a warning, which bites when resuming a run or
logging evaluation results after the training loop has advanced.

### MLflow
MLflow is Apache-licensed open source that started at Databricks. Its tracking server has a backend
store (a SQL database for runs, params and metrics) and an artifact store (local disk, S3, GCS or Azure
Blob for files), which is why self-hosting is a container plus a database plus a bucket. Around tracking
it adds a model registry with versions and aliases, a packaging format with "flavours" (`pyfunc`,
`transformers`, `sklearn` and others) that lets any registered model be loaded with one call, and
autologging integrations that capture params and metrics from common training frameworks without
explicit calls. Through the later 2.x releases and the 3.x line it added GenAI features — tracing, LLM
evaluation APIs and a prompt registry — so an LLM team can keep evals and prompts in the same store as
training runs. Databricks offers a managed version, and some cloud ML platforms (Amazon SageMaker, for
example) offer managed MLflow tracking servers; otherwise you self-host.

### Weights & Biases
W&B is SaaS-first with a self-hosted server option for data-residency requirements. The API is
`wandb.init(project=..., config=...)`, `wandb.log({...}, step=...)` and `run.finish()`. Its distinguishing
pieces are **Artifacts**, versioned datasets and models with an automatic lineage graph (which run
consumed which artifact version and produced which), **Sweeps**, a built-in hyperparameter search
orchestrator that schedules agents across machines, **Tables** for per-example analysis inside the UI,
**Reports** for sharing, and **Weave**, its toolkit for tracing and evaluating LLM applications. The UI
is the usual reason teams pick it; the usual reasons they do not are cost per seat and the fact that
metrics and artifacts leave the network unless you self-host.

### How an LLM team uses either
A fine-tuning run logs `base_model`, `base_revision`, `lora_r`, `lr`, `train_set` version hash and
`git_sha` as params, train and eval loss per step as metrics, the adapter directory and the per-item
eval outputs as artifacts, then registers the adapter as a model version so lineage flows into the
registry. An evaluation run logs prompt version, model snapshot, dataset hash and judge config as
params, aggregate and per-slice scores as metrics, and the per-item outputs file as an artifact so error
analysis can start from the run page. The tracker becomes the place where "what did we try and what
happened" is answered.

### Choosing
Data residency or regulated environments push toward self-hosted MLflow or a W&B server. A team already
on Databricks gets MLflow for free. A research-heavy team that runs sweeps daily usually prefers W&B's
UI and sweep orchestration. LLM application teams increasingly keep traces and evals in an observability
platform such as Langfuse or LangSmith and use the tracker only for training; the important decision is
one system of record per artifact class, not one tool for everything.

## Example
The same LoRA fine-tuning run logged two ways. Parameters are fixed at the start, metrics are
step-indexed, artifacts are files, and the dataset and code versions are logged as params so the run is
reproducible. `train_and_eval` is your loop, yielding per-step metrics; the two halves are alternatives,
shown together only for comparison (as written, the loop would train twice).

```python
import mlflow, wandb, subprocess

cfg = {"base": "meta-llama/Llama-3.1-8B-Instruct", "base_revision": "<hf commit sha>",
       "lora_r": 16, "lr": 2e-4, "epochs": 2,
       "train_set": "tickets-v3@9c1e04", "golden_set": "golden-v8@5c2e91"}
cfg["git_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()

# --- MLflow: self-hosted tracking server; runs grouped by experiment -------------------------
mlflow.set_tracking_uri("http://mlflow.internal:5000")
mlflow.set_experiment("support-classifier")
with mlflow.start_run(run_name="lora-r16-lr2e-4") as run:
    mlflow.log_params(cfg)                                            # immutable for the run's lifetime
    for step, (train_loss, eval_acc) in enumerate(train_and_eval(cfg)):
        mlflow.log_metrics({"train_loss": train_loss, "eval_acc": eval_acc}, step=step)
    mlflow.log_artifact("outputs/eval_items.jsonl")                   # per-item outputs for error analysis
    mlflow.log_artifacts("outputs/adapter", artifact_path="model")    # raw files; use a flavour's log_model for pyfunc loading
    print("register with run id:", run.info.run_id)

# Comparison is the point: params are strings (quote them); ordering uses each metric's LAST value
best = mlflow.search_runs(experiment_names=["support-classifier"], filter_string="params.lora_r = '16'",
                          order_by=["metrics.eval_acc DESC"], max_results=5)

# --- W&B: SaaS or self-hosted server; config + log + a versioned Artifact with lineage --------
run = wandb.init(project="support-classifier", name="lora-r16-lr2e-4", config=cfg)
for step, (train_loss, eval_acc) in enumerate(train_and_eval(cfg)):
    wandb.log({"train_loss": train_loss, "eval_acc": eval_acc}, step=step)
art = wandb.Artifact("support-classifier-adapter", type="model", metadata=cfg)
art.add_dir("outputs/adapter")
art.add_file("outputs/eval_items.jsonl")
run.log_artifact(art)                                                 # becomes v0, v1, ... in the lineage graph
run.finish()
```

## Trade-offs
- Self-hosted MLflow keeps data in your network and costs nothing in licences, but you operate a
  server, a database and an artifact store, and the UI is thinner than W&B's.
- W&B's hosted UI, sweeps and lineage graph save engineering time, but per-seat pricing and data leaving
  the network flip the decision in regulated environments unless you run the self-hosted server.
- Logging every per-item output as an artifact makes error analysis one click away, at the cost of
  storage that grows with runs times dataset size; sample or compress once eval sets reach six figures.
- Autologging captures params and metrics with zero code, but it also captures whatever the framework
  decides is interesting, which can bury the three numbers you care about under three hundred.
- Keeping LLM eval runs in the training tracker unifies history, whereas keeping them in the
  observability platform links them to traces; the split that wins depends on whether your evals start
  from production traces or from training jobs.

## Pitfalls
- Logging the final metric only; the symptom is a run that looks best on its last value while its curve
  shows it diverged and recovered by luck, which the next seed will not repeat.
- Forgetting the data version and git commit in params; the symptom is a top run that nobody can
  reproduce because the training set was regenerated since.
- Mutating a config dict after `log_params` and assuming the tracker noticed; the symptom is runs whose
  recorded learning rate differs from the one actually used.
- Treating the tracker as a backup for weights; the symptom is a bucket bill that doubles monthly and a
  cleanup that deletes the one checkpoint the registry still points to.
- Ranking runs by a metric whose final value is not its best, such as eval accuracy after over-fitting;
  the symptom is the run table hiding the checkpoint that actually peaked, because sorting uses the last value.
- Resuming a W&B run or logging post-training evals with a step lower than the last one logged; the symptom
  is eval points that silently never appear on the chart, with only a warning in the console.
- Running evals outside the tracker "because it is just a prompt change"; the symptom is a team arguing
  from memory about whether version 11 or 12 did better on the hard slice.

## When to use
Use a tracker from the first fine-tuning run or the first eval you intend to compare against a later
one, which is essentially always. Choose MLflow when you need self-hosting, already run Databricks, or
want the registry and model packaging in the same system; choose W&B when sweep orchestration, the UI
and artifact lineage are worth the subscription and data can leave your network or you will run the
self-hosted server. Measure whether every registered model version links to a run, whether every run
has a data version and commit, and how long it takes a new team member to find "the best run on the
refund slice". In interviews, be ready to describe the run data model, how lineage flows from a run into
the registry, and the hosting and ecosystem differences between the two tools.

## Role lens
- **Engineer:** Instruments training and eval scripts with params, step metrics and artifacts, and
  registers the resulting model version from the run so lineage is automatic.
- **Architect:** Chooses the system of record per artifact class, decides self-hosted versus SaaS from
  residency and cost, and sets the minimum metadata every run must carry.
- **FDE:** Stands up a tracker inside the customer's environment when their data cannot leave, so
  customer-specific fine-tuning and evals have the same history and reproducibility as internal work.

## Check yourself
**Q:** A colleague reports that run 42 reached 0.91 accuracy and asks you to promote its adapter. The
run page shows only `lr` and `epochs` as params and a single final metric. What questions do you ask
before promoting, and what should the run have logged to make them unnecessary?
**A:** First, which base model revision, which training set version and which code commit produced it;
without those the adapter cannot be retrained or audited. Second, what the evaluation set was and
whether it was the current golden version, because 0.91 on last month's set is not comparable to this
week's baseline. Third, how the metric evolved over steps, since a single final number can hide a
divergent run that landed on a good value by chance. Fourth, where the per-item outputs are, so the
refund-intent slice can be checked. The run should have logged base revision, dataset hashes, git sha
and LoRA settings as params, train and eval metrics per step, and the eval outputs and adapter as
artifacts, with the adapter registered from the run so the registry version carries that lineage.

## Quiz
### Q: Why are a run's parameters fixed at the start while metrics are logged over time?
- [ ] Parameters are stored in a different database table for speed
- [x] Parameters define the experiment and must stay constant for comparison, while metrics describe how it unfolded
- [ ] Metrics cannot be stored as strings
- [ ] Parameters are only used by sweeps
> A run is an answer to "what happens with this configuration"; if the configuration changes mid-run the
> question changes. Step-indexed metrics let you see divergence and over-fitting instead of a final number.

### Q: Which difference most often decides between MLflow and W&B in a regulated enterprise?
- [ ] W&B cannot log artifacts
- [ ] MLflow has no model registry
- [x] Self-hosting and data residency: MLflow is self-hosted by default, W&B is SaaS-first with a self-hosted option
- [ ] MLflow only supports PyTorch
> Both tools cover the core data model. Where metrics, configs and artifacts are allowed to live is usually
> the constraint that decides, with W&B's UI and sweeps as the counterweight when residency is not binding.

### Q: What links a registered model version back to the data it was trained on?
- [ ] The alias assigned at promotion time
- [x] The run that produced it, which logged the dataset version as a parameter
- [ ] The artifact file's modification timestamp
- [ ] The model card's free-text description
> Lineage is captured at training time in the run; registering from the run carries that link into the
> version. An alias says where a model is deployed, not where it came from.
