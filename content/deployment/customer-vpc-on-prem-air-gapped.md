---
id: customer-vpc-on-prem-air-gapped
module: deployment
name: Customer VPC / on-prem / air-gapped deployment
kind: pattern
difficulty: core
est_minutes: 18
related:
  - deployment/private-endpoints
  - deployment/vpn
  - deployment/terraform
  - deployment/compliance-constraints
  - infra-capacity-economics/hybrid-on-prem
  - infra-capacity-economics/cloud-ai-architecture
  - llmops-platform/ai-control-plane-vs-data-plane
  - safety-security/supply-chain-security-for-models-and-datasets
sources:
  - Official docs: Helm
  - Official docs: Kubernetes
  - Official docs: Sigstore (cosign)
---

## TL;DR
Three deployment targets where the customer, not you, owns the perimeter: in a **customer VPC** your
software runs inside their cloud account; **on-prem** it runs on their hardware and their Kubernetes;
**air-gapped** it runs with no network path to the outside at all. They exist because some data may
not leave the customer's custody. Each step inward trades your operability (telemetry, upgrades,
hosted frontier models) for their control, so the engineering job becomes making the product
installable, verifiable and upgradable without the network you are used to.

## Why it exists
A hosted SaaS deployment assumes the customer will send you their data. For a bank's transaction
records, a hospital's clinical notes, a defence contractor's documents or a government's citizen data,
that assumption fails before any technical discussion starts: the contract, the regulator or the
customer's own classification policy says the data stays inside a perimeter they control. "We encrypt
it and promise not to train on it" does not satisfy a rule about *where processing happens* and *who
can be compelled to hand it over*.

The naive answer is to copy your SaaS stack into their environment and run it as if it were yours.
That fails in three ways. First, your stack quietly depends on the open internet: container images
pulled from public registries, model weights fetched from a hub at start-up, Python packages installed
at build time inside their network, licence checks and feature flags served from your SaaS, traces
shipped to an observability vendor. Each of those is an outbound connection a security review will
question and an air gap will simply break. Second, you no longer own the platform: their Kubernetes
version, their storage classes, their certificate authority, their egress proxy, their identity
provider. Third, you lose your eyes: no dashboards, no logs, no ability to hot-fix at 2 a.m.

So the pattern is not "run the same thing elsewhere". It is a different product shape: a signed,
self-contained bundle, a configuration surface that captures every environment-specific decision,
and an operating model in which the customer's platform team, not you, holds the keys. The further
inward you go, the more of your product becomes packaging and the less of it is infrastructure you
operate.

## How it works
### Three targets, one spectrum
**Customer VPC (often sold as "bring your own cloud").** Your containers run in the customer's AWS,
Azure or GCP account, in a VPC/VNet they own. Identity is their IAM; storage, databases and KMS keys are
theirs; model calls go to the cloud's managed model service (Bedrock, Azure OpenAI, Vertex AI) over a
private endpoint, or to open-weight models on GPU instances in the same VPC. Your access, if any, is a
cross-account role with a time-bound, logged session and an `ExternalId` condition to prevent the
confused-deputy problem. Many vendors split the system: a vendor-hosted **control plane** (licensing,
version catalogue, configuration, upgrade orchestration) and a customer-hosted **data plane** (every
component that touches customer data). The split is attractive *because* the customer's requirement
is about data custody, not about metadata, so you keep operability for everything that is not data.
It must be optional *because* some customers reject any outbound connection on principle.

**On-prem.** The customer's datacentre or colocation space. There are no managed services: you get a
Kubernetes distribution (OpenShift, Rancher, vanilla upstream, k3s) at whatever version they run,
their storage classes, their ingress controller, GPUs exposed through a device plugin or the NVIDIA GPU
Operator, an internal CA, and identity via their directory through OIDC or LDAP. Outbound traffic, if
allowed at all, goes through a proxy that often terminates TLS with its own certificate. Your
deliverable is a Helm chart or operator plus images in a registry they can reach, usually a mirror
they populate from your registry.

**Air-gapped.** Everything on-prem implies, plus: no network path exists. Artifacts cross the boundary
on physical media or through a one-way transfer device (a data diode or a cross-domain solution).
Inside, there is a mirrored container registry, a package mirror, their own time source and their own
CA. Nothing can call home, so there is no licence server, no telemetry, no automatic update, and no
hosted model. Updates are a new bundle carried in; support is a log bundle carried out.

### The delivery pipeline
1. **Build deterministically.** Pin every dependency, build images from a locked manifest, and make
   sure nothing happens at container start that needs the network. Model weights are baked into an
   image or seeded into a persistent volume from the bundle; `HF_HUB_OFFLINE=1` and
   `TRANSFORMERS_OFFLINE=1` turn Hugging Face libraries into pure local loaders; tokenizer files are
   shipped, not fetched.
2. **Sign and attest.** Sign images and the bundle manifest (cosign is the common choice), ship an SBOM
   (SPDX or CycloneDX), and publish SHA-256 hashes. This is done *because* the receiving side cannot
   ask you "is this file right?" and *because* the security review will ask how they know the
   artifact is yours and unmodified.
3. **Package.** Images exported with `docker save` or `skopeo copy` into a tarball or OCI layout, the
   chart, the weights, a wheelhouse built with `pip download` for anything installed on-site, the
   docs, and a verify script.
4. **Transfer.** Registry-to-registry replication for VPC and connected on-prem; media or diode for
   the air gap. A 70B-parameter model in 16-bit precision is roughly 140 GB of weights (2 bytes per
   parameter), so the transfer itself is a planned activity, not a download.
5. **Install.** `helm install` with a values file that holds every environment decision: registry
   host, storage class, ingress class, CA bundle, proxy, model endpoint, identity provider, resource
   limits. Hard-coded assumptions are the single biggest source of failed installs.
6. **Verify.** Smoke tests that exercise the real path inside their network: a model call, a retrieval,
   an authenticated request, a write to their database.
7. **Operate.** Logs and metrics stay inside and flow to *their* backends (ship Prometheus and
   OpenTelemetry collector configs that point at them). A `support-bundle` command gathers redacted
   logs, config and version info into a tarball they can send you. Upgrade is a new bundle plus
   `helm upgrade`; rollback is the previous chart version, which only works if your schema migrations
   are backward compatible.

### What changes about the software itself
Licensing becomes a signed offline licence file with an expiry and a grace period, checked locally.
Feature flags move from a remote service into the values file. Every HTTP client must trust the
customer's CA bundle (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`) *because* their proxy re-signs traffic.
Secrets come from their secret store, not yours. Time-dependent behaviour (certificate expiry,
licence expiry, token lifetimes) must fail loudly and early *because* nobody will be watching a
dashboard when it fails silently. And the model is whatever you shipped: on-prem and air-gapped
customers run open-weight models on vLLM or similar, so your evaluation suite must have been run on
that exact model and prompt set before the bundle was cut.

## Example
A minimal bundle manifest tool. `build` runs on your connected side after packaging; `verify` runs on
the customer's side, before anything is loaded into their mirror registry, so a corrupted 140 GB copy
is caught before the install starts rather than during it. The manifest is then signed with
`cosign sign-blob` so the customer can check authorship as well as integrity.

```python
"""Build and verify an offline install bundle: `build <dir> <version>` or `verify <dir>`."""
import hashlib, json, sys, pathlib

def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()

def build(bundle_dir, version):
    root = pathlib.Path(bundle_dir)
    files = [p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"]
    manifest = {"version": version, "artifacts": {
        str(p.relative_to(root)): {"sha256": sha256(p), "bytes": p.stat().st_size} for p in files}}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1))
    total = sum(a["bytes"] for a in manifest["artifacts"].values())
    print(f"{len(files)} artifacts, {total / 1e9:.1f} GB")

def verify(bundle_dir):
    root = pathlib.Path(bundle_dir)
    manifest = json.loads((root / "manifest.json").read_text())
    bad = [rel for rel, meta in manifest["artifacts"].items()
           if not (root / rel).exists() or sha256(root / rel) != meta["sha256"]]
    print("OK" if not bad else f"CORRUPT OR MISSING: {bad}")
    return not bad

if __name__ == "__main__":
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "build":
        build(path, sys.argv[3])
    else:
        sys.exit(0 if verify(path) else 1)
# Typical layout: images/app-1.8.3.tar, charts/assistant-1.8.3.tgz, models/<name>/, wheels/, docs/
```

## Trade-offs
- The customer keeps custody of data and uses their own cloud discounts and controls, but you lose
  direct observability and fast iteration; this flips toward hosted SaaS plus a strong DPA when the
  customer has no platform team to operate what you ship.
- A control-plane/data-plane split keeps upgrades and configuration manageable across many
  customers, but any outbound connection is a review item; it flips to fully self-contained the
  moment the customer's policy forbids egress.
- On-prem removes cloud dependency and per-token fees, but GPU capacity is fixed on a procurement
  cycle measured in months; it flips when the workload is bursty or needs frontier-model quality.
- Air-gapped isolation is the strongest possible, but every bug fix is a physical delivery and bugs
  surface weeks later through log bundles; accept it only where mandated, and budget support time
  accordingly.
- Shipping open-weight models makes the product deployable anywhere, but your quality bar must be
  re-established per shipped model, since the hosted model your SaaS uses is not an option inside.

## Pitfalls
- Runtime downloads (model weights from a hub, tokenizer files, `pip install` on start, NLTK or
  tiktoken data): works in CI, then the pod crash-loops in the customer's cluster with DNS resolution
  or connection-timeout errors for a public hostname.
- Hard-coded registry host, DNS domain or storage class in the chart: the symptom is
  `ImagePullBackOff` or persistent volume claims stuck in `Pending` on an otherwise healthy cluster.
- Not trusting the customer's CA: every outbound call from your containers fails with a certificate
  verification error because their egress proxy re-signs TLS.
- Licence or certificate expiry inside an air gap: the system stops on a date, nobody can renew
  online, and the customer discovers it during business hours.
- Assuming you will have the logs: incidents are debugged from screenshots in email until a support
  bundle command exists and the customer knows how to run it.
- Version sprawl across customers: each site is on a different bundle, so a fix must be back-ported
  to several versions; the symptom is a support matrix nobody can keep in their head.
- Schema migrations that cannot roll back: a failed upgrade on-site leaves the customer unable to
  return to the previous chart version, turning a bad hour into a bad week.

## When to use
Reach for a customer-VPC deployment when the blocker is data custody within a cloud the customer
already runs; it keeps managed model services and most of your operability. Go on-prem when the
customer has no cloud footprint, needs GPU proximity to their systems or has a contractual
prohibition on public cloud. Accept air-gapped only when a regulator or classification policy
mandates it, and price the support model in. Before committing, measure what each tier costs you:
install time per customer, bundle size, number of supported versions, and mean time to deliver a fix.
In day-to-day work this shows up as the values file, the bundle pipeline and the support-bundle tool.
In interviews, the expected depth is being able to list what breaks when the network goes away and
how the delivery pipeline changes as a result.

## Role lens
- **Engineer:** Removes every runtime network dependency, moves environment decisions into chart
  values, builds the signed bundle and the support-bundle command, and keeps migrations reversible.
- **Architect:** Decides which deployment tiers the product supports, where the control-plane/data-plane
  boundary sits, and which models and dependencies each tier is allowed to assume.
- **FDE:** Runs the install on the customer's cluster, negotiates registry, CA, proxy and identity
  details with their platform and security teams, and operates the sneakernet for air-gapped sites.

## Check yourself
**Q:** A customer wants your RAG assistant inside an air-gapped facility. Today the product calls a
hosted frontier model, sends traces to a SaaS observability tool, downloads its embedding model from a
hub at start-up and validates a licence against your server. What has to change, in what order, and
what do you tell the customer they are giving up?
**A:** First replace the hosted model with an open-weight model served inside (vLLM or similar) and
re-run the evaluation suite on it, because that decides whether the product is still good enough to
ship. Then remove the start-up download by baking or seeding the embedding weights and setting the
offline flags; replace the licence call with a signed offline licence file with expiry and grace; and
redirect traces to a collector targeting the customer's own backend. Package images, chart, weights
and wheels into a signed bundle with a hash manifest and a verify step, and add a support-bundle
command. Tell the customer they lose frontier-model quality, automatic updates and vendor
observability; fixes arrive as bundles on a schedule, and they must operate the stack.

## Quiz
### Q: Why do many vendors split a customer-VPC deployment into a vendor-hosted control plane and a customer-hosted data plane?
- [ ] Because cloud providers require the control plane to run in the vendor's account
- [x] Because the customer's requirement is custody of data, so keeping only non-data functions vendor-side preserves operability while meeting the requirement
- [ ] Because it removes the need for the customer to grant the vendor any IAM access
- [ ] Because Kubernetes cannot run both planes in the same cluster
> The split keeps licensing, configuration and upgrades manageable across customers while every
> component that touches data stays in the customer's account. It is a design choice, not a platform
> rule, and it must be optional because some customers forbid any outbound connection.

### Q: An on-prem install crash-loops; the pod log shows a DNS resolution failure for a public model-hub hostname. What is the root cause and fix?
- [ ] The customer's cluster lacks a GPU operator, so the model cannot load
- [ ] The Helm chart has the wrong storage class configured
- [x] The application downloads model or tokenizer files at start-up; weights must be bundled and the libraries run in offline mode
- [ ] The customer's CA certificate is not trusted by the container
> A DNS failure for a hub hostname means code is trying to fetch artifacts at runtime. The fix is in
> the build: ship the weights and tokenizer files in the bundle and set the offline environment flags.
> A CA problem would show as a certificate verification error, not a resolution failure.

### Q: Which artifact lets an air-gapped customer confirm a bundle is complete, unmodified and genuinely from you without contacting you?
- [ ] A README listing the included files and their versions
- [ ] A licence file with an expiry date
- [x] A manifest of SHA-256 hashes signed with the vendor's key, plus signed images
- [ ] A telemetry ping sent once the installation completes
> Hashes prove integrity and a signature over the manifest proves origin, and both can be checked
> offline. A README is unverifiable, a licence file proves entitlement rather than integrity, and a
> telemetry ping is impossible without a network path.
