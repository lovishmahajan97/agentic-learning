---
id: model-and-dataset-provenance
module: data-architecture
name: Model & dataset provenance
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
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
Provenance is the verifiable record of where a model or dataset came from: the exact bytes it was built
from, the code and parameters that built it, who built it, under which licence, and the evaluations that
were run on it. It exists because a model file is opaque: nothing in a checkpoint tells you what data it
saw, whether that data was licensed or contained your eval set, or whether the file is even the one you
think it is. Provenance turns "trust me" into hashes, manifests and signatures.

## Why it exists
A fine-tuned model lands in the registry as `router-v4.safetensors`. Six months later the questions
start. Legal asks whether any training data came from a source with a non-commercial licence. A customer
asks whether their data was used. The eval team notices suspiciously high scores and wants to know whether
the benchmark leaked into training. Security asks whether the base model was downloaded from the official
repository or a lookalike. An engineer wants to reproduce the run to fix a regression. Every one of these
is answerable only if, at build time, someone recorded the inputs precisely and made the record
tamper-evident.

The naive alternative is the model card written after the fact and the README in the dataset folder.
They fail because they are prose written from memory: they describe what the author believes the inputs
were, not what the job actually read. A dataset folder that was "the cleaned support tickets" when the
README was written has since had rows added and removed. A base model pulled by name resolves to a
different revision today. Prose cannot be diffed, verified or traced.

The second naive alternative is "git is enough": commit the training script and call it reproducible.
Code is one of six inputs. The data snapshot, the base model revision, the environment (library and
CUDA versions), the hyperparameters and the random seed all change outputs, and none of them lives in
the training script. Provenance is the discipline of capturing all of them, as identifiers that can be
checked, at the moment the artifact is created.

## How it works
### Content addressing: identity by hash
The foundation is that every input and output is identified by a cryptographic hash of its bytes, not by
a name or path. A dataset snapshot is the sorted list of its files with their SHA-256 digests, hashed
again into one dataset digest (a Merkle-style root). A model artifact is its file digests. This is chosen
*because* a hash is the only identifier that is stable across copies, cannot drift when someone edits the
folder, and lets a reader verify they hold the same bytes. Hugging Face model repositories are git
repositories, so a commit hash identifies a revision exactly; always pin `revision` rather than relying
on the default branch, which moves.

### The build manifest
Every training or fine-tuning run writes a manifest beside its output, containing:
- base model: repository id and revision hash, plus file digests;
- datasets: each snapshot digest, row counts, and the filter or sampling parameters used;
- code: commit hash and container or environment digest, with library versions;
- parameters: the full hyperparameter set and the seed;
- outputs: checkpoint digests and the eval results with the eval set's own digest;
- actor and time: who or what launched the run, when.

Merged, distilled and adapter models list every parent artifact as an input (each with its own manifest
digest), *because* a merge has several ancestors and the licence and contamination questions must be
answered for all of them. The format of the record matters less than its completeness; supply-chain
attestation formats from software (in-toto and SLSA-style provenance) and bill-of-materials formats with
ML profiles (CycloneDX and SPDX both have them) are being adopted so registries and scanners can read
manifests without bespoke parsers.

The manifest is written by the job, not by a person, *because* that is the only way it describes what
ran. It is stored in the model registry as the artifact's metadata and should also be committed where
the lineage system can link it to dataset versions.

### Dataset documentation
Datasheets (Gebru et al.) formalize the questions a dataset should answer: motivation, composition,
collection process, preprocessing, intended uses, distribution and maintenance. For an AI team the
operationally important subset is: source systems and their terms, consent or licence basis, PII handling
applied, the filtering recipe and its parameters, known gaps, and the eval sets that must never overlap
with it. Attach this as a structured file next to the snapshot digest, so documentation and bytes are
versioned together.

### Model documentation
Model cards (Mitchell et al.) do the same for models: intended use, out-of-scope use, training data
summary, evaluation results broken down by relevant slices, limitations and ethical considerations. The
provenance manifest is the machine-readable backbone; the model card is the human-readable layer that
references it. Regulation increasingly expects both: the EU AI Act, for example, requires technical
documentation covering data and evaluation for high-risk systems, and for general-purpose model providers
technical documentation plus a public summary of training content. Templates, codes of practice and
application dates are still being phased in, so check current obligations rather than relying on a
summary.

### Synthetic and model-generated data
A growing share of fine-tuning data is generated by another model: distilled answers, synthetic
instructions, LLM-written labels. Such a dataset's inputs are not just its seed data but also the
generator model and revision, the prompt template version, the sampling parameters and the filter that
kept or discarded outputs. Record all of them, *because* the generator's behaviour is now part of your
model (its errors and biases propagate), a change of generator explains a change of downstream quality,
and the generator's usage terms travel with its outputs: some providers' terms restrict using outputs to
train competing models, which is a question for counsel that the manifest must make answerable.

### Licence and terms tracking
Each dataset and base model carries its licence identifier in the manifest. The safe engineering policy
is that a derived artifact carries the combined obligations of all its inputs, computed by walking the
lineage graph, *because* publishing a fine-tune under a permissive licence does not release it from the
base model's terms (many model licences explicitly bind derivatives). Licences are not a simple
restrictiveness ranking: two inputs can impose different, even incompatible, obligations, and whether a
given dataset licence reaches the trained weights is a legal question, so the graph's job is to surface
every applicable term for counsel, not to decide it.
This is also where "the customer's data may not be used to train shared models" is enforced: the
manifest of any shared model must show no customer-restricted dataset digests among its inputs.

### Integrity and supply chain
A hash tells you what you have; a signature tells you who produced it. Signing model artifacts and
manifests (Sigstore-style keyless signing is one approach being adopted for ML artifacts) lets a deployer
verify that a checkpoint came from the registry's build pipeline, not from a laptop or a tampered
download. Prefer `safetensors` over pickle-based formats, because loading a pickle executes arbitrary
code embedded in the file; a signed safetensors file is both authentic and inert. Record dependency
digests too, since a training run is only as trustworthy as the libraries that ran it.

### Using provenance
- **Contamination check:** hash the eval set's examples and the training snapshot's examples; exact
  overlap is found by digest comparison without re-reading the pipeline. Hashes only catch exact copies,
  so normalize text before hashing and add n-gram or embedding-similarity checks for reformatted or
  paraphrased leaks.
- **Data subject requests:** the dataset snapshot digests in a model's manifest, joined with lineage,
  answer whether a person's records contributed to a model.
- **Reproduction:** the manifest is the command line: same base revision, same snapshot, same commit,
  same parameters and seed. Bitwise identity is not guaranteed on GPUs, but the result should land within
  the known run-to-run variance.
- **Incident response:** when a model misbehaves, compare its manifest with the last good one; the diff
  is the list of suspects.

## Example
A manifest writer for a fine-tuning run. It hashes the dataset snapshot and base model files, captures
code and environment identifiers, and writes a JSON manifest beside the checkpoint. The training call is a
stub; everything around it is what provenance requires.

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

## Trade-offs
- Content-addressed snapshots make inputs exact but require immutable copies or a lakehouse with
  snapshot isolation; hashing a mutable table "as of now" is cheap and nearly useless.
- Full per-file digests for large corpora cost a scan per run; for multi-terabyte datasets, hash
  partition manifests the lakehouse already maintains rather than every byte again.
- Signing artifacts gives tamper evidence but adds key management and a verification step at load time;
  skip it only for throwaway experiments that never reach a registry.
- Rich datasheets improve downstream decisions but are written by people and go stale; keep the
  machine-written manifest authoritative and the prose as commentary on it.
- Pinning base model revisions protects reproducibility but means security patches to a model repo are
  not picked up automatically; make upgrading the revision an explicit, evaluated change.

## Pitfalls
- Referencing the base model by name without a revision; the symptom is a reproduction attempt that
  silently uses a newer upstream commit and lands outside the expected variance.
- Writing the model card from memory after the run; the symptom is documented training data that does
  not match the snapshot the job actually read.
- Hashing a dataset directory that was later edited in place; the symptom is a manifest digest that
  matches nothing on disk when an audit asks for the bytes.
- Loading pickle-format checkpoints from an unverified source; the symptom is arbitrary code execution
  at load time, usually discovered by a security review rather than by the model misbehaving.
- Forgetting licence propagation through fine-tunes and merges; the symptom is a commercial deployment of
  a model whose ancestor forbids it.
- Treating eval results as part of the model card but not recording the eval set digest; the symptom is
  scores that cannot be compared across runs because the set quietly changed.
- Recording a synthetic dataset's digest but not the generator model and prompt version that produced it;
  the symptom is a quality shift after regenerating "the same" dataset that nobody can explain.

## When to use
Record provenance for every artifact that reaches a registry: every fine-tune, every merged model, every
dataset snapshot used for training or evaluation, and every eval set. Experiments that never leave a
notebook can skip signing but should still capture the manifest, since the one that works will be
promoted. Measure coverage (fraction of registry artifacts with complete manifests), reproduction success
rate on a sampled run, and time to answer "what was this trained on". In day-to-day work provenance shows
up as the manifest you read before deploying someone else's checkpoint. In interviews, "how would you
prove your model was not trained on the benchmark" is answered with dataset and eval digests and an
overlap check, not with "we were careful".

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
