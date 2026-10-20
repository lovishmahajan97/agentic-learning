---
id: model-lifecycle-and-deprecation-policy
module: llmops-platform
name: Model lifecycle & deprecation policy
kind: practice
difficulty: core
est_minutes: 14
related:
  - llmops-platform/model-registry
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/rollback
  - llmops-platform/evaluation-gates-for-production-releases
  - security-governance-compliance/model-governance
  - security-governance-compliance/vendor-exit-strategy
  - backend-fundamentals/api-versioning-and-backward-compatibility
  - data-architecture/data-retention-and-deletion-architecture
sources:
  - Official docs: OpenAI model deprecations
  - Official docs: Anthropic model deprecations
  - Official docs: MLflow Model Registry
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
---

## TL;DR
A model version is registered, validated, promoted, superseded and eventually switched off, and each
step changes what callers may rely on. A lifecycle policy names the states, who may move a version
between them and what evidence each move needs. A deprecation policy is the exit half: how far ahead
a retirement is announced, when new pins are frozen, how consumers are found and migrated with
evaluation, and what is archived afterwards. Without it, every provider sunset becomes an unplanned
migration on someone else's calendar.

## Why it exists
Models do not stay still. Providers retire snapshots on published schedules and ship replacements with
different behaviour. Self-hosted weights are superseded by a better fine-tune, a cheaper quantisation
or a security fix in the serving stack. Embedding models are upgraded, which invalidates every index
built with the old one. Each of these is a change to something dozens of prompts, bundles and tenants
depend on, usually built by people who have since moved to another project.

The naive approach is to treat model versions like library versions: pin them and upgrade when
convenient. That fails because the supply side is not under your control. A pinned provider snapshot
has a shutdown date; on that date requests fail, or are silently redirected to a successor that
behaves differently. Teams discover this through an incident, then migrate a dozen bundles in a week
without the evaluation they would normally require, and ship regressions to avoid an outage. The
opposite naive approach, "always use the latest alias", converts every provider release into an
unreviewed production change.

A second failure is the internal one. A platform team that offers models to twenty product teams
becomes a provider itself. If it retires a fine-tuned model without a notice period, a freeze window
and a migration path, it causes the same incident internally that the external provider would have
caused. Lifecycle and deprecation policy exist so that "this version will stop existing" is a planned,
evaluated, dated event with an owner, in both directions of the supply chain.

## How it works
### States and transitions
A version moves through a small state machine; the registry lesson describes the alias mechanics,
this one describes the policy around them. A typical set: **registered** (artifact and lineage
recorded, no claims made) → **evaluated** (offline gates passed, evidence attached) → **staged**
(deployable to pre-production) → **canary** (serving a fraction of production) → **production**
(the champion for one or more aliases) → **deprecated** (still served, no new consumers, sunset date
set) → **retired** (not served; requests fail with a clear error) → **archived** (bytes kept for
the retention period, then deleted). Transitions are restricted *because* each state is a promise to
consumers: production → retired is forbidden, not merely discouraged, so that nobody can switch off a
model that still has callers. Each transition records who, when and why, which makes the lifecycle
itself an audit trail.

### Evidence per gate
Each forward move names its evidence: evaluated needs the eval report against the current golden set
and slice results; staged needs integration tests against the real serving config; canary needs the
rollback plan and the monitoring dashboard for the candidate; production needs the canary verdict and
sign-off from the owning team. The policy fixes *what* is required, not *how good* the numbers must
be; thresholds belong to the use case's evaluation gates. This split exists *because* the lifecycle is
platform-wide while quality bars are domain-specific.

### Provider models have a lifecycle too
A hosted model registered as a virtual version carries the provider's snapshot id, its announced
retirement date if any, and the recommended successor. Providers publish deprecation schedules and
give advance notice measured in months; the exact period varies by provider and by whether the model
was generally available, so the policy reads the provider's page rather than assuming a number. The
platform mirrors each external deprecation as an internal one, *because* consumers should learn about
retirements from one place with one format. Some clouds offer deployments that auto-upgrade to the
successor when a version retires; the policy should decide explicitly whether that is allowed, since
it is a silent model change in production.

### The deprecation workflow
1. **Decide and date.** Triggers: provider sunset, a validated successor, a security or compliance
   finding, cost, or a version with no owner. The sunset date is set at least the policy's notice
   period ahead, except for security retirements, which have their own expedited path.
2. **Announce.** The version is marked deprecated in the registry, and every consumer is notified
   through the channel they registered with. Notification is targeted, not broadcast, *because* "we
   emailed everyone" is how migrations get missed.
3. **Inventory consumers.** The platform must be able to answer "who still calls this version". That
   comes from the gateway's per-request telemetry and the bundle registry's pins, never from asking
   around. Each consumer gets a tracked migration item with an owner.
4. **Freeze.** From a fixed date before sunset, no new bundle may pin the deprecated version, and CI
   fails on such pins. Existing traffic continues. This stops the consumer list growing while you are
   trying to shrink it.
5. **Migrate with evaluation.** Each consumer evaluates the successor against its own golden set,
   fixes prompts where behaviour changed, and promotes through the normal canary path. The
   scheduled model-upgrade job from the CI/CD lesson is the mechanism; the policy is what makes it
   mandatory rather than optional.
6. **Brownout.** In the last weeks, the deprecated version is made unavailable for short, announced
   windows. Consumers who ignored every notice discover the dependency during a planned ten-minute
   window instead of at sunset. Brownouts exist *because* an error in a test window costs less than
   the same error on the retirement date.
7. **Retire and archive.** Serving stops; requests return a specific error naming the successor.
   Weights, config and evaluation evidence stay archived for the retention period the policy sets,
   *because* audits, incident investigations and reproducibility all need to re-run the model that
   was in production on a given date. Deletion is its own dated, recorded event.

### Compatibility promises
The policy states what a consumer may assume across versions: which fields of the response are
stable, whether output format is guaranteed, and that behaviour is *not* guaranteed to be identical.
Many platforms adopt an "N−1" or "N−2" rule: the current major version and the one or two before it
are supported, older ones are deprecated automatically. That rule bounds the number of versions
anyone must keep evaluated, monitored and secure at once.

## Example
A lifecycle state machine with restricted transitions, and a deprecation planner that turns a sunset
date into dated obligations plus a list of consumers still pinned to the old version. Durations are
illustrative policy values, not a standard.

```python
from datetime import date, timedelta

TRANSITIONS = {                       # what may follow each state
    "registered": {"evaluated", "retired"},
    "evaluated":  {"staged", "retired"},
    "staged":     {"canary", "retired"},
    "canary":     {"production", "staged"},        # back to staged = candidate withdrawn
    "production": {"deprecated"},                  # never straight to retired: consumers must move first
    "deprecated": {"retired", "production"},       # un-deprecate allowed, with a recorded reason
    "retired":    {"archived"},
}

def move(version, new_state, actor, reason):
    if new_state not in TRANSITIONS[version["state"]]:
        raise ValueError(f"{version['id']}: {version['state']} -> {new_state} not allowed")
    version["history"].append((date.today(), version["state"], new_state, actor, reason))
    version["state"] = new_state

POLICY = {"notice_days": 90, "freeze_days": 60, "brownout_days": 14}   # illustrative

def deprecation_plan(version, sunset, consumers):
    """Sunset date -> dated obligations and the consumers still to chase."""
    todo = [c["bundle"] for c in consumers
            if c["pinned_model"] == version["id"] and not c.get("migration_pr")]
    return {"announce_by": sunset - timedelta(days=POLICY["notice_days"]),
            "freeze_new_pins": sunset - timedelta(days=POLICY["freeze_days"]),
            "brownouts_from": sunset - timedelta(days=POLICY["brownout_days"]),
            "sunset": sunset, "unmigrated": todo}

v = {"id": "chat-model@2025-03-01", "state": "production", "history": []}
move(v, "deprecated", actor="platform-team", reason="provider sunset announced")
plan = deprecation_plan(v, date(2026, 12, 15), consumers=[
    {"bundle": "support-assistant@41", "pinned_model": v["id"]},
    {"bundle": "claims-extractor@7", "pinned_model": v["id"], "migration_pr": 812},
])
print(plan)   # announce_by 2026-09-16, freeze 2026-10-16, brownouts 2026-12-01, unmigrated: support-assistant@41
```

The consumer list comes from the bundle registry's pins joined with gateway telemetry; each
unmigrated entry becomes a ticket owned by the bundle's team, and the freeze date is enforced by a CI
check on pinned model ids.

## Trade-offs
- Long notice periods give consumers time to evaluate properly but keep more versions alive, each
  of which must stay monitored, patched and paid for; the balance shifts toward shorter notice when
  versions are cheap to keep and toward longer when migrations need re-prompting and re-indexing.
- Mirroring provider deprecations internally gives consumers one calendar, but makes the platform
  the messenger for dates it does not control; when the provider shortens a window, the platform
  absorbs the pressure.
- Freezing new pins early shrinks the migration list but blocks a team that legitimately needs the
  old behaviour for one more release; an exception path with an expiry date handles that without
  reopening the freeze.
- Allowing auto-upgrade deployments removes the sunset incident but turns every retirement into an
  unreviewed behaviour change; acceptable for low-stakes internal tools, not for regulated or
  customer-facing flows.
- Archiving weights and evidence for years satisfies audit and reproducibility, but costs storage and
  creates a sensitive artifact store; the retention period should follow the compliance requirement,
  not be set to "forever" by default.

## Pitfalls
- Learning about a provider retirement from an error; the symptom is a week of emergency migrations,
  skipped evaluations and a regression that gets blamed on the new model rather than the process.
- Deprecating by broadcast email; the symptom is a sunset day with several unknown consumers, found
  only because their error rate went to 100%.
- Retiring without an inventory; the symptom is the platform team asking in chat "does anyone still
  use model X?" and being wrong.
- No freeze window; the symptom is a migration list that grows during the migration because new
  bundles keep pinning the version everyone was told to leave.
- Retiring the version and deleting the artifact in the same step; the symptom is an audit or an
  incident post-mortem that needs to reproduce last quarter's behaviour and cannot.
- Treating an embedding model like a chat model in the lifecycle; the symptom is a successor promoted
  without re-indexing, and retrieval quality collapsing on the promotion day.

## When to use
A written lifecycle and deprecation policy is worth having as soon as more than one team depends on a
model you serve or pin, or as soon as you have a production dependency on a provider snapshot, which
is almost immediately. Start small: name the states, forbid the dangerous transitions, require an
inventory before any retirement, and set a notice and freeze period you can honour. Measure it by how
many retirements happened as planned, dated events versus incidents, and by how many consumers were
still unmigrated at the freeze date. Day to day, it shows up as the scheduled upgrade job opening PRs,
CI rejecting pins to deprecated versions, and a dashboard of versions by state. In interviews, expect
to be asked how you would handle a provider sunset across many teams; the strong answer has an
inventory, a freeze, per-consumer evaluation, brownouts and archival, in that order.

## Role lens
- **Engineer:** Pins snapshot ids in bundles, runs the upgrade job's evaluation when a successor is
  announced, and adds the CI check that fails builds pinning a deprecated version.
- **Architect:** Owns the state machine, the notice and freeze periods, the retention rule for retired
  artifacts, and the decision on whether auto-upgrading deployments are permitted anywhere.
- **FDE:** Translates a provider's deprecation into a migration plan the customer can approve, runs the
  evaluation on the customer's golden set, and schedules the cutover inside their change window.

## Check yourself
**Q:** A provider announces that the snapshot behind your most-used model retires in four months. Twelve
bundles across five teams pin it. Describe the plan, including what you would refuse to do.
**A:** First register the deprecation internally with the sunset date and the recommended successor, so
all five teams see one calendar. Build the inventory from bundle pins joined with gateway telemetry,
then open one tracked migration item per bundle with the owning team named. Set a freeze date, say
two months out, after which CI rejects new pins to the old snapshot. Each team evaluates the successor
against its own golden set and canaries it through the normal path, because the successor's behaviour
is not identical; the platform provides the upgrade job but does not do the evaluation for them. In
the last weeks, run announced brownouts to flush unknown consumers. On sunset, retire with an error
naming the successor and archive evidence for the retention period. Refuse to enable silent
auto-upgrade on customer-facing bundles and refuse to skip evaluation to meet the date; if a team
cannot migrate in time, the fallback is a short, dated exception, not an unreviewed switch.

## Quiz
### Q: Why does a sound lifecycle forbid moving a version directly from production to retired?
- [ ] Retired versions cannot be archived without a deprecated step in the history
- [x] The deprecated state is where consumers are inventoried and migrated; skipping it switches off a model that still has callers
- [ ] Provider APIs require a deprecation flag before a snapshot can be deleted
- [ ] It prevents the registry from reusing the version number
> Deprecated is a serving state with a sunset date, during which the consumer list shrinks to zero
> under observation. Retiring straight from production skips that work and causes an outage.

### Q: What is the purpose of a freeze window before a model's sunset?
- [ ] To stop all traffic to the version so consumers notice early
- [ ] To give the provider time to prepare the successor
- [x] To stop new bundles pinning the version so the migration list cannot grow while it is being worked down
- [ ] To lock the golden set so evaluations are comparable
> Freezing new pins, enforced in CI, bounds the migration; stopping traffic is what brownouts do later,
> briefly and by announcement, to find consumers the inventory missed.

### Q: Why keep a retired model's weights and evaluation evidence after it stops serving?
- [ ] So it can be promoted back if the successor fails
- [ ] Because providers require it for billing reconciliation
- [x] Audits and incident investigations may need to reproduce the behaviour that was in production on a given date
- [ ] To seed the training data for the next fine-tune
> Archival is about reproducibility and accountability for past decisions, for a defined retention
> period. Rolling back to a retired version is not a goal; the successor has its own rollback target.
