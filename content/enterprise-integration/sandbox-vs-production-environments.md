---
id: sandbox-vs-production-environments
module: enterprise-integration
name: Sandbox vs production environments
kind: practice
difficulty: core
est_minutes: 13
status: reviewed
related:
  - enterprise-integration/enterprise-connectors
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - enterprise-integration/data-onboarding
  - llmops-platform/canary-shadow-deployments
  - customer-data-evaluation/shadow-mode-validation
  - deployment/uat
  - deployment/rollout-criteria-and-rollback-triggers
  - backend-fundamentals/secrets-management
sources:
  - Humble & Farley, 2010 — Continuous Delivery
  - Official docs: Salesforce (sandboxes)
---

## TL;DR
Customer systems come in pairs: a production instance with real data and real side effects, and
sandboxes (Salesforce sandboxes, SAP development and quality systems, ServiceNow sub-production
instances) that resemble it but differ in data, configuration, integrations and limits. An AI
integration is built and verified against the sandbox, promoted to production deliberately, and
prevented by hard guards from mixing the two, because a system that reads stale sandbox data behaves
differently from one reading production, and one that writes to production by mistake is an incident.

## Why it exists
Software teams already separate development, staging and production. Enterprise integration adds a
second axis: the customer's systems have their own environments, created by the customer on their
schedule, under their rules. A Salesforce org has sandboxes that are copies of production taken at a
point in time; an SAP landscape moves configuration through development and quality systems before
production; ServiceNow customers clone production into sub-production instances. Your integration has
to bind each of your environments to one of theirs, and the differences between a sandbox and production
are where AI integrations fail.

The failures are specific. An agent that creates cases or sends emails cannot be developed against
production, because every test run would have real side effects on real customers, so you build against
the sandbox. But the sandbox's data is a copy from weeks ago, may be a subset, and may be masked;
prompts, retrieval settings and extraction rules tuned on it meet a different distribution in
production. Ids drift apart: some platforms keep record ids when they copy production (Salesforce full
and partial copies do), but anything created afterwards on either side, and every record in a developer
sandbox, gets its own id, so an id you hard-coded in a prompt or a filter may work in UAT by coincidence
and point at nothing, or at the wrong record, in production. Integrations the customer disabled in the sandbox (outbound
email, callouts to other systems) silently work in production. The sandbox's API quota and performance
are not production's.

The naive alternatives both fail. "Test in production with a test account" leaks test data into
production records, logs and the retrieval index, and the test account's permissions never match a real
user's. "Assume the sandbox is production" ships behaviour that was never exercised on real data, real
volume or real permissions. The practice is to use the sandbox for what it is good at (functional
correctness, side effects, permissions, schema), to use production-safe methods (read-only checks, shadow
mode, canaries) for what only production can show, and to make cross-wiring impossible rather than
merely discouraged.

## How it works
### The environment matrix
List your environments on one axis and the customer's on the other and fill in exactly one binding per
row: development → developer sandbox, staging or UAT → the customer's UAT or full-copy sandbox,
production → production. No row ever points at two of theirs, and no two rows share one. A binding is a versioned configuration artefact: base URL,
the *name* of the credential in the secret store (never the credential itself), whether writes are
allowed, which index namespace ingested data lands in, and which identity provider issues tokens.
Credentials are per binding *because* a customer's sandbox typically has its own connected app, API
client or service account, and because a production credential that exists in a non-production secret
store is the cross-wiring waiting to happen.

### What differs, and what to do about each
- **Data.** Sandboxes are point-in-time copies, often partial and sometimes masked. Use them for
  functional tests, and seed them with versioned fixtures you can re-create after every refresh. Do not
  tune prompts or thresholds on masked data alone: a name replaced by `Contact 00123` changes what an
  extraction prompt sees. Evaluate on a sample of real data under the customer's data agreement, or in
  shadow mode in production.
- **Identifiers.** Record ids, picklist ids, user ids and object ids cannot be assumed equal between a
  sandbox and production: even where a copy preserves the ids that existed at copy time, records and
  metadata created later on either side diverge. Reference everything by stable business keys or external ids, and resolve ids at runtime
  through metadata or lookups, never in configuration.
- **Configuration drift.** Custom fields, validation rules and workflows move through the customer's
  own change process (change sets, transports, update sets), so a sandbox may be ahead of or behind
  production. Introspect the schema at startup and fail fast on a missing field, rather than discovering
  it as a 400 response in production.
- **Integrations and side effects.** Outbound email, callouts to other systems and scheduled jobs are
  commonly disabled or redirected in sandboxes. Keep an explicit list per binding, so a feature that
  "worked in UAT" is not sending its first real email in production.
- **Limits and performance.** Sandboxes often run on smaller capacity with different API quotas.
  Throughput is verified against production-shaped limits, either on a full-copy sandbox or in a
  controlled production canary.
- **Identity.** Users may not exist in the sandbox, or exist with different roles. Permission-sensitive
  behaviour (what the assistant may retrieve for whom) is verified with a seeded set of sandbox users
  that mirrors production roles.

### Guardrails against cross-wiring
Every outbound request carries the binding pair in its context and in its logs. The HTTP client checks
the destination host against the binding's allow-list and refuses anything else, so a mis-set environment
variable produces a loud error, not a write to production. Non-production bindings have no path to
production secrets; production bindings reject sandbox hosts, so sandbox data cannot be ingested into the
production index. Ingested records are tagged with the binding they came from, which is what lets you
prove later that no sandbox fixture sits behind a production answer.

The inbound direction needs its own guard, because the customer's side can cross-wire too. A sandbox
cloned from production inherits production's configuration, and that includes any webhook
subscription, outbound message or notification rule that points at *your production receiver*. After
the next refresh, sandbox test edits start arriving at your production ingestion endpoint looking like
real events. An outbound host allow-list cannot see this. The fix is to authenticate the sender per
binding: a separate webhook signing secret or receiver URL per customer environment, plus a check of the
source instance identifier that most platforms put in the payload or headers, so that events from an
unbound instance are rejected and counted rather than indexed.

### The promotion path
Your changes (prompts, mappings, flags, connector settings) move development → staging → production as
the same artefact with different bindings, aligned with the customer's release calendar. Sandbox
refreshes are events to plan for: a refresh replaces the sandbox's data and typically invalidates
credentials and seeded fixtures, so the staging integration breaks on the customer's schedule unless
re-seeding is automated. Full copies are refreshed rarely (a Salesforce full sandbox, for example, can be
refreshed at most once every 29 days), so a refresh is negotiated, not assumed.

### What only production can show
Behaviour on the real data distribution, real volume, real permissions and real integrations. The
production-safe tools for that are read-only smoke tests after deployment, shadow mode (the system runs
on live inputs without acting), and a canary to a small user slice with rollback criteria agreed in
advance. The sandbox proves the integration is *correct*; these prove it is *right for this customer*.

## Example
A binding per environment, resolved from a single environment variable with no default, and a guard
that fails loudly on any mismatch between the binding and the host it points at. `http` and `secrets`
and `index` stand for your HTTP client, secret-store client and retrieval index.

```python
import os
from dataclasses import dataclass
from urllib.parse import urlparse

@dataclass(frozen=True)
class Binding:
    ours: str              # dev | staging | prod
    theirs: str            # the customer's environment name
    base_url: str
    secret_ref: str        # name in the secret store; the value never lives in config
    writes_allowed: bool
    index_namespace: str   # where ingested data lands; never shared across bindings

BINDINGS = {
    "dev":     Binding("dev", "dev-sandbox", "https://dev-sandbox.crm.acme.example", "crm/dev", True, "acme-dev"),
    "staging": Binding("staging", "uat-sandbox", "https://uat-sandbox.crm.acme.example", "crm/uat", True, "acme-uat"),
    "prod":    Binding("prod", "production", "https://crm.acme.example", "crm/prod", True, "acme-prod"),
}
PROD_HOSTS = {"crm.acme.example"}

def binding() -> Binding:
    b = BINDINGS[os.environ["APP_ENV"]]       # KeyError if unset: there is no default, least of all prod
    host = urlparse(b.base_url).hostname
    if (host in PROD_HOSTS) != (b.ours == "prod"):
        raise RuntimeError(f"cross-wired: {b.ours} is bound to {host}")
    return b

def request(method: str, path: str, body=None):
    b = binding()
    if method != "GET" and not b.writes_allowed:
        raise PermissionError(f"writes are disabled in {b.ours}")
    return http(method, b.base_url + path, token=secrets.get(b.secret_ref), json=body,
                headers={"X-Env-Pair": f"{b.ours}->{b.theirs}"})   # tags your logs (and theirs, if logged)

def ingest(record: dict):
    b = binding()
    index.upsert(namespace=b.index_namespace, doc={**record, "env_pair": f"{b.ours}->{b.theirs}"})
```

The guard is deliberately dumb: it does not try to be clever about hostnames, it only refuses to run
when the two facts it knows (which binding, which host) disagree.

## Trade-offs
- A full-copy sandbox gives the most realistic testing but costs the customer money, refreshes slowly,
  and holds a copy of production data that now needs the same protection, which is why many customers
  only grant partial or developer sandboxes.
- Masking sandbox data protects PII but changes text distributions; the more a feature depends on free
  text (extraction, summarisation), the less a masked sandbox tells you and the more you need shadow
  mode on real traffic.
- Strict host allow-lists make cross-wiring impossible but add friction whenever the customer renames an
  instance or adds a sandbox; the friction is worth it wherever a write path exists.
- Separate credentials per binding multiply onboarding steps with the customer's admin; the alternative,
  one credential reused across environments, removes the only hard boundary you have.

## Pitfalls
- Hard-coding sandbox record ids in prompts, filters or fixtures; symptom: everything passes UAT, then
  production returns empty results or "record not found" on the first real request.
- Defaulting the environment to production when the variable is unset; symptom: a developer's laptop or
  a misconfigured CI job writes into the customer's live system.
- Forgetting that a sandbox refresh wipes seeded data and sandbox-only credentials; symptom: staging tests fail
  the morning after the customer's scheduled refresh with authentication errors that nobody's change
  explains.
- Letting sandbox-ingested documents into the production index namespace; symptom: a production answer
  cites a test fixture or a months-old version of a policy.
- Sharing one webhook receiver and signing secret across the customer's environments; symptom: after a
  sandbox refresh, records with test names or obviously fake values appear in the production index,
  and their source instance id is the sandbox's.
- Treating "works in sandbox" as the release gate; symptom: the first production run hits a validation
  rule, an email trigger or an API limit that the sandbox did not have.

## When to use
Any integration that touches a customer system with side effects or permissions needs the full
practice: a binding per environment, hard guards and a promotion path. A read-only pilot over an
exported snapshot can start lighter, but the moment a connector or a write path appears, build the
matrix. Measure the time to re-establish staging after a sandbox refresh, the number of ids or settings
that differ between sandbox and production (each one is a latent production failure), and the share of
behaviour verified only in the sandbox versus in shadow or canary. Day to day it shows up as the question
"which environment is this pointed at?", which must be answerable from any log line. In interviews,
expect to be asked how you would prevent a development deployment from writing to a customer's
production system, and why sandbox parity cannot be assumed for an AI feature.

## Role lens
- **Engineer:** Implements per-binding configuration, the host allow-list guard, environment tagging on
  ingested data, and automated re-seeding of fixtures after a sandbox refresh.
- **Architect:** Owns the environment matrix, the rule that credentials never cross bindings, and the
  decision of what must be verified in production through shadow mode or canary rather than in sandbox.
- **FDE:** Negotiates sandbox type, refresh schedule and credentials with the customer's admin, and
  explains why masked sandbox data cannot be the only evidence that the assistant works.

## Check yourself
**Q:** A customer offers only a developer sandbox with synthetic data for your ticket-triage assistant,
which writes a suggested category onto each new ticket. How do you reach a confident production release?
**A:** Use the sandbox for what it can prove: schema and field mappings, write permissions, validation
rules, the full create-read-update path and failure handling, with seeded tickets covering every
category and edge case. It cannot prove classification quality or behaviour on real text and volume.
For that, negotiate a read-only sample of real tickets under the data agreement and build an evaluation
set with the customer's SMEs, then run in shadow mode in production: the assistant reads live tickets and
records its suggested category without writing it, and you compare against what agents chose. Release
as a canary to one queue with writes enabled and a rollback criterion agreed in advance, with hard guards
ensuring the development binding can never reach the production host.

## Quiz
### Q: Why must record ids captured in a sandbox never be hard-coded into prompts, filters or configuration?
- [ ] Because sandbox ids are encrypted and cannot be read by the integration
- [x] Because ids of records and metadata created independently in each environment diverge, so a hard-coded value can resolve in UAT and point at nothing, or at the wrong record, after promotion
- [ ] Because ids change every time the sandbox is queried through the API
- [ ] Because production ids are longer and would overflow the prompt
> Even where a copy preserves the ids that existed at copy time, anything created later on either side
> gets its own id. Anything resolved by id must be looked up at runtime through stable business keys or
> metadata, otherwise it can break on the first production call.

### Q: Which measure most reliably prevents a non-production deployment from writing to a customer's production system?
- [ ] A code review checklist item reminding engineers to check the environment variable
- [ ] Logging the target host on every request so mistakes can be found afterwards
- [x] A per-binding host allow-list enforced in the client, with production credentials absent from non-production secret stores, so a mismatch fails loudly before any call
- [ ] Running all tests against the sandbox before each deployment
> Review and logging detect mistakes after the fact. A guard that refuses to run when binding and host
> disagree, combined with credentials that physically do not exist outside production, prevents the
> write from happening at all.

### Q: A feature passed every UAT test in a full-copy sandbox, but its first production run sent several hundred emails to customers. What was most likely missed?
- [ ] The production API quota was higher, so more records were processed
- [x] Outbound email was disabled in the sandbox, and the per-binding list of side-effect differences was never written down or tested for
- [ ] The sandbox's data was older, so fewer records matched
- [ ] Production users had more permissions than sandbox users
> Sandboxes commonly disable or redirect outbound integrations. A side effect that silently does nothing
> in the sandbox must be listed explicitly per binding and verified in shadow or canary mode, not inferred
> from a green UAT run.
