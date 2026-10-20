---
id: model-registry
module: llmops-platform
name: Model registry
kind: pattern
difficulty: core
est_minutes: 15
related:
  - llmops-platform/experiment-tracking
  - llmops-platform/prompt-registry
  - data-architecture/model-and-dataset-provenance
  - security-governance-compliance/model-cards
  - python-for-ai/hugging-face
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - llmops-platform/rollback
  - safety-security/supply-chain-security-for-models-and-datasets
sources:
  - Official docs: MLflow
  - Official docs: Hugging Face Hub
  - Mitchell et al., 2019 — Model Cards for Model Reporting
---

## TL;DR
A model registry is the system of record that maps a model name to immutable, numbered versions, each
pointing at exact weights or a provider snapshot plus adapters and serving config, with lineage to the
run and data that produced it, attached evaluation evidence, and mutable aliases such as `champion`
that deployments resolve at startup. It exists so that "which model is in production" has exactly one
answer and promotion or rollback is a pointer move rather than a file copy done by hand.

## Why it exists
Without a registry, models live as files in a bucket with names like `adapter_final_v2_really.safetensors`.
Nobody can say with confidence which file is serving, a retraining job produces new weights that someone
copies into place by hand, rolling back means finding the old file and hoping it is the right one, and
the first compliance question, "which data was this trained on", has no answer. For LLM applications the
problem is sharper because the thing you deploy is rarely one file: it is a base model identity, a LoRA
adapter, a quantization choice, a tokenizer revision and a serving configuration, and each of these can
drift independently while all of them still look like "the model".

The naive alternatives fail in predictable ways. "Put it in git" does not survive multi-gigabyte weights
even with LFS, and git has no notion of lineage to a training run or an evaluation. "Deploy the newest
file in the bucket" makes deploys non-deterministic: two replicas started an hour apart can serve
different models. "Keep a spreadsheet" is the registry pattern with no enforcement, and it is wrong
within a week.

The registry fixes this by borrowing the two ideas every package manager already uses: immutable
versions with content hashes, and mutable named pointers that environments resolve. It then adds the
things models need that packages do not: lineage, evaluation evidence, a model card and an approval
state.

## How it works
### Entities
A **registered model** is a name, such as `support-classifier`. Each **version** is immutable and
records the artifact URI and content hash of the weights, the format or flavour (a Transformers
checkpoint, a LoRA adapter, an ONNX graph, a pyfunc wrapper), an input and output signature, the
parameters and metrics of the run that produced it, lineage (tracking run id, training dataset version,
code commit, parent base model and its revision), free-form tags, a description or model card, and an
approval state. Versions are immutable because a deployment that resolved version 7 must mean the same
bytes next year, and because evaluation evidence attached to a version is meaningless if the bytes can
change underneath it.

An **alias** is a mutable pointer from a name to a version: `champion`, `challenger`, `staging`,
`tenant-acme`. MLflow's registry originally used a fixed set of stages (Staging, Production, Archived);
newer versions replaced them with free-form aliases plus tags, because fixed stages could not express
"two production models for two tenants" or "the canary candidate". Promotion is setting an alias;
rollback is setting it back to the previous version.

### What counts as a model in an LLM application
The registry should hold every artifact whose change alters model behaviour: fine-tuned weights and
LoRA adapters, quantized variants (a version per quantization, because they are evaluated separately),
embedding models (whose version must match the vector index built with them), reward or judge models,
and provider-hosted models. The last case matters: a "virtual" version that records only a provider
snapshot id and decoding config gives the API-based model the same lifecycle, evidence and rollback
story as self-hosted weights. A release bundle then references a model version and a prompt version;
some teams register the bundle itself as a model so that one alias move changes both.

### Lifecycle
Register from a tracking run, so lineage is automatic. Attach the eval run fingerprint and scores on a
named golden-set version. Request review; the reviewer reads the model card, the diff against the
champion's metrics and the slice results. Promote by alias, deploy, observe the canary, promote to
`champion`. Later, deprecate: mark the version archived, keep the bytes for the retention period the
lifecycle policy specifies, and refuse new deployments of it.

### Serving integration
A serving configuration references `models:/support-classifier@champion`. At process start the server
resolves the alias to a version, downloads the artifact by hash, verifies it, and logs the resolved
version into every trace and response header. Resolving once at startup rather than on every request
is deliberate: a running replica never changes model mid-flight, and a rollout happens by starting new
replicas against the new alias target, which is what a canary needs. For embedding models the same
mechanism prevents the classic bug of querying an index built with embedder v2 using embedder v3: the
index build records the embedder version and the query path refuses a mismatch.

### Hugging Face Hub as a registry
For open weights the Hub is a git-backed registry: each repository has a commit history, a model card in
the README, and `from_pretrained(..., revision="<commit sha>")` pins an exact version. Pinning to a
branch name such as `main` is the same mistake as a floating `-latest` alias. Many teams mirror the pinned
revision into their own registry so that an upstream deletion or license change cannot break a deploy.

### Governance
Access control on alias moves, signed or hash-verified artifacts, a model card per version, and a record
of who approved what are the parts auditors and security reviewers ask for. They are cheap when designed
in and expensive to retrofit.

## Example
The registry lifecycle with MLflow's client API: register a version from a tracked run, attach the
evidence a reviewer needs, promote by alias, and have the server resolve the alias once at startup.
Tag values are illustrative; the lineage fields are what matter.

```python
import mlflow
from mlflow import MlflowClient

client = MlflowClient()
NAME = "support-classifier"

# 1. Register the artifact a tracked run produced; the run id is the lineage link to params, data and code
run_id = "3f1c0a9e2b7d"                                          # the run that trained the LoRA adapter
mv = mlflow.register_model(f"runs:/{run_id}/model", NAME)        # returns a new immutable version number

# 2. Attach the evidence a reviewer and, later, an auditor will ask for
client.set_model_version_tag(NAME, mv.version, "base_model", "meta-llama/Llama-3.1-8B-Instruct")
client.set_model_version_tag(NAME, mv.version, "base_revision", "<hf commit sha>")   # pinned, not 'main'
client.set_model_version_tag(NAME, mv.version, "golden_set", "golden-v8@5c2e91")
client.set_model_version_tag(NAME, mv.version, "eval_fingerprint", "run:9a71cc")
client.set_model_version_tag(NAME, mv.version, "eval_accuracy", "0.912")
client.update_model_version(NAME, mv.version,
                            description="Retrained on Q3 tickets; fixes refund-intent confusion")

# 3. Promote by moving a pointer; rollback is this same call with the previous version number
client.set_registered_model_alias(NAME, "challenger", mv.version)
# ... canary healthy for the agreed window ...
client.set_registered_model_alias(NAME, "champion", mv.version)

# 4. The serving process resolves the alias once at startup and records what it got
resolved = client.get_model_version_by_alias(NAME, "champion")
print("serving", NAME, "version", resolved.version, "from", resolved.source)   # log into every trace
model = mlflow.pyfunc.load_model(f"models:/{NAME}@champion")
```

## Trade-offs
- Immutable versions plus aliases give deterministic deploys and one-call rollback, at the cost of
  storage for every version ever promoted; a retention policy matters once weights are tens of gigabytes.
- Resolving the alias at startup keeps replicas stable and rollouts explicit, but a hot swap without
  restart is impossible by design; teams that want instant global switches must accept replicas that
  disagree for a while.
- Registering provider-hosted models as virtual versions gives API models the same lifecycle, but it only
  helps if application code reads the model id from the registry rather than from its own config.
- A full registry with approvals and signing satisfies auditors and security; for a two-person team with
  one model it is ceremony, and a pinned artifact URI in a config file is the honest minimum.
- Registering the whole bundle (model plus prompt) as one versioned unit makes promotion atomic, but it
  multiplies versions whenever either half changes and blurs which half caused a metric move.

## Pitfalls
- Pointing serving config at a branch or `latest` tag instead of a version or alias; the symptom is two
  replicas returning different answers for the same input after a rolling restart.
- Registering weights without the tokenizer and serving config; the symptom is a version that scores
  well in the eval harness and produces garbage in production because the tokenizer revision differs.
- Letting the embedding model version advance without rebuilding the index; the symptom is retrieval
  quality collapsing after a "routine" model upgrade while every service reports healthy.
- Attaching eval results to the alias rather than the version; the symptom is a rolled-back champion
  that inherits the evidence of the version that replaced it.
- Mirroring nothing from the Hub and pinning only to a repository name; the symptom is a broken deploy
  the day the upstream author force-pushes, gates the repository or changes the license.

## When to use
Introduce a registry the first time a model is retrained, quantized or swapped after launch, or the
first time two environments need different models; before then a pinned artifact URI with a hash in
config is sufficient and honest. Include provider-hosted models as virtual versions once an API model
swap would be a user-visible change, which is nearly always. Measure whether every serving replica logs
a resolved version, how long a rollback takes end to end, and whether every promoted version carries
eval evidence and a lineage link. In interviews, expect to explain immutable versions versus aliases,
why aliases are resolved at startup, how the registry prevents an embedder-index mismatch, and how
provenance flows from a tracking run into a registered version and out into a trace.

## Role lens
- **Engineer:** Registers versions from training runs, wires the server to resolve aliases at startup and
  log the resolved version, and refuses index queries whose embedder version mismatches.
- **Architect:** Defines what counts as a model artifact, the alias vocabulary per environment and tenant,
  the approval and retention policy, and whether bundles or components are the unit of promotion.
- **FDE:** Keeps a customer-scoped registry or alias set so that a fine-tuned adapter trained on one
  client's data can never be promoted into another tenant's serving path.

## Check yourself
**Q:** A team serves a RAG system with an open embedding model loaded from a Hub repository's `main`
branch. One morning retrieval quality drops sharply, no deploy happened, and every health check is
green. Walk through the likely cause and how a registry would have prevented it.
**A:** The likely cause is that the upstream repository received a new commit on `main`, so replicas that
restarted overnight loaded a different embedding model than the one that built the index; query vectors
and index vectors now live in different spaces, nearest neighbours are nonsense, and nothing errors
because the shapes still match. A registry prevents this in three places: the embedder is a registered
version pinned to a commit hash and mirrored, so a restart loads identical bytes; the index build
records the embedder version it used; and the query path checks that the resolved embedder version
equals the index's recorded version and refuses to serve, or alerts, on mismatch. The fix is to pin the
revision, rebuild or roll back, and add the mismatch check so the failure becomes loud.

## Quiz
### Q: Why does a serving process resolve the `champion` alias at startup rather than on each request?
- [ ] Alias lookups are too slow for request latency budgets
- [x] A replica should never change model mid-flight; rollouts happen by starting new replicas, which is what a canary needs
- [ ] Registries only allow one resolution per client
- [ ] Per-request resolution would bypass the model cache
> Resolving once makes each replica's behaviour stable and attributable; a change in the alias target
> becomes a controlled rollout instead of an instantaneous, unobservable fleet-wide switch.

### Q: What does registering a provider-hosted model as a "virtual version" buy you?
- [ ] Lower API cost through registry-level caching
- [ ] The ability to fine-tune the provider's weights locally
- [x] The same lifecycle, evidence and rollback story for API models as for self-hosted weights
- [ ] Automatic migration when the provider deprecates a snapshot
> The version holds a snapshot id and decoding config, so swapping the API model is a promoted, evaluated
> change with a rollback, not a config edit nobody can trace.

### Q: Which lineage field is most important for an auditor asking "what was this model trained on"?
- [ ] The alias the model currently holds
- [ ] The artifact storage region
- [x] The link from the version to the tracking run and its dataset version
- [ ] The model's parameter count
> Lineage flows from the training run, which recorded the dataset version, code commit and params, into
> the registered version; without that link the question has no verifiable answer.
