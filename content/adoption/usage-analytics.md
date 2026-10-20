---
id: usage-analytics
module: adoption
name: Usage analytics
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - adoption/adoption-and-value-realization-measurement
  - observability/tracing
  - observability/logging
  - evals/offline-vs-online-evaluation
  - customer-data-evaluation/production-feedback-classification
  - safety-security/pii-redaction
  - scoping/success-metrics-kpis
  - evals/a-b-testing
sources:
  - Rodden, Hutchinson, Fu, 2010 — Measuring the User Experience on a Large Scale (HEART framework)
  - Kohavi, Tang, Xu, 2020 — Trustworthy Online Controlled Experiments
---

## TL;DR
Usage analytics is instrumenting an AI feature so you can see who uses it, how often, how deeply, and
what they do with its output: accept, edit, regenerate or abandon. Traces tell you what the system did.
Analytics tells you what people did with it. You need it because a deployed assistant can be technically
healthy and still go unused, and only behavioural events against a defined eligible population show that.

## Why it exists
After go-live the usual question is "is anyone using it?", and the usual answer is a login count or a
request count on the API gateway. Both mislead. A request count goes up when one enthusiastic team uses
the tool heavily while forty others ignore it. It also goes up when users regenerate the same answer five
times because the first four were wrong. Logins count people who opened the page, not people who got
anything done. Neither has a denominator: 2,000 weekly users is a success among 2,500 eligible agents and
a failure among 40,000.

AI features add a problem ordinary product analytics does not have. The output is probabilistic, so the
interesting behaviour comes *after* the response: did the user send the draft unchanged, rewrite half of
it, copy one paragraph, or close the panel? Those downstream actions are the cheapest large-scale quality
signal you will ever get. Explicit thumbs-up/down buttons are clicked by a small, unrepresentative
fraction of users, mostly when annoyed. The naive alternative is to read raw prompt logs or the tracing
backend. That fails for two reasons: traces are keyed by request, not by user journey, and copying raw
prompts into an analytics warehouse spreads sensitive customer content to a system with broad access.

## How it works
### 1. Define the eligible population and the unit of value
Start with who *could* use the feature (licensed seats, agents in the pilot queues) and what one unit of
work is (a ticket answered, a contract reviewed, a query resolved). Every rate needs these two: adoption
is active users ÷ eligible users, not active users alone, and depth is units of work done with AI ÷ units
of work done in total.

### 2. Design an event schema around outcomes, not clicks
A small, stable vocabulary beats hundreds of ad-hoc events. A typical AI-feature schema is:
- **exposure**: the feature was shown or offered (needed for any funnel denominator);
- **invocation**: the user deliberately asked for AI help;
- **response**: the system returned output, carrying the `request_id` that joins to the trace;
- **disposition**: accepted as-is, accepted after edit (with an edit-size measure), regenerated,
  discarded, escalated to a human;
- **explicit feedback**: thumbs, ratings, free-text reasons.
Each event carries a pseudonymous user ID, account or tenant, feature name, prompt/model version and a
timestamp. The `request_id` is the design choice that matters most: it lets you go from "acceptance
dropped on Tuesday" to the exact traces, prompt version and retrieved documents behind the dropped
requests, *without* storing content in the analytics store.

Edit size deserves a precise definition, because "edited" alone lumps a fixed typo together with a full
rewrite. Compute it in the client, where the draft and the sent text are both already present, for
example as `1 − similarity(draft, sent)` with a character- or token-level diff ratio, and emit only the
number. Bucket it (0, under 10%, 10–50%, over 50%) so the dashboard shows the shape of the distribution;
the mean hides the difference between "most drafts need a light touch" and "half are fine, half are
rewritten from scratch".

### 3. Prefer implicit signals, calibrate them with explicit ones
Disposition events are emitted for every response, so they avoid the self-selection bias of explicit
ratings. They are not bias-free: "abandoned" has to be inferred from a timeout or a closed panel, and
users copy text out in ways the UI may not see. They are also noisy: a user may accept a mediocre draft
because fixing it is quicker than writing from scratch. Treat
"accepted unchanged", "edited by more than half" and "regenerated" as proxies. Periodically check them
against human-labelled samples, so you know what a 60% acceptance rate means for *this* workflow.

### 4. Build the standard views
- **Funnel**: eligible → exposed → tried once → used in 3+ distinct weeks. Where people drop out tells you
  whether the problem is awareness, first experience or ongoing value.
- **Cohort retention**: of users who first used it in week *w*, the share still using it in week *w+n*.
  Retention that flattens above zero signals a habit; retention decaying toward zero is a novelty curve.
- **Stickiness and depth**: weekly-active ÷ monthly-active, and AI-assisted share of all work items.
- **Breadth**: active users by team, region or role, because adoption is usually lumpy.
- **Quality proxies over time**: acceptance, edit size and regeneration rate, sliced by prompt version.
Frameworks such as Google's HEART (Happiness, Engagement, Adoption, Retention, Task success) are a
useful checklist that the views above cover the whole journey.

### 5. Check noise and mix before reading a change
Every rate on the dashboard is a proportion over a finite count, so it moves by chance. For a rate *p*
over *n* events, the standard error is about `sqrt(p(1−p)/n)`, and for the difference between two
periods or two prompt versions it is about `sqrt(p₁(1−p₁)/n₁ + p₂(1−p₂)/n₂)`. Treat a move smaller than
roughly two standard errors as unproven. Small pilots make this bite: at 50 drafts a week, acceptance
can swing ten points with nothing changed.

The second check is mix. An aggregate rate is a weighted average over segments, so it moves when the
weights move even if no segment changes. If a new team with naturally harder tickets joins in the same
week a prompt ships, overall acceptance falls and the prompt gets blamed. Slice the change by team or
cohort first: a real regression shows up *within* segments; a mix shift shows flat segments and a moving
total.

### 6. Keep it clean and lawful
Exclude internal, test and service accounts, or the vendor team's own testing inflates week one. Hash or
tokenize user IDs. Keep prompt and response text in the access-controlled tracing store, not in analytics.
Agree retention periods and works-council or employee-monitoring constraints with the customer *before*
instrumenting. In many organisations, per-employee productivity tracking is a legal and political issue,
so aggregate to team level by default.

## Example
Two Postgres queries over an `ai_events` table for a support-reply drafting assistant. The first computes
weekly cohort retention on a deliberate action (`draft_requested`), excluding internal users; the second
tracks what agents did with the drafts.

```sql
WITH active AS (
  SELECT DISTINCT e.user_id, date_trunc('week', e.ts)::date AS wk
  FROM ai_events e JOIN users u ON u.user_id = e.user_id
  WHERE e.feature = 'draft_reply'
    AND e.event_type = 'draft_requested'      -- an intentional action, not an impression
    AND NOT u.is_internal
),
cohort AS (SELECT user_id, min(wk) AS cohort_wk FROM active GROUP BY user_id),
size   AS (SELECT cohort_wk, count(*) AS n FROM cohort GROUP BY cohort_wk)
SELECT c.cohort_wk,
       (a.wk - c.cohort_wk) / 7                              AS weeks_since_first_use,
       count(DISTINCT a.user_id)                             AS active_users,
       round(count(DISTINCT a.user_id)::numeric / max(s.n), 3) AS retention
FROM cohort c
JOIN active a ON a.user_id = c.user_id
JOIN size s   ON s.cohort_wk = c.cohort_wk
GROUP BY c.cohort_wk, weeks_since_first_use
ORDER BY c.cohort_wk, weeks_since_first_use;

-- Disposition of drafts per week, sliced by prompt version.
SELECT date_trunc('week', ts)::date AS wk, prompt_version,
       count(*) FILTER (WHERE event_type = 'draft_shown')          AS shown,
       count(*) FILTER (WHERE event_type = 'draft_sent_unchanged') AS sent_as_is,
       count(*) FILTER (WHERE event_type = 'draft_sent_edited')    AS sent_edited,
       count(*) FILTER (WHERE event_type = 'draft_regenerated')    AS regenerated
FROM ai_events
WHERE feature = 'draft_reply'
GROUP BY 1, 2 ORDER BY 1, 2;
```

Reading them together is the point. Suppose (illustrative numbers) the week-4 retention of the first
cohort is 0.35, and `sent_as_is / shown` fell from 0.42 to 0.30 the week prompt v7 shipped, over about
500 drafts on each version. The standard error of the difference is about
`sqrt(0.42·0.58/500 + 0.30·0.70/500) ≈ 0.03`, so a 12-point drop is roughly four standard errors: not
noise. With 50 drafts per version the same error would be about 0.095, and the drop would prove nothing.
Next, slice by team: if every team's sent-as-is rate fell, it is the prompt; if only the newly onboarded
billing team is low and the others are flat, it is mix. Here every team fell, and regenerations rose,
so you have a quality regression to chase in the traces by `request_id`, not an adoption problem.

## Trade-offs
- Rich disposition events give unbiased coverage of every response, but they need front-end work in the customer's UI; when you cannot touch the UI, you fall back to sparse explicit feedback.
- Per-user granularity enables cohort and funnel analysis, but it raises employee-monitoring concerns; where works councils or regulators object, team-level aggregates are the right default.
- A small fixed event vocabulary keeps dashboards comparable across releases, at the cost of missing feature-specific nuance; add events only when a decision depends on them.
- Storing content alongside events makes ad-hoc analysis easy but copies sensitive data widely; keep content in the tracing store and join by `request_id` unless the data is non-sensitive.

## Pitfalls
- Counting requests instead of users and work items: the symptom is a rising usage chart while most eligible teams show zero activity in a breadth view.
- Reporting adoption without a denominator: leadership celebrates "1,200 users" until someone asks how many were licensed and the answer is 9,000.
- Leaving internal and test accounts in: week-one usage looks spectacular and then "collapses" when the vendor team stops testing.
- Treating thumbs-down rate as quality: it moves with UI placement and annoyance, so it disagrees with acceptance and edit-size trends and with human review.
- Blaming a prompt for a mix shift: overall acceptance drops in the week a new team onboards, yet every segment's rate is flat when sliced.
- Turning acceptance rate into a target: drafts get shorter and blander, acceptance climbs, and downstream outcomes such as reopen rate or customer satisfaction slip.
- Logging raw prompts into the product analytics tool: discovered at a security review, after customer data has spread to a system with broad access.

## When to use
Instrument usage analytics before the pilot starts, not after someone asks for numbers. You cannot
reconstruct disposition events afterwards, and the first weeks are when the funnel is most informative.
Use it whenever you need to separate "the system is wrong" from "people are not using it" from "people
use it but it doesn't help". Those three diagnoses lead to engineering, training and scoping work
respectively. A minimal dashboard that covers those three diagnoses is: weekly active ÷ eligible,
AI-assisted share of work items, week-4 cohort retention, and accepted-unchanged, edit-size and
regeneration rates sliced by prompt version and team. Do not use it as a substitute for outcome measurement: high usage proves engagement, not value
(see adoption and value-realization measurement). In interviews, expect to design the event schema for
an AI feature and say which implicit signals you would trust and why.

## Role lens
- **Engineer:** Emits disposition events with a `request_id` that joins to traces, versions every prompt
  in the event payload, and writes the cohort and funnel queries.
- **Architect:** Decides where content lives versus where behavioural events live, sets the pseudonymisation
  and retention policy, and standardises the event vocabulary across features.
- **FDE:** Negotiates what may be tracked inside the customer's environment, gets UI hooks into their
  tools, and reads the breadth view to find the teams that need help.

## Check yourself
**Q:** A customer's AI drafting tool shows weekly requests up 40% month over month, and the sponsor wants
to declare success. What would you check before agreeing, and why?
**A:** I would check whether the growth is broad or concentrated, and whether it reflects value or
retries. First, normalise by eligible users and look at breadth: are more teams active, or is one team
using it harder? Second, check requests per completed work item and the regeneration rate. A jump in
retries after a prompt or model change inflates requests while quality falls. Third, check cohort
retention, to see whether new users stick or decay after a novelty week. Fourth, check disposition:
sent-as-is and edit-size trends. Only broad, retained usage with stable or improving acceptance supports
the claim. Even then it shows engagement, and value still needs an outcome comparison.

## Quiz
### Q: Why are disposition events (accepted, edited, regenerated, discarded) usually preferred over thumbs ratings as the main quality signal?
- [ ] They are more accurate than human judgement of output quality
- [x] They are recorded for every response, so they avoid the self-selection bias of the few users who click ratings
- [ ] They do not require any changes to the user interface
- [ ] They can be computed from the model's log-probabilities alone
> Implicit dispositions cover every response. Ratings come from a small, skewed subset, often annoyed
> users. Dispositions are still noisy proxies and need calibrating against labelled samples, and they do
> require UI instrumentation.

### Q: What is the main reason to carry a `request_id` on analytics events instead of copying prompt and response text into the analytics store?
- [ ] Analytics tools cannot store long text fields
- [ ] It makes the cohort retention query run faster
- [x] It lets you drill from a behavioural anomaly to the exact trace while keeping sensitive content in the access-controlled tracing store
- [ ] It is required by the OpenTelemetry specification
> The join key gives you both views without duplicating customer content into a widely accessible
> system. Speed and storage limits are not the reason, and no telemetry standard mandates this specific design.

### Q: A cohort chart shows each weekly cohort's usage falling steadily toward zero by week six. What does this most likely indicate?
- [ ] A logging bug that drops events from older users
- [x] A novelty effect: people try the feature but do not find enough ongoing value to form a habit
- [ ] Healthy adoption, because new cohorts keep arriving each week
- [ ] That the eligible population was defined too broadly
> Retention that decays toward zero instead of flattening is the classic novelty curve. New cohorts
> arriving can hide it in a total-users chart, which is exactly why cohort views exist.
