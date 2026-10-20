---
id: ai-control-plane-vs-data-plane
module: llmops-platform
name: AI control plane vs data plane
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - system-design/ai-gateway
  - llmops-platform/model-registry
  - llmops-platform/prompt-registry
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - llmops-platform/centralized-vs-federated-ai-platform
  - agent-runtime-architecture/policy-enforcement-across-agents-and-tools
  - reliability/tenant-level-quotas-and-noisy-neighbor-isolation
sources:
  - Official docs: Kubernetes components
  - Official docs: Envoy xDS protocol
  - Official docs: Istio architecture
---

## TL;DR
Split an AI platform into a data plane, the per-request path (gateway, routing, model call,
retrieval, guardrails, caching) that must be fast and stay up, and a control plane, the low-volume
system of record for what the data plane should do (registries, aliases, rollout percentages,
policies, budgets, keys, audit). The data plane reads versioned config snapshots and keeps serving
the last one if the control plane is down; the control plane changes behaviour by publishing a new
snapshot, never by redeploying the data plane.

## Why it exists
The two halves of an AI platform have opposite requirements. The request path handles thousands of
calls a second, where every millisecond the platform adds before the model starts lands directly in
time-to-first-token, and must keep working through partial failures. The management side handles a few changes an
hour: promote a bundle, change a canary percentage, add a tenant's model allow-list, rotate a key. It
must be consistent, audited and permissioned, and nobody cares if a change takes two seconds.

Mixing them fails in both directions. If the request path looks up the alias, the policy and the
budget in the management database on every call, the database's latency becomes part of every
request's p99 and its availability becomes the platform's availability; one maintenance window on
the config store is a total outage. If, to avoid that, the policy is baked into the deployed service,
every alias move or rollout change becomes a redeploy, so rollback takes as long as a build and
nobody can tell which configuration was live at 14:07 yesterday.

The split has a long history outside AI. Routers separate the forwarding path from the routing
protocols that compute the tables it uses. Kubernetes separates the API server, store and
controllers from the kubelets that run pods; a cluster keeps running workloads while the API server
is down, you just cannot change anything. Service meshes push configuration from a control plane to
sidecar proxies over a streaming protocol and the proxies keep their last configuration if the
control plane disappears. AI platforms inherit this *because* an AI gateway is a proxy with policy,
and model registries, prompt registries and rollout state are exactly the configuration such a
proxy needs. The split exists so that the thing that must be reliable is simple and reads local
state, and the thing that must be auditable is small and changes slowly.

## How it works
### What lives where
The **control plane** is the system of record: model registry and aliases, prompt registry and
bundle definitions, rollout state (which bundle is stable, which is the candidate, what percentage,
whether shadow mode is on), per-tenant and per-use-case policy (allowed models, data classification
routing such as "confidential tenants only to in-region models", budgets, rate limits), credential
management, evaluation gates and release approvals, the deprecation calendar, cost accounting and
the audit log of every change. It is low-QPS, strongly consistent, permissioned and boring.

The **data plane** is the request path: authenticate the caller against cached credentials, resolve
tenant and use case, pick the bundle according to rollout state, render the prompt, enforce policy
locally, route to the model with fallbacks, retrieve, execute tools, run guardrail checks, cache,
and emit telemetry. It is stateless with respect to configuration, horizontally scaled, and makes
no synchronous call to the control plane per request. In policy terms, the control plane is the
decision point and the data plane is the enforcement point, with the decisions precomputed into
configuration *because* a per-request decision call would put the control plane back on the hot path.

### Configuration as versioned snapshots
The control plane publishes its state as an immutable, monotonically numbered snapshot that contains
everything the data plane needs: aliases, bundles, rollouts, policies. Data-plane replicas pull it on
a short interval or receive it by push, verify its signature, and swap it in atomically. Whole
snapshots rather than per-key updates *because* a bundle and its model pin must change together; a
replica that applied the new prompt but not the new model for three seconds is serving a combination
nobody evaluated. Monotonic versions *because* two replicas that fetch from two control-plane nodes
must never regress. Every span the data plane emits carries the snapshot version, which is what makes
"what was live at 14:07" a query and makes rollout and rollback attributable.

### Fail static
If the control plane is unreachable, the data plane keeps serving its last snapshot. Replicas also
persist the last snapshot locally so a cold start during a control-plane outage still comes up. The
consequence is that during such an outage you cannot change anything, including rolling back, so the
design needs a break-glass path: an operator-supplied local override snapshot that replicas accept
with a logged, time-limited exception. This is the same trade the meshes make: availability of
serving over freshness of configuration.

### Telemetry flows up
The data plane emits spans, usage and cost tagged with tenant, bundle, model and snapshot version.
The control plane consumes the aggregates: budgets are enforced against them, canary analysis reads
the candidate-versus-stable comparison from them, and a failed canary results in the control plane
publishing a snapshot with the rollout reverted. The feedback loop is asynchronous by design;
per-request decisions use local counters, and precise global quotas use a shared fast store that is
part of the data plane, not the control plane.

### Where the gateway fits
An AI gateway is a data-plane component. Its admin API, configuration database and UI are control
plane. Many gateway products ship both in one process, which is fine for one team; at platform scale
the gateway runs as stateless replicas consuming snapshots, and the admin side is a separate service
with its own availability target, access control and audit.

## Example
A minimal data-plane handler: it starts from the snapshot persisted on local disk, refreshes it in the
background, keeps serving the old one when the control plane is unreachable, picks the bundle with a
sticky canary bucket, enforces tenant policy locally, and stamps the snapshot version on telemetry.
Signature verification is left out for brevity. `call_model` and `emit_span`
are the application's existing functions.

```python
import hashlib, json, threading, time, urllib.request

LOCAL = "/var/lib/gateway/last_snapshot.json"
CONF = json.load(open(LOCAL))      # last-known-good from disk, so a cold start survives an outage

def refresh(url="http://control-plane/config/current", every=30):
    """Pull the control plane's snapshot; keep serving the old one if it is unreachable."""
    global CONF
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                snap = json.load(r)
            if snap["version"] > CONF["version"]:        # monotonic: a stale node never wins
                CONF = snap                               # whole-snapshot atomic swap
                json.dump(snap, open(LOCAL, "w"))         # persist for the next cold start
        except Exception as e:
            print(f"control plane unreachable ({e}); still serving config v{CONF['version']}")
        time.sleep(every)

threading.Thread(target=refresh, daemon=True).start()

def bucket(key):                                           # stable across replicas, unlike hash()
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 100

def resolve(c, use_case, tenant):
    r = c["rollouts"].get(use_case)      # {"stable": "support@41", "candidate": "support@42", "percent": 5}
    if r and bucket(f"{use_case}:{tenant}") < r["percent"]:
        return r["candidate"]               # sticky per tenant: percent counts tenants, not requests
    return r["stable"] if r else c["aliases"][use_case]

def handle(tenant, use_case, messages):
    """Data-plane path: every decision is a local lookup; no control-plane call per request."""
    c = CONF                                               # read the pointer once per request
    bundle = resolve(c, use_case, tenant)
    model, prompt = c["bundles"][bundle]["model"], c["bundles"][bundle]["prompt"]
    allowed = c["policy"].get(tenant, {}).get("allowed_models")
    if allowed is not None and model not in allowed:
        return {"error": "model not allowed for tenant", "config_version": c["version"]}
    resp = call_model(model, prompt, messages)
    emit_span(tenant=tenant, bundle=bundle, model=model, config_version=c["version"], usage=resp.usage)
    return resp
# A canary is snapshot v+1 with percent=5; promotion sets stable=support@42, percent=0; rollback sets
# stable=support@41. None of them redeploys this process.
```

## Trade-offs
- Snapshot-based configuration removes any per-request dependency on the control plane, but changes
  propagate within the refresh interval; a kill switch that must act in seconds needs a push channel
  or a shorter interval for a small emergency subset of the config.
- Local policy enforcement is fast, but per-replica counters make budgets and rate limits approximate;
  precise global quotas need a shared counter in the data plane, which reintroduces a dependency you
  must size and protect.
- Separate control and data services scale and fail independently, at the cost of two deployables and
  two on-call surfaces; for one team with one application, a gateway with an embedded admin store is
  the right size until the second team arrives.
- Fail static keeps serving through control-plane outages but freezes rollbacks too; a break-glass
  override restores agility and must itself be audited, or it becomes the way around every policy.
- Immutable, versioned snapshots give full attribution and reproducibility, but a busy platform
  publishes thousands of them; retention and compaction become real operational tasks.

## Pitfalls
- Calling the registry or policy database on every request; the symptom is request p99 tracking the
  config database's latency, and a config-store maintenance window turning into a serving outage.
- Hot-reloading configuration key by key; the symptom is a burst of errors or odd answers for a few
  seconds after each change, when a new prompt ran against the old model or tool set.
- Bucketing the canary per request instead of per tenant or user; the symptom is a user flipping
  between bundle versions mid-conversation and support tickets nobody can reproduce.
- Using Python's built-in `hash()` for buckets; the symptom is "sticky" assignments that differ per
  replica, because string hashing is randomised per process.
- Omitting the snapshot version from telemetry; the symptom is a canary analysis that cannot tell
  which traffic ran under which rollout percentage, and a rollback whose effect cannot be dated.
- No break-glass path; the symptom is a bad bundle live during a control-plane outage with every
  engineer able to see the problem and nobody able to change the alias.

## When to use
The split is worth making explicit as soon as configuration changes more often than code deploys, or
as soon as more than one application shares the gateway, registries and policies. Below that, a
single gateway process with its own small database is fine, provided it already reads config into
memory rather than per request. Measure it by two numbers: the number of control-plane calls per
request (target zero) and the time from an alias move to the last replica serving it. In daily work
the split shows up as "promote", "canary to 5%" and "roll back" being control-plane operations that
take seconds and leave an audit entry, while the data plane is only ever redeployed for code changes.
In interviews, expect to be asked what still works when the control plane is down and how a rollback
happens without a deploy.

## Role lens
- **Engineer:** Implements snapshot refresh, atomic swap, sticky bucketing and local policy checks in
  the gateway or application, and stamps the config version on every span.
- **Architect:** Draws the boundary: which decisions are precomputed into snapshots, the refresh and
  push strategy, the fail-static and break-glass rules, and the availability target of each plane.
- **FDE:** Deploys data-plane replicas inside the customer's network or region while the control plane
  stays central, and explains why their rollback is an alias move rather than a redeploy.

## Check yourself
**Q:** The control plane's database is unavailable for two hours in the middle of the day. In a
well-designed AI platform, what keeps working, what stops, and what does the answer tell you about
the design?
**A:** Serving keeps working: every data-plane replica holds the last snapshot in memory and on local
disk, so requests resolve bundles, enforce policy and route to models as before, and new replicas can
still start from the persisted copy. Telemetry keeps flowing to the collector, since that is a
data-plane concern. What stops is change: no promotions, no canary adjustments, no new tenants, no key
rotations, and, importantly, no rollback through the normal path. Budget enforcement degrades to
local counters. If any of the serving path stopped, the design had a synchronous dependency on the
control plane that should not exist. If rollback was impossible for two hours, the design lacked a
break-glass override. If nobody could later say which snapshot was live during the window, spans
were not stamped with the version. The outage is a test of exactly those three properties.

## Quiz
### Q: Which of these belongs in the control plane rather than the data plane?
- [ ] Rendering the prompt template for a request
- [ ] Running the guardrail classifier on a completion
- [x] Changing the canary percentage for a use case's candidate bundle
- [ ] Choosing the fallback model after a provider timeout
> Rollout state is configuration the control plane publishes; the data plane reads it. Rendering,
> guardrail inference and fallback selection all happen per request on the serving path.

### Q: Why does the data plane consume whole versioned snapshots instead of hot-reloading individual keys?
- [ ] Snapshots are smaller on the wire than key-by-key updates
- [x] Related settings such as a bundle's prompt and model pin must change together, and a version number makes behaviour attributable
- [ ] Control-plane databases cannot stream individual changes
- [ ] Hot reloading is unsupported by gateway products
> Applying keys independently creates combinations nobody evaluated for a few seconds after each
> change. An atomic snapshot with a monotonic version avoids that and gives every span a config id.

### Q: What should happen when the data plane cannot reach the control plane?
- [ ] Reject requests, since policy cannot be verified
- [ ] Fall back to a default model with no policy
- [x] Keep serving the last known snapshot and log that configuration is stale
- [ ] Redeploy the data plane with configuration baked in
> Fail static is the design goal: availability of serving outranks freshness of configuration. The
> trade is that changes, including rollbacks, wait for the control plane or a break-glass override.
