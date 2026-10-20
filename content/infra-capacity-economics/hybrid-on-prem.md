---
id: hybrid-on-prem
module: infra-capacity-economics
name: Hybrid / on-prem
kind: pattern
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/cloud-ai-architecture
  - infra-capacity-economics/gpu-infrastructure
  - infra-capacity-economics/break-even-analysis
  - infra-capacity-economics/tco
  - system-design/self-host-vs-api
  - system-design/model-routing-cascading
  - deployment/customer-vpc-on-prem-air-gapped
  - security-governance-compliance/data-residency
sources:
  - Official docs: vLLM
  - Official docs: Kubernetes
---

## TL;DR
Hybrid AI infrastructure runs some inference on hardware you control (your datacenter, a colo, a
customer's VPC or an air-gapped site) and the rest on hosted model APIs, with a gateway routing each
request by data classification, latency need and model need. It exists because some data cannot
leave, some sites lack connectivity, and high steady volume can be cheaper on owned GPUs,
while on-prem alone loses frontier models and elasticity. The hard parts are the routing policy, a
uniform API surface, and keeping fixed capacity busy.

## Why it exists
Three forces push inference on-prem. Regulation and contracts: health, financial, defence and
public-sector data often may not be processed outside a named facility, jurisdiction or the
customer's own account, and "the provider promises not to train on it" does not satisfy that rule.
Physics and connectivity: a factory, ship, hospital or retail site may need inference near the
machines generating the data, or have links that are slow, metered or intermittent. Economics: a
workload that runs flat-out all day on open-weight models can cost less on amortised owned GPUs
than on per-token prices, once utilization is high enough.

Two forces push the other way. The strongest models are available only as hosted APIs, and most
products want them for at least some tasks. Elasticity: owned GPUs are a fixed quantity bought on a
procurement cycle measured in months, so a traffic spike or a new use case cannot be met by
"buying more" this week. On-prem also means owning the operations: drivers, firmware, CUDA
stacks, hardware failures, spares, power and cooling, security patching of the serving stack.

The naive answers both fail. "Everything on-prem" leaves teams on last year's open-weight model,
unable to serve a launch spike, and paying a platform team for work the cloud would have done.
"Everything in the cloud" fails the first compliance review of the regulated workload and the
first site with a bad link. Hybrid is the admission that different requests have different
constraints and the infrastructure should route accordingly, which turns the question into a
policy problem: which request goes where, enforced by a gateway, with the application unaware.

## How it works
### What "on-prem" covers
The term spans a spectrum: your own datacenter; colocation space with your hardware; a private
cloud; a customer's cloud account or VPC (cloud hardware, customer control); cloud-provider
on-premises extensions that place the provider's managed stack in your facility (offerings such as
AWS Outposts, Azure's on-premises stack and Google Distributed Cloud are examples of this
category); and fully air-gapped sites with no external connectivity at all. The control, cost and
operational burden differ at each point, so "on-prem" in a requirement must be pinned down before
anything is designed. The deployment module covers the customer-VPC and air-gapped cases in depth
from the forward-deployed perspective; this lesson covers the architecture that spans both sides.

### The routing policy
Every request carries attributes the gateway can read: the data classification of its inputs,
the latency budget, the model capability required, the tenant and the site. The policy maps those
to a target:

- **Restricted data** → on-prem pool only, never bursts, no fallback to cloud. The rule is
  enforced at the gateway *because* enforcing it in each application means every application is
  a compliance risk.
- **Internal data, frontier capability needed** → cloud platform in the approved region, through
  private connectivity.
- **Public or synthetic data, bulk volume** → on-prem when capacity is free (it is already paid
  for), cloud burst when the on-prem queue exceeds a wait threshold.
- **Site-local, low latency or offline** → the site's own small pool, with a degraded mode when
  the central systems are unreachable.

Classification comes from the data source (document labels, database column tags, tenant
contract), not from inspecting the prompt at request time, since inspection is a detector with a
miss rate and the rule must hold at the boundary regardless.

### A uniform API surface
Application code should not know which side served it. On-prem engines (vLLM, TGI and others)
expose OpenAI-compatible endpoints, and hosted platforms are reached through their SDKs; the
gateway presents one interface and holds the policy, the credentials for each side, the quotas and
the telemetry. This is what makes moving a workload from one side to the other a configuration
change. The exception is behaviour: an open-weight model on-prem and a frontier model in the cloud
answer differently, so prompts and evals are per route, and a workload that may be served by either
side must pass its eval on both.

### Bursting and backflow
Cloud burst works like an overflow queue: when the on-prem pool's queue wait exceeds a threshold,
eligible requests (eligibility is the classification again) go to the cloud. For workloads allowed
on both sides, on-prem first with cloud overflow is the right default, because the owned cost is
already sunk while every cloud token is a marginal cost. Backflow is the reverse movement: deferrable
eligible work (batch jobs, re-indexing, evaluation runs) is pulled back onto owned GPUs whenever they
would otherwise idle, for example overnight, so the fixed pool stays busy instead of the cloud bill
growing.

### Operating the owned side
A fixed pool is only cheaper when busy, so on-prem capacity should not be sized for the peak of all
traffic. The sizing has two parts: restricted work has nowhere else to go, so its *peak* (plus a
spare for a node failure) must fit on-prem; burst-eligible work is sized only at its steady base,
filling the gaps the restricted peak leaves, and its peaks go to the cloud. The owned side needs a hardware lifecycle
(procurement lead time, spares, replacement cadence, typically a three-to-five-year amortisation
for the TCO model), a serving-stack lifecycle (engine upgrades, CVE patching, driver and CUDA
compatibility), monitoring unified with the cloud side, and a model lifecycle (which open-weight
models, which quantization, evaluated against which cloud baseline). Connectivity between sides is
private (VPN, dedicated links or private endpoints) and secrets for each side are held only where
they are used, so that a compromised on-prem node does not hold cloud credentials and vice versa.

### Economics in one line
Owned cost per token = (amortised hardware + power + space + people) ÷ tokens actually served, so
it falls with utilization; hosted cost per token is roughly flat. The crossover utilization is the
break-even, covered in its own lesson; the hybrid pattern's job is to keep the owned side above it
by giving it all the eligible work.

## Example
A gateway routing policy that enforces classification, routes for capability and bursts only
eligible traffic. Thresholds and model names are placeholders.

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class DataClass(Enum):
    RESTRICTED = "restricted"   # may never leave the owned environment
    INTERNAL = "internal"       # cloud allowed in the approved region, via private link
    PUBLIC = "public"

@dataclass
class Request:
    data_class: DataClass
    needs_frontier: bool        # task requires a capability only the hosted model has
    latency_budget_ms: int
    site: str | None = None     # set for site-local traffic

ONPREM_QUEUE_WAIT_MS = {"central": 0, "plant-7": 0}   # refreshed from engine metrics
BURST_THRESHOLD_MS = 800

def route(r: Request) -> str:
    if r.data_class is DataClass.RESTRICTED:
        if r.needs_frontier:
            raise PermissionError("restricted data cannot use the hosted frontier model")
        return "onprem:central"                          # no burst, no fallback, by policy
    if r.site and r.latency_budget_ms < 300:
        return f"onprem:{r.site}"                        # site-local pool, degraded mode if central is down
    if r.needs_frontier:
        return "cloud:eu-region"                         # private endpoint, approved region
    if ONPREM_QUEUE_WAIT_MS["central"] > BURST_THRESHOLD_MS and r.latency_budget_ms < 5_000:
        return "cloud:eu-region"                         # eligible burst: internal/public data only
    return "onprem:central"                              # owned capacity is sunk cost: keep it busy

print(route(Request(DataClass.RESTRICTED, False, 2000)))        # onprem:central
print(route(Request(DataClass.INTERNAL, True, 2000)))           # cloud:eu-region
ONPREM_QUEUE_WAIT_MS["central"] = 1500
print(route(Request(DataClass.PUBLIC, False, 2000)))            # cloud:eu-region (burst)
```

The policy refuses rather than degrades when restricted data needs a capability only the cloud
has; that refusal is the signal to procure a better on-prem model, not a bug to route around.

## Trade-offs
- Owned GPUs lower cost per token at high utilization, but they are a fixed quantity with months of
  lead time; the trade flips against ownership when load is spiky or still growing fast.
- On-prem satisfies residency and air-gap rules, but confines those workloads to open-weight models
  that typically trail the frontier; when the task needs frontier quality, the choice is a better
  on-prem model or a policy change, not a routing trick.
- Bursting to the cloud absorbs peaks without over-buying hardware, but every burst path is a data
  path that must be gated by classification, and prompts must be evaluated on both models.
- A uniform gateway interface makes migration a configuration change, but it hides behavioural
  differences between routes, which is why evals and prompts must stay per route.
- Cloud-provider on-premises extensions reduce the operations burden of owned hardware, but keep
  the provider's pricing and lock-in inside your building.

## Pitfalls
- Sizing on-prem for the peak of all traffic, including work that could burst to the cloud; the
  symptom is a GPU fleet averaging well under a quarter utilization while the finance model assumed
  near-full.
- Classification by prompt inspection instead of by source; the symptom is a restricted document
  found in cloud logs because a detector missed it.
- One prompt tuned on the cloud model and reused on the on-prem model; the symptom is a quality
  regression that only appears during bursts or on-prem fallback.
- No spare hardware or replacement plan; the symptom is weeks of reduced capacity after a single
  GPU or node failure.
- Cloud credentials stored on on-prem nodes for "convenience"; the symptom is a security review
  finding that an on-prem compromise exposes the cloud account.
- Treating a customer VPC deployment as "on-prem" in the TCO model; the symptom is a cost estimate
  that omits the customer's cloud bill and the provider's egress and support terms.

## When to use
Reach for hybrid when a named class of data or sites cannot use hosted APIs *and* other workloads
need frontier models or elastic capacity. Stay fully hosted when no residency or connectivity
constraint exists and volume is below the break-even. Go fully on-prem only when policy forbids
every hosted path and open-weight models meet the quality bar. Measure owned-pool utilization,
burst volume and its cost, per-route quality on shared evals, and the number of policy refusals,
which tells you where the on-prem model is falling short. In day-to-day work this shows up as the
gateway's policy table and the quarterly review of what each side costs; in interviews, expect to
defend why classification is enforced at the gateway and why on-prem is sized for the restricted
peak plus the base of eligible work rather than for total peak.

## Role lens
- **Engineer:** Builds the gateway policy and burst logic, runs OpenAI-compatible engines on the
  owned side, and maintains per-route prompts and evals so either side passes the same test suite.
- **Architect:** Defines the data classes and their permitted targets, sizes the owned pool to the
  base load, owns the TCO and break-even model, and signs off on the connectivity and secrets design.
- **FDE:** Deploys the on-prem or customer-VPC side inside the customer's environment, maps their
  data classifications to routes, and sets up burst eligibility and monitoring that the customer's
  security team accepts.

## Check yourself
**Q:** A hospital group wants an assistant over patient records (must stay on-site), a general
staff assistant, and overnight batch summarisation of public research papers. They own eight
GPUs. Design the hybrid layout and say what you would measure to know it is working.
**A:** Patient-record traffic is restricted: it runs on the owned pool with an open-weight model,
no burst, no fallback, enforced by the gateway from the record system's classification, and the
model is evaluated on clinical tasks before launch. The staff assistant needs frontier quality and
touches internal but not restricted data, so it goes to a cloud platform in an approved region
over a private endpoint. The research batch is public data: it runs on the owned pool overnight
when the restricted workload is quiet, bursting to the cloud batch API if it cannot finish by
morning. Size the owned pool for the restricted workload's peak (it cannot burst) with one GPU
or node of spare, and let the overnight batch fill the idle hours, not for the combined peak of all
three workloads.
Measure owned-pool utilization (target high, since cost per token depends on it), queue wait and
burst volume, per-route eval scores, and policy refusals, which show where the on-prem model is
insufficient.

## Quiz
### Q: Why should data classification for routing be derived from the data source rather than from inspecting the prompt?
- [ ] Prompt inspection is too slow for interactive latency budgets
- [x] A detector has a miss rate, while source-level labels make the restriction hold at the boundary regardless of prompt content
- [ ] Prompts are encrypted before they reach the gateway
- [ ] Source labels are required by the serving engine to allocate KV cache
> Compliance rules are absolute; a classifier that misses one restricted document has already
> violated them. Labels attached to the record system, tenant or document travel with the request
> and are enforced deterministically.

### Q: Which sizing rule keeps owned GPUs economical in a hybrid design?
- [ ] Size on-prem for peak so the cloud is never needed
- [x] Fit the restricted workload's peak, add the steady base of cloud-eligible work, and send eligible peaks to the cloud
- [ ] Size on-prem for the restricted workload's average load and send everything else to the cloud
- [ ] Size on-prem to match the cloud quota in the approved region
> Owned cost per token falls with utilization, so the pool must be busy, but restricted work cannot
> burst, so its peak sets a floor. Filling the remaining capacity with the base of eligible work
> keeps the pool full, while elastic cloud capacity absorbs eligible peaks that would otherwise sit
> idle on owned hardware; sizing restricted work at its average would drop requests at its peak.

### Q: A restricted-data workload needs a capability only the hosted frontier model has. What should the gateway do?
- [ ] Route to the cloud once with logging so the exception is auditable
- [ ] Strip identifying fields from the prompt and route to the cloud
- [x] Refuse the request and surface it as a signal to improve the on-prem model or change policy
- [ ] Route to the cloud only during off-peak hours when scrutiny is lower
> The restriction is on the data, not on the time or on identifiers that a detector might miss. The
> correct response is a policy refusal, which becomes evidence for procuring a stronger on-prem model
> or formally revisiting the rule.
