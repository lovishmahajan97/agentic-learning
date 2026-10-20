---
id: production-debugging
module: delivery-ownership
name: Production debugging
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - delivery-ownership/failure-analysis
  - delivery-ownership/incident-severity-and-escalation
  - delivery-ownership/root-cause-analysis-and-postmortems
  - observability/tracing
  - agent-reliability/deterministic-replay-and-reproducibility
  - agent-reliability/conversation-execution-identifiers
  - rag-retrieval/retrieval-failure-diagnosis
  - llmops-platform/rollback
sources:
  - David J. Agans, 2002 — Debugging: The 9 Indispensable Rules
  - Beyer, Jones, Petoff, Murphy (eds.), 2016 — Site Reliability Engineering
---

## TL;DR
Production debugging is finding why a live system misbehaved when you cannot pause it, attach a
debugger, or trust that the bug will happen again. For LLM systems it hinges on one move: reconstruct the
*exact* input the model saw (rendered prompt, retrieved context, tool results, model version, sampling
parameters) from traces, then bisect the pipeline and replay with one variable changed at a time, after
first stopping the bleeding.

## Why it exists
In development you control everything: the input, the code version, the data, and you can step through
it. In production you get a ticket that says "the assistant told a customer the wrong refund policy
yesterday afternoon." The request is gone, the process that served it has been recycled, the vector index
may have been rebuilt since, and the model behind the API may have been updated by the provider.

The naive approach is to copy the user's question into a notebook, send it to the model, and see what
comes back. It almost always "works", and the ticket gets closed as "cannot reproduce." That fails
because the user's question is a small fraction of what the model actually received. The real input was a
system prompt at some template version, plus chunks retrieved from the index *as it was then*, plus
conversation history, plus tool outputs, sent to a specific model snapshot at a specific temperature.
Change any of these and you are debugging a different request. On top of that, sampling makes the
system stochastic: a failure that happens 1 time in 10 will look fixed in a single replay 90% of the
time.

Production debugging is the discipline of capturing enough at request time that the failure can be
reconstructed afterwards, and of narrowing it down by evidence rather than by guessing and redeploying.

## How it works
### 1. Stabilize before you diagnose
If users are being harmed now, mitigation comes first: roll back the last prompt or model change, flip a
feature flag, route to a fallback, or disable the failing tool. You do this *before* understanding the
cause because diagnosis can take hours while the damage accrues per request. Capture evidence (trace IDs,
a sample of bad outputs, current config) before rolling back, because the rollback destroys the live
state you would otherwise examine.

### 2. Scope the failure
Ask: since when, for whom, how often? Query metrics and logs to get a failure rate over time, sliced by
tenant, model, prompt version, region and client version. A step change at 14:02 points at an event; a
slow climb points at drift (index staleness, traffic mix). A failure confined to one tenant points at
their data or config, not your code. Scoping turns "it's broken" into "3% of tenant X's requests since
the 14:00 deploy."

### 3. Find concrete failing requests
Pick specific failing requests via their correlation or trace ID, plus a few *succeeding* requests from
the same window. Comparing a bad trace against a good one (differential debugging) is usually faster than
studying the bad one alone, because the difference is the clue.

### 4. Reconstruct the exact input
From the trace, recover everything that determined the output:
- the fully rendered messages, not the template plus variables;
- the model identifier and snapshot the API actually reported, not the alias you configured;
- temperature, top_p, max tokens, tool schemas, response format;
- retrieved chunk IDs and their text *at that time*;
- every tool call and its raw result, including errors and truncation.
If any of these was not logged, that gap is itself a finding: you will fix logging as an action item.

### 5. Bisect the pipeline
An LLM application is a chain: input handling → retrieval → prompt assembly → model call → parsing →
tool execution → post-processing. Check the intermediate output at each boundary. If the right document
was never retrieved, no prompt change will help; if it was retrieved but sat below a truncation limit,
the bug is in assembly; if it was in the prompt and the model ignored it, the bug is in generation.
Bisecting is logarithmic in the number of stages, which is why it beats reading the whole trace linearly.

### 6. Replay with one variable changed, many times
Replay the reconstructed input N times at the logged parameters to measure the failure rate, then change
exactly one factor (current index instead of historical, new prompt version, other model) and measure
again. Repeating matters because a single stochastic sample proves nothing; changing one factor at a time
matters because changing two leaves you unable to say which one fixed it. Do not assume temperature 0
makes a hosted model deterministic: batching and floating-point effects on the provider side can still
vary outputs, and seed parameters, where offered, are typically best-effort. So even a "deterministic"
replay deserves a handful of repeats before you trust it.

Stub side-effecting tools during replay. Feed the model the recorded tool results instead of calling the
live tools, *because* a replay that re-executes `issue_refund` is a second incident, and because live
tools return today's data, which silently changes the input you were trying to hold fixed.

### When the failure is latency, cost or errors rather than a wrong answer
The same scope-trace-bisect loop applies, but the evidence is the span waterfall rather than the content.
Find the critical path in a slow trace: is the time in queueing before the model call, in the provider's
time to first token, in a long generation (output length often dominates end-to-end latency,
because each output token is a separate decode step), in retries, or in a tool? Compare p50 against p99, because a
healthy median with a terrible tail usually means a minority of requests are doing something different
(retry storms, oversized contexts, one slow tool, agent loops that ran many more steps), and the fix is
to find what distinguishes them, not to speed up the average request. Cost spikes yield to the same
slicing: tokens per request by tenant, route and prompt version usually reveal one path whose context or
step count grew.

### 7. Correlate with change
Most production regressions follow a change: a deploy, prompt edit, config or flag flip, index rebuild,
dependency upgrade, upstream schema change, or a provider-side model update. A change log with timestamps
lets you line up the onset of the failure against candidate causes.

### 8. Close the loop
The fix ships with the captured failing case added to the regression suite, so the same bug cannot
silently return, and with any logging gaps found in step 4 closed.

## Example
A minimal replay harness. It loads a logged trace, replays it at the original parameters to estimate the
failure rate, then swaps one factor. `complete` stands in for your provider client and `check` for a
task-specific assertion.

```python
import json
from statistics import mean

def complete(messages, model, **params) -> str:
    raise NotImplementedError("call your provider SDK here")

def check(output: str) -> bool:
    # task-specific: did the answer state the 30-day refund window?
    return "30 days" in output

def replay(trace, n=20, **overrides):
    t = {**trace, **overrides}            # override exactly one factor
    outs = [complete(t["messages"], t["model"], **t["params"]) for _ in range(n)]
    return mean(not check(o) for o in outs)   # failure rate

trace = json.load(open("trace_7f3a.json"))   # rendered messages, model, params, chunk ids
baseline = replay(trace)
print(f"failure rate at logged inputs: {baseline:.0%}")

# Hypothesis: the retrieved chunk was the old policy page. Rebuild messages with today's chunk.
fixed_ctx = dict(trace, messages=rebuild_with_current_chunks(trace))  # your helper
print(f"with current index: {replay(fixed_ctx):.0%}")

# Hypothesis: temperature is the cause. Change only that.
print(f"at temperature 0: {replay(trace, params={**trace['params'], 'temperature': 0}):.0%}")
```

If the baseline reproduces at, say, 40% and swapping in today's chunk drops it to 0% while temperature
0 still fails, the defect is stale retrieval, not sampling, and the fix belongs in index freshness. Keep
the sample size honest: 0 failures in 20 replays only bounds the true rate below roughly 3/20 ≈ 15% at 95%
confidence (the "rule of three"), so a failure that was rare to begin with needs a few hundred replays
before "0%" means fixed.

## Trade-offs
- Logging full rendered prompts and tool results makes reconstruction possible, but costs storage and
  creates a PII store; it flips toward sampling or redaction when inputs are sensitive or volume is huge.
- Rolling back immediately stops harm but destroys the live state you wanted to inspect; it is the right
  call whenever users are affected, provided you snapshot evidence first.
- Replaying many samples gives a trustworthy failure rate but costs tokens and time; for deterministic
  failures (parsing, tool errors) a single replay is enough.
- Debugging directly against production data gives the most faithful reproduction but risks side effects
  and access violations; prefer read-only replicas and replay with tools stubbed to recorded results.

## Pitfalls
- Replaying only the user's question instead of the full rendered input; the symptom is a long run of
  "cannot reproduce" closures for problems customers keep reporting.
- Trusting a single successful replay of a stochastic failure; the symptom is a fix that "worked" in
  testing and a failure rate in production that never moved.
- Logging the configured model alias rather than the snapshot the API returned; the symptom is a
  regression nobody can explain because no deploy happened on your side.
- Changing several things in one hotfix; the symptom is that the problem goes away and nobody can say
  why, so it returns on the next unrelated change.
- Debugging before mitigating; the symptom is an hour of elegant diagnosis while the incident's impact
  keeps growing.
- Replaying with live tools instead of recorded results; the symptom is duplicate side effects (a second
  email or refund) or replays whose tool outputs differ from the trace, so the "reproduction" is not one.

## When to use
Use this practice whenever a live system produces wrong, slow or failed results and the cause is not
obvious from one error message. The investment that pays for itself is upfront: trace IDs threaded
through every stage, rendered prompts and retrieval results captured (or sampled), a change log, and a
replay harness. Measure your time-to-reproduce; if most investigations stall at "cannot reproduce", the
problem is instrumentation, not skill. In interviews, a strong answer to "a customer says the bot gave a
wrong answer" walks through mitigate, scope, find the trace, reconstruct, bisect, replay and regress,
rather than jumping to "I'd tweak the prompt."

## Role lens
- **Engineer:** Instruments every stage with trace IDs, records rendered prompts and model snapshots, and
  owns the replay harness that turns tickets into reproducible test cases.
- **Architect:** Decides what the platform must capture by default, how long it is retained, who can
  access it, and where redaction happens, so debugging is possible without a privacy breach.
- **FDE:** Debugs inside a customer environment with limited access, so negotiates log export, read-only
  replicas and trace sharing early, before the first production issue arrives.

## Check yourself
**Q:** A customer reports the assistant cited a discontinued product twice last week. You paste the
question into the playground and get a correct answer. What do you do next, and why is your playground
result not evidence?
**A:** The playground used today's prompt template, today's index and possibly a different model
snapshot and temperature, so it tested a different request. I would find the two traces by request ID,
recover the rendered messages, retrieved chunk IDs and model snapshot, and check the retrieval stage
first: was the discontinued product's page returned? If so, the index or its filters are stale and the
fix is freshness, not the prompt. I would replay the logged input 20 or more times to get a failure rate,
then swap only the retrieved context to confirm the cause, and add the case to the regression suite.

## Quiz
### Q: Why should you replay a reconstructed LLM request many times rather than once?
- [ ] Providers rate-limit single requests more aggressively than batches
- [x] Sampling makes the output stochastic, so one replay cannot distinguish a fixed bug from a lucky draw
- [ ] The first call warms the cache, so only later replays reflect production behaviour
- [ ] Multiple replays are required to recover the original random seed
> With temperature above zero a failure may occur in only a fraction of samples; measuring the failure
> rate across many replays, before and after a change, is the only way to show the change mattered.

### Q: An answer was wrong. The trace shows the correct document was the top retrieval result, but the prompt assembler's token budget cut it before the model call. Where is the defect?
- [ ] In the embedding model, which ranked the document too low
- [ ] In the generation step, which should have inferred the missing content
- [x] In prompt assembly, which dropped retrieved content before it reached the model
- [ ] In sampling, because a lower temperature would have found it
> Bisecting the pipeline shows retrieval did its job and the model never saw the content; the fix belongs
> in how context is budgeted and ordered, not in retrieval tuning or prompt wording.

### Q: Users are receiving harmful outputs after a prompt change an hour ago. What comes first?
- [ ] Reproduce the failure locally so the rollback can be justified
- [ ] Write the regression test so the bug cannot recur
- [ ] Bisect the pipeline to locate the faulty stage
- [x] Capture sample trace IDs and config, then roll back the prompt change
> Mitigation precedes diagnosis when users are being harmed, but grabbing evidence first keeps the
> investigation possible after the rollback erases the live state.
