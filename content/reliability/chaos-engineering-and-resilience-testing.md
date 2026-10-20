---
id: chaos-engineering-and-resilience-testing
module: reliability
name: Chaos engineering & resilience testing
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - reliability/fmea
  - reliability/circuit-breakers
  - reliability/bulkheads
  - reliability/fault-tolerance
  - reliability/disaster-recovery
  - serving-inference/fallbacks
  - deployment/runbooks
  - delivery-ownership/root-cause-analysis-and-postmortems
sources:
  - Basiri et al., 2016 — Chaos Engineering (IEEE Software)
  - Rosenthal & Jones, 2020 — Chaos Engineering: System Resiliency in Practice
---

## TL;DR
Resilience mechanisms (fallbacks, breakers, retries, failover) run rarely and rot silently, so you do
not know whether they work until they are needed. Resilience testing exercises them deliberately in
pre-production: fault injection, load, stress and soak tests, failover drills, game days. Chaos
engineering extends this to controlled experiments on real systems: state a steady-state hypothesis,
inject a realistic fault with a bounded blast radius, measure whether it held. For AI systems the faults
include provider 429s and latency, tool failures, stale retrieval and malformed model output.

## Why it exists
Fallback code is the least-tested code in any system. A team ships multi-provider failover and feels
safer. Six months later the primary provider degrades, the fallback fires for the first time in
production, and fails: the secondary's API key expired, its model does not accept the same tool-call
format, or its rate limit is a tenth of the primary's. Every layer of resilience you add is another
untested path, and the testing you already do does not reach it. Unit tests mock the dependency. End-to-
end tests run the happy path. Staging has neither production traffic patterns nor production data
volumes nor the real vendor quotas.

The naive alternative is to let real incidents validate the design. That works, in the sense that you
learn, but the lesson arrives at three in the morning, at full scale, uncontrolled, with customers
watching. Chaos engineering's founding observation is that the failure is going to happen anyway; the
only choice is whether it happens while you are watching, at one percent of traffic, with a kill switch,
or later.

Two further problems push beyond "test the fallback". Distributed systems have emergent failure modes
that no component test predicts: retry storms, thundering herds on recovery, cache stampedes, cost
amplification when traffic falls to a pricier fallback model. Only exercising the whole system under
fault reveals them. And systems drift: a fallback verified in March is unverified by June after three
model upgrades and a vendor's quota change. Verification has to be repeated, which means it has to be
cheap and automated.

## How it works
### Resilience testing: proving the mechanisms you built
This is the pre-production family, and it comes first because there is no point running experiments
against mechanisms that do not exist yet.

- **Fault injection** at the dependency boundary: make the provider client return 429s, 5xxs, 8-second
  latencies or truncated completions for a configurable fraction of calls, and confirm that timeouts,
  breakers, backoff and fallbacks behave as designed.
- **Load, stress and soak tests**: load at expected peak to confirm SLOs hold; stress beyond peak to find
  the knee where latency explodes and to see whether shedding engages; soak for hours at steady load to
  catch leaks, including agent-specific ones such as conversation state that never expires or token
  budgets that creep upward per session.
- **Failover and recovery drills**: kill the primary database, region or model deployment on a schedule
  and measure actual recovery time and data loss against the disaster-recovery targets.
- **Continuous canaries for every fallback path**: a synthetic request through the secondary provider
  every few minutes. This is the single cheapest control against fallback rot, because it catches
  expired credentials, renamed models and quota changes before a real failover does.
- **Game days**: scheduled, cross-team scenario exercises that include the humans: paging, runbooks,
  communication, decision-making. A perfect failover with a confused on-call is still an outage.

### Chaos engineering: discovering what you did not build for
The discipline is defined by its method, not by breaking things.

1. **Define steady state as user-facing output.** Success rate, p95 latency, task completion rate, cost
   per task: the SLIs. Not CPU or queue depth, because the hypothesis is about whether users notice.
2. **State a falsifiable hypothesis.** "If the primary provider returns 429 on 30 percent of calls,
   success rate stays at or above 99 percent and p95 rises by less than 20 percent." A hypothesis makes
   the result a finding either way; "we killed a pod and nothing happened" teaches nothing.
3. **Inject realistic events.** Latency, errors, instance loss, network partition, dependency
   unavailability, resource exhaustion, zone loss, clock skew. Prefer the events your FMEA ranked highest;
   that table is the experiment backlog.
4. **Minimize blast radius.** Start in staging, then one percent of production, an internal tenant, one
   availability zone. Define an abort condition and a kill switch before starting. Run during business
   hours with on-call informed, because an experiment that is indistinguishable from an incident is one.
5. **Compare experiment and control.** Route a slice of traffic through the fault and an equal slice
   without, and compare the SLIs, as in an A/B test. This separates the fault's effect from background
   noise.
6. **Fix, then widen, then automate.** Each refuted hypothesis becomes a fix and a regression test. Verified
   hypotheses graduate to larger blast radius and eventually to continuous automated runs, because the
   system will drift away from the verified state.

### Injection mechanisms
Application-level hooks in your own provider and tool clients are the most useful for AI systems: you
cannot make a vendor's API go down, but you can make your client behave as if it had. Network-level
tools add latency, bandwidth caps and connection resets through a proxy (Toxiproxy is a common choice)
or a service mesh's fault-injection rules. Infrastructure-level tools kill instances and pods, fill disks
and hog CPU; this is the Chaos Monkey lineage, continued by Kubernetes-native projects such as
LitmusChaos and Chaos Mesh, commercial platforms, and the cloud providers' own fault-injection services.
Data-level injection is specific to AI and usually hand-built: serve a stale index snapshot, drop a
fraction of retrieved chunks, revoke a tool's credentials, or return refusals, truncated text and
malformed JSON from a model stub.

### What is different about LLM and agent systems
Faults are semantic as well as operational. A model returns plausible nonsense; retrieval returns the
wrong tenant's documents; a tool reports success with wrong data. Infrastructure chaos never produces
these, so the experiment must inject them and the hypothesis must be about whether guards (schema
validation, groundedness checks, confirmation steps) catch them. Amplification is the second difference:
injecting 429s should reveal whether backoff, jitter and step budgets hold, or whether an agent loops
fifty times and a retry storm forms. The third is cost: a fault that pushes traffic to a fallback model
has a dollar blast radius as well as a latency one, so experiments need a spending cap and the cost SLI
belongs in the hypothesis.

## Example
A fault-injecting wrapper around the real model client, controlled by environment variables so it is
inert by default, plus a minimal experiment runner with an abort condition. The experiment is run once
as a control and once with a fault, and the two results are compared against the hypothesis.

```python
import asyncio, os, random, time

class RateLimitError(Exception): pass

class ChaosLLM:
    """Wraps the real client. Injects faults on a fraction of calls; inert unless CHAOS_RATE is set."""
    def __init__(self, client):
        self.client = client
        self.rate = float(os.getenv("CHAOS_RATE", "0"))
        self.mode = os.getenv("CHAOS_MODE", "latency")

    async def complete(self, **kw):
        if random.random() < self.rate:
            if self.mode == "latency":
                await asyncio.sleep(8.0)                 # provider at 8 s: do timeouts and breakers fire?
            elif self.mode == "rate_limit":
                raise RateLimitError("429")              # do backoff, jitter and step budgets hold?
            elif self.mode == "garbage":
                return {"content": "{not json", "usage": {"total_tokens": 5}}   # does validation catch it?
        return await self.client.complete(**kw)

async def experiment(handler, requests, min_success=0.95):
    """Runs traffic through `handler`, aborts if steady state is violated, reports the SLIs."""
    latencies, ok = [], 0
    for req in requests:
        t0 = time.perf_counter()
        try:
            await handler(req); ok += 1
        except Exception:
            pass
        latencies.append(time.perf_counter() - t0)
        if len(latencies) >= 20 and ok / len(latencies) < min_success:
            raise RuntimeError("steady state violated; experiment aborted")   # the kill switch
    latencies.sort()
    return {"success_rate": ok / len(latencies),
            "p95_s": latencies[int(0.95 * (len(latencies) - 1))]}

# Control run: CHAOS_RATE=0. Experiment: CHAOS_MODE=rate_limit CHAOS_RATE=0.3.
# Hypothesis: success_rate >= 0.99 and p95_s < 1.2 x the control's. Anything else is a finding.
```

In practice the same wrapper sits in the deployed service behind a per-tenant or per-request flag, so
the fault can be applied to one percent of real traffic; the runner's abort check becomes an alert on
the experiment slice's SLIs that flips the flag off.

## Trade-offs
- Production experiments give true signal, with real traffic, data and vendor limits, at real risk;
  staging is safe but misses emergent behavior. Start in staging and graduate to production only once
  abort conditions, observability per slice and a kill switch exist.
- Every experiment costs engineering time and sometimes vendor dollars, because faults push traffic to
  fallbacks and retries; prioritize by FMEA risk rather than by what is easy to inject.
- Continuous automated chaos catches drift but demands a mature on-call and can be mistaken for a real
  incident; scheduled game days are cheaper and clearer but only test what someone remembered to
  schedule.
- Semantic fault injection tests the guards that matter for AI but needs trustworthy evals to judge
  success; infrastructure fault injection is easy to measure but blind to wrong-answer failures. Do
  both, in that order of importance for an LLM product.

## Pitfalls
- Running without a steady-state hypothesis. The symptom: a log of experiments none of which produced a
  fix, and nobody able to say what was verified.
- No abort condition or kill switch. The symptom: a postmortem whose root cause is the chaos experiment
  itself.
- Injecting only infrastructure faults into an AI system. The symptom: pods survive every drill, then
  the first real 429 storm triggers agent loops and a cost spike nobody tested for.
- Never canarying the fallback. The symptom: the first real failover fails for a configuration reason,
  an expired key, a renamed model, a lower quota, that a five-minute synthetic check would have caught.
- Experimenting without per-slice observability. The symptom: the hypothesis cannot be evaluated because
  the experiment's traffic is indistinguishable from everything else in the dashboards.
- Applying faults to shared customer traffic without regard to contracts. The symptom: a customer's SLA
  breached by your own experiment, and a difficult conversation about who caused the outage.
- Ignoring the cost blast radius. The symptom: a full-traffic primary-failure drill that routes an hour
  of traffic to the expensive fallback model, visible on the next invoice.

## When to use
Begin once the basic mechanisms exist and observability can show SLIs for an experiment slice; before
that, build the mechanisms. A sensible progression: dependency fault injection in staging for the top
three FMEA rows, synthetic canaries through every fallback path, one game day per quarter for failover
and disaster recovery, then production chaos at one percent for the most critical mechanisms, growing
toward automation. Do not bother for a prototype or a service with no SLO, because there is no steady
state to defend. Measure hypotheses verified and refuted, weaknesses found per experiment, time to
detect and time to abort. In interviews, describe one experiment end to end: steady state, hypothesis,
fault, blast radius, abort condition, result, and the fix it produced.

## Role lens
- **Engineer:** Builds the fault-injection hooks into provider and tool clients, writes the experiment and
  its abort check, and turns every refuted hypothesis into a regression test.
- **Architect:** Decides which failure modes must be proven rather than assumed before launch, sets the
  blast-radius and approval policy, and ties experiments to SLOs and the FMEA backlog.
- **FDE:** Runs a game day with the customer before go-live, cutting their SSO or ERP connector to show
  the agent degrading gracefully, so their operations team has seen the behavior and the runbook works
  in their environment.

## Check yourself
**Q:** You have shipped multi-provider fallback for an agent. How would you prove it works without
waiting for the primary provider to fail, and what would you watch for?
**A:** Three layers. First, a synthetic canary every few minutes through the secondary path, which
catches expired keys, renamed models and quota changes continuously. Second, staging fault injection:
force 100 percent 503s from the primary through the client hook with the hypothesis that success rate
stays at or above 99 percent, p95 stays within budget and cost per request stays under twice the
control; watch specifically for tool-call format incompatibilities, differing context limits and
schema differences between the two models. Third, production chaos on one percent of an internal
tenant's traffic with 30 percent injected 429s, aborting if success drops below 98 percent. Throughout,
watch retry amplification (provider calls per user request), fallback rate, breaker state transitions
and spend, and sample the fallback outputs through your quality evals because the secondary model may be
measurably worse. Finally test recovery: when the primary returns, traffic should move back through the
breaker's half-open probes without a thundering herd.

## Quiz
### Q: What distinguishes chaos engineering from ordinary fault-injection testing?
- [ ] Chaos engineering only runs in production, while fault injection only runs in staging
- [x] A falsifiable steady-state hypothesis measured on user-facing SLIs, a bounded blast radius, and comparison against a control
- [ ] Chaos engineering uses random faults, while fault injection uses chosen ones
- [ ] Chaos engineering requires a dedicated tool such as Chaos Monkey
> Fault injection is a mechanism; chaos engineering is an experimental method built on it. The hypothesis
> is what turns "we broke it and watched" into a finding, and the blast radius and control group are
> what make running it on real systems defensible.

### Q: Which class of fault is most specific to AI systems and most often skipped in drills?
- [ ] Killing a pod in the inference deployment
- [ ] Losing an availability zone
- [x] Semantic faults such as plausible-but-wrong output, malformed JSON and stale or cross-tenant retrieval
- [ ] Filling the disk on a worker node
> Infrastructure faults are easy to inject and measure, but an LLM product's worst failures are silent
> wrong answers. Only injecting those, via model and retrieval stubs, tests whether validation,
> grounding checks and confirmation steps actually catch them.

### Q: Why run a synthetic request through the fallback provider continuously rather than only during drills?
- [ ] To keep the fallback provider's cache warm for lower latency
- [ ] Because vendors require minimum usage to keep an account active
- [x] Fallback paths rot through credential expiry, model renames and quota changes, and a canary detects that before a real failover does
- [ ] To accumulate usage data for negotiating a better contract
> The fallback carries no traffic in normal operation, so nothing exercises it between drills. A cheap
> periodic request turns months of silent decay into an alert within minutes of the configuration
> breaking.
