---
id: reflection-self-critique
module: agents
name: Reflection / self-critique
kind: pattern
difficulty: core
est_minutes: 14
status: reviewed
related:
  - agents/agent-loop
  - agents/planning
  - agents/multi-agent-patterns
  - agents/tool-result-interpretation
  - agent-reliability/agent-trajectory-evaluation
  - evals/hallucination-evaluation
  - prompting/chain-of-thought
sources:
  - Shinn et al., 2023 — Reflexion: Language Agents with Verbal Reinforcement Learning
  - Madaan et al., 2023 — Self-Refine: Iterative Refinement with Self-Feedback
  - Gou et al., 2023 — CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing
  - Huang et al., 2023 — Large Language Models Cannot Self-Correct Reasoning Yet
---

## TL;DR
Reflection is a generate–critique–revise loop: the agent produces an attempt, evaluates it against a
goal or rubric, and tries again using what it learned. It pays off when the critique brings *new
information*: a failing test, a tool error, a verifier's verdict, or a fresh context with a rubric.
Pure "look again and fix your mistakes" with no new signal is unreliable and can turn right answers
into wrong ones, so the design question is always where the critique's evidence comes from.

## Why it exists
A single forward pass commits to an answer token by token with no chance to step back. For a short
factual question that is fine. For code, long-form writing, multi-step tool use or anything with a
checkable goal, first attempts have a known failure profile: they satisfy the surface of the request,
skip an edge case, misread a tool result, or stop before the goal is met. Humans fix this by reviewing
their own work; the agent equivalent is a second call whose job is to judge, not to produce.

The naive alternative, "ask for the best answer and trust it", leaves the model's error rate exactly
where it was. The next naive step, "ask the model to check its answer", is where people get burned.
Without external evidence the model is re-rolling with a bias toward *changing* its answer because the
prompt implies something is wrong, and studies of intrinsic self-correction on reasoning tasks found it
often fails to help and sometimes hurts. The pattern earns its cost when the critique step sees
something the generator did not: an execution result, a retrieved fact, a rubric applied in a clean
context, or a different model. Reflexion, for example, showed large gains on coding benchmarks
precisely because the reflection was grounded in execution: the agent ran unit tests (which it wrote
itself, so the signal came from actually running code, not from rereading it) and carried the lesson
into the next attempt as text. That is the mechanism to copy: feedback that is grounded, persisted, and fed forward.

## How it works
### The loop
1. **Generate** an attempt from the task and any prior reflections.
2. **Evaluate** the attempt. The evaluator may be (a) deterministic code, such as tests, schema
   validation, a linter or a reconciliation check; (b) a tool-grounded critic that runs searches or
   executes code to verify claims (the CRITIC approach); (c) an LLM critic with a rubric in a separate
   context; or (d) the same model asked to reconsider, which is the weakest option.
3. **Reflect** by turning the evaluation into a short, specific lesson: "the function failed on an
   empty list because it indexes `[0]` without a guard". Reflexion stores these verbal lessons in
   episodic memory and prepends them to the next attempt.
4. **Revise** by generating again with the lesson in context, and stop when the evaluator passes, the
   attempt stops changing, or the iteration budget runs out.

### Three named variants, and what each feeds back
- **Self-Refine** keeps one model in one task: generate, have the same model write feedback on its own
  output, refine the output in place, repeat. No external signal enters, so its reported gains are on
  tasks where a second look at the visible output suffices (style, readability, constraint coverage),
  not on facts the model did not know.
- **Reflexion** works *across attempts*: an evaluator (tests, an environment's success signal) scores a
  whole trial, the model writes a verbal lesson, and the next trial starts fresh with the lessons
  prepended. It regenerates rather than edits, which helps when the first attempt's approach was wrong.
- **CRITIC** makes the critique itself use tools: to check a claim, the critic searches or runs code,
  then revises using what it found. The new information arrives during critique rather than from a
  pre-built test suite.

### Why a separate critic context helps
When the generator critiques its own output inside the same conversation, its context is saturated
with the reasoning that produced the answer, and attention to that reasoning makes it likely to confirm
it. A fresh call that sees only the task, the artifact and a rubric is a different computation: it has
no prior reasoning to defend. Using a different model, or the same model with a role prompt and no
history, is cheap and weakens that agreement bias; it does not remove it, since LLM judges are known to
favour outputs that resemble their own, so measure critic-versus-human agreement on your task. Make the critic output structured: `{"pass": bool,
"issues": [{"where": ..., "what": ..., "severity": ...}]}` *because* a free-text "looks good overall"
cannot drive the revise step or a stopping rule.

### Why grounding matters most
The evaluator's value is the information it adds. Tests and tool results are the gold standard
*because* they are independent of the model's beliefs. A rubric-driven LLM critic adds a different
viewpoint and catches omissions and format violations well, but it shares the generator's blind spots
on facts. Intrinsic reconsideration adds nothing new, so expect it to help only on tasks where the
first pass was sloppy rather than wrong, such as formatting, completeness against a checklist, or tone.

### Stopping and budgets
Each iteration costs a generate call plus an evaluate call, and revisions are not monotone: attempt 3
can be worse than attempt 2. Keep the best-scoring attempt, cap iterations (two or three is typical for
writing, more for code with tests), and stop early when the critic passes or the diff between attempts
is trivial. Log every attempt and critique, since this trace is what you will use to tune the rubric.

Gate the loop on the evaluator, so you pay for revision only on failures. With a cheap deterministic
check, expected model calls per task are roughly 1 + (1 − p₁)·(reflect + regenerate), where p₁ is the
one-shot pass rate: at p₁ = 0.9 the loop costs about 20% extra on average and only touches the 10% that
failed. An LLM critic, by contrast, runs on *every* task, so it adds at least one call even when the
first attempt was already right; that is the case where reflection is most often not worth it.

### Where reflection sits in an agent
Inside a step (revise a tool call after an error), at the end of a task (review the deliverable before
returning it), and across episodes (carry lessons into the next task via memory). The cross-episode
form is what turns reflection into learning without changing weights.

## Example
A test-grounded code-repair loop: generate, run `pytest`, convert failures into a reflection, revise
with the reflection in context, keep the best attempt.

```python
import pathlib, re, subprocess, sys, tempfile

def run_tests(code: str, tests: str) -> tuple[bool, int, str]:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "solution.py").write_text(code)
    (d / "test_solution.py").write_text(tests)
    try:                                                # no -x: we want to count every pass
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(d)],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, 0, "Tests timed out after 60 s (infinite loop?)"
    m = re.search(r"(\d+) passed", p.stdout)
    return p.returncode == 0, int(m.group(1)) if m else 0, p.stdout[-2000:]

def reflect(task, code, report):
    return llm(f"Task: {task}\nCode:\n{code}\nTest output:\n{report}\n"
               "In three sentences, state the specific cause of failure and what to change. "
               "Do not rewrite the code.")

def solve(task, tests, max_iters=4):
    lessons, best = [], (-1, None)
    for i in range(max_iters):
        code = llm(f"Task: {task}\nLessons from earlier attempts:\n" + "\n".join(lessons)
                   + "\nReturn only Python code for solution.py.")
        ok, passed, report = run_tests(code, tests)
        if passed > best[0]:
            best = (passed, code)                       # revisions are not monotone; keep the best
        if ok:
            return code, i + 1
        lessons.append(f"Attempt {i+1}: " + reflect(task, code, report))
    return best[1], max_iters
```

The reflection prompt is forbidden from rewriting code so the lesson stays short and specific, and the
test output, not the model's opinion, decides whether to continue. Scoring by tests *passed* (rather than
failures counted with `-x`, which stops at the first one) is what makes "keep the best" meaningful, and
it ranks an attempt that does not even import below one that fails a single test.

## Trade-offs
- Grounded reflection raises success rates on checkable tasks substantially, but each iteration adds a
  critique call and an evaluator run on top of a fresh generation; for tasks that are usually right in
  one shot, the extra cost is pure overhead.
- A separate critic model reduces agreement bias, yet it adds latency and a second prompt to maintain;
  when the generator is already strong and tasks are simple, the critic rarely changes the outcome.
- Carrying lessons across episodes compounds improvement on recurring task types, but stale lessons
  mislead on new task types unless the memory is scoped and expires.
- Rubric-based LLM critique scales to tasks without tests, at the price of sharing the generator's
  factual blind spots; pair it with tool-grounded checks for claims.

## Pitfalls
- Asking the same context "are you sure?" with no new evidence; the symptom is correct answers being
  changed to wrong ones and accuracy that drops with more iterations.
- Letting the critic rewrite the artifact; the symptom is reflections that are longer than the work,
  and a revise step that merely copies the critic's version with its own errors.
- Unbounded loops that stop only on "pass"; the symptom is runaway cost on tasks the evaluator can
  never satisfy, such as a test that is itself wrong.
- Keeping only the last attempt; the symptom is returning attempt 4 when attempt 2 passed more tests.
- Vague critic output ("could be improved"); the symptom is revisions that change style, not substance.
- Letting the generator see and optimise against the exact tests; the symptom is code that special-cases
  test inputs, passing the visible suite and failing held-out cases or production inputs.

## When to use
Use reflection when a cheap, trustworthy evaluator exists (tests, validators, reconciliations), when
the deliverable is long and has a checklist, or when the same task type recurs so lessons can
accumulate. Skip it for short factual answers and for tasks where no evaluator better than the
generator exists. Measure accuracy per iteration, the fraction of revisions that improve the score, and
cost per success. Day to day it appears as the "review before submit" step in coding agents and as
rubric critics in content pipelines. In interviews, the strong answer distinguishes grounded from
intrinsic self-correction and explains why the evidence source decides whether the loop helps.

## Role lens
- **Engineer:** Builds the evaluator, the structured critic prompt and the keep-best loop, and logs
  each attempt so the rubric can be tuned from traces.
- **Architect:** Decides which tasks justify a reflection budget, which get a separate critic model,
  and how lessons are stored and scoped across episodes.
- **FDE:** Turns the customer's acceptance criteria into the critic's rubric and shows reviewers the
  critique trail so they trust what the agent ships.

## Check yourself
**Q:** A team added "review your answer and fix any mistakes" to their summarisation agent and found
factual accuracy got slightly worse while formatting got better. Explain the result and redesign the
reflection step.
**A:** The result is what the self-correction literature predicts. The reflection had no new
information: the same context, biased toward its own reasoning and nudged to change something, altered
correct statements as often as wrong ones, so facts did not improve and sometimes regressed. Formatting
improved because checklist-style properties are visible in the output without external evidence. The
redesign gives the critic something the generator lacked. Run the critique in a fresh context with the
source document and the summary, and ask it to verify each claim against the source and return
`{claim, supported, evidence_span}`. Add a deterministic check for required sections. Revise only the
unsupported claims, keep the best-scoring draft, and cap at two iterations. Then measure claim-level
support rate before and after rather than trusting that more iterations means better.

## Quiz
### Q: Which reflection setup is most likely to improve correctness on a reasoning or coding task?
- [ ] Asking the model in the same conversation whether it is sure
- [x] Critiquing with evidence the generator did not have, such as test results or a verifier's output
- [ ] Repeating the generation prompt with a higher temperature
- [ ] Asking the model to rate its own confidence from one to ten
> Self-correction works when the critique adds information. Test outcomes and tool-grounded checks are
> independent of the model's beliefs; "are you sure" in the same context mostly induces random changes.

### Q: Why should the critic return structured issues rather than a prose opinion?
- [ ] Prose cannot be stored in memory
- [x] Structured issues can drive the revise step and a stopping rule, while "looks good overall" cannot
- [ ] Structured output is cheaper to generate
- [ ] Models are more honest in JSON
> The revise step needs to know where and what to fix, and the loop needs a pass/fail signal to stop.
> A free-text judgement gives neither, so revisions drift toward stylistic changes.

### Q: What does the Reflexion pattern add beyond a single critique-and-revise pass?
- [ ] A reinforcement-learning update to the model weights after each failure
- [ ] A larger context window for the generator
- [x] Verbal lessons derived from grounded feedback, stored and prepended to later attempts
- [ ] A vote among several parallel generations
> Reflexion keeps reflections as text in episodic memory and feeds them into the next attempt, so the
> agent improves across trials without any weight update; the feedback came from outcomes such as tests.
