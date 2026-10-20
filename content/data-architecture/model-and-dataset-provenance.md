---
id: model-and-dataset-provenance
module: data-architecture
name: Model & dataset provenance
kind: practice
difficulty: core
est_minutes: 16
status: crisp
related:
  - data-architecture/data-lineage
  - data-architecture/training-data-pipelines
  - llmops-platform/model-registry
  - security-governance-compliance/model-cards
  - security-governance-compliance/model-governance
  - safety-security/supply-chain-security-for-models-and-datasets
  - evals/dataset-contamination
  - training-adaptation/data-curation
sources:
  - Mitchell et al., 2019 — Model Cards for Model Reporting
  - Gebru et al., 2021 — Datasheets for Datasets
  - Official docs: Hugging Face Hub
---

## TL;DR
Provenance is the verifiable record of a model's or dataset's origin: exact input bytes, code,
parameters, builder, licence and evaluations. A checkpoint is opaque about what it saw, so provenance
replaces "trust me" with hashes, manifests and signatures.

## Why it exists
- **The questions arrive months later.** A fine-tune lands in the registry as `router-v4.safetensors`.
  Six months on: Legal asks about non-commercial data sources; a customer asks whether their data was used;
  the eval team suspects the benchmark leaked; Security asks whether the base model came from the official
  repository or a lookalike; an engineer wants to reproduce the run to fix a regression.
- **Only build-time records answer them,** and only if someone recorded the inputs precisely and made the
  record tamper-evident.
- **After-the-fact model cards and READMEs fail,** because they are prose written from memory: what the
  author believes the inputs were, not what the job read. "The cleaned support tickets" folder has since had
  rows added and removed; a base model pulled by name resolves to a different revision today. Prose cannot
  be diffed, verified or traced.
- **"Git is enough" fails,** because code is one of six inputs. The data snapshot, base model revision,
  environment (library and CUDA versions), hyperparameters and random seed all change outputs, and none
  lives in the training script.
- **What provenance is:** the discipline of capturing all of them as checkable identifiers at the moment
  the artifact is created.

## How it works
### Content addressing: identity by hash
- **Identify by bytes, not by name or path.** A dataset snapshot is the sorted list of its files with their
  SHA-256 digests, hashed again into one dataset digest (a Merkle-style root). A model artifact is its file
  digests.
- **Why:** a hash is the only identifier stable across copies, it cannot drift when someone edits the
  folder, and it lets a reader verify they hold the same bytes.
- **Pin hub revisions.** Hugging Face model repositories are git repositories, so a commit hash identifies a
  revision exactly. Always pin `revision` rather than relying on the default branch, which moves.

### The build manifest
Every training or fine-tuning run writes a manifest beside its output:

| Section | Contents |
|---|---|
| Base model | repository id and revision hash, plus file digests |
| Datasets | each snapshot digest, row counts, filter or sampling parameters used |
| Code | commit hash, container or environment digest, library versions |
| Parameters | the full hyperparameter set and the seed |
| Outputs | checkpoint digests, eval results with the eval set's own digest |
| Actor and time | who or what launched the run, and when |

- **Merged, distilled and adapter models list every parent** as an input, each with its own manifest
  digest, *because* a merge has several ancestors and the licence and contamination questions must be
  answered for all of them.
- **Completeness beats format.** Software supply-chain attestation formats (in-toto and SLSA-style
  provenance) and bill-of-materials formats with ML profiles (CycloneDX and SPDX both have them) are being
  adopted so registries and scanners can read manifests without bespoke parsers.
- **The job writes it, not a person,** *because* that is the only way it describes what ran. Store it in
  the model registry as the artifact's metadata, and commit it where the lineage system can link it to
  dataset versions.

### Dataset documentation
- **Datasheets (Gebru et al.)** formalize what a dataset should answer: motivation, composition, collection
  process, preprocessing, intended uses, distribution and maintenance.
- **The operational subset for an AI team:** source systems and their terms, consent or licence basis, PII
  handling applied, the filtering recipe and its parameters, known gaps, and the eval sets that must never
  overlap with it.
- **Store it next to the snapshot digest** as a structured file, so documentation and bytes are versioned
  together.

### Model documentation
- **Model cards (Mitchell et al.)** cover intended use, out-of-scope use, training data summary, evaluation
  results by relevant slices, limitations and ethical considerations.
- **Two layers:** the provenance manifest is the machine-readable backbone; the model card is the
  human-readable layer that references it.
- **Regulation increasingly expects both.** The EU AI Act, for example, requires technical documentation
  covering data and evaluation for high-risk systems, and for general-purpose model providers technical
  documentation plus a public summary of training content. Templates, codes of practice and application
  dates are still being phased in, so check current obligations rather than relying on a summary.

### Synthetic and model-generated data
- **A generated dataset has more inputs than its seed data:** the generator model and revision, the prompt
  template version, the sampling parameters and the filter that kept or discarded outputs. Record all of
  them.
- **Why each matters:** the generator's errors and biases propagate into your model; a change of generator
  explains a change of downstream quality; and the generator's usage terms travel with its outputs.
- **Terms are a counsel question:** some providers' terms restrict using outputs to train competing models,
  and the manifest must make that answerable.

### Licence and terms tracking
- **Each dataset and base model carries its licence identifier** in the manifest.
- **Derived artifacts inherit combined obligations,** computed by walking the lineage graph, *because*
  publishing a fine-tune under a permissive licence does not release it from the base model's terms (many
  model licences explicitly bind derivatives).
- **Licences are not a simple ranking.** Two inputs can impose different, even incompatible, obligations,
  and whether a dataset licence reaches the trained weights is a legal question. The graph surfaces every
  applicable term for counsel; it does not decide.
- **Customer isolation is enforced here:** the manifest of any shared model must show no
  customer-restricted dataset digests among its inputs.

### Integrity and supply chain
- **A hash says what you have; a signature says who produced it.** Signing artifacts and manifests
  (Sigstore-style keyless signing is one approach being adopted for ML artifacts) lets a deployer verify a
  checkpoint came from the registry's build pipeline, not a laptop or a tampered download.
- **Prefer `safetensors` over pickle-based formats,** because loading a pickle executes arbitrary code
  embedded in the file. A signed safetensors file is both authentic and inert.
- **Record dependency digests too,** since a training run is only as trustworthy as the libraries that ran
  it.

### Using provenance
- **Contamination check:** hash the eval set's examples and the training snapshot's examples; exact overlap
  is found by digest comparison without re-reading the pipeline. Hashes only catch exact copies, so
  normalize text before hashing and add n-gram or embedding-similarity checks for reformatted or paraphrased
  leaks.
- **Data subject requests:** the dataset snapshot digests in a model's manifest, joined with lineage, answer
  whether a person's records contributed to a model.
- **Reproduction:** the manifest is the command line: same base revision, snapshot, commit, parameters and
  seed. Bitwise identity is not guaranteed on GPUs, but the result should land within the known run-to-run
  variance.
- **Incident response:** when a model misbehaves, compare its manifest with the last good one; the diff is
  the list of suspects.

## Diagram
Every input is reduced to a digest before training; gates run on the digests, and only a signed manifest
lets the checkpoint into the registry.

```mermaid
flowchart TD
    B["Base model at pinned revision"] --> H["Hash every input"]
    D["Dataset snapshot"] --> H
    G["Code commit and container digest"] --> H
    H --> C{"Eval overlap or licence conflict?"}
    C -->|"yes"| X["Block the run"]
    C -->|"no"| T["Train with params and seed"]
    T --> K["Checkpoint safetensors"]
    K --> M["Job writes manifest with all digests"]
    M --> S["Sign manifest and checkpoint"]
    S --> R[("Model registry")]
    R -->|"deploy verifies signature and digest"| P["Serving"]
```

## Examples
### A manifest writer for a fine-tuning run
It hashes the dataset snapshot and base model files, captures code and environment identifiers, and writes
a JSON manifest beside the checkpoint. The training call is a stub; everything around it is what
provenance requires.

```python
import hashlib, json, subprocess, sys, platform, time
from pathlib import Path

def file_digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def tree_digest(root: Path) -> dict:
    files = sorted(p for p in root.rglob("*") if p.is_file())
    per_file = {str(p.relative_to(root)): file_digest(p) for p in files}
    root_hash = hashlib.sha256(json.dumps(per_file, sort_keys=True).encode()).hexdigest()
    return {"root": root_hash, "files": len(per_file), "per_file": per_file}

def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

def train(out_dir: Path, params: dict) -> Path:       # stand-in for the real trainer
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = out_dir / "model.safetensors"
    ckpt.write_bytes(b"weights-placeholder")
    return ckpt

def run(base_model: dict, dataset_dir: Path, eval_dir: Path, out_dir: Path, params: dict):
    data, evalset = tree_digest(dataset_dir), tree_digest(eval_dir)
    overlap = set(data["per_file"].values()) & set(evalset["per_file"].values())
    if overlap:
        raise SystemExit(f"eval files present in training snapshot: {len(overlap)}")
    ckpt = train(out_dir, params)
    manifest = {
        "base_model": base_model,                       # {"repo": ..., "revision": <commit hash>}
        "datasets": [{"path": str(dataset_dir), "digest": data["root"], "files": data["files"],
                      "license": "customer-restricted:acme"}],
        "eval_set_digest": evalset["root"],
        "code": {"commit": git_commit(), "python": sys.version.split()[0], "platform": platform.platform()},
        "params": params,
        "outputs": {"checkpoint": str(ckpt), "digest": file_digest(ckpt)},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest
```

The overlap check is deliberately crude (whole-file hashes); a real contamination check hashes individual
examples or n-grams. The licence field on the dataset is what later blocks this checkpoint from being
promoted to a shared, multi-customer deployment.

### Example-level contamination check: exact digests plus n-grams
The step the crude file check above defers. Normalizing before hashing catches reformatted copies; shared
n-grams catch light paraphrases. The 8-token n-gram size is an illustrative choice; tune it on your data.

```python
import hashlib, re

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()

def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()

def ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    toks = normalize(text).split()
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}

def contamination(train: list[str], evalset: list[str], n: int = 8, min_shared: int = 1):
    train_digests = {digest(t) for t in train}
    train_grams = set().union(*(ngrams(t, n) for t in train))
    exact = [e for e in evalset if digest(e) in train_digests]
    near = [e for e in evalset if e not in exact and len(ngrams(e, n) & train_grams) >= min_shared]
    return exact, near

train = ["How do I reset my password? Go to Settings, then Security, then Reset.",
         "Refunds are issued to the original card within five business days of approval."]
evalset = ["how do I reset my password?  go to settings, then security, then reset",   # reformatted copy
           "Refunds are issued to the original card within five business days, per policy.",
           "What is the API rate limit for the free tier?"]
exact, near = contamination(train, evalset)
print(len(exact), "exact;", len(near), "near-duplicate;", len(evalset) - len(exact) - len(near), "clean")
# 1 exact; 1 near-duplicate; 1 clean
```

### Worked scenario: diffing manifests after a regression
`router-v4` routes worse than `router-v3` in production. Diff the two manifests (illustrative values):

| Field | router-v3 | router-v4 | Suspect? |
|---|---|---|---|
| Base model revision | commit e1f… | commit e1f… | no |
| Training snapshot digest | 9ab… (120k rows) | c42… (131k rows) | yes: 11k new rows |
| Synthetic subset generator | model A, prompt v2 | model B, prompt v2 | yes: generator changed |
| Code commit | 5d0… | 5d0… | no |
| Seed and params | identical | identical | no |
| Eval set digest | 77c… | 77c… | no, scores are comparable |

- **Two suspects, both in data:** retrain v4's recipe with the old generator's subset to isolate the
  effect. Without the generator recorded, "the same" synthetic dataset would look unchanged.

## Production patterns
- **Pin every hub download to a commit.** Use for all base models and hub datasets. Pass
  `revision="<commit sha>"` to `from_pretrained` or `huggingface_hub.snapshot_download`, and record that
  sha in the manifest. Monitor any training config that resolves a branch name instead of a commit.
- **Manifest required for registry promotion.** Use for every artifact that reaches a registry. Attach the
  job-written manifest to the registered model (in MLflow, for example, `mlflow.log_dict(manifest,
  "manifest.json")` on the run), and have the promotion step reject artifacts without one. Monitor coverage:
  fraction of registry artifacts with complete manifests.
- **Safetensors-only loading.** Use for any third-party or shared checkpoint. In Transformers pass
  `use_safetensors=True` so a pickle file is refused rather than executed. Monitor registry artifacts still
  stored as pickle-based formats.
- **Sign at build, verify at deploy.** Use for artifacts that cross trust boundaries. Sign the manifest and
  checkpoint in the build pipeline (Sigstore's `cosign sign-blob` is one option), and verify signature and
  digest before the serving process loads weights. Monitor verification failures; each is a blocked
  tampered or out-of-band artifact.
- **Licence and isolation gate in CI.** Use before promoting shared models. Walk the manifest closure and
  fail if any input carries a customer-restricted tag or an unreviewed licence. Monitor gate failures and
  time to counsel review.
- **Contamination gate before training.** Use whenever eval sets exist. Run the digest and n-gram overlap
  check between the training snapshot and every registered eval set, and record the result in the manifest.
  Monitor overlap counts per run.

## Trade-offs
- **Exact inputs vs immutable storage.** Content-addressed snapshots make inputs exact but require immutable
  copies or a lakehouse with snapshot isolation; hashing a mutable table "as of now" is cheap and nearly
  useless.
- **Full digests vs scan cost.** Per-file digests for large corpora cost a scan per run. For multi-terabyte
  datasets, hash partition manifests the lakehouse already maintains rather than every byte again.
- **Tamper evidence vs key management.** Signing adds key management and a verification step at load time;
  skip it only for throwaway experiments that never reach a registry.
- **Rich datasheets vs staleness.** Datasheets improve downstream decisions but are written by people and go
  stale. Keep the machine-written manifest authoritative and the prose as commentary on it.
- **Reproducibility vs automatic patches.** Pinning base model revisions means security patches to a model
  repo are not picked up automatically; make upgrading the revision an explicit, evaluated change.

## Pitfalls
- **Referencing the base model by name without a revision.** Symptom: a reproduction attempt silently uses a
  newer upstream commit and lands outside the expected variance. Fix: pin the commit.
- **Writing the model card from memory after the run.** Symptom: documented training data does not match the
  snapshot the job actually read. Fix: generate the card from the manifest.
- **Hashing a dataset directory that was later edited in place.** Symptom: a manifest digest that matches
  nothing on disk when an audit asks for the bytes. Fix: immutable snapshots.
- **Loading pickle-format checkpoints from an unverified source.** Symptom: arbitrary code execution at load
  time, usually discovered by a security review rather than by the model misbehaving.
- **Forgetting licence propagation through fine-tunes and merges.** Symptom: a commercial deployment of a
  model whose ancestor forbids it. Fix: compute obligations over the full closure.
- **Recording eval results but not the eval set digest.** Symptom: scores that cannot be compared across
  runs because the set quietly changed.
- **Recording a synthetic dataset's digest but not the generator model and prompt version.** Symptom: a
  quality shift after regenerating "the same" dataset that nobody can explain.

## When to use
- **Record provenance for every artifact that reaches a registry:** every fine-tune, merged model, dataset
  snapshot used for training or evaluation, and eval set.
- **Notebook experiments** can skip signing but should still capture the manifest, since the one that works
  will be promoted.
- **Measure:** coverage (fraction of registry artifacts with complete manifests), reproduction success rate
  on a sampled run, and time to answer "what was this trained on".
- **Day to day:** the manifest is what you read before deploying someone else's checkpoint.
- **Interviews:** "how would you prove your model was not trained on the benchmark" is answered with dataset
  and eval digests and an overlap check, not with "we were careful".

## Role lens
- **Engineer:** Makes the training job write the manifest, pins base model revisions and dependency
  digests, and adds the contamination and licence checks as pipeline gates.
- **Architect:** Defines what a complete manifest contains, requires it for registry promotion, and sets
  the licence inheritance and customer-data isolation rules the manifests enforce.
- **FDE:** Shows a customer's compliance team the manifest proving which of their datasets touched which
  models, and that none reached a shared model.

## Check yourself
**Q:** A customer alleges that a shared model you deploy to several clients was trained on their
proprietary documents. How do you establish the truth, and what must have been in place beforehand?
**A:** Pull the deployed model's manifest from the registry and walk its ancestry: the fine-tune's dataset
snapshot digests, its base model revision, and recursively the manifests of any merged or distilled
inputs. Each dataset snapshot carries a licence or restriction tag and a lineage link to its source
systems. If no snapshot in the closure carries the customer's restriction tag or derives from their source
systems, the manifests are strong evidence for the negative across everything your own pipelines fed in
(a third-party base model's pretraining data is covered only by its vendor's documentation); if one does, you know exactly which run introduced it and which
other models share that ancestor. For this to be credible, the manifests must have been written by the
jobs at build time with content hashes, signed so they cannot be edited afterwards, and stored with the
artifact so the deployed digest can be matched to its manifest. Without that, the honest answer is "we
believe not", which does not survive a dispute.

## Interview questions
### What is the difference between a model card and a provenance manifest?
- **Manifest:** machine-written at build time, with digests of the base model, data snapshots, code,
  environment, params, seed and outputs; it can be verified and diffed.
- **Model card:** human-readable intended use, limitations and sliced eval results that reference the
  manifest; written alone from memory it drifts from what actually ran.

### Your benchmark scores jumped 9 points after a data refresh. How do you check for contamination?
- **Exact overlap:** normalize and hash every eval example and every training example, then intersect the
  digests.
- **Near overlap:** n-gram or embedding-similarity checks catch reformatted and paraphrased leaks that
  hashes miss.
- **Then diff manifests:** the new snapshot digest and any changed synthetic-data generator are the
  suspects.

### A merged model has three parents. How do you decide whether it can be deployed commercially?
- **Walk the closure:** every parent's manifest, recursively, down to base models and datasets with their
  licence identifiers.
- **Surface, do not rank:** obligations can be different or incompatible, so the tooling lists every
  applicable term and counsel decides; a permissive licence on the merge does not override a parent's terms.

### When is signing artifacts not worth the cost?
- **Throwaway experiments** that never reach a registry can skip it; they should still write the manifest.
- **Anything deployed or shared across trust boundaries should be signed,** because a hash alone cannot say
  who produced the file.

### Can you reproduce a fine-tune bit for bit from its manifest?
- **Usually not on GPUs:** non-deterministic kernels mean bitwise identity is not guaranteed.
- **But you can land within known run-to-run variance** using the same base revision, snapshot, commit,
  params and seed; a result outside that variance points at an unpinned input.

## Quiz
### Q: Why is a dataset identified by a content hash rather than by its path or name?
- [ ] Hashes are shorter than paths
- [x] A hash is stable across copies and changes when any byte changes, so it can be verified and cannot drift
- [ ] Paths are not allowed in model registries
- [ ] Hashing is required by safetensors
> A name points at whatever is currently in that location, which changes silently. A digest identifies
> the exact bytes, so a manifest naming it can be checked later and a reader can confirm they hold the
> same data.

### Q: Which input is most often missing from "we committed the training script, so it is reproducible"?
- [ ] The programming language version
- [ ] The name of the engineer who ran it
- [x] The exact data snapshot and base model revision the run consumed
- [ ] The number of GPUs used
> Code is one input among several. The data snapshot and base model revision usually change the output
> more than anything in the script, and neither is captured by a commit unless the manifest records their
> digests.

### Q: Why prefer safetensors over pickle-based checkpoint formats when loading third-party models?
- [ ] Safetensors files are smaller
- [ ] Pickle cannot store floating-point tensors
- [x] Loading a pickle executes code embedded in the file, while safetensors is a pure data format
- [ ] Safetensors includes the model card automatically
> Pickle deserialization runs arbitrary Python, so an unverified checkpoint is an executable. A data-only
> format combined with a verified digest or signature gives you an artifact that is both authentic and
> inert to load.
