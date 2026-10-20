---
id: supply-chain-security-for-models-and-datasets
module: safety-security
name: Supply-chain security for models & datasets
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - python-for-ai/safetensors
  - python-for-ai/hugging-face
  - python-for-ai/security-scanning-and-dependency-auditing
  - data-architecture/model-and-dataset-provenance
  - llmops-platform/model-registry
  - safety-security/rag-poisoning
  - security-governance-compliance/model-governance
  - evals/dataset-contamination
sources:
  - Gu et al., 2017 — BadNets: Identifying Vulnerabilities in the Machine Learning Model Supply Chain
  - Carlini et al., 2024 — Poisoning Web-Scale Training Datasets is Practical
  - Hubinger et al., 2024 — Sleeper Agents: Training Deceptive LLMs that Persist Through Safety Training
  - Official docs: safetensors
---

## TL;DR
A model checkpoint or dataset pulled from a public hub is a dependency with worse properties than a
library: it can execute code when loaded (pickle), it can carry behaviour you cannot read in a diff
(backdoored weights), and its training data may have been poisoned before anyone you trust touched it.
Supply-chain security treats models and datasets like any other build artifact, pinned, hashed, scanned,
signed, quarantined and loaded with the least capability, because you cannot review ten billion floats
for intent.

## Why it exists
Software supply chains have a decade of tooling: lockfiles, CVE databases, signed packages, SBOMs. Model
and dataset supply chains arrived with none of it and with four properties that make them harder. First,
the dominant checkpoint formats were executable. PyTorch `.bin` and `.pt` files are Python pickles, and
unpickling runs whatever `__reduce__` returns, so "loading a model" could mean "running the uploader's
code with your credentials". Second, repositories ship code as well as weights: `trust_remote_code=True`
imports and executes Python from the repo on every load. Third, weights cannot be reviewed. A backdoor,
behaviour triggered by a specific input that is otherwise invisible, was shown in classifiers by Gu et al.
in 2017 and in LLMs by Hubinger et al. in 2024, where trigger behaviour survived standard safety training.
Fourth, datasets are often lists of URLs rather than content; Carlini et al. showed in 2024 that buying
expired domains referenced in popular web-scale datasets lets an attacker control what future downloaders
receive; they estimated that about US$60 would have bought control of 0.01% of LAION-400M or COYO-700M.

The naive defence, "we only use popular models from well-known organisations", does not hold. Hub
namespaces can be squatted with look-alike names, accounts get compromised, a branch like `main` is
mutable, and researchers have repeatedly found malicious pickle payloads in public repositories.
Popularity also says nothing about data poisoning or backdoors, which pass every download count.

## How it works
### Threat model by layer
- **Artifact.** Malicious serialization (pickle, also pickles hidden inside zip-based formats), and parser
  bugs in model-file loaders that turn a crafted file into memory corruption.
- **Code.** Remote code in the repo, conversion scripts, and the loader's own dependency tree.
- **Weights.** Backdoors and trojans; also quietly degraded or safety-stripped fine-tunes relabelled as the
  original.
- **Data.** Split-view poisoning (the content behind a URL changes after the dataset was curated),
  front-running poisoning (editing a source just before a scheduled snapshot), label flipping, and
  poisoned instruction-tuning examples that implant a trigger.
- **Identity.** Typosquatted organisations, hijacked maintainer accounts, unpinned references.
- **Licence and provenance.** Not security in the narrow sense, but handled by the same pipeline: gated
  models, consent and usage restrictions on datasets.

### Controls in order of leverage
1. **Safe formats only.** Load `safetensors`, a flat tensor format with a JSON header and no code path,
   and reject pickle-based files in production. Where `torch.load` is unavoidable, pass
   `weights_only=True`; newer PyTorch releases default to it, older pins do not.
2. **No remote code by default.** `trust_remote_code=False`. If an architecture genuinely needs custom
   code, vendor it into your repository after review and pin it there, so a later push to the hub cannot
   change what runs.
3. **Pin by content, not by name.** Reference a commit hash (`revision=`) and record the SHA-256 of every
   file at review time, because names and branches move and hashes do not.
4. **Mirror through a registry.** Production pulls only from an internal registry; ingestion is
   quarantine, scan, evaluate, promote. Block egress from training and serving clusters to public hubs so
   the registry cannot be bypassed. Scanners such as picklescan and modelscan catch known-dangerous
   opcodes and imports; hubs run similar scans but your pipeline should not depend on theirs.
5. **Sign and attest.** Sign promoted artifacts and their build attestations with the same tooling you use
   for containers (Sigstore/cosign, in-toto), and maintain an AI bill of materials listing base model,
   adapters, datasets and hashes, so an incident can be traced to every deployment that used the
   affected component.
6. **Dataset provenance.** Snapshot content rather than URLs, hash the snapshot, store it immutably, keep a
   manifest; for URL-indexed sets, verify per-item hashes captured at curation time, which is the
   mitigation Carlini et al. recommend. Deduplicate and inspect outliers.
7. **Behavioural acceptance.** Run your eval suite, including safety and refusal behaviour, on every
   ingested model or adapter, and compare against the publisher's reported numbers for the same
   checkpoint; a large unexplained gap suggests you do not have the weights you think you have. Backdoor
   detection in general is unsolved, so this is a sanity check, not a guarantee.
8. **Least privilege at load time.** Convert, scan and evaluate in a sandbox with no network and no
   production secrets, because the first time an artifact executes anything is during these steps.

## Example
A loader that pins a reviewed revision, downloads only tensor and config files, verifies hashes recorded at
review time, runs a pickle scan as a second line of defence, and loads with remote code disabled. The hash
and revision placeholders are filled in by the review step that promoted this artifact into the registry.

```python
import hashlib, subprocess, sys
from huggingface_hub import snapshot_download
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO = "Qwen/Qwen2.5-0.5B"
REV = "<commit-sha-reviewed>"                 # never "main": branches move, hashes do not
EXPECTED = {"model.safetensors": "<sha256 recorded at review time>"}

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

# 1. fetch only what a safe load needs; *.bin / *.pt / *.pkl are never downloaded
local = snapshot_download(REPO, revision=REV,
                          allow_patterns=["*.safetensors", "*.json", "tokenizer*"])

# 2. integrity: the bytes must match what was reviewed and promoted
for name, want in EXPECTED.items():
    got = sha256(f"{local}/{name}")
    if got != want:
        sys.exit(f"hash mismatch for {name}: got {got}")

# 3. belt and braces: scan the snapshot for pickle payloads (also run in CI on the mirror)
subprocess.run(["picklescan", "--path", local], check=True)

# 4. load with no code execution path: safetensors only, no remote code
tok = AutoTokenizer.from_pretrained(local)
model = AutoModelForCausalLM.from_pretrained(local, use_safetensors=True, trust_remote_code=False)
```

In a real pipeline steps 1 to 3 run once at ingestion inside the sandboxed registry job, and serving
nodes pull the promoted, signed artifact from the registry rather than from the hub at all.

## Trade-offs
- A registry with quarantine and scanning gives you provenance and a kill switch, but it adds hours or
  days between a model's release and its availability to your teams; for research sandboxes with no
  production credentials a lighter path is reasonable.
- Refusing `trust_remote_code` protects against arbitrary execution, yet some new architectures ship only
  that way; vendoring the code after review preserves safety at the cost of maintaining a fork until
  upstream support lands.
- Pinning by hash guarantees reproducibility, but it means security fixes to a tokenizer or config do not
  arrive automatically; pair pins with a review cadence rather than treating them as permanent.
- Snapshotting dataset content instead of URLs removes split-view poisoning and makes training
  reproducible, at the cost of storing terabytes you previously only referenced and taking on licence
  obligations for holding copies.

## Pitfalls
- Loading community checkpoints with `torch.load` on a developer laptop that also holds cloud credentials;
  the symptom is a security alert about outbound connections from a Python process during "model loading".
- Pinning `revision="main"` or no revision at all; the symptom is an evaluation that regresses overnight
  although nobody changed anything on your side.
- Assuming a hub's own malware scan is sufficient; the symptom is a flagged file that was already pulled
  into a training job because your pipeline never looked at the scan result.
- Treating a leaderboard score as evidence of safety; the symptom is a fine-tune that performs well on the
  benchmark and has had its refusal behaviour stripped, discovered by a customer.
- Building datasets from live URLs at training time; the symptom is a training run whose data differs from
  the one that was audited, and a model behaviour nobody can reproduce.

## When to use
Apply the full pipeline to anything that reaches production or touches customer data: base models,
fine-tunes, adapters, embedding models, tokenizers, and every dataset used for training or evaluation.
Apply a lighter version, safe formats and no remote code at minimum, even for experiments, because
laptops hold credentials too. Measure the fraction of production models served from the registry (target
100%), the share of artifacts with recorded hashes and signatures, scan coverage at ingestion, and the
time from upstream release to promoted availability. In interviews, expect "a team wants to use a
community fine-tune; what do you check?" and be ready to separate what you can verify (format, hash,
code, behaviour on your evals) from what you cannot (absence of backdoors).

## Role lens
- **Engineer:** Writes loaders that enforce safetensors, pinned revisions and hash checks, and adds
  picklescan and dependency audits to the ingestion CI job.
- **Architect:** Owns the registry design: quarantine and promotion flow, signing, AI-BOM contents, egress
  policy for training and serving clusters, and the acceptance evals a model must pass.
- **FDE:** Explains to a customer's security team where each model and dataset came from, with hashes and
  attestations, and adapts the pipeline to their air-gapped or regulated environment.

## Check yourself
**Q:** Your team wants to adopt a community fine-tune of an open-weight model, published by an individual
account, because it tops a leaderboard for your task. Walk through what you would check before it reaches
production and what you cannot check.

**A:** First the artifact: does the repository ship safetensors only, or pickle files; does it require
remote code, and if so what does that code do; are there unexpected files. Pin the exact commit, record
hashes, scan with picklescan or modelscan, and ingest through the registry sandbox with no network. Then
provenance: who is the account, what base model and dataset do they claim, is the licence compatible, and
does the model card match the file sizes and architecture. Then behaviour: run your own task evals plus
safety and refusal suites and compare to the base model, because fine-tunes frequently trade safety for
benchmark gains, and check for suspicious behaviour on inputs with unusual tokens or phrases. What you
cannot check is the absence of a backdoor trained into the weights or subtle data poisoning; there is no
general detector. So the decision also weighs blast radius: an internal classifier is tolerable, a
customer-facing agent with tools is not, unless you can reproduce the fine-tune yourself from an audited
dataset.

## Quiz
### Q: Why is a safetensors checkpoint safer to load than a pickle-based one?
- [ ] It is encrypted, so only authorised users can read the weights
- [x] It contains only tensors and a JSON header, so loading involves no code execution path
- [ ] It is smaller, so there is less room to hide a payload
- [ ] It is signed by the hub automatically on upload
> Pickle deserialization executes arbitrary callables; safetensors is parsed as plain data, which removes
> the entire class of load-time code execution regardless of who produced the file.

### Q: Why pin a model by commit hash instead of by repository name?
- [ ] Hashes download faster because the hub can cache them
- [ ] The name may contain characters that break the tokenizer
- [x] The name and its default branch are mutable, so a compromised or careless maintainer can change what they point to
- [ ] Pinning by hash disables remote code automatically
> A name is a pointer; a content hash is the content. Reproducibility and integrity both require that the
> thing you reviewed is byte-for-byte the thing you deploy.

### Q: Which supply-chain risk cannot be addressed by scanning the artifact's bytes?
- [ ] A pickle opcode that imports `os` and runs a shell command
- [ ] A truncated file that fails its hash check
- [x] A backdoor trained into the weights that activates on a trigger phrase
- [ ] A repository that requires `trust_remote_code=True`
> Scanners detect dangerous code and integrity failures, but backdoored behaviour is encoded in ordinary
> floating-point weights; only behavioural evaluation, provenance and limiting blast radius address it,
> and none of them fully.
