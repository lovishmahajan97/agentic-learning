---
id: loop-detection
module: agent-reliability
name: Loop detection
kind: pattern
difficulty: core
est_minutes: 13
status: reviewed
related:
  - agent-reliability/stopping-conditions-termination
  - agent-reliability/max-step-budgets
  - agent-reliability/audit-logs-execution-traces
  - agents/agent-loop
  - agents/reflection-self-critique
  - agents/tool-result-interpretation
---

## TL;DR
Agents get stuck in loops: the same tool call with the same arguments, a two-state oscillation, or a
stretch of actions that produces no new information. Budgets eventually stop them, but only after
burning the whole allowance. Loop detection watches the action stream for exact repeats, short cycles
and no-progress runs, and responds with an escalation ladder (tell the model, block the call, force a
replan, abort) because the cheapest fix is usually to make the repetition visible to the model.

## Why it exists
A model picks its next action from the context, and if the context after a failed action looks almost
identical to the context before it, the most likely next action is the same one. Typical triggers: a
file read that returns an error the model does not interpret; a search that returns nothing; a test
command failing the same way; two edits that undo each other; a flaky tool returning different errors
so the model keeps "trying one more time". The model has no counter; nothing in its input says "this
is attempt four".

A budget alone is a poor answer. With a thirty-step budget, a loop that starts at step five burns
twenty-five steps of increasingly expensive calls, then yields an exhausted partial, and you learn
nothing about *why*. Detecting the loop at step eight saves most of the cost and, more importantly,
creates a chance to recover: a model told explicitly "you have run this exact command three times with
the same output" will often change strategy. What is obvious to a human watching the trace is not
obvious to the model from inside the context, because repeated blocks look like ordinary history.

The naive rule, "abort if the same tool is called twice", fails in both directions. It kills
legitimate repeats (polling a job status, reading two files with the same tool, re-running tests after
each edit) and it misses loops where the arguments differ trivially (whitespace, a reworded query) or
where the cycle spans several steps.

## How it works
### Signals
1. **Exact repeats.** Canonicalize each action: tool name plus arguments with sorted keys and
   normalized whitespace, then hash. Keep a count per hash within the run and flag at a threshold
   (three is a common starting point). Canonicalization matters because models vary key order and
   formatting between attempts.
2. **Short cycles.** Keep the sequence of action hashes and look for a repeated subsequence of period
   *p* in the recent window (A B A B, or A B C A B C). Compare the last *p* actions with the *p*
   before them for *p* from 2 to a small maximum *P* (period 1 is just an exact repeat, which signal
   1 already counts); with hashed actions this is O(P²) comparisons per step, trivial for P ≈ 4.
3. **No progress.** Hash the tool *result* as well. If the pair (action, result) repeats, nothing in
   the world changed, which is the strongest loop signal. Alternatively track a task-specific
   progress metric (failing test count, files changed, distinct URLs visited) and count steps since
   it last improved.
4. **Semantic near-duplicates.** Embed the action or the model's stated intent and flag cosine
   similarity above a threshold with a recent action ("trying the same thing in different words").
   This costs an embedding call per step and produces false positives, so reserve it for high-value
   runs or run it offline over traces.
5. **Reflection text patterns.** The model repeating phrases such as "let me try again" is a weak
   signal, useful only as a tie-breaker.

### Legitimate repetition
Polling (`get_job_status(id)` every few seconds), re-running tests after each edit, and re-reading a
file after modifying it are all repeats that must be allowed. Handle them by marking tools as
pollable in the schema (a higher repeat ceiling or a time budget instead of a count), by including a
monotonically changing argument such as an attempt number in the key for those tools, or by relying
on the result hash: polling that returns a changing status is progress, while polling that returns the
identical status ten times is a loop.

### Escalation ladder
Information is the cheapest intervention, so the rungs are ordered by cost and severity:
1. **Inform.** Inject a message: "You have called `run_tests` with identical arguments three times and
   the output was identical each time. Explain what you will do differently, or report the blocker."
   This alone resolves many loops.
2. **Block.** Return a harness-generated tool error for the repeated call instead of executing it
   ("refused: identical to call 12, whose result did not change"). This saves the tool cost and forces
   a different action.
3. **Force a replan or reflection.** Switch to a reflection prompt, invoke a planner, or narrow the
   available tool set.
4. **Abort.** End the run with status `stuck`, attach the loop evidence, and checkpoint the state for
   a human.
Each rung has its own threshold, for example inform at three, block at four, abort at six.

### Loops above the tool stream
Not every loop is one tool called twice. Two agents can hand a task back and forth (a router sends
it to the billing agent, which decides it is a support question and sends it back); a
critique-and-revise loop can fail to converge, each revision trading one flagged issue for another;
a planner can regenerate essentially the same plan after every failure; a sub-agent can delegate to a
copy of itself. The same three signals apply one level up. Hash handoffs as (from, to, task) and
count repeats; treat a revision whose critique score did not improve as a no-progress step; and cap
delegation depth. These loops are usually invisible to a per-agent guard, because each agent sees
only its own short, healthy-looking history, so the detector has to live where the whole run is
visible: the orchestrator.

### Why deterministic settings make it worse
A loop is the model making the same choice from nearly the same context. Greedy or low-temperature
decoding makes "nearly the same context in, same action out" more likely, which is why agents run at
temperature 0 for reproducibility tend to repeat exactly rather than drift. That is an argument for
detection, not for raising temperature: changing the context (the inform message) changes the
choice for a reason, while sampling noise changes it at random.

### Where it lives
In the harness, between the model's response and tool dispatch, so it sees every action regardless of
which model is used. Log every detection with its evidence (hashes, window contents, counts): this is
also the best data for improving tools and prompts, because it shows which error messages failed to
help the model change course.

## Example
The guard escalates on *stale* repeats: calls with identical canonical (tool, args) whose result
matched an earlier result for the same call. Calls whose results keep changing (re-running tests
after each edit) never escalate by count; the cycle check catches them only if the actions
themselves start repeating, such as two edits undoing each other. Pollable tools get the same ladder
with thresholds scaled up, as a stand-in for a time budget. A refused call counts as stale, because
blocking made no progress either, so a model that keeps insisting climbs to abort.

```python
import hashlib, json
from collections import Counter, deque

def canon(*parts):     # strip volatile fields (timestamps, request ids) from results first
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode()).hexdigest()

class LoopGuard:
    def __init__(self, inform=1, block=2, abort=4, window=12, max_period=4,
                 pollable=(), poll_factor=10):
        self.calls, self.stale, self.seen = Counter(), Counter(), set()
        self.seq, self.max_period = deque(maxlen=window), max_period
        self.pollable, self.poll_factor = set(pollable), poll_factor
        self.inform, self.block, self.abort = inform, block, abort

    def _cycle(self):
        s = list(self.seq)
        for p in range(2, self.max_period + 1):  # period 1 is the stale count's job
            if len(s) >= 2 * p and s[-p:] == s[-2 * p:-p] and len(set(s[-p:])) > 1:
                return p
        return 0

    def before(self, name, args):                # call before dispatching the tool
        h = canon(name, args)
        self.seq.append(h); self.calls[h] += 1
        n, st = self.calls[h], self.stale[h]
        f = self.poll_factor if name in self.pollable else 1
        if st >= self.abort * f:
            return "abort", f"{name}: {n} identical calls, result unchanged {st} times"
        if st >= self.block * f:
            self.stale[h] += 1                   # a refused call is no progress either
            return "block", f"refused: identical to {n - 1} earlier calls; result never changed"
        if st >= self.inform * f:
            return "inform", f"call {n} of {name} with these exact args; last result was identical"
        if p := self._cycle():
            return "inform", f"your last {2 * p} actions repeat with period {p}; change approach"
        return "ok", ""

    def after(self, name, args, result):         # call after the tool returns
        if (k := canon(name, args, str(result))) in self.seen:
            self.stale[canon(name, args)] += 1
        self.seen.add(k)
```

Trace a failing `run_tests` that prints the same 40-line failure each time. Call 1 runs; call 2
runs and its result matches call 1 (stale = 1). Call 3 runs with an inform message attached. Call 4
is refused (stale was 2), as is call 5, and call 6 aborts the run as `stuck`: inform at three,
block at four, abort at six. Now trace a healthy edit-test cycle: every `run_tests` output differs
because the failing count drops, so stale stays at 0 and nothing fires. A deploy poller declared
pollable that returns "building" over and over is informed on call 12 (ten results that repeated an
earlier one) and refused from call 22, by which point a wall-clock budget should normally have taken over.

## Trade-offs
- Exact-match detection is cheap with near-zero false positives, but misses loops with trivially
  varied arguments; normalization or semantic similarity widens coverage at the cost of false alarms
  on legitimate variation.
- Informing the model before blocking preserves autonomy and often recovers the run, but spends an
  extra model call per warning; if warnings rarely change a given model's behaviour, move the block
  rung earlier.
- Result-hash no-progress detection is the most reliable signal, yet requires hashing possibly large
  outputs and fails on outputs that embed timestamps or ids; strip volatile fields first.
- Tight thresholds catch loops early but break legitimate polling and edit-test cycles; per-tool
  thresholds and result-aware rules restore those at the price of more configuration.

## Pitfalls
- Keying only on the tool name, so every file read looks like a repeat; the symptom is aborts on
  perfectly healthy runs that read several files.
- Blocking polling tools; the symptom is long-running jobs that never complete because the status
  check is refused after the third call.
- Detecting the loop but only logging it; the symptom is traces showing "loop detected" at step six
  and budget exhaustion at step thirty.
- Warning the model vaguely ("you seem stuck"); the symptom is the model acknowledging and repeating.
  Include the exact call, the count and the fact that the result was identical.
- Hashing arguments without canonicalization; the symptom is obvious repeats with reordered JSON keys
  that are never counted.
- Escalating on raw call counts instead of unchanged results; the symptom is healthy edit-test
  cycles aborted as "stuck" on the sixth test run while every run was making progress.
- Running the detector per agent in a multi-agent system; the symptom is a task bouncing between two
  agents for the whole budget while each agent's guard reports nothing.

## When to use
Any agent that calls tools more than a few times per run, and especially anything that runs
unattended. Start with exact-repeat and (action, result) no-progress detection in the harness, which
is an afternoon of work, plus the inform/block/abort ladder. Add cycle detection when traces show
oscillation, and semantic detection only when exact matching demonstrably misses loops you care
about. Measure loop detections per hundred runs by tool, the recovery rate after the inform rung, and
the share of budget-exhausted runs that contained an undetected repeat pattern (mine the traces
offline). In interviews, describe the signals, the legitimate-repeat problem, and why informing the
model comes before aborting.

## Role lens
- **Engineer:** Implements the guard in the tool-dispatch path, canonicalizes arguments, tunes
  thresholds per tool and writes warning messages that name the exact repeated call.
- **Architect:** Decides where loop evidence is recorded and how `stuck` outcomes feed retries, human
  escalation and the agent-quality metrics shared across teams.
- **FDE:** Mines customer traces for repeat patterns to show which tools return unhelpful errors,
  turning loop detections into concrete tool and prompt fixes.

## Check yourself
**Q:** Your agent polls a deployment status tool every step until the deploy finishes, and separately
it sometimes re-runs a failing test command endlessly. How do you detect the second without breaking
the first?
**A:** Key detection on the tool result as well as the call. Polling returns a changing status
(pending, building, done), so the (call, result) pair rarely repeats, while the failing test returns
byte-identical output each time, which is exactly the no-progress signal. Mark the status tool as
pollable with a higher repeat ceiling or a time budget, strip volatile fields such as timestamps
before hashing results, and apply the ladder: after three identical (call, result) pairs inform the
model with the exact command and output, refuse the fourth, and abort with status stuck if it
persists. Log both cases so the thresholds can be tuned from real traces rather than guesses.

## Quiz
### Q: Why is the pair (action, result) a stronger loop signal than the action alone?
- [ ] Results are smaller to hash than arguments
- [x] An identical action producing an identical result means the world did not change, which separates a stuck loop from legitimate repeats such as polling
- [ ] Models never repeat an action whose result changed
- [ ] It makes semantic similarity unnecessary in every case
> Repeating a call whose result changes can be productive; repeating one whose result is identical
> gains no information. Using the result distinguishes polling and edit-test cycles from genuine loops.

### Q: What is the first rung of the escalation ladder when a repeat is detected?
- [ ] Abort the run immediately with status stuck
- [x] Inject a message naming the exact repeated call, its count and the unchanged result, so the model can change approach
- [ ] Silently drop the tool call and continue
- [ ] Switch to a larger model
> The model cannot see its own repetition from inside the context; making it explicit is cheap and
> often sufficient. Blocking and aborting come later if the warning does not change behaviour.

### Q: Which design choice most reduces false negatives caused by models reformatting their arguments between attempts?
- [ ] Hash only the tool name
- [ ] Compare the model's reasoning text instead of the arguments
- [x] Canonicalize arguments (sorted keys, normalized whitespace) before hashing
- [ ] Increase the repeat threshold
> Models vary key order and whitespace between calls; without canonicalization identical intents hash
> differently and are never counted. Hashing only the name would flag unrelated calls instead.
