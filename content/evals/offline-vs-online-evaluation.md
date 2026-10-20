---
id: offline-vs-online-evaluation
module: evals
name: Offline vs online evaluation
kind: decision
difficulty: core
est_minutes: 14
status: reviewed
related:
  - evals/a-b-testing
  - evals/golden-dataset
  - evals/llm-as-a-judge
  - customer-data-evaluation/shadow-mode-validation
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/evaluation-gates-for-production-releases
  - observability/monitoring
  - llmops-platform/drift
sources:
  - Kohavi, Tang and Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
Offline evaluation scores a system on a fixed dataset before it meets users; online evaluation measures
it on live traffic through user behaviour, sampled judging of real traces, or controlled experiments.
Offline is fast, cheap, reproducible and safe but only as good as its dataset and graders; online is the
truth about outcomes but slow, noisy, risky and confounded. Neither replaces the other: offline gates
what ships, online tells you whether offline measured the right thing, and sampled online traffic
refills the offline set.

## Why it exists
Offline scores and real outcomes drift apart for four structural reasons. The golden set is last
quarter's traffic, and the mix of intents shifts every week. The grader is a proxy: "the judge says the
answer is correct" is not "the customer's ticket was resolved without a human". Users adapt to the
system: they rephrase, retry, give up, or learn which questions it answers well, so the inputs change in
response to the outputs. And production has effects the harness does not model: tool latencies, an
index refreshed hours later than the eval copy, conversations that run eight turns instead of one. A
team that only evaluates offline ships a version that scores higher and performs worse, and cannot see
why.

The opposite habit, "ship it and watch the dashboard", fails just as reliably. There is no
counterfactual: did satisfaction move because of the prompt or because of the holiday, the marketing
campaign, or the provider's silent model update? Outcome signals are sparse and noisy, so detecting a
real change takes weeks of traffic. Failures reach users before anyone measures them, and when a
regression is noticed there is no way to bisect it across the ten changes shipped that fortnight.

The resolution is not to pick one but to arrange them as a ladder, each rung more expensive and more
truthful than the one below, with defined hand-offs: offline catches what can be caught cheaply, online
confirms what only users can confirm, and the two are reconciled whenever they disagree.

## How it works
### What each one measures
Offline evaluation fixes the inputs (a golden set with slices) and the graders (code checks, reference
comparisons, judges), so its output is a set of per-slice scores that are comparable across versions and
reproducible on demand. It measures capability under the dataset's distribution. Online evaluation
takes whatever inputs arrive and measures with four kinds of signal: implicit behaviour (thumbs,
regenerate, copy, abandonment, escalation to a human, a task-completion event such as an order placed
or a PR merged, return visits); explicit ratings; sampled-trace judging, where a judge or rubric scores
a stratified sample of production traces continuously; and controlled experiments, where traffic is
randomised between variants to obtain a causal estimate. Only the last one answers "did the change
cause the difference".

### The ladder
1. **Regression and unit tests** (offline, on every change): catches breakage in minutes.
2. **Golden-set evaluation** (offline, before release): capability per slice, with confidence
   intervals.
3. **Shadow mode** (online inputs, no user exposure): the candidate runs alongside production on live
   traffic; its outputs are logged, never shown, and graded against production's by judge or pairwise
   comparison. It gives the real input distribution without user risk, *because* nobody sees the
   candidate; for the same reason it cannot measure user reaction, latency under real load, or
   multi-turn effects where the user's next message depends on the candidate's reply.
4. **Canary** (a small percentage of users): guardrail metrics such as error rate, latency, cost,
   refusal rate and safety flags, with automatic rollback. It exists to catch catastrophes, not to
   measure quality; a slice of a few percent watched for hours or a day collects far too few
   conversations to detect a modest quality change.
5. **A/B test** (randomised, sized in advance for the primary metric): the causal answer about
   outcomes.
6. **Monitoring** (ongoing): drift alerts on input mix, sampled judging, outcome dashboards.

Promote only after the rung below passes, because a cheaper rung that fails stops you earlier and
tells you more precisely what broke.

### Closing the loop
Online traffic feeds offline. Sample production traces, stratified by intent and including the ones
users flagged or judges scored low, label them, and add them to the golden set, so the next offline run
measures this quarter's distribution. And validate the proxy: periodically score the same traces with
the offline judge and with the online outcome, and look at the agreement. When offline says better and
the A/B test says flat or worse, the offline metric is wrong; fix the grader, do not argue with the
users.

Make that agreement a tracked number rather than an impression. Keep a launch log with one row per
change that went through an online test: the offline delta, the online delta with its interval, and the
decision. Two readings matter. Directional agreement: of the changes where the online result was
conclusive, how often did the offline metric point the same way? And rank agreement: does a bigger
offline gain predict a bigger online gain? A metric that agrees on direction but not magnitude is
still a usable gate; one that agrees no better than a coin flip should stop gating releases until it is
repaired. Expect agreement to decay over time even with no one changing the grader, because teams
optimise against whatever the offline metric rewards (Goodhart's law), which is a second reason to
re-validate it on every A/B test you run.

### Signal quality online
Implicit signals are biased. Thumbs come from users at the extremes; "regenerate" may mean a bad answer
or a curious user; a longer session may mean engagement or struggle. Define one primary outcome per
product (resolution without escalation, task completed, code merged) and treat the rest as guardrails
or diagnostics.

### The latency of truth
Offline results arrive in minutes, shadow results in hours or days, A/B results in days or weeks. The
decision cadence has to match: prompt iteration runs on offline evals, a model swap earns a shadow run
and an A/B test, and no team should wait two weeks to learn that a prompt broke JSON output.

## Example
Illustrative numbers for a support assistant, chosen to show the failure mode rather than to report any
real system.

Version 2 of the prompt raises the offline judge's helpfulness score from 78% to 86% on a 600-item
golden set, with faithfulness unchanged at 94%. Shadow mode on 5,000 live tickets has a pairwise judge
preferring v2 on 58% of comparisons. The canary at 5% of traffic trips no guardrail, but p95 latency is
up 40% because v2's replies are about 60% longer, and escalation to a human moves from 14.0% to 15.5%,
which at a few hundred conversations is inside the noise. The A/B test, sized in advance and run for two
weeks over roughly 40,000 conversations split evenly, reports resolution-without-escalation at 60.2% for
v1 and 61.0% for v2, with a confidence interval that includes zero; satisfaction ratings are flat; cost
per conversation is up 35%.

The decision is not to ship v2 as it stands, and the more important output is the diagnosis: the
offline judge rewarded length and thoroughness, which users did not value, and the golden set contained
no cost or length criterion. The fix is to the offline measurement, adding a length penalty and a
resolution-shaped rubric, and to re-validate the judge against the A/B traces before the next
iteration. Had the team shipped on the 86%, they would have paid 35% more for nothing.

## Trade-offs
- Offline runs are reproducible and cheap enough to run on every commit, but they measure a frozen
  distribution with proxy graders; the gap grows with every week the golden set is not refreshed.
- Shadow mode gives the real input distribution with no user risk, but it cannot see user reaction or
  multi-turn effects, which matters more the more conversational the product.
- A/B tests give causal answers about outcomes, but they need traffic, time and tolerance for exposing
  users to a worse variant; detecting a 3-point change in a 60% resolution rate needs roughly 4,000
  conversations per arm (80% power, α = 0.05) and a 1-point change roughly 37,000, so low-traffic
  products rarely get a conclusive answer.
- Sampled online judging is continuous and cheap, but it inherits every judge bias and must be
  re-validated against outcomes, or it becomes a second offline metric with a live data feed.
- Canaries catch catastrophes fast but detect nothing subtle, so a team that relies on them alone will
  ship slow quality regressions invisibly.

## Pitfalls
- Shipping on an offline gain without an online confirmation; the symptom is higher scores on the
  dashboard and higher cost or escalation in the field, as in the example above.
- Reading shadow-mode preference as user preference; the symptom is a candidate that "wins" shadow and
  loses the A/B because its replies are slower or longer.
- Using implicit signals as the primary metric; the symptom is optimising thumbs-up from the vocal few
  while the silent majority escalates.
- Never feeding production traces back into the golden set; the symptom is an offline score that stays
  high while the intent mix in production has moved on.
- Treating the offline metric's validity as settled once; the symptom is a run of launches that each
  win offline by a few points while the online outcome stays flat quarter after quarter.
- Comparing this week's online metric to last week's as if it were an experiment; the symptom is a
  "regression" that was a holiday, a campaign or a provider update.

## When to use
Use offline evaluation for every iteration decision: prompt edits, retriever settings, parser changes,
regression gates. Use shadow mode when the candidate is a different model or a large prompt change and
you need the real distribution before exposure. Use a canary for every production rollout, for
guardrails only. Use an A/B test when the change is expensive or strategic enough to need a causal
answer on outcomes and the traffic exists to get one. Measure, over time, the agreement between your
offline metric and your online outcome; that number is the health of the whole evaluation stack. In
interviews, the expected depth is to describe the ladder, say what shadow mode cannot measure, and
explain what you do when offline and online disagree.

## Role lens
- **Engineer:** Builds the shadow pipeline and the trace sampler, keeps the golden set refreshed from
  production, and wires judge scores and outcome events into the same dashboard.
- **Architect:** Defines the ladder for the organisation: which changes need which rungs, the guardrail
  metrics for canaries, and the primary outcome metric each product is evaluated against.
- **FDE:** Sets up shadow mode inside the customer's environment before cutover, because the customer's
  live traffic is the only distribution that matters and it cannot leave their network for a lab eval.

## Check yourself
**Q:** Your offline judge says a new prompt is 8 points better, shadow mode agrees, and a two-week A/B
test shows no change in resolution rate with 35% higher cost. What happened, and what do you do next?
**A:** The offline metric and the shadow judge were measuring something users do not value, most
likely length or thoroughness, since both rely on the same judge and the cost rose with output size.
The A/B test is the causal signal, so the change does not ship. The actionable step is to repair the
measurement: pull the A/B traces, compare judge scores with resolution outcomes on the same
conversations, find what the judge rewards that outcomes do not, and add that to the rubric as a
penalty or a constraint. Then add cost and length as offline criteria and guardrails, refresh the
golden set from the A/B traffic, and re-run the candidate offline. If the repaired offline metric now
calls the candidate flat, the evaluation stack is consistent again and the next iteration can trust it.

## Quiz
### Q: What does shadow mode give you that a golden-set eval does not, and what can it still not measure?
- [ ] Causal estimates of outcome changes; it cannot measure latency
- [x] The live input distribution without user risk; it cannot measure user reaction or multi-turn effects
- [ ] User satisfaction ratings; it cannot measure cost
- [ ] Reproducible scores; it cannot run judges
> Shadow mode runs the candidate on real traffic but never shows its output, so inputs are realistic
> and users are safe. Because nobody sees the candidate, there is no behavioural signal and no user
> follow-up turn conditioned on its reply.

### Q: Offline scores rise after a change, but an A/B test sized in advance for that effect shows no outcome improvement. What is the right response?
- [ ] Ship the change, since offline evals are more precise than noisy online metrics
- [ ] Extend the A/B test until it confirms the offline result
- [x] Treat the offline metric as broken, diagnose what it rewards that users do not, and fix the grader
- [ ] Replace the A/B test with a larger golden set
> The experiment is the causal measurement of outcomes; a proxy that disagrees with it is measuring the
> wrong thing. Extending the test to chase a result is p-hacking, and shipping on the proxy pays for
> nothing.

### Q: Why keep offline evaluation at all once online evaluation is in place?
- [ ] Online evaluation cannot use LLM judges
- [ ] Offline evaluation is required for regulatory audits
- [x] Offline runs are fast, cheap, reproducible and safe, so they catch most breakage before any user sees it
- [ ] Online metrics cannot be computed per slice
> Online truth arrives in days or weeks and exposes users to failures. Offline checks run in minutes on
> every change, stop regressions early and point at what broke; online evaluation then confirms what only
> users can confirm.
