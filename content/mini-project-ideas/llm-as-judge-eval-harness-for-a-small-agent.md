---
id: llm-as-judge-eval-harness-for-a-small-agent
module: mini-project-ideas
name: LLM-as-judge eval harness for a small agent
kind: practice
difficulty: core
est_minutes: 22
status: agentic-verified
agent_relevance: core
related:
  - evals/llm-as-a-judge
  - evals/judge-calibration-and-bias
  - evals/rubrics
  - evals/eval-harness
  - agent-reliability/agent-trajectory-evaluation
  - evals/inter-annotator-agreement
  - evals/tooling
  - evals/regression-tests
sources:
  - OpenAI Evals documentation
  - Inspect AI documentation
  - promptfoo documentation
---

## TL;DR
Build a harness that runs a small agent over 40-60 cases, scores answers and trajectories with deterministic checks
plus a calibrated LLM judge, and gates changes in CI. It proves you can measure an agent, not just eyeball it.

## For agent builders
- **Skills it exercises.** Case design, deterministic and trajectory checks, rubric-based judge prompts with JSON
  verdicts, judge calibration against human labels, pairwise comparison with position swap, and regression gating.
- **Earlier lessons it uses.** LLM-as-a-judge, judge calibration and bias, rubrics, eval harnesses, agent trajectory
  evaluation and inter-annotator agreement; the evals tooling lesson covers frameworks you can swap in.
- **Why it matters for agents.** Agents change with every prompt, tool description and model swap; without a harness,
  each change is a guess and regressions ship silently.
- **What you can skip.** A hosted eval platform. A JSONL file, a script and a CI job are enough.

## Why it exists
- **Agents fail in ways exact-match cannot see.** A correct answer can be phrased many ways, and a correct answer
  reached by 9 tool calls instead of 2 is still a problem.
- **Judges are cheap but biased.** LLM judges show position, length and self-preference biases, so an uncalibrated
  judge can report a regression as an improvement.
- **Calibration makes the number trustworthy.** Comparing judge verdicts with 30-50 of your own labels gives an
  agreement rate and Cohen's kappa you can put next to every score.
- **CI gating turns evals into a habit.** A failing build when pass rate drops by more than a set margin stops
  "it felt better" merges.

## How it works
### Stack and data
- **The agent under test:** your tool-calling CLI assistant or RAG notes project, wrapped so a run returns the answer,
  tools called and tool outputs.
- **Cases:** `cases.jsonl` with `question`, `reference`, `expected_tool`, `max_steps` and a `tags` list (lookup,
  multi-step, should-refuse, adversarial).
- **Judge:** a different model from the agent where possible, temperature 0, JSON verdicts.
- **Optional frameworks:** Inspect AI, promptfoo or OpenAI Evals once the hand-rolled version works.

### Three layers of scoring
| Layer | Examples | Cost |
|---|---|---|
| Deterministic | expected tool called, steps under budget, valid JSON, no forbidden tool | free |
| Reference-based judge | answer agrees with reference facts, grounded in tool outputs | one judge call |
| Pairwise judge | is version B better than A, asked in both orders | two judge calls |

### Milestones
1. **Cases and runner.** 40-60 cases, a runner that saves each run as JSON. Acceptance: one command runs all cases.
2. **Deterministic checks.** Tool used, step budget, refusal on should-refuse cases. Acceptance: per-tag pass rates.
3. **Rubric judge.** Pass/fail per criterion with short reasoning first. Acceptance: verdicts parse 100% of the time.
4. **Calibration.** Hand-label 30-50 runs, compute agreement and kappa, refine the rubric until kappa is acceptable
   (many teams aim for roughly 0.6 or higher). Acceptance: numbers in README.
5. **Pairwise mode.** Compare two agent versions, swapping order to cancel position bias. Acceptance: win, loss, tie counts.
6. **CI gate.** Run on every pull request, fail on a drop beyond a margin. Acceptance: a deliberately broken prompt fails CI.

## Diagram
Cases flow through the agent once; cheap deterministic checks run first, the judge scores what remains, and
calibration decides how much to trust the judge.

```mermaid
flowchart TD
    C[("cases.jsonl")] --> R["Runner calls the agent"]
    R --> T["Run record: answer, tools, outputs"]
    T --> D["Deterministic checks"]
    T --> J["Rubric judge: JSON verdict"]
    H["Human labels on a sample"] --> K["Calibration: agreement and kappa"]
    J --> K
    D --> S["Scorecard by tag"]
    J --> S
    S --> G["CI gate vs baseline"]
```

## Examples
### Starter code: judge, deterministic checks and calibration
The judge model call is injected; the kappa function needs no libraries.

```python
import json
from collections import Counter
from typing import Callable

RUBRIC = """You are grading an AI agent's answer. Criteria:
1. correct: the answer agrees with the reference facts.
2. grounded: every claim is supported by the tool outputs shown.
Return JSON only: {"reasoning": "<one short paragraph>", "correct": "pass" or "fail", "grounded": "pass" or "fail"}"""

def judge(case: dict, run: dict, llm: Callable[[str], str]) -> dict:
    prompt = (f"{RUBRIC}\n\nQuestion: {case['question']}\nReference: {case['reference']}\n"
              f"Tool outputs: {json.dumps(run['tool_outputs'])[:4000]}\nAgent answer: {run['answer']}")
    verdict = json.loads(llm(prompt))
    assert verdict["correct"] in ("pass", "fail") and verdict["grounded"] in ("pass", "fail")
    return verdict

def deterministic_checks(case: dict, run: dict) -> dict:
    return {"used_expected_tool": case["expected_tool"] in run["tools_called"],
            "within_budget": len(run["tools_called"]) <= case.get("max_steps", 6)}

def cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)

human = ["pass", "pass", "fail", "pass", "fail", "pass", "fail", "pass"]
judge_labels = ["pass", "pass", "fail", "pass", "pass", "pass", "fail", "pass"]
print(f"agreement={sum(h == j for h, j in zip(human, judge_labels)) / len(human):.2f} "
      f"kappa={cohen_kappa(human, judge_labels):.2f}")   # agreement=0.88 kappa=0.71
```

### Milestone plan with time estimates
| Milestone | Time | Done when |
|---|---|---|
| 1. Cases and runner | 3-4 h | 40-60 cases run with one command, results saved |
| 2. Deterministic checks | 2 h | per-tag pass rates printed |
| 3. Rubric judge | 2-3 h | 100% of verdicts parse, reasoning stored |
| 4. Calibration | 3 h | agreement and kappa on 30-50 human labels in README |
| 5. Pairwise mode | 2 h | swapped-order comparison with win, loss, tie counts |
| 6. CI gate | 2 h | a broken prompt fails the pull-request check |

### Worked scenario: the judge that liked long answers
Illustrative numbers showing the shape of a length-bias incident; your own will differ.

| Step | Observation |
|---|---|
| Agent v2 adds "be thorough" to its prompt | answers grow about 2x longer |
| Judge pass rate | rises from 71% to 80% |
| Human labels on the same 40 runs | pass rate flat at about 70% |
| Kappa on v2 runs | drops from 0.7 to 0.4 |
| Fix | rubric says "length is not a criterion", judge sees reference facts, pairwise runs swap order |

## Production patterns
- **Deterministic first, judge second.** Check tool use, step counts, schemas and refusals in code, and spend judge calls
  only on what code cannot decide. It cuts cost and gives exact failure reasons.
- **Binary criteria with reasoning first.** Ask for pass or fail per criterion and a short reasoning field before the
  verdict; binary labels calibrate better than 1-10 scales, and reasoning makes disagreements debuggable.
- **Calibrate and re-calibrate.** Keep 30-50 human-labelled runs, report agreement and kappa with every score, and
  re-check when the judge model or rubric changes.
- **Pin and version everything.** Store case-set version, judge model ID, rubric hash and agent commit with each run, so
  a score change can be traced to a cause.
- **Gate on a margin with repeats.** Agents are non-deterministic; run each case 2-3 times or use enough cases, and fail
  CI only on drops beyond the observed run-to-run noise.

## Trade-offs
- **Judge vs human review.** A judge scales to thousands of runs for cents each, while humans catch subtle errors; use
  humans to calibrate the judge rather than replacing either.
- **Reference-based vs reference-free judging.** References make verdicts more reliable but cost labelling time;
  reference-free rubrics suit open-ended tasks with lower agreement.
- **Hand-rolled harness vs a framework.** Your script is transparent and tiny; Inspect AI, promptfoo or OpenAI Evals add
  logging, viewers and parallelism, with their own conventions to learn.
- **Strong judge vs cheap judge.** A stronger judge agrees more with humans but costs more per run; measure kappa for
  both and pick the cheapest that meets your bar.

## Pitfalls
- **Same model as agent and judge.** Self-preference inflates scores. Use a different model family where possible and
  check calibration.
- **Pairwise without swapping.** Position bias favours whichever answer comes first. Ask in both orders and count a win
  only when both agree.
- **1-10 scales without anchors.** Scores cluster at 7-8 and differences are noise. Use binary criteria or anchored levels.
- **Cases only from the happy path.** The agent looks great until real users arrive. Tag and include should-refuse,
  ambiguous and adversarial cases.
- **Judging the answer but not the path.** The agent reaches the right answer by calling a write tool it should not have
  touched. Add trajectory checks.

## When to use
- **Prerequisites.** A working small agent (the CLI or RAG project), and the LLM-as-a-judge and calibration lessons.
- **Suits.** Anyone shipping agent changes regularly, and candidates for roles where evals are a core skill.
- **Avoid when.** Every answer is checkable by exact match or a unit test; skip the judge and use code.
- **Measure.** Pass rate per tag, judge-human agreement and kappa, cost per eval run and run-to-run variance.

## Role lens
- **Engineer:** Builds the runner, deterministic checks, judge prompt and calibration script, and wires the gate into CI
  with a noise-aware margin.
- **Architect:** Defines what every agent change must pass, which judges are trusted for which criteria, and how eval
  results are versioned and reviewed.
- **FDE:** Builds a case set from a customer's real requests in week one and uses calibrated scores to agree on
  acceptance criteria before launch.

## Check yourself
**Q:** After a prompt change your judge reports pass rate rising from 70% to 82%, but users complain answers got worse.
What do you check?
**A:** First check whether the judge is still calibrated: hand-label 30-50 runs of the new version and compute agreement
and kappa against the judge. If agreement dropped, look for a bias the change exploited, such as longer answers or more
confident tone. Then check the deterministic and trajectory checks, which judges cannot inflate, and the per-tag results,
since gains on easy lookups can mask losses on should-refuse cases. Fix the rubric, re-calibrate, and re-run before trusting either number.

## Interview questions
### How do you know your LLM judge is trustworthy?
- **I calibrate it against human labels** on 30-50 runs and report agreement and Cohen's kappa next to every score.
- **I re-calibrate** whenever the judge model, rubric or agent behaviour changes, because biases can appear with new answer styles.

### What do you check deterministically versus with a judge?
- **Code checks** whatever is exact: expected tool called, step budget, schema validity, refusals, forbidden tools.
- **The judge handles** what needs reading: factual agreement with a reference, groundedness in tool outputs, helpfulness.

### How do you handle position bias in pairwise comparison?
- **Ask in both orders** and count a win only when the judge prefers the same answer both times; otherwise record a tie.
- **Report** win, loss and tie counts rather than a single preference rate, so ties caused by bias are visible.

### How do you set a CI gate for a non-deterministic agent?
- **Measure noise first** by running the same version several times, then gate on drops larger than that spread.
- **Use enough cases per tag,** repeat flaky cases, and keep the gate per tag so a regression in one category cannot hide in the average.

### How would you evaluate the agent's trajectory, not just its answer?
- **Record every tool call and argument,** then check expected tools, forbidden tools, step counts and redundant calls in code.
- **Use a judge for path quality** only where it needs judgment, such as whether a search query was sensible.

## Quiz
### Q: Why ask the judge for pass or fail per criterion instead of a 1-10 score?
- [ ] Binary scores use fewer tokens
- [x] Binary criteria give more consistent verdicts that are easier to calibrate against human labels
- [ ] Judges cannot output numbers
- [ ] 1-10 scales are not valid JSON
> Unanchored numeric scales cluster and drift, so small changes are noise. Clear binary criteria are more repeatable and
> map directly to human pass or fail labels for calibration.

### Q: What does Cohen's kappa add over raw agreement between judge and human?
- [ ] It measures latency
- [ ] It counts tokens used by the judge
- [x] It corrects for agreement expected by chance, for example when most labels are pass
- [ ] It measures the agent's accuracy directly
> If 80% of runs pass, a judge that always says pass agrees 80% of the time. Kappa subtracts chance agreement, so it
> exposes judges that only look accurate.

### Q: In a pairwise comparison, how do you reduce position bias?
- [ ] Always put the new version first
- [ ] Use a longer rubric
- [x] Run each comparison in both orders and only count consistent preferences
- [ ] Use temperature 1 for the judge
> Judges tend to favour one position. Swapping order and requiring the same preference both times cancels that bias
> and turns inconsistent verdicts into ties.

## Use cases & projects
### Use cases
- **Prompt and model change reviews.** Attach the scorecard diff to every pull request that touches prompts, tools or the model.
- **Model bake-offs.** Run the same harness across three models and pick on pass rate, cost and latency together.
- **Production sampling.** Judge a daily sample of real traces and alert when pass rate on a tag drops.
- **Framework port.** Re-implement the harness in Inspect AI or promptfoo and compare effort and features.
- **Judge distillation.** Test whether a smaller judge model keeps kappa above your bar at a fraction of the cost.

### Mini project
**Warm-up: calibrate one judge on 20 answers (1-2 hours).**
- **Goal:** find out how often an LLM judge agrees with you on a simple criterion.
- **Steps:** collect 20 agent answers to questions with known references; label each pass or fail yourself first;
  run the rubric judge with reasoning-before-verdict; compute agreement and kappa; read every disagreement.
- **Done when:** you have the two numbers, a list of disagreements with a one-line cause each, and one rubric change
  that raises agreement on a re-run.
