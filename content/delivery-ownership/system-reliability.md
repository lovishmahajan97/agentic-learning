---
id: system-reliability
module: delivery-ownership
name: System reliability
kind: practice
difficulty: core
est_minutes: 15
status: typed-verified
lesson_type: decision
agent_relevance: core
related:
  - reliability/high-availability
  - reliability/fault-tolerance
  - reliability/slas-slos
  - delivery-ownership/slas
  - delivery-ownership/failure-analysis
  - delivery-ownership/root-cause-analysis-and-postmortems
  - agent-reliability/task-completion-rate
  - llmops-platform/canary-shadow-deployments
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Nygard, 2018 — Release It! (2nd ed.)
---

## TL;DR
An agent is reliable when it completes the right task for users over time. Owning that means four
levers: fail less often, fail smaller, notice sooner, recover faster. "Right" includes answer quality,
so a fully-up agent can be unreliable.

## For agent builders
- **Why it matters for agents.** Per-step failures compound: ten tool calls at 99 percent each finish
  about 90 percent of tasks. An agent that fails mid-workflow also leaves half-done side effects, so
  reliability is designed into the loop, not bolted on afterwards.
- **Where it shows up in your stack.** Tool-call timeouts and retry budgets, idempotency keys on tools
  that write, checkpointed run state your orchestrator can resume, fallback models behind a feature flag,
  pinned model snapshots and quality evals on sampled agent traces.
- **What you control.** Which errors you retry, how many steps a run may take, whether a tool is safe to
  call twice, what the agent does when a dependency is down, and how fast you can roll back a prompt or
  model.
- **What you can skip.** Hardware redundancy theory and the MTBF versus MTTF naming debate beyond knowing
  conventions differ. The impact formula and composition arithmetic are what you use.

## Decision case
### Scenario
You own a customer-support agent used by 40 enterprise tenants. Its quarter of incident history is the
failure-mode table in the examples below: 180 + 60 + 15 + 12 = 267 full-outage-equivalent minutes a
month. All figures are illustrative planning numbers.
- **Target:** an internal SLO of 99.5 percent over a 30-day month, a 216-minute allowance. You are 51
  minutes over it.
- **Capacity:** two engineers for three weeks next quarter, so 6 engineer-weeks of reliability work.
- **Pressure:** sales has promised a large tenant "multi-provider redundancy" and wants a second model
  provider funded first.
- **Constraint:** the plan must bring the month under 216 minutes with room to spare, and leave no
  tenant-facing change that needs a new contract term.

### Options
| Option | How it works | Cost (eng-weeks) | Impact after (min/month) | Minutes saved per eng-week |
|---|---|---|---|---|
| Second provider fallback | Route to a backup model when the primary region fails | 6 | provider row 15 to about 0: 252 total | about 2.5 |
| Prompt eval gate plus sampled quality alerts | Block failing prompt deploys; alert within 15 minutes on quality drops | 4 | prompt row 180 to 45: 132 total | about 34 |
| Index freshness check | Alert when the retrieval index lags its source; mean stale time 600 to 60 minutes | 2 | stale row 60 to 6: 213 total | 27 |
| Per-tenant quotas | Bulkhead batch tenants away from interactive traffic | 2 | tenant row 12 to about 0: 255 total | 6 |

### Decision
**Fund the prompt eval gate with quality alerts, plus the index freshness check.** Together they cost the
full 6 engineer-weeks and bring the month to 45 + 6 + 15 + 12 = 78 minutes, about a third of the
allowance. The second provider alone would cost the same 6 weeks and leave 252 minutes, still over
the SLO.
- **Ranked by impact, not by recency or noise:** the two chosen rows are both self-inflicted and both
  detection problems, which is the cheapest term to cut.
- **Quotas wait:** they cap what batch tenants may send, a tenant-facing limit the constraint rules out
  this quarter, and they would save only 12 minutes.
- **Sales gets a written answer:** the fallback is scheduled once its row dominates, and the impact
  table shows why it is not first.

**What would change it.** If provider region outages rise to, say, 2 a month at 60 minutes, that row
becomes 120 minutes and the fallback moves up. If a contract signs 99.9 percent, the 43.2-minute
allowance is mostly used by one 30-minute provider outage, so failover becomes mandatory.

### Trade-offs accepted
- **A provider outage still hits every tenant.** Track provider-row minutes monthly and revisit when they
  pass a quarter of the remaining impact.
- **The eval gate slows prompt releases.** Watch time from prompt merge to production and the gate's
  false-block rate; a gate people bypass protects nothing.
- **Batch tenants can still starve others.** Keep the per-tenant latency alert, and fund quotas next
  quarter if that row grows.

### Review rubric
- **Impact computed per failure mode:** frequency x duration x blast radius, summed and compared to the
  SLO allowance in minutes.
- **Cost-normalised ranking:** options are compared by minutes saved per unit of effort.
- **Lever named inside each row:** the write-up says which term (detect, diagnose, recover, blast radius)
  the work shrinks, with evidence from incident timelines.
- **Stakeholder request handled with numbers:** the popular option is answered, not ignored.
- **Reopen triggers stated:** the frequency or contract change that would reorder the list.

## Why it exists
- **Delivered AI systems fail differently from demos.** They fail at 3 a.m. when a provider rate-limits
  a region, the day a vendor silently updates a model, when one tenant's batch job starves everyone
  else, or when an agent's tenth tool call times out and leaves half a workflow done.
- **Tests do not catch these.** Not "it works on my machine", and mostly not unit tests, *because* the
  failures come from load, time, dependencies and change rather than from logic errors.
- **"Add redundancy everywhere" fails twice.**
  - It is expensive and still misses the dominant cause: postmortem experience across the industry,
    Google's SRE writing included, finds a large share of outages is triggered by changes (deploys,
    config, prompts, model versions), and redundant copies running the same bad change fail together.
  - It optimises only one lever. A system that fails rarely but takes four hours to diagnose each time
    can be less available than one that fails weekly and rolls back in five minutes.
- **Someone has to own the whole picture:** which failures matter, which lever is cheapest to pull
  next, and whether the system is getting better or worse. Without that owner, reliability work happens
  only after incidents and only on whatever the last incident touched.
- **Scope.** This lesson is the owner's view; the patterns themselves (redundancy, circuit breakers,
  bulkheads) are taught in the reliability module.

## How it works
### The four levers and the impact formula
- **Availability over time** ≈ MTBF / (MTBF + MTTR): mean uptime between failures over the whole cycle
  of working plus being broken.
- **Convention caveat.** Strictly that numerator is MTTF, mean time to failure. If your MTBF already
  includes repair time, the formula is (MTBF − MTTR) / MTBF. Conventions differ, so say which you use.
- **Split MTTR** into time to detect, time to diagnose and time to recover, add blast radius (the share
  of users affected), and you have every lever you can pull. They combine into one cost function the
  owner can rank work by:

```
user_impact_minutes per period ≈ Σ over incidents of
    (time_to_detect + time_to_diagnose + time_to_recover) × blast_radius_share
expected for a failure mode ≈ frequency × mean_duration × mean_blast_radius
```

- **The product form is the point.** Each term is a separate lever; halving any one term halves the
  impact, so the cheapest term to halve is where the next sprint goes.

### Lever 1: fail less often (raise MTBF)
- **Change safety beats hardware:** canary and shadow releases, evaluation gates on prompt and model
  changes, pinned model versions, config review.
- **Start here,** *because* change causes most incidents, so this is usually where the first effort
  pays off.

### Lever 2: fail smaller (reduce blast radius)
- **Contain the width:** per-tenant quotas, bulkheads between workloads, feature flags, cell or region
  isolation. A failure that hits 2 percent of users for an hour costs far less error budget than one
  that hits everyone.
- **Graceful degradation shrinks the depth.** Serve a worse answer instead of none: a smaller or
  secondary model, a cached response, retrieval-only results without generation, a read-only mode,
  *because* users tolerate degraded far better than down.
- **Stop the spread.** Timeouts, retry budgets, circuit breakers and load shedding stop one slow
  dependency from exhausting threads, connections or quota for everything else, *because* the usual way
  a small failure becomes a big one is queueing behind it.

### Lever 3: notice sooner (reduce detection time)
- **Alert on symptoms users feel:** SLO burn, failed tasks, quality-eval pass rate on sampled traffic,
  not CPU.
- **Quality monitoring is decisive for AI,** *because* it is the difference between detecting a
  regression in an hour or from a customer email a week later.

### Lever 4: recover faster (reduce diagnose and recover time)
- **One-step rollback** for code, prompts and model versions.
- **Runbooks, and traces** that connect a user's request to every model and tool call.
- **Automatic failover** for failures that need sub-minute recovery, *because* humans cannot act that fast.

### Composition arithmetic
| Shape | Formula | Example |
|---|---|---|
| Serial dependencies | multiply: ∏ aᵢ | four components at 99.99, 99.9, 99.95 and 99.9 percent succeed about 99.74 percent of the time |
| Redundant alternatives | 1 − ∏(1 − aᵢ) | only if failures are independent |
| Multi-step agent task | per-step success to the power of steps | ten steps at 99 percent each complete about 90 percent of tasks |

- **Independence is the trap.** Shared regions, shared quotas and shared bad configs violate it, so real
  redundancy gains are smaller than the formula says.

### AI-specific failure classes
- **Dependency failures**: provider outages, rate limits (HTTP 429), regional quota exhaustion.
- **Silent quality failures**: a model update, a retrieval index gone stale, a prompt edit; every request
  returns 200 and the answers get worse.
- **Partial side effects**: an agent fails mid-workflow; reliability then requires idempotent tools and
  resumable state, not just retries.
- **Nondeterminism**: the same input sometimes fails, so "could not reproduce" is not evidence of a fix.

### The four levers inside an agent loop
| Lever | What it looks like in the agent |
|---|---|
| Fail less often | eval gate on prompt, tool-schema and model changes; pinned model snapshot; validated tool arguments |
| Fail smaller | per-step timeout, step cap per run, per-tenant quota, degraded mode (smaller model, retrieval-only answer, handoff) |
| Notice sooner | task-completion rate and sampled quality evals on agent traces, not only HTTP status |
| Recover faster | checkpointed run state, idempotent tools so a resumed run does not repeat writes, one-step prompt rollback |

- **Resumable beats restartable.** A run that checkpoints after each completed tool call can resume at
  step 7 instead of repeating steps 1-6, *because* repeating them costs tokens and, for writes, creates
  duplicates.
- **Retry only what is transient.** Timeouts, HTTP 429 and 5xx are worth a bounded retry; a tool that
  rejects the arguments the model produced needs the error fed back to the model or a stop, not a
  blind retry.

### The ownership loop
1. **Define SLOs**, including a quality SLO (for example, the pass rate of an automated evaluation on
   sampled production traffic), *because* availability alone cannot see the silent failures above.
2. **Agree an error-budget policy in advance**: what happens when the budget is spent (change freeze
   except fixes, reliability work moves to the top of the queue). Agreeing it before the crisis is what
   stops the argument from being re-litigated every time a launch is due.
3. **Review weekly**: budget burn, incidents, and the per-incident split of detect, diagnose and recover
   time, *because* incident count alone hides which lever is failing.
4. **Rank failure modes** from incidents and failure analysis with the impact formula, and fund the
   top items, not the most recent ones.
5. **Verify** with game days, failover drills and chaos tests, *because* a recovery path that has not
   been exercised should be assumed broken. Then repeat.

Before launch, a production readiness review checks each lever explicitly.

## Diagram
Follow one incident through its life; each dotted "Lever" box points at the stage it shortens or
shrinks, and the weekly review at the bottom decides what gets funded next.

```mermaid
flowchart TD
    C["Prompt change or tool fault"] --> F["Failure in production"]
    F --> D["Detected"]
    D --> G["Diagnosed"]
    G --> R["Recovered"]
    L1["Lever 1: eval gates, canaries"] -.->|"fewer failures"| C
    L2["Lever 2: bulkheads, degradation"] -.->|"smaller blast radius"| F
    L3["Lever 3: symptom and quality alerts"] -.->|"faster detect"| D
    L4["Lever 4: rollback, checkpoints, traces"] -.->|"faster diagnose, recover"| G
    R --> W["Weekly review: rank by impact"]
    W -->|"fund cheapest lever"| L1
```

## Examples
### Composition and recovery arithmetic
The arithmetic an owner runs before deciding where the next sprint of reliability work goes. All
component figures are illustrative, not vendor numbers.

```python
from math import prod

def serial(*a):   return prod(a)                        # every component must work
def parallel(*a): return 1 - prod(1 - x for x in a)     # any one suffices; assumes independence
def availability(mtbf_h, mttr_h): return mtbf_h / (mtbf_h + mttr_h)

gateway, llm, vectors, app = 0.9999, 0.999, 0.9995, 0.999
backup_llm = 0.995
print(f"single provider: {serial(gateway, llm, vectors, app):.2%}")                      # 99.74%
print(f"with fallback:   {serial(gateway, parallel(llm, backup_llm), vectors, app):.2%}") # 99.84%

# The app's own term: one incident per month (~720 h) at two recovery speeds
for mttr in (2.0, 0.25):
    print(f"MTTR {mttr:>4} h -> {availability(720, mttr):.3%}")   # 99.723% vs 99.965%

# Agent task of 10 tool calls, 1% transient failure each, without and with one retry
p, n = 0.01, 10
print(f"no retry {(1 - p) ** n:.1%}, one retry {(1 - p ** 2) ** n:.2%}")  # 90.4% vs 99.90%
```

Three conclusions follow:
- **The fallback provider buys only a tenth of a percent,** *because* the other serial terms now dominate.
- **Cutting recovery from two hours to fifteen minutes** (one-step rollback plus a runbook) moves the
  app's own term far more.
- **Retries rescue the agent only if failures are transient and the tools are idempotent;** a
  deterministic bug fails both attempts.

### Worked scenario: ranking failure modes by expected impact
A quarter's incident history, reduced to per-month averages with the impact formula
`frequency × mean_duration × mean_blast_radius`. Figures are illustrative.

| Failure mode | Per month | Mean minutes | Users hit | Impact (full-outage-equivalent minutes) |
|---|---|---|---|---|
| Bad prompt deploy | 1 | 180 | 100% | 180 |
| Stale retrieval index | 1 | 600 | 10% | 60 |
| Provider region outage | 0.5 | 30 | 100% | 15 |
| Tenant batch starves others | 2 | 20 | 30% | 12 |

- **Fund the top row first.** Prompt deploys produce more impact than the other three combined, and they
  are self-inflicted, so they are cheap to fix.
- **Pick the term inside it.** If incident timelines show about 150 of the 180 minutes were detection
  (a bad prompt still returns 200s), sampled quality evals that alert within 15 minutes cut that row to
  about 45 user-minutes.
- **A second provider is last,** *because* it addresses the 15-minute row while costing the most.

### Agent loop with retry budget, idempotent writes and checkpoints
A minimal sketch of the recover-faster and fail-smaller levers inside one agent run. `call_tool`,
`save_checkpoint` and the error classes stand in for your own orchestrator and tool client.

```python
import time, uuid

TRANSIENT = (TimeoutError, ConnectionError)   # plus your client's 429 / 5xx error types
MAX_STEPS, MAX_RETRIES = 12, 2

def run_agent(task, plan_next_step, call_tool, save_checkpoint, state=None):
    state = state or {"run_id": str(uuid.uuid4()), "step": 0, "history": []}
    while state["step"] < MAX_STEPS:
        action = plan_next_step(task, state["history"])      # model call
        if action["type"] == "final":
            return {"status": "done", "answer": action["text"]}
        # same key on every retry or resume, so the tool server can drop duplicates
        key = f'{state["run_id"]}:{state["step"]}'
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = call_tool(action["tool"], action["args"], idempotency_key=key, timeout=20)
                break
            except TRANSIENT:
                if attempt == MAX_RETRIES:
                    return {"status": "handoff", "reason": "tool unavailable", "state": state}
                time.sleep(2 ** attempt)                       # backoff; add jitter in production
        state["history"].append({"action": action, "result": result})
        state["step"] += 1
        save_checkpoint(state)                                 # resume here, not from step 0
    return {"status": "handoff", "reason": "step cap", "state": state}
```

- **What it buys:** a 1 percent transient failure per call becomes about 0.01 percent with one retry
  (this sketch allows two), the 90.4 percent versus 99.90 percent shown above.
- **What it does not buy:** a deterministic bug fails every attempt, and without the idempotency key a
  retried write (a refund, a ticket update) may run twice.
- **Persist the planned action before calling a write tool.** On resume this sketch re-plans the step,
  and the model may choose a different action under the same key, which the tool side would wrongly drop
  as a duplicate. Checkpoint the action first and replay it, so the key always names one action.

## Production patterns
- **Pinned model versions with a promotion step** — when calling a hosted model, reference a dated
  model snapshot rather than a floating alias, and promote a new version only after it passes the eval
  set on a canary. Monitor quality-eval pass rate per model version.
- **Timeouts, retry budgets and circuit breakers on every provider call** — set an explicit per-call
  timeout, retry only transient errors such as HTTP 429 and 5xx with exponential backoff and jitter,
  honour a `Retry-After` header when the provider sends one, and open a breaker after repeated failures.
  Monitor retry rate, breaker state changes and queue depth.
- **Degraded-mode kill switch** — when a dependency fails, flip a feature flag that routes to a
  secondary model, cached answers or retrieval-only results. Rehearse the flip in game days. Monitor
  traffic share in degraded mode and its quality score.
- **Quality SLO on sampled traffic** — run an automated evaluation on a fixed sample of production
  requests every hour and page when the pass rate drops below the SLO. Monitor pass rate per route,
  judge disagreement, and eval spend.
- **Per-tenant quotas and bulkheads** — give batch and interactive workloads separate concurrency pools
  and rate limits so one tenant cannot exhaust shared provider quota. Monitor per-tenant quota
  utilisation and rejections.
- **Detect, diagnose, recover timestamps in every postmortem** — record the three durations as fields,
  not prose, so the weekly review can see which lever is failing. Monitor their medians per quarter.
- **Idempotency keys and checkpoints for agent runs** — derive a key from run id and step for every tool
  call that writes, have the tool side drop repeats, and persist run state after each step so a crashed
  or timed-out run resumes. Monitor duplicate-write rejections, resumed runs and task-completion rate.

## Trade-offs
- **Redundancy vs cost and rot.** It raises availability for independent failures, but doubles cost and
  adds failover paths that rot unless exercised; it flips to worthwhile once recovery time, not failure
  rate, is already small.
- **Strict change gates vs delivery speed.** Evals, canaries and approvals cut change-induced incidents,
  but slow delivery; loosen them when the error budget is healthy and tighten them when it is being
  burned.
- **Quality monitoring vs spend and noise.** Monitoring sampled traffic catches silent regressions, but
  costs evaluation spend and judge noise; it flips to essential as soon as outputs feed decisions
  without human review.
- **Automatic vs manual failover.** Automatic failover recovers in seconds, but a flapping health check
  can cause the outage it guards against; prefer manual failover where minutes of recovery are
  contractually acceptable.
- **Agent retries vs latency and cost.** Bounded retries lift task completion sharply for transient
  failures, but every retried model call adds tokens and seconds; it flips against retrying when failures
  are deterministic or the run is already near its latency budget, where a handoff is better.

## Pitfalls
- **Measuring reliability only as HTTP success.** Symptom: green dashboards during a week of customer
  complaints about wrong answers after a provider's model update.
- **Adding retries everywhere without idempotency or budgets.** Symptom: duplicate side effects and retry
  storms that turn a brief provider blip into a long outage.
- **Building a fallback or degraded path nobody tests.** Symptom: discovering during the first real
  outage that the backup provider's credentials expired, its prompt format was never adapted, or its
  quota cannot absorb full production traffic.
- **Fixing whatever the last incident touched.** Symptom: the same failure class recurring across
  postmortems while the team feels busy.
- **Assuming failures are independent when computing redundancy.** Symptom: both "redundant" providers
  failing together because they share a region, quota or upstream.
- **Restarting failed agent runs from step 0.** Symptom: duplicate tickets or refunds and doubled token
  spend after a crash. Fix: checkpoint after each step and pass an idempotency key on writes.
- **Tracking only HTTP errors for an agent.** Symptom: runs that loop to the step cap or end without an
  answer count as successes. Fix: alert on task-completion rate per agent and tool.

## When to use
- **Start on day one:** reliability ownership begins the day an agent has users who depend on it, not
  after the first outage; idempotent tools and step caps are cheapest to add before the first write tool
  ships.
- **Before launch:** run a readiness review against the four levers: how changes are gated, how failures
  are contained, which symptom alerts exist, and how long rollback takes.
- **In operation:** let the error budget and the ranked failure list decide what to fund, and measure
  MTTR and detection time per incident, not just incident count.
- **Pilots:** do not gold-plate with multi-region redundancy; do insist even there on rollback and
  quality monitoring.
- **Interviews:** reliability questions reward reasoning about which lever is cheapest for the given
  failure profile, with the composition arithmetic shown.

## Role lens
- **Engineer:** Builds the mechanisms that move each lever: idempotent tools, retry budgets, one-step
  rollback for prompts and models, and traces that make a failed task diagnosable.
- **Architect:** Decides where redundancy, isolation and fallback belong by running the composition
  arithmetic, and sets the reliability targets each component must meet.
- **FDE:** Learns how the customer's network, identity provider and data systems fail, because in their
  environment those dependencies, not your code, often dominate the reliability budget.

## Check yourself
**Q:** Your assistant had six incidents last quarter: two provider outages of 30 minutes, three bad prompt
deploys each taking about three hours to notice and roll back, and one stale index. Where do you invest first?
**A:** The prompt deploys dominate: roughly nine hours of degraded service versus one hour from the
provider, and they are self-inflicted, so they are cheap to fix. The formula also says where in their
duration to cut: I would expect most of those three hours to be detection, because a bad prompt returns 200s, so I would confirm that from the incident timelines before choosing the fix. I would add an evaluation gate and a
canary for prompt changes to cut their frequency, quality monitoring on sampled traffic to detect a bad
prompt within minutes, and one-step prompt rollback to shrink recovery. The stale index points to the
same detection gap and needs a freshness check. A second provider is worth less until those are done,
because it addresses the smallest slice of downtime.

## Interview questions
### What does "reliable" mean for an LLM agent, and how is it different from "available"?
- **Reliable means doing the right thing for users over time,** which includes answer quality, not just
  returning responses.
- **Available is narrower:** a system can serve HTTP 200 on every request while a model update or stale
  index makes answers wrong, so it is up but unreliable.
- **So** you need a quality SLO on sampled traffic alongside the availability SLO.

### An agent completes only about 90 percent of its 10-step tasks. How do you debug and improve that?
- **Do the arithmetic first:** 99 percent per step over ten steps is about 90 percent per task, so the
  per-step failure rate is the target.
- **Classify the failures** from traces: transient (timeouts, 429s) versus deterministic bugs, because
  retries help only the first kind.
- **Fix in order:** idempotent tools and resumable state, then a bounded retry, which moves the task to
  about 99.9 percent for transient failures.

### When would you not add a second LLM provider for redundancy?
- **When the other serial terms dominate:** in the worked example the fallback adds only about a tenth
  of a percent.
- **When recovery time is the bigger lever:** cutting MTTR from two hours to fifteen minutes moves the
  app's own term much more.
- **When failures are correlated:** a shared region, quota or client bug takes both paths down together,
  and an untested fallback is likely broken anyway.

### Your service has one incident a month. What availability does a two-hour versus a fifteen-minute MTTR give?
- **Use MTBF / (MTBF + MTTR)** with about 720 hours between failures.
- **Two hours** gives about 99.72 percent; **fifteen minutes** gives about 99.97 percent.
- **Lesson:** at this failure rate, recovery speed is worth far more than another nine of component
  redundancy.

### How do you decide where the next sprint of reliability work goes?
- **Rank failure modes by expected impact:** frequency × mean duration × mean blast radius, from incident
  history and failure analysis.
- **Inside the top mode,** look at the detect, diagnose and recover split and halve the cheapest term,
  because halving any term halves the impact.
- **Let the error-budget policy decide timing,** agreed in advance so it is not re-argued at each launch.

### Your agent crashed at step 6 of 9 after creating a ticket and issuing a refund. How should a retry behave?
- **Resume, do not restart:** load the checkpoint written after step 6 and continue from step 7, so the
  model does not re-plan and repeat earlier writes.
- **Make writes idempotent:** each tool call carries a key built from run id and step, so a replayed
  refund is rejected as a duplicate by the tool side.
- **Escalate if unsure:** if the last write's outcome is unknown, reconcile against the target system or
  hand off to a human rather than guess.

## Quiz
### Q: Which change most directly reduces MTTR rather than failure frequency?
- [ ] Adding an evaluation gate before prompt changes reach production
- [x] Making prompt and model versions revertible in one step
- [ ] Pinning the model version instead of using a floating alias
- [ ] Running a canary on 5 percent of traffic before full rollout
> Recovery time shrinks when reverting is fast and safe. Gates, pinning and canaries prevent or contain
> bad changes, which raises MTBF or reduces blast radius rather than shortening recovery.

### Q: Two redundant LLM providers each claim 99.9 percent. Why might the pair deliver far less than 1 − 0.001²?
- [ ] Because SLA percentages cannot be multiplied
- [ ] Because the formula applies only to hardware failures
- [x] Because failures can be correlated through shared regions, quotas, client code or configuration
- [ ] Because failover always takes longer than the outage
> The parallel formula assumes independence. A shared cloud region, a common client library bug or a
> bad shared config takes both paths down at once, so real gains are smaller.

### Q: Why is answer quality part of system reliability for an LLM product?
- [ ] Because quality metrics are required in every SLA
- [x] Because a system can return successful responses while users receive wrong answers, which is a failure to them
- [ ] Because low quality always causes HTTP errors eventually
- [ ] Because quality monitoring replaces availability monitoring
> Reliability means doing the right thing for users. Silent quality regressions produce 200 responses,
> so they need their own SLO and monitoring alongside availability, not instead of it.

## Use cases & projects
### Use cases
- **Agents that write to customer systems.** Ticketing, refunds or CRM updates need idempotent tools and
  resumable runs so a crash or retry never duplicates a business action.
- **Agents on a single hosted model provider.** Rate limits and regional outages are the common
  dependency failure; timeouts, retry budgets and a degraded-mode flag keep runs finishing.
- **Prompt and model upgrades.** Most incidents are change-induced, so eval gates, canaries and one-step
  rollback are the first reliability investment for an agent team.
- **Quarterly reliability planning.** Ranking failure modes by frequency × duration × blast radius
  decides whether the next sprint goes to detection, rollback or a second provider.

### Mini project
**Make a flaky agent finish its tasks (2-3 hours).**
- **Goal:** raise the task-completion rate of a 10-step tool-calling agent without duplicating writes.
- **Steps:** build an agent whose tools fail transiently 1 percent of the time and whose one write tool
  records every call; run 500 tasks and measure completion; add a bounded retry with backoff, an
  idempotency key per run and step, and a checkpoint after each step; inject crashes mid-run and resume
  from the checkpoint.
- **Done when:** completion climbs from about 90 percent toward 99.9 percent, the write tool shows zero
  duplicate effects after resumes, and you can name which failures retries could not fix.
