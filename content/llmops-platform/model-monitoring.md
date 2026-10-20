---
id: model-monitoring
module: llmops-platform
name: Model monitoring
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - llmops-platform/drift
  - llmops-platform/observability-stack
  - observability/monitoring
  - observability/token-cost-tracking
  - evals/llm-as-a-judge
  - evals/offline-vs-online-evaluation
  - customer-data-evaluation/production-feedback-classification
  - reliability/slas-slos
sources:
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
  - Breck et al., 2017 — The ML Test Score
  - Beyer et al., 2016 — Site Reliability Engineering
---

## TL;DR
A deployed model can be up, fast and returning 200s while giving steadily worse answers, because
correctness is not a status code. Model monitoring layers three kinds of signal over live traffic:
operational (latency, errors, tokens, cost), behavioural proxies computable on every request without
labels (refusals, schema failures, output length, tool-call mix), and quality signals that arrive late
and sparse (judge scores on a sample, user feedback, business outcomes). Sliced by version and tenant,
they turn silent regressions into alerts with examples attached.

## Why it exists
Service monitoring assumes failures are visible: a request errors, times out or is slow. An LLM
application fails differently. The model answers confidently with the wrong refund policy; the
retriever returns last year's price list; the agent stops calling the search tool because the vendor
quietly updated the model and it now prefers to answer from memory. All of these are HTTP 200, p95
within budget, dashboards green.

Ground truth is also late or absent. A classifier in a bank gets labels when the loan defaults, months
later; a support assistant never learns whether the customer's problem was solved unless you build the
loop. So you cannot monitor "accuracy" directly the way you monitor error rate.

The naive alternatives each cover one corner. "We have evals in CI" measures a fixed set at release
time; production is a different distribution and it changes after release, while the model itself can
change underneath a provider alias. "We watch user complaints" is sparse, biased toward the angriest
users and days late. "We log everything" produces terabytes nobody reads. Monitoring exists to make
the question "is the system behaving as it did when we validated it?" answerable continuously, cheaply
and per segment, with a path from any alert to the concrete requests behind it.

## How it works
### Layer 1: operational signals
The ordinary SRE set, tagged with AI-specific dimensions: request rate, error rate by class (provider
429s, 5xxs, timeouts, guardrail blocks), latency as time-to-first-token and total, input and output
tokens, cost per request, tool-call failures and retries. Standard plumbing, but every series must
carry model id, prompt version, bundle id and tenant as labels, *because* the first question after any
anomaly is "which version, which customer".

### Layer 2: behavioural proxies (no labels needed)
Cheap functions of the output computed on 100% of traffic: refusal rate (a small classifier or pattern
match), schema-validation failure rate for structured outputs, output length distribution, empty or
truncated responses (finish reason), repetition, language mismatch between input and output, tool-call
rate and which tools, citation presence in RAG answers, retrieval score distribution, "I don't know"
rate. None of these is quality. Each is a proxy whose *shift* is informative: if the refusal rate
doubles after a model update, something changed, and you know within minutes rather than after the
weekly feedback review. Proxies are chosen *because* they are deterministic, free, and move early.

### Layer 3: quality and outcome signals
- Online judge: an LLM scores a sample of live responses against a rubric (groundedness, completeness,
  policy adherence). Sampled *because* a judge call is another LLM inference over the full prompt and
  response, often comparable to or more than the cost of the request it scores; 1–5% is a common order
  of magnitude (illustrative), stratified so small tenants are still covered.
- Human review queues: low-judge-score or flagged traces routed to reviewers; the labels feed both the
  monitor's calibration and the golden set.
- Explicit feedback: thumbs, ratings, "report". Sparse and selection-biased, so track the rate of
  negative feedback per hundred responses rather than its raw count.
- Implicit feedback: regenerate, copy, abandon, escalate to human, task completion. Higher volume, less
  precise; correlate once with human labels to learn what each means.
- Business outcomes: resolution rate, conversion, handle time. The slowest signal and the one leaders
  believe.

Each signal has a label latency: implicit feedback in seconds, explicit in minutes, judge in minutes to
hours, business metrics in days. Monitoring design is largely about using fast proxies to decide where
to spend slow, expensive labels.

### Slicing, baselines and alerting
Aggregates hide regressions. A 2% drop in overall judge score can be a 40% drop for one tenant or one
intent. Slice every metric by version, tenant, intent class, language and input length bucket. Compare
against a baseline that controls for time: the same hour last week, or the incumbent version in a
concurrent canary. Alert on sudden shifts and on slow trends separately, and require a minimum sample
per window *because* judged metrics on thirty requests are noise. Route operational breaches to the
pager and quality shifts to a ticket with the top disagreeing examples attached.

Slicing has a statistical cost that is easy to miss: every slice is another hypothesis test. With 200
slices each checked hourly at a threshold that fires falsely 5% of the time, you should expect about ten
false alerts per run from noise alone. Two standard defences: make the threshold depend on the slice's
own standard error (a drop must exceed roughly two standard errors of the difference, so small slices
need bigger drops), and require the breach to persist across consecutive windows before alerting.

### From proxies to SLOs
The proxies that matter most can be written as service-level objectives in the SRE sense: "99% of
extraction responses pass schema validation over 28 days" or "refusal rate on the billing intent stays
below 3%". That gives each one an error budget, and you alert on how fast the budget is burning rather
than on single bad windows: a fast burn (a large fraction of the month's budget gone in an hour) pages,
a slow burn opens a ticket. This is the same machinery used for availability, applied to behaviour,
and it is what stops quality alerting from becoming either silent or noisy.

### Close the loop
Every metric should be derivable from traces so an alert can drill into the requests behind it, and
every reviewed failure should become a golden-set case. Monitoring that does not feed the eval suite
finds the same regression twice.

## Example
A windowed monitor over logged events: proxies on all requests, judge scores on the sampled subset,
compared per version against a baseline window with a minimum-sample guard.

```python
import math
from collections import defaultdict
from statistics import mean, stdev

MIN_N = 200                       # no verdicts on tiny windows
LIMITS = {"refusal_rate": 0.05, "schema_fail_rate": 0.02, "judge_drop": 0.3}

def summarise(events):            # events: dicts from the trace store, one per request
    by = defaultdict(list)
    for e in events:
        by[(e["bundle"], e["tenant"])].append(e)
    out = {}
    for key, es in by.items():
        judged = [e["judge"] for e in es if e.get("judge") is not None]   # sampled subset
        out[key] = {"n": len(es),
                    "refusal_rate": mean(e["refused"] for e in es),
                    "schema_fail_rate": mean(not e["schema_ok"] for e in es),
                    "p95_ms": sorted(e["ms"] for e in es)[math.ceil(0.95 * len(es)) - 1],  # nearest rank
                    "cost_per_req": mean(e["cost"] for e in es),
                    "judge_mean": mean(judged) if judged else None, "judge_n": len(judged),
                    "judge_sd": stdev(judged) if len(judged) > 1 else None}
    return out

def judge_dropped(c, b, z=2.0):   # drop must be large relative to its own noise
    se = math.sqrt(c["judge_sd"] ** 2 / c["judge_n"] + b["judge_sd"] ** 2 / b["judge_n"])
    return b["judge_mean"] - c["judge_mean"] > max(LIMITS["judge_drop"], z * se)

def alerts(current, baseline):
    found = []
    for key, c in current.items():
        if c["n"] < MIN_N:
            continue
        b = baseline.get(key)
        for m in ("refusal_rate", "schema_fail_rate"):
            if c[m] > LIMITS[m] and (b is None or c[m] > 2 * b[m]):
                found.append((key, m, c[m], "page"))
        if b and c["judge_n"] >= 50 and b["judge_n"] >= 50 and judge_dropped(c, b):
            found.append((key, "judge_mean", c["judge_mean"], "ticket+examples"))
    return found
# Run hourly: alerts(summarise(last_hour), summarise(same_hour_last_week)).
# With sd ≈ 0.8 and 50 judged per side, se ≈ 0.16, so only drops above ~0.32 fire: the
# floor of 50 judged requests is what bounds the smallest regression this slice can see.
```

## Trade-offs
- Judge sampling at 1% is cheap but a small tenant may get five scored requests a day; stratified
  sampling fixes coverage at the cost of a more complex sampler.
- Proxy metrics are free and fast but indirect; a refusal-rate drop can mean a better model or a
  jailbroken one, so proxies trigger investigation, not conclusions.
- Fine slicing catches per-tenant regressions but multiplies alert series and shrinks samples per
  slice; past a point the right move is anomaly detection on the slice set rather than fixed thresholds.
- Online judges give continuous quality signal but inherit judge bias and drift when the judge model
  itself is updated; pin the judge and re-calibrate it against human labels periodically.
- Capturing full prompts and outputs makes every alert debuggable but creates a sensitive data store
  with retention and access obligations; hashing or redaction trades debuggability for safety.

## Pitfalls
- Monitoring only HTTP health; the symptom is a week of green dashboards followed by a customer
  escalation about answers that have been wrong since the last model update.
- Alerting on raw judge scores without a sample floor; the symptom is 3 a.m. pages caused by twelve
  unlucky requests, followed by the alert being muted.
- Events not tagged with bundle, prompt and model version; the symptom is a regression you can see but
  cannot attribute to a release, so rollback becomes guesswork.
- Treating thumbs-down counts as representative; the symptom is optimising for the loudest segment
  while the silent majority's task completion falls.
- Dashboards without drill-down; the symptom is an alert that says "quality down 8%" and a two-hour
  hunt for a single example.
- Letting the judge model auto-update; the symptom is a step change in every quality series on the
  day the vendor shipped, with no change in your system.
- Fixed thresholds across hundreds of slices; the symptom is a daily stream of one-window alerts on
  small tenants that never reproduce, until the whole channel is muted.

## When to use
From the first production deployment: operational metrics and the cheapest proxies (schema failures,
refusals, length, finish reason) sliced by version cost almost nothing and catch the loud failures.
Add sampled judging once you have a rubric that correlates with human labels, and add feedback
capture as soon as the product surface allows it. Invest in slicing when you have more than a handful
of tenants or intents. What to measure is determined by what the system is for: a RAG assistant
needs groundedness and citation rate; an agent needs tool-failure and step-count distributions; a
structured extractor needs schema validity and field-level agreement on a sample. In interviews, the
expected answer distinguishes proxies from quality, explains label latency, and shows how an alert
leads to examples and then to a new eval case.

## Role lens
- **Engineer:** Emits version-tagged events, implements the proxy checks and the sampler, and builds the
  drill-down from an alert to the traces that caused it.
- **Architect:** Chooses the signal set per use case, the judge sampling and calibration policy, the
  slicing dimensions, and which alerts page versus ticket, within the data-retention constraints.
- **FDE:** Agrees with the customer which outcome metric counts as success, wires their feedback
  channels into the monitor, and reviews the per-tenant slice with them during hypercare.

## Check yourself
**Q:** Your support assistant's provider model alias updated overnight. Nothing crashed. Describe how a
well-designed monitor would surface the change within hours and what it would show you.
**A:** Operational series would show a cost-per-request or latency step change tagged to the same bundle
id, since the bundle did not change but the model behind the alias did. Behavioural proxies would move
first: output length distribution, refusal rate, tool-call rate or schema-failure rate shifting at the
same timestamp across all tenants, which is the signature of an upstream change rather than a user
mix change. The sampled online judge would show a score shift within a few hours once the minimum
sample accrues, with the lowest-scoring traces attached for review. Explicit feedback would lag by a
day. Where the provider returns the resolved model snapshot in response metadata, logging it would show
that value changing at the same timestamp, which is the root cause; the fix is to pin snapshots and
treat any model change, alias update included, as a release that goes through shadow or canary.

## Quiz
### Q: Why are behavioural proxies such as refusal rate and schema-failure rate monitored on every request while judge scores are sampled?
- [ ] Judge scores are only valid on requests with user feedback
- [x] Proxies are deterministic and nearly free to compute, whereas each judge call is another LLM inference, often as costly as the request it scores
- [ ] Proxies are more accurate measures of answer quality than judges
- [ ] Sampling is required by data-protection rules for judged content
> Proxies are cheap functions of the output, so full coverage is affordable; judging adds a second inference per scored request, so it is sampled and used to decide where expensive human labels go.

### Q: A monitor reports overall judge score unchanged after a release, yet one enterprise customer complains loudly. What design gap most likely explains it?
- [ ] The judge model was not run at temperature zero
- [ ] The release did not include a prompt change
- [x] Metrics were not sliced by tenant, so a large drop in one tenant was hidden by the aggregate
- [ ] Explicit feedback was not collected from that customer
> Aggregates average across tenants and intents. A regression confined to one segment can be invisible in the overall mean, which is why every series is sliced by version, tenant and intent.

### Q: Which alert design most reduces false pages from a noisy quality metric?
- [ ] Alert on the metric's raw value every five minutes
- [ ] Alert only when a human reviewer confirms the drop
- [x] Require a minimum sample per window and compare against a time-matched baseline before alerting
- [ ] Replace the judge with a keyword match
> Judged metrics vary widely on small samples and with time of day. A sample floor and a time-matched baseline separate real shifts from noise and from weekly seasonality.
