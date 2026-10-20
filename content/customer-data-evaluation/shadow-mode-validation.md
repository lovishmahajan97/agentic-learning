---
id: shadow-mode-validation
module: customer-data-evaluation
name: Shadow-mode validation
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - llmops-platform/canary-shadow-deployments
  - evals/offline-vs-online-evaluation
  - evals/a-b-testing
  - customer-data-evaluation/customer-specific-regression-suites
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - deployment/rollout-criteria-and-rollback-triggers
  - agents/sandboxing
  - scoping/pilot-to-production-plan
---

## TL;DR
Shadow mode runs a candidate system (new model, prompt or pipeline) on a copy of real production
traffic while only the current system's answer reaches users. You compare the two outputs offline. It
exists because curated eval sets never match a customer's real input distribution, and you want that
evidence before any user is exposed. It measures agreement and judged quality, not user outcomes, and
it is only safe if the candidate cannot cause side effects.

## Why it exists
Before go-live, or before swapping a model under a live customer, you have a golden set and a
regression suite. Both are curated: someone chose which cases to include, and that choice is biased
toward cases people thought of. Real traffic contains the long tail nobody wrote down: scanned faxes,
mixed languages, users pasting entire email threads, queries the customer's own SMEs would not have
predicted. The question a customer asks is "will it work on *our* traffic?", and only their traffic
can answer it.

The naive alternatives each fail. Releasing to everyone and watching metrics exposes every user to an
unvalidated system, which many customers (regulated ones especially) will not accept. A canary or A/B
test exposes a fraction of users, which is better, but still lets real users act on candidate output
before you know it is sound. Replaying last month's logs offline gets the distribution right but goes
stale and misses things only visible live: latency under real load, rate limits, timeouts, new document
types arriving this week.

Shadow mode sits between offline evaluation and exposure: live distribution, live infrastructure, zero
user impact. It is a common last step before a canary, and for a forward-deployed team it is often
the evidence that gets a customer's risk committee to approve the switch.

## How it works
### 1. Fork the traffic
Each production request is handled by the current system as usual. A copy, possibly a sampled
fraction, is sent to the candidate. There are two ways to fork:
- **Asynchronous fork (preferred).** The production path finishes and returns first; the request and
  the production output are published to a queue, and a shadow worker runs the candidate later. Because
  the candidate is off the request path, its latency, errors or crashes cannot affect users.
- **Inline fork.** The candidate is called concurrently in the same request, with a strict timeout and
  its result discarded. This measures latency under identical conditions, but a careless implementation
  (awaiting the shadow call, sharing a connection pool or rate-limit quota) can slow production down.

### 2. Neutralise side effects
This is the step that makes shadow mode safe or dangerous. A candidate agent that sends emails, writes
records or calls payment APIs must not do so in shadow. Tool adapters run in **dry-run mode**: read-only
tools may execute (or be served from the production call's recorded results), and write tools record
the intended call and return a stub. The intended calls are themselves valuable data: "the candidate
would have refunded order 4417" is something you compare against what production did.

### 3. Capture comparable records
For each request, log the input, both outputs, both latencies, token cost, errors, and the versions of
everything (model, prompt, index). Shadow data is still customer data: it goes to the same store, under
the same retention and access rules, and sending it to a *new* model or vendor needs the same data
approval as production would. Mirroring traffic to an unapproved provider "only for testing" is still
a data transfer.

### 4. Compare
- **Deterministic fields** (extracted values, classifications, routing decisions, tool calls): compute
  exact agreement rate and a confusion matrix between production and candidate.
- **Free-text outputs:** agreement is meaningless at the string level, so sample the disagreements and
  have an LLM judge or SME do a pairwise comparison: candidate better, production better, equivalent.
- **Operational metrics:** p50/p95 latency, error and timeout rate, cost per request.

Focus human review on disagreements, because that is where the information is; where both systems agree,
both are usually right (or both wrong in ways the regression suite should cover).

### 5. Know the blind spots
Users never see shadow output, so there is no click, edit or thumbs signal for the candidate. Shadow
cannot tell you whether users would have completed their task with it. In multi-turn conversations the
shadow sees the history produced by production, so you are evaluating one-step counterfactuals, not
full candidate trajectories; after the first disagreement the conversations diverge. User-outcome
questions need a canary or A/B test afterwards.

### 6. Exit criteria
Agree them with the customer *before* starting: minimum volume and duration (so weekly cycles are
covered), agreement thresholds on critical fields, the candidate-better versus production-better ratio
on adjudicated disagreements, no unexplained dry-run write calls, and latency and error budgets.

## Example
An asynchronous shadow worker for a claims-triage service. Production has already answered; this runs
the candidate with write tools stubbed and records a comparable row.

```python
import asyncio, time, json

WRITE_TOOLS = {"update_claim", "send_email", "issue_payment"}

class DryRunTools:
    def __init__(self, real_tools, recorded_reads):
        self.real, self.recorded, self.intended_writes = real_tools, recorded_reads, []
    async def call(self, name, args):
        if name in WRITE_TOOLS:                       # never execute writes in shadow
            self.intended_writes.append({"tool": name, "args": args})
            return {"status": "ok", "dry_run": True}
        key = json.dumps([name, args], sort_keys=True)
        if key in self.recorded:                      # reuse prod's read result: same facts
            return self.recorded[key]
        return await self.real.call(name, args)       # read-only, safe to execute

async def shadow_one(event, candidate, real_tools, store, timeout_s=30):
    tools = DryRunTools(real_tools, event["recorded_reads"])
    t0 = time.monotonic()
    try:
        out = await asyncio.wait_for(candidate.run(event["input"], tools), timeout_s)
        err = None
    except Exception as e:                            # shadow failures are data, not incidents
        out, err = None, repr(e)
    store.write({
        "request_id": event["request_id"], "tenant": event["tenant"],
        "prod": event["prod_output"], "cand": out, "cand_error": err,
        "cand_latency_s": round(time.monotonic() - t0, 3),
        "cand_intended_writes": tools.intended_writes,
        "prod_writes": event["prod_writes"], "versions": candidate.versions,
    })
```

Suppose two weeks of shadow on one customer yields 18,000 requests (illustrative). Triage category agrees
on 94%; of 1,080 disagreements, 300 sampled and SME-adjudicated split 140 candidate-better, 90
production-better, 70 equivalent. That ratio, plus a clean write-call comparison and p95 latency inside
budget, is the case for moving to a 5% canary, not for a full switch.

## Trade-offs
- Zero user exposure in exchange for no user-outcome signal; when task completion is the KPI, shadow
  must be followed by a canary or A/B test rather than replacing it.
- Asynchronous forking protects production latency completely but cannot measure latency under identical
  load; inline forking measures it but risks contention unless quotas and pools are isolated.
- Mirroring all traffic gives rare cases a chance to appear but doubles inference cost; sampling cuts
  cost and is fine once common cases are covered, unless the risk sits in rare inputs.
- Replaying recorded tool reads keeps the comparison fair, but hides failures where the candidate would
  have needed a different read; execute live reads when tool choice is what changed.

## Pitfalls
- Forgetting to stub a write tool; the symptom is duplicate emails, records or payments that trace back to
  the shadow worker, often discovered by the customer first.
- Sending mirrored traffic to a model or vendor the customer has not approved; the symptom is a security
  review finding that the shadow pipeline was an unapproved data transfer.
- Measuring string-level agreement on free text; the symptom is an alarmingly low agreement rate that
  says nothing about quality because paraphrases count as disagreements.
- Running shadow for a few days and declaring victory; the symptom is a regression in month-end or Monday
  traffic patterns that the short window never contained.
- Letting the shadow call share the production rate-limit quota; the symptom is production 429 errors that
  begin exactly when shadow mode is switched on.

## When to use
Use shadow mode before replacing the model, prompt or pipeline under a live customer, before go-live when
the customer's legacy process or human team can act as "production", and whenever the customer requires
evidence on real traffic before exposure. Skip it for changes the regression suite fully characterises
(a formatting fix) or when doubled cost is unaffordable and a small canary is acceptable. Measure field
agreement, adjudicated win/loss on disagreements, intended writes, latency and errors. In interviews,
naming side-effect isolation and the lack of user-outcome signal shows real experience.

## Role lens
- **Engineer:** Builds the async fork, dry-run tool adapters, comparison records and the disagreement
  sampler, and keeps shadow quotas and pools separate from production's.
- **Architect:** Decides where the fork happens, makes every tool declare read or write so dry-run is
  enforceable, and defines shadow, canary and full-rollout stages.
- **FDE:** Negotiates exit criteria and data approval with the customer, runs SME adjudication of
  disagreements, and presents the shadow evidence to the customer's risk or change board.

## Check yourself
**Q:** A customer wants to switch their support agent to a cheaper model. Two weeks of shadow show 97%
agreement on ticket routing and a judged tie on replies. The product lead says, "Ship it to everyone."
What is missing?
**A:** Shadow shows the candidate behaves like production on the inputs it saw, but not how users react:
nobody acted on its replies, and for multi-turn chats it only answered given production's history, so
its own trajectories were never tested. I would check that the 3% routing disagreements are not
concentrated in a high-severity queue, compare intended write calls such as refunds against production's,
and confirm latency and errors. Then go to a staged canary with rollback triggers on resolution rate,
escalations and complaints, rather than a full switch.

## Quiz
### Q: What is the most important safety requirement when shadowing an agent that can call tools?
- [ ] The shadow must use the same random seed as production
- [x] Write tools must run in dry-run mode so the candidate records intended actions without executing them
- [ ] The shadow must run on a GPU separate from the one production uses
- [ ] Shadow outputs must be shown to a small fraction of users for feedback
> Without dry-run writes, the candidate would duplicate or contradict real actions such as emails or
> payments. Showing output to users is a canary, not shadow mode.

### Q: Why does shadow mode not replace a canary or A/B test?
- [ ] Shadow traffic is too small to measure anything
- [ ] Shadow mode cannot measure latency
- [x] Users never see candidate outputs, so their behaviour and task outcomes under the candidate are never observed
- [ ] Shadow mode only works for classification tasks
> Shadow gives agreement, judged quality and operational metrics on real inputs, but user outcomes need
> exposure. Latency can be measured in shadow, especially with an inline fork.

### Q: In a multi-turn chat shadowed on each turn, what exactly is being evaluated?
- [ ] The candidate's full conversations from start to finish
- [x] The candidate's next reply given a history produced by the production system
- [ ] The production system's replies as judged by the candidate
- [ ] Only the first turn, since later turns cannot be mirrored
> Each shadow turn is conditioned on production's previous replies, so it is a one-step counterfactual.
> After the first divergence, the candidate's own trajectory is never actually played out.
