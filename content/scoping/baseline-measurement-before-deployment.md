---
id: baseline-measurement-before-deployment
module: scoping
name: Baseline measurement before deployment
kind: practice
difficulty: core
est_minutes: 15
status: typed
lesson_type: decision
agent_relevance: useful
related:
  - scoping/success-metrics-kpis
  - scoping/workflow-analysis
  - scoping/roi-model
  - evals/a-b-testing
  - customer-data-evaluation/shadow-mode-validation
  - customer-data-evaluation/representative-test-case-sampling
  - adoption/adoption-and-value-realization-measurement
sources:
  - Kohavi, Tang, Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
A baseline is each success metric's measured value under the current, pre-agent process. You cannot
recover it once the agent ships, so measure variance, stratify by case type, audit human quality with the
agent's rubric, and freeze the definitions.

## For agent builders
- **Why it matters for agents.** Every claim your agent makes ("cut handle time 2 minutes", "fewer
  errors than humans") is a difference against this number. Without it, your agent is judged against an
  imagined perfect human.
- **Where it shows up in your stack.** The same rubric and eval harness you use for agent outputs is
  reused for a blind audit of human outputs; shadow mode runs your agent on live cases without acting; a
  holdout routing flag tags every case with its arm.
- **What you control.** Running the agent in shadow mode before go-live, logging proposals next to human
  actions, and making post-launch telemetry use exactly the baseline's definitions.
- **What you can skip.** Deriving the sample-size formulas. Know `n ≈ z²·p(1−p)/e²`, the two-arm version
  and difference-in-differences, and use them.

## Decision case
### Scenario
A manufacturer will pilot an agent that resolves accounts-payable invoice exceptions: it reads the
purchase order and goods receipt with `lookup_po` and `lookup_receipt`, then proposes an adjustment that
`post_adjustment` writes to the ERP. Four regional AP teams each handle about 600 exceptions a week.

Constraints (figures illustrative):
- **Deadline.** The go or no-go is in week 10 from today.
- **Seasonality.** Quarter-end close falls in week 7, with higher volume and a harder case mix.
- **Access to the agent.** The CFO will not keep any team off the agent for more than 4 weeks after the
  first team goes live.
- **Write approval.** Security approves `post_adjustment` only in week 5; until then the agent is read-only.
- **No quality history.** Nobody has ever audited human error. SMEs can give 50 audit hours in total.
- **Known spread.** Handle time has a median of 18 minutes and a standard deviation of 12; the sponsor
  wants to detect a 3-minute drop.

### Options
| Option | How it works | Handles quarter-end? | Fits the 4-week rule? | Quality baseline? |
|---|---|---|---|---|
| **A. Before/after** | Baseline weeks 1-4, launch all teams in week 5, compare | No: week 7 lands only in the "after" | Yes | Only if audited separately |
| **B. Concurrent holdout** | One team stays on the old flow for weeks 5-10, difference-in-differences | Yes | No: 6 weeks without the agent | Only if audited separately |
| **C. Shadow only** | Agent runs read-only on all live cases; drafts graded against human outputs | Yes, same cases | Yes | Yes, paired, but no behavioural effect |
| **D. Shadow, audit, then staggered rollout** | Weeks 1-4 shadow plus a blind audit of past human work; teams go live in weeks 5, 6, 7 and 8 | Yes: each week compares live with not-yet-live teams | Yes: the last team waits 3 weeks | Yes, from the audit and shadow |

### Decision
Choose **D**, because it is the only option that meets every constraint:
- **The read-only weeks are not wasted.** Shadow needs only the read tools, so weeks 1-4 produce paired,
  per-segment agent-versus-human error while `post_adjustment` waits for approval.
- **The audit fits the budget.** To pin a human error rate near 6% to within ±2 points, n ≈ 1.96² × 0.06 ×
  0.94 / 0.02² ≈ 542 audited exceptions. At 5 minutes each that is about 45 hours, inside the 50.
- **Quarter-end cancels within a week.** In week 7, three teams are live and one is not, so the shared
  close-period effect hits both groups.
- **Volume is enough per week.** Detecting 3 minutes with a standard deviation of 12 needs about 251
  exceptions per arm, and one team-week is 600.

Before trusting the staggered estimate, check that the four teams' weekly medians moved in parallel over
the last 8 weeks of history.

What would change it:
- **Early write approval.** If `post_adjustment` were approved now, shorten shadow to 2 weeks and start
  the stagger in week 3.
- **A longer holdout allowed.** If the CFO accepted 6 weeks, option B gives a cleaner causal read.
- **Low volume.** Under about 251 exceptions per team-week, pool two weeks per comparison.

### Trade-offs accepted
- **The last team gets the agent 3 weeks later.** Watch its handle time; it is the comparison group, so
  do not let its manager adopt agent-like shortcuts informally.
- **Staggered order can carry team effects.** Randomise the rollout order of the four teams rather than
  starting with the keenest or the worst-performing one, which invites regression to the mean.
- **Shadow doubles inference spend for 4 weeks.** Cap it with a step budget per run and stop at week 4.
- **A ±2-point audit is not per-segment precise.** Enlarge the audit only for the segment where the agent
  is closest to the human interval.

### Review rubric
- **Definitions frozen before launch.** The write-up shows the metric spec, query and snapshot that the
  after-reading will reuse.
- **Quality is baselined, not only speed.** It sizes a blind human audit on the agent's rubric and costs
  it in SME hours.
- **Volume is computed.** It states cases per arm for the smallest effect and checks the window delivers
  them.
- **Seasonality is designed out.** Each known peak falls inside a comparison, not only in the "after".
- **Constraints map to the design.** Approval dates and access limits are visible in the timeline.
- **Reversal conditions are concrete.** It names the approval, volume or holdout change that flips it.

## Why it exists
- **Every impact claim is a difference.** Handle time fell, errors dropped, throughput rose: a difference
  needs two numbers, and the first must be measured before the agent is deployed, because deployment
  destroys it.
- **The old process disappears.** Once the agent is live, staff work differently, the old tool is
  switched off, the team is reorganised, and history was never logged at the right granularity. A
  baseline reconstructed at review time is exactly the contested, cherry-picked number the review was
  meant to avoid.
- **Management reports are not baselines.** They use their own definitions: they often exclude escalated
  cases, mix business days with calendar days, or report a target rather than a measurement.
- **Self-report is unreliable.** People tend to misjudge how long their own work takes and how often they
  make mistakes, which is why direct measurement beats SME estimates.
- **Skipping the quality baseline is the most expensive failure.** Customers demand near-perfect agent
  accuracy while the human process, once checked, errs on a meaningful fraction of cases. Unmeasured, you
  are held to an imaginary standard and can fail a bar the incumbent never met.

## How it works
### 1. Start from the metric spec
- **Baseline exactly the agreed metrics,** with the same numerator, denominator, population and
  exclusions you will use after the agent launches.
- **Different definitions make the comparison invalid,** however careful the measurement was.

### 2. Define the unit of work and the window
- **Pick the unit you will count:** a ticket, a claim, a document, a report. It should be the same unit
  one agent run handles, so per-run traces join cleanly later.
- **Choose a window that covers the work's natural cycles:** month-end peaks, weekly patterns, seasonal
  products. A two-week baseline in a quiet month compared against a peak-season pilot shows a
  "regression" that is really just volume.

### 3. Choose measurement methods per metric
| Method | Use for | Strength / caveat |
|---|---|---|
| **System timestamps** (ticketing, case-management or workflow tool) | cycle time, throughput | cheap and complete, but check what each timestamp actually records |
| **Time-and-motion or work sampling** | effort the system does not log, such as searching other tools (the lookups your agent's tools will automate) | observational and small-sample, but the only way to see hidden work |
| **Retrospective quality audit** | human error rate | SMEs blind re-review a random sample of past human outputs against the same rubric the agent will face; the step teams skip, and the one that sets a fair bar |
| **Shadow labelling** | paired same-case comparison | humans work normally while the agent's proposed output is logged but not used |

### 4. Capture distribution, not just averages
- **Record median, p90 and standard deviation,** and keep the raw per-case data.
- **Variance sets the sample size:** it determines how many post-launch cases you need to detect a change.
- **Handle times are usually right-skewed,** so medians and percentiles are more honest than means, and a
  log transform helps when testing.

### 5. Stratify by case type
- **Report each metric per segment:** category, channel, region, complexity.
- **This matters because of mix shift.** If the agent's pilot happens to receive easier cases, the overall
  number improves even when no segment does. This is a composition (mix) effect; its extreme form, where
  the aggregate trend runs opposite to every segment's trend, is Simpson's paradox.
- **Fix:** compare within segments and re-weight to the baseline mix. Agents that route "hard" cases to
  humans create exactly this mix shift.

### 6. Size the comparison
```
proportion to within ±e:            n ≈ z²·p(1−p)/e²
mean shift δ, std dev σ, two arms:  n ≈ 2(z_α/2 + z_β)²σ²/δ² per arm
```
- **If the pilot cannot reach that volume in its window,** you learn it before launch, not at the review.

### 7. Freeze it
- **Store** the queries, the data snapshot, the definitions, the date range, and the names of the people
  who signed off.
- **A baseline that nobody can re-run is just an anecdote with a decimal point.**

### Choosing the after-comparison design
| Design | What it controls | Weakness |
|---|---|---|
| Simple before/after | nothing beyond the baseline | weakest: absorbs seasonality and any concurrent change |
| Concurrent holdout (teams or queues without the agent) | time effects | withholds the agent from some users |
| Staggered rollout | both time and team effects | slower to reach everyone |
| Shadow mode | paired comparisons on identical cases | measures agent quality, not the behavioural effect on humans |

With a holdout, the estimate is a difference-in-differences:

```
effect = (pilot_after − pilot_before) − (holdout_after − holdout_before)
```

- **The second bracket subtracts whatever happened to everyone** during the pilot: a seasonal dip, a
  policy change, a new-hire cohort. That is why the holdout needs its own baseline over the same window,
  not just a post-launch reading.
- **Parallel-trends assumption:** both groups would have moved in parallel without the agent, so check
  that their pre-period trends really do look parallel before trusting the number.

## Diagram
The baseline is captured and frozen before anyone sees the agent; the after-reading reuses the same
definitions and the chosen comparison design.

```mermaid
flowchart TD
    S["Agreed metric spec"] --> W["Unit of work and window"]
    W --> M["Measure: timestamps, sampling, audits, shadow"]
    M --> X["Distribution and per-segment stats"]
    X --> N{"Pilot volume enough?"}
    N -->|"no"| W
    N -->|"yes"| F[("Frozen baseline: queries, snapshot, sign-off")]
    F --> L["Agent pilot launch"]
    L --> A["After-reading, same definitions"]
    A --> E["Effect via holdout or staggered design"]
```

## Examples
### Per-category baseline with an audit interval and pilot sizing
A support team will pilot a drafting agent that searches the knowledge base and order system before
proposing a reply.

This script computes the per-category baseline from an 8-week ticket export,
including a confidence interval on the human error rate from an SME audit sample, and the pilot volume
needed to detect a 2-minute drop in handle time.

```python
import csv, math, statistics as st
from collections import defaultdict

def wilson(k, n, z=1.96):
    """95% Wilson score interval for a proportion k/n (well-behaved for small n)."""
    p, d = k / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half

def n_per_arm(sd, delta, z_a=1.96, z_b=0.84):
    """Cases per arm to detect a mean shift of `delta` (two-sided 5%, 80% power)."""
    return math.ceil(2 * (z_a + z_b) ** 2 * sd ** 2 / delta ** 2)

by_cat = defaultdict(list)
with open("tickets_w01_w08.csv") as f:          # 8-week export, one row per closed ticket
    for r in csv.DictReader(f):
        by_cat[r["category"]].append(
            (float(r["handle_min"]), r["audited"] == "1", r["audit_error"] == "1"))

for cat, rows in sorted(by_cat.items()):
    mins = [m for m, _, _ in rows]
    errs = [e for _, audited, e in rows if audited]   # SME blind re-review sample
    lo, hi = wilson(sum(errs), len(errs)) if errs else (float("nan"),) * 2
    print(f"{cat:10} n={len(mins):5} median={st.median(mins):5.1f} "
          f"p90={st.quantiles(mins, n=10)[8]:5.1f} sd={st.stdev(mins):5.1f} "
          f"audited={len(errs):4} error_rate_95ci=[{lo:.1%}, {hi:.1%}]")

print(n_per_arm(sd=9.0, delta=2.0))   # billing: sd 9 min, detect a 2-minute drop -> 318
```

Read the output critically:
- **False precision.** Suppose billing shows 11 errors in 120 audited tickets. The point estimate is 9.2%,
  but the Wilson interval is roughly 5% to 16%. "The agent must beat 9.2%" is therefore false precision.
- **The honest bar** is "not worse than the human interval", and you may need to enlarge the audit before
  signing it.
- **Volume check.** The sample-size line says the pilot needs about 318 billing tickets in each arm. If
  billing only gets 80 tickets a week, a two-week pilot cannot answer the question.

### Worked scenario: shadow-mode agent baseline (illustrative numbers)
Before go-live, the agent runs on every billing ticket for two weeks; its tool calls are read-only and its
draft is logged next to what the human actually sent. The same rubric grades both.

| Segment | Tickets | Human error (audited) | Agent draft error | Agent asked to escalate |
|---|---|---|---|---|
| Refund status | 90 | 4% | 3% | 2% |
| Plan change | 60 | 8% | 10% | 5% |
| Disputed charge | 30 | 13% | 27% | 40% |

- **The blended rate hides where the risk is.** Weighted by volume, agent drafts err on about 9.3% of
  tickets against 6.8% for humans; the segment view shows the gap is almost all disputed charges, and the
  agent beats humans on refund status.
- **This becomes the launch scope:** autonomous drafts for refund status, assisted drafts for plan change,
  human-only for disputes until the eval improves.

### Worked scenario: difference-in-differences with a holdout (illustrative numbers)
Two comparable queues are baselined over the same 8 weeks; one gets the agent.

| Queue | Median handle time before | After | Change |
|---|---|---|---|
| Pilot | 14.0 min | 11.0 min | −3.0 min |
| Holdout | 14.5 min | 13.5 min | −1.0 min |
| **Effect** | | | (−3.0) − (−1.0) = **−2.0 min** |

- **A naive before/after would claim 3 minutes.** One of those minutes happened to everyone, perhaps a
  quieter season or a new knowledge-base article.
- **Check parallel trends first:** if the two queues' weekly medians already diverged during the 8 baseline
  weeks, the 2-minute estimate is not trustworthy.

## Production patterns
- **Baseline as a versioned job.** Keep the extraction SQL, the date range and the metric definitions in
  the repository and run them as a scheduled job against the same source the agent's post-launch
  telemetry uses. Monitor that the job still reproduces the frozen baseline value on the frozen snapshot.
- **One rubric for humans and agents.** Draw the SME audit sample with a fixed random seed per segment,
  strip any indication of who produced the output, and grade human outputs with the same rubric (or the
  same LLM judge, calibrated on SME labels) used for agent outputs. Monitor interval width per segment
  and enlarge the sample where it is too wide to set a bar.
- **Holdout routing flag.** Route a fixed set of queues or teams to the old flow and tag every case with
  its arm at creation time, so the difference-in-differences can be computed by a query. Monitor arm
  balance and any leakage of the agent into the holdout.
- **Mix re-weighting in the report.** Report both the raw pilot number and the number re-weighted to the
  baseline segment mix side by side. Monitor the gap: a large one means mix shift (often the agent's own
  routing) is doing the work.
- **Shadow logging before go-live.** Run the agent with write tools disabled, log its proposed output and
  tool plan next to the human's actual output on the same case, and show nothing to users. Monitor
  agreement rate per segment as a quality baseline.

## Trade-offs
- **Window length vs schedule.** Longer baseline windows capture seasonality and reduce noise but delay
  the project. Shorten them only when the work has no strong cycles and volume is high enough to reach
  the needed sample size.
- **Audit cost vs a fair bar.** Retrospective quality audits cost SME hours, which the customer often
  resists. They become non-negotiable when the success criterion compares agent quality to human quality,
  which is most of the time.
- **Causal clarity vs access.** Concurrent holdout groups give the cleanest causal read but withhold the
  agent from some users. Use a pure before/after comparison only when the expected effect is far larger
  than seasonal variation.
- **Safety vs realism.** Shadow mode gives paired same-case comparisons without risk, but it does not
  measure how humans change when they actually rely on the agent, so it cannot replace a live comparison.
- **Fairness vs sample size.** Heavy stratification makes comparisons fair but splits the sample thin.
  Merge segments whose baselines are statistically indistinguishable.

## Pitfalls
- **Using management-report numbers as the baseline.** Symptom: a "before" figure nobody can reproduce
  from raw data, and a dispute at review about what it included. Fix: recompute from raw case data with
  the metric spec's definitions.
- **Baselining only speed and not quality.** Symptom: the customer holds the agent to a near-perfect
  accuracy bar the human process was never measured against. Fix: a blind retrospective audit on the
  agent's rubric.
- **Ignoring case mix.** Symptom: an overall improvement that disappears when results are broken down by
  category, because the agent pilot queue received easier work. Fix: stratify the baseline and re-weight.
- **Changing the metric definition after launch,** for example by excluding a newly "out of scope" case
  type. Symptom: an improvement that appears exactly when the definition changed. Fix: freeze
  definitions, and re-baseline if one truly must change.
- **Announcing the measurement to the team being measured, then using that period as the baseline.**
  Symptom: unusually good baseline numbers (an observer, or Hawthorne, effect) that regress once
  observation ends, which makes the agent look worse than it is. Fix: prefer retrospective system data.
- **Picking the pilot team or queue because its recent numbers are the worst.** Symptom: a large
  "improvement" that the holdout, or the same team's longer history, shows would have happened anyway,
  because an unusually bad period tends to be followed by a more ordinary one (regression to the mean).
  Fix: select pilot groups on other grounds and keep a holdout.

## When to use
- **Use when:** any metric in the success spec, before any pilot user sees the agent, ideally during
  discovery while you are already mapping the workflow.
- **Plan the effort:** with good historical logs the baseline can be largely retrospective and take days.
  If effort or quality is not logged, plan two to four weeks of sampling and audits, and put that in the
  statement of work as a customer dependency.
- **Skip only for:** pure feasibility spikes with no value claim.
- **Interviews:** a baseline question usually hides inside "how would you prove the agent pilot worked?"
  The expected answer covers before/after definitions, human error rates, mix shift and sample size.

## Role lens
- **Engineer:** Writes the extraction queries and audit-sampling scripts, and makes the post-launch
  telemetry compute metrics with exactly the same definitions as the baseline.
- **Architect:** Chooses the comparison design (holdout, staggered rollout, shadow mode) and makes sure
  the platform can route and tag cases so that design is possible.
- **FDE:** Gets access to historical exports, schedules SME audit time, and gets the customer to sign
  off on the frozen baseline before the pilot starts.

## Check yourself
**Q:** The customer wants the AI to reach 99% accuracy before rollout. You have no baseline for the human
team. What do you do, and why?
**A:** I would run a blind retrospective audit. SMEs re-review a random, stratified sample of recent
human outputs against the same rubric we will use for the AI. The sample should be large enough that
the error-rate interval is narrow enough to set a bar. This changes the conversation from an
aspirational number to a fair comparison. If humans turn out to be at, say, 90% to 94%, a target of "at
least as good as humans, with no regression on high-severity case types" is defensible. The 99% figure
can stay as a stretch goal for low-risk categories. I would also record per-category human rates,
because the AI may beat humans on routine work and lose on rare, complex cases. Those rare cases are
where review should stay mandatory.

## Interview questions
### Why can't you just reconstruct the baseline from historical reports after the agent ships?
- **Deployment destroys the old process:** behaviour shifts, the old tool is switched off and history was
  rarely logged at the needed granularity.
- **Reports use other definitions,** such as excluding escalations or mixing business and calendar days,
  so the "before" number becomes contestable exactly when it matters.

### Your agent pilot shows handle time down 20%, but the customer's ops lead says it is seasonal. How do you settle it?
- **Compare against a holdout or staggered cohort** with its own baseline over the same window, and compute
  the difference-in-differences so shared seasonal movement cancels out.
- **Check parallel pre-period trends** and stratify by category to rule out mix shift, including mix
  created by the agent escalating hard cases.
- **If there was no holdout,** compare against the same weeks in previous years and be explicit about the
  weaker evidence.

### How would you use shadow mode to baseline an agent before it is allowed to act?
- **Run it with write tools disabled** on live cases, logging its draft and planned tool calls beside the
  human's actual action on the same case.
- **Grade both with one rubric,** blind to source, and report error and escalation rates per segment.
- **Use the segment results to set launch scope,** for example autonomous on low-risk segments and human
  review where the agent is worse than the human interval.

### When would you accept a simple before/after comparison instead of a holdout?
- **When the expected effect is far larger than seasonal variation,** so confounders cannot plausibly
  explain it.
- **Or when withholding the agent is unacceptable** to the customer; then lengthen the baseline window to
  cover cycles and document the limitation in the review.

### How many audited cases do you need before the human error rate can set the bar?
- **Use the proportion formula:** n ≈ z²·p(1−p)/e². With 11 errors in 120 tickets the 95% Wilson interval is
  roughly 5% to 16%, too wide to set a sharp bar.
- **Worked size:** to pin a rate near 9% to within ±3 points at 95%, n ≈ 1.96² × 0.09 × 0.91 / 0.03² ≈ 350
  audited cases, about three times the 120 above.
- **Decide the acceptable interval width first,** then size the audit per high-risk segment rather than
  overall, because each segment needs its own bar.

### What is regression to the mean and how does it bite pilot selection?
- **Definition:** an unusually bad period tends to be followed by a more ordinary one, whatever you do.
- **The trap:** picking the worst-performing team for the agent pilot guarantees an apparent improvement.
- **Fix:** choose pilot groups on other grounds, baseline over a longer history, and keep a holdout.

## Quiz
### Q: Why must the baseline be measured before deployment rather than reconstructed afterwards?
- [ ] Historical data is always deleted when new software is installed
- [x] Deployment changes the process and people's behaviour, and old logs rarely match the new metric definitions
- [ ] Statistical tests require the baseline to be collected first chronologically
- [ ] Regulators require baselines to be dated before a go-live
> Once the system is live, the old process is gone and behaviour has shifted, so it cannot be
> re-observed. Historical reports usually use different definitions, which makes a reconstructed
> baseline contestable.

### Q: A pilot shows a 20% drop in average handle time overall, but no individual ticket category improved. What is the most likely explanation?
- [ ] The confidence interval is too wide to tell
- [ ] The model improved only on the largest category
- [x] The pilot received a different mix of case types, with more easy cases, than the baseline
- [ ] Handle time should have been measured with medians instead of means
> If no segment improved but the total did, the weights changed: this is mix shift, the composition
> effect behind Simpson's paradox. Stratifying the baseline and re-weighting pilot results to the
> baseline mix exposes it.
> Medians would not fix a composition change.

### Q: Why record the standard deviation of a baseline metric, not just its mean?
- [ ] Executives prefer dashboards that show variability
- [ ] The standard deviation is needed to compute the ROI
- [ ] It replaces the need to stratify by case type
- [x] It determines how many post-launch cases are needed to detect a given improvement
> Required sample size scales with the variance divided by the square of the effect you want to detect.
> Without the spread, you cannot tell whether the pilot window has enough volume to distinguish a real
> change from noise.

## Use cases & projects
### Use cases
- **Before an agent replaces a manual lookup workflow.** Work sampling measures the minutes staff spend
  searching other systems, which is exactly what the agent's tools will claim to save.
- **Setting a fair accuracy bar.** A blind human audit on the agent's rubric turns "99% or nothing" into
  "at least as good as humans per segment".
- **Shadow-mode launch gates.** Running the agent read-only on live traffic produces per-segment error
  and escalation rates that decide which segments go autonomous first.
- **Pilot reviews under seasonal noise.** A holdout queue with its own baseline lets the team report a
  difference-in-differences instead of a disputed before/after.

### Mini project
**Baseline a workflow and shadow-test an agent against it (2-3 hours).**
- **Goal:** produce a frozen, per-segment baseline for a ticket workflow and compare a shadow-mode agent
  to it with the same rubric.
- **Steps:** take or synthesise 300 historical tickets with category, handle time and a human reply;
  compute median, p90, standard deviation and an audited error rate with a Wilson interval per category;
  run a small tool-calling agent on 60 of them with read-only tools; grade human and agent replies blind
  with one rubric; use `n_per_arm` to size a live pilot.
- **Done when:** you have a frozen baseline file (query, snapshot, definitions), a per-segment table of
  human versus agent error, and a written launch scope saying which segments the agent may handle alone.
