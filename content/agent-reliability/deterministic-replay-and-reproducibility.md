---
id: deterministic-replay-and-reproducibility
module: agent-reliability
name: Deterministic replay & reproducibility
kind: concept
difficulty: advanced
est_minutes: 15
status: fact-checked
related:
  - agent-reliability/durable-execution-and-checkpointing
  - agent-reliability/structured-event-streams
  - agent-reliability/audit-logs-execution-traces
  - agent-reliability/agent-trajectory-evaluation
  - llmops-platform/reproducible-evaluations
  - model-internals/sampling-strategies
  - reliability/durable-workflow-execution
  - evals/pass-k
sources:
  - Official docs: Temporal (workflow determinism constraints)
  - Official docs: LangGraph (time travel and checkpoints)
---

## TL;DR
You cannot reproduce an agent run by rerunning it: the model samples, providers change, tools return
live data, and one different token changes every later step. Reproducibility therefore comes in three
grades: exact replay from a recording of every non-deterministic boundary, best-effort re-execution with
pinned model, seed and temperature, and statistical reproduction over many runs. Design the runtime so the
orchestration logic is deterministic and every effect is recorded, and the first grade is always
available.

## Why it exists
A run fails at 03:00: the agent called the wrong tool at step 7 and corrupted a record. The engineer
reruns the same task in the morning and it works. There is no bug to fix because there is no failure to
look at. This happens because an agent run is a chain of decisions, each conditioned on everything before
it, and several links in the chain are not functions of their inputs: the model's sampled output, the
provider's silently updated weights or serving stack, the tool that returned today's data instead of last
night's, the clock, the random id generator. Change one token at step 2 and step 7 may not happen at all.

The naive alternative is "set temperature to 0 and rerun". It fails for reasons worth knowing. Greedy
decoding removes sampling noise but not numerical noise: floating-point reductions on GPUs are
order-dependent, batching puts your request next to different neighbours, and kernels get updated, so the
argmax can flip on near-tied logits. Hosted providers additionally move alias model names to new
snapshots, and even a dated snapshot can see serving-stack changes. And
none of this touches the tools, which are the bigger source of divergence in practice.

Durable workflow engines solved the same problem for ordinary distributed code by splitting it into
deterministic orchestration and recorded effects. Agents inherit that solution: record every effect,
replay the recording, and the orchestration reproduces the exact trajectory, step for step, on any
machine, with no model call at all.

## How it works
### Classify everything as orchestration or effect
Orchestration is the code that decides what to do next given the history: assembling the prompt, parsing
the tool call, checking the policy, choosing the next step. It must be a pure function of the recorded
history. Effects are everything whose output is not a function of its inputs from the orchestrator's point
of view: LLM calls, tool calls, `time.now()`, `random()`, `uuid4()`, environment reads. Each effect goes
through one interface.

### Record mode
The runtime runs normally. Every effect request is written to the run's event stream before execution with
its sequence number and an input hash; its output is written after. The LLM request event stores the model
identifier, the full parameters and a reference to the exact prompt; the response event stores the exact
text and tool calls. Tool events store arguments and results by reference. Clock and random draws are
recorded too, *because* a timestamp in a prompt or a generated id in an argument is enough to make the
next step differ.

### Replay mode
The orchestrator runs again from the initial input, but the effect interface is switched to serve from the
recording: the nth effect request returns the nth recorded output, after checking that the request matches
what was recorded (same effect type and input hash). No model is called; no tool runs. Because
orchestration is deterministic, it issues exactly the same sequence of requests, and the run reproduces
step by step. You can set breakpoints, inspect the prompt the model saw at step 7, and see why its answer
led to the wrong tool.

The match check is the crucial safety feature. If the orchestrator issues a request that does not match
the recording, the code has changed in a way that alters the trajectory; the replayer raises a
non-determinism error rather than silently serving a mismatched answer. This is the same rule durable
workflow engines enforce: code versions and recordings go together, so the runtime records a code version
with each run and replays against the same version.

### Fork: replay to a point, then go live
To test a fix, replay the recording up to step k, then switch the effect interface back to live for steps
after k. Everything up to the divergence is identical, so the comparison isolates the change. This is what
"time travel" means in checkpoint-based frameworks: load the state at a checkpoint, optionally edit it,
continue live.

### The three grades of reproducibility
1. **Exact replay** (recording). Always achievable if the recording is complete. It reproduces the past;
   it cannot tell you what the model would do now.
2. **Best-effort re-execution.** Pin the model version where the provider exposes one, set temperature 0
   or a fixed seed, pin tool data with snapshots or fakes, and freeze the clock. Some providers expose a
   seed parameter and a fingerprint of the serving configuration, documented as best-effort rather than
   guaranteed; self-hosted engines can get closer with fixed batch composition and kernels, but "close" is
   the honest claim. Use it to ask "does the model still make this mistake", not to prove anything
   bit-exact.
3. **Statistical reproduction.** Accept non-determinism and measure it: run the task N times, report the
   pass rate with an interval, and compare distributions before and after a change. This is the only grade
   that answers "did my fix help" for a stochastic system.

### What the recording must contain
The checklist, in the order people forget it: model id and parameters, exact prompt text or a hash
resolving to it, the exact response including tool-call JSON, every tool's arguments and result, clock
reads, random draws, environment variables that reach prompts, and the code version. Miss one and replay
diverges at the first step that touches it.

## Example
An effect recorder/replayer wrapped around an agent loop. In `record` mode it logs every boundary; in
`replay` mode it serves recorded outputs and refuses to continue if the orchestrator asks for something
different.

```python
import hashlib, json

def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]

class NonDeterminismError(RuntimeError):
    pass

class Effects:
    """Every non-deterministic boundary goes through `do`. Orchestration stays pure."""
    def __init__(self, mode: str, log: list[dict], live: dict):
        self.mode, self.log, self.live, self.i = mode, log, live, 0

    def do(self, kind: str, **inputs):
        key = _h({"kind": kind, **inputs})
        if self.mode == "replay":
            if self.i >= len(self.log):
                raise NonDeterminismError(f"effect #{self.i} ({kind}) not in recording")
            rec = self.log[self.i]
            if rec["key"] != key:
                raise NonDeterminismError(f"effect #{self.i}: recorded {rec['kind']}/{rec['key']}, got {kind}/{key}")
            self.i += 1
            return rec["output"]
        out = self.live[kind](**inputs)                      # record mode: run it, then log it
        self.log.append({"seq": self.i, "kind": kind, "key": key, "output": out}); self.i += 1
        return out

def agent(task: str, fx: Effects, max_steps: int = 8) -> str:
    messages = [{"role": "user", "content": f"{task} (today is {fx.do('clock')})"}]
    for _ in range(max_steps):
        resp = fx.do("llm", model="model-x@2026-06", messages=messages, temperature=0)
        messages.append({"role": "assistant", "content": resp["text"], "tool_calls": resp.get("tool_calls", [])})
        if not resp.get("tool_calls"):
            return resp["text"]
        for call in resp["tool_calls"]:
            result = fx.do("tool", name=call["name"], arguments=call["arguments"])
            messages.append({"role": "tool", "name": call["name"], "content": result})
    return "step budget exhausted"

# Record once against live services and persist `log`. Later, replay with no network at all:
#   answer = agent(task, Effects("replay", log, live={}))   -> identical trajectory, step by step
```

## Trade-offs
- Exact replay is cheap to run and perfectly faithful to the past, but it can only reproduce what was
  recorded; the moment you change the prompt or the code before the failing step, you are in re-execution
  territory with its uncertainty.
- Recording every effect including clock and random draws makes replay watertight but imposes discipline:
  any developer who calls `datetime.now()` directly inside orchestration breaks replay, and the error
  appears weeks later on an old recording.
- Pinning model versions gives stability for reproduction and evals, but pinned versions get deprecated and
  a pinned model drifts away from the one users get; schedule the re-pinning rather than discovering it.
- Statistical reproduction answers the right question for stochastic systems but costs N runs per task;
  for expensive multi-step agents, replay-then-fork from the step before the failure cuts the cost to the
  divergent tail.

## Pitfalls
- Believing temperature 0 means deterministic; the symptom is a "reproduction" that diverges at step 3 with
  no code change and a team debugging a phantom.
- Recording the prompt template name but not its exact text or hash; the symptom is a replay that silently
  uses today's template and produces a different trajectory from the one in the incident.
- Calling the model inside orchestration without going through the effect interface, for example a helper
  that "just summarizes" tool output; the symptom is a non-determinism error on replay pointing at a step
  that looks pure.
- Changing code and replaying an old recording without a version check; the symptom is a mismatched
  response served to a different request, producing a run that never happened.
- Treating a single successful rerun as proof a bug is fixed; the symptom is the bug returning in
  production at roughly its old rate.
- Tools with live side effects running during replay; the symptom is the incident reproduction creating a
  second real ticket.

## When to use
Record every production run by default; the event stream you already keep for resumption and audit is
most of the recording, and the extra cost is clock and random draws. Reach for exact replay first in any
incident, because it is free and faithful, then fork from the step before the divergence to test a fix,
then run the statistical grade to confirm the fix across N runs before shipping. Measure replay coverage
(fraction of runs that replay without a non-determinism error), the divergence rate of best-effort
re-execution, and the run count needed to detect the improvement you care about. In interviews, the
strong answer distinguishes the three grades and explains why orchestration must be pure.

## Role lens
- **Engineer:** Routes every LLM, tool, clock and random call through one effect interface and keeps
  orchestration code free of hidden non-determinism.
- **Architect:** Decides what the recording contains, how code versions are tied to recordings, and which
  grade of reproducibility each process (incident, eval gate, regression) requires.
- **FDE:** Replays a customer's failing run offline, without touching their live systems, to show exactly
  which step diverged and why.

## Check yourself
**Q:** An agent corrupted a record at step 7 of a 12-step run last night. Rerunning the task today
succeeds. Walk through how you find the cause and how you prove your fix works.
**A:** Rerunning is not reproduction; the model, tools and clock differ. Load last night's recording and
replay it with the effect interface in replay mode: no model or tool is called, the orchestrator reissues
the same requests, and the replayer serves the recorded outputs, so the run reproduces exactly. Inspect the
exact prompt the model saw when it chose the step-7 call, including the tool results from steps 1 to 6,
which usually reveals a misleading tool result or a prompt assembled with an unexpected value such as a
timestamp. Make the fix, then fork: replay steps 1 to 6 under the fixed code, go live from step 7, and
confirm the divergence is gone. If the fix changes requests before step 7, the replayer raises a
non-determinism error at that point, which tells you the fork has to start earlier. Because the model is stochastic, a
single success proves little, so run the task N times with the fix and compare the failure rate to the
baseline with a confidence interval. Finally, record the code version with the fix so future replays of
this recording warn that it predates the change.

## Quiz
### Q: Why does setting temperature to 0 not guarantee a reproducible agent run?
- [ ] Temperature only affects the first token of each response
- [x] Greedy decoding removes sampling noise but not numerical, batching, model-version or tool-data variation, any of which can change a step
- [ ] Temperature 0 is ignored by tool-calling models
- [ ] Because tools always run at temperature 1
> Determinism of one component is not determinism of the chain; near-tied logits, changed serving stacks
> and live tool data all alter trajectories regardless of sampling settings.

### Q: During replay the orchestrator requests a tool call that does not match the recording. What should the runtime do?
- [ ] Serve the next recorded output anyway, since the order is probably close enough
- [ ] Call the live tool to fill the gap
- [x] Raise a non-determinism error, because the code has changed in a way that alters the trajectory and the recording no longer applies
- [ ] Skip the request and continue from the following recorded event
> Serving a mismatched output fabricates a run that never happened; failing loudly is what makes replay
> trustworthy, exactly as durable workflow engines do.

### Q: Which grade of reproducibility answers "did my fix reduce the failure rate"?
- [ ] Exact replay of the failing recording
- [ ] Best-effort re-execution with a fixed seed
- [x] Statistical reproduction over many runs with a confidence interval
- [ ] Any single successful rerun after the fix
> Replay reproduces the past and a single rerun is one sample from a stochastic process; only repeated
> runs measure a change in rate.
