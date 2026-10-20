---
id: monitoring
module: observability
name: Monitoring
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - observability/latency-percentiles
  - observability/token-cost-tracking
  - observability/logging
  - reliability/slas-slos
  - llmops-platform/model-monitoring
  - llmops-platform/drift
  - deployment/health-checks
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Official docs: Prometheus
---

## TL;DR
Monitoring turns the stream of individual requests into aggregate signals (rates, errors, latency
distributions, saturation, plus LLM-specific quality and cost signals), compares them with targets,
and pages someone when they drift. A trace explains one request; monitoring says whether the system is
healthy now. The LLM-specific difficulty is that the worst failures (wrong answers, prompt
regressions, silent model changes) return HTTP 200, so you must monitor proxies and sampled quality
scores, not just errors.

## Why it exists
You cannot read traces faster than users produce them, and you cannot afford to learn about an outage
from a customer. Monitoring exists to compress millions of events into a handful of numbers that a
human can watch and a machine can alert on. For a classic web service that is largely a solved
problem: latency, traffic, errors and saturation, with alerts tied to what users experience.

LLM systems break the classic setup in two ways. First, dependencies are volatile in ways databases
are not: a provider degrades for one model and not another, rate limits bite at a tenant's traffic
peak, a model version is deprecated, a prompt change ships without a code change. Second, and more
important, the failures that matter most produce no errors. A retrieval index that stopped updating, a
prompt edit that makes the model refuse more often, a tool that now returns empty results, a context
window that quietly fills so compaction drops the system instructions: each returns 200 with
plausible text. An error-rate dashboard stays green while the product gets worse.

The naive alternatives fail accordingly. "Average latency and error rate" misses the tail and the
silent failures. "Watch the provider's status page" tells you about their incidents, not your
tenant's rate limits or your prompt regression. "We'll notice in the weekly eval" means a week of bad
answers. Monitoring for LLM systems therefore adds quality proxies (refusal rate, parse-failure rate,
tool success rate, agent steps per task), cost signals and sampled online evaluation to the golden
signals, and alerts on symptoms rather than causes.

## How it works
### Metric types and what they are for
Monitoring systems store time series built from three instruments. A **counter** only increases
(requests, tokens, errors) and is read as a rate. A **gauge** is a current value (queue depth,
in-flight requests, cache size). A **histogram** records a distribution (latency, tokens per request)
in buckets so percentiles can be computed later across instances. Latency must be a histogram
*because* an average hides the slow tail that users actually complain about; the latency-percentiles
lesson covers why p95 and p99 are the numbers to watch.

### The baseline: golden signals with LLM dimensions
Latency, traffic, errors and saturation still apply, with two changes. Latency splits into time to
first token and total time, since streaming makes the first matter most for perceived speed. And each
signal gets *dimensions* that matter here: model, provider, route or feature, prompt version, and
(carefully) tenant. Dimensions are labels on the series, so each distinct combination is a new
series; a per-user label on a histogram with 20 buckets creates more than 20 series per user (one per
bucket plus the sum and count), which is how metrics systems fall over. The rule is bounded label values only: enums and small sets, never ids.

### LLM-specific signals
- **Provider health as you see it**: error rate by class (rate limited, server error, timeout,
  overloaded), retry and fallback rate, and provider latency split from your own. This is your view,
  not their status page.
- **Format and tool correctness**: structured-output parse-failure rate, tool-argument validation
  failures, tool-call success rate, empty-retrieval rate.
- **Behavioural proxies**: refusal rate, guardrail trigger rate, and the distribution of output tokens
  (a sudden drop often means truncated or refused answers; a rise often means a prompt regression),
  agent iterations per task and the rate of hitting the step budget.
- **Context pressure**: prompt tokens per request and the fraction of requests where compaction or
  truncation ran.
- **Cost**: tokens and computed cost per request, per feature and per tenant, with a budget burn rate.
- **Cache**: prompt-cache and semantic-cache hit rates, since a drop shows up as both cost and latency.

### Online quality: sampled evaluation
Because correctness does not raise, you sample a small fraction of traces (a few percent is a common
starting point, plus 100% of flagged ones) and score them continuously with an LLM-as-a-judge or
task-specific checks, then emit the score as a metric. It is a lagging, noisy signal, so it is charted
and alerted on over wide windows rather than paged on a single bad sample. Thumbs-down and escalation
rates from users are the other quality series: slower still, but ground truth.

### Synthetic probes
A canary request with a known expected answer, run every few minutes per model and region, catches
silent provider changes, deprecated models and broken retrieval before real traffic does. It is the
LLM equivalent of a health check that actually exercises the dependency rather than pinging it.

### Targets, SLOs and alerting
Pick a small number of user-facing symptoms (p95 time to first token, successful-turn rate, judge
score over a day) and set targets. Alert on burn rate against those targets, *because* alerting on
causes (CPU, a single rate-limit response) produces pages for things users never noticed, while
alerting on symptoms with fast- and slow-burn windows pages for what matters and stays quiet
otherwise. Everything else is a dashboard for diagnosis, not a page.

### Where the data comes from
Metrics should be derived from the same instrumentation as traces: a generation span's attributes
(model, tokens, latency, status) are exactly the fields the counters and histograms need.
OpenTelemetry can emit both from one SDK; alternatively a metrics client is called in the same wrapper
that creates the span. Deriving metrics by counting log lines works at small scale and becomes the
most expensive query in the company at large scale.

## Example
A wrapper around a streaming model call that records the signals above with `prometheus_client`.
Note the bounded label sets: `outcome` is mapped to a handful of values, and nothing per-user or
per-conversation becomes a label. The comment at the end sketches the symptom-based alerts you would
write on top of these series.

```python
import time
from prometheus_client import Counter, Histogram, Gauge, start_http_server

REQS = Counter("llm_requests_total", "Model calls", ["model", "route", "outcome"])
TOKENS = Counter("llm_tokens_total", "Tokens used", ["model", "route", "direction"])
LATENCY = Histogram("llm_latency_seconds", "Total call latency", ["model", "route"],
                    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 30))
TTFT = Histogram("llm_ttft_seconds", "Time to first token", ["model", "route"],
                 buckets=(0.1, 0.25, 0.5, 1, 2, 4))
PARSE_FAIL = Counter("llm_parse_failures_total", "Structured output failed", ["route"])
INFLIGHT = Gauge("llm_inflight", "Concurrent model calls", ["model"])

def monitored_call(model, route, call):
    """call() yields (chunk, usage); usage is None until the final chunk."""
    INFLIGHT.labels(model).inc(); t0 = time.perf_counter(); outcome = "ok"
    try:
        text, usage, first = [], None, None
        for chunk, u in call():
            if first is None:
                first = time.perf_counter(); TTFT.labels(model, route).observe(first - t0)
            text.append(chunk); usage = u or usage
        TOKENS.labels(model, route, "input").inc(usage["input_tokens"])
        TOKENS.labels(model, route, "output").inc(usage["output_tokens"])
        return "".join(text)
    except TimeoutError:
        outcome = "timeout"; raise
    except Exception as e:                      # map provider errors to a bounded set
        outcome = "rate_limited" if "429" in str(e) else "error"; raise
    finally:
        LATENCY.labels(model, route).observe(time.perf_counter() - t0)
        REQS.labels(model, route, outcome).inc()
        INFLIGHT.labels(model).dec()

start_http_server(9000)   # exposes /metrics for Prometheus to scrape
# Alerts (symptom-based): page when the 5-minute p95 of llm_ttft_seconds for the
# "chat" route exceeds 2 s, or when rate_limited outcomes exceed 2% of calls.
```

## Trade-offs
- More label dimensions make every chart sliceable, but series count multiplies with each one; it
  flips the moment a label value is unbounded (user id, conversation id), which belongs in traces and
  logs, not metrics.
- Symptom-based alerting pages rarely and only for user impact, but it says nothing about cause; pair
  it with cause dashboards, and page on a cause only when it is a known precursor (a cache hit rate
  collapse that always precedes a latency breach).
- Sampled online evaluation gives a continuous quality signal without scoring every request, but it is
  noisy and lagging; raise the sample rate for new features or prompt versions and lower it once the
  signal is stable.
- Synthetic canaries catch silent provider and model changes early, but they cost tokens and mislead
  if the canary prompt is too easy; keep a handful of realistic prompts per model, not one trivial one.
- Static thresholds are explainable and easy to tune; anomaly detection adapts to daily cycles but
  produces alerts nobody can explain, and it wins only once traffic patterns are too variable for
  fixed numbers.

## Pitfalls
- Charting average latency; the symptom is a green dashboard while p99 users time out, discovered via
  support tickets rather than alerts.
- Labelling metrics by user or conversation id; the symptom is the metrics store running out of memory
  or queries timing out, often weeks later once cardinality has accumulated.
- Monitoring only errors and latency on an LLM feature; the symptom is a quality regression after a
  prompt change that nobody detects until the weekly eval or a customer complaint.
- Paging on causes (any rate-limit response, any retry); the symptom is alert fatigue and the real
  page being ignored.
- Trusting the provider's status page over your own measurements; the symptom is a tenant-specific
  rate limit or regional degradation that never appears there.
- Forgetting to add the prompt or model version as a dimension; the symptom is a step change in output
  tokens or refusals that cannot be tied to the deploy that caused it.

## When to use
Monitoring is not optional for anything with users, but what you monitor should follow the failure
modes you expect. At launch, instrument the golden signals with model, route and version labels, the
parse and tool failure rates, token and cost counters, and a canary; add sampled online evaluation
once the feature is stable enough that a judge score means something. Measure your monitoring by
asking after each incident whether an alert fired before the first user report, and how long the
dashboards took to point at the cause. Day to day, this is the dashboard you look at after every
prompt or model change. In interviews, the test is whether you can name the LLM-specific signals that
catch silent failures and explain why you would alert on p95 time to first token rather than on
provider rate-limit responses.

## Role lens
- **Engineer:** Instruments the call wrapper with counters and histograms, keeps label cardinality
  bounded, and adds the parse-failure, tool-success and token metrics that generic APM does not know
  about.
- **Architect:** Defines the SLOs and alert policy, decides which signals page versus inform, and owns
  the budget for sampled online evaluation and canaries across models and regions.
- **FDE:** Builds the customer-facing dashboard that proves the system meets its targets, wires alerts
  into the customer's on-call tooling, and uses provider error breakdowns to settle whose fault an
  outage was.

## Check yourself
**Q:** After a prompt change, support tickets about "unhelpful answers" double over a week while your
error rate and latency dashboards stay flat. What monitoring would have caught it within hours, and
how would you alert on it without constant false pages?
**A:** The regression was semantic, so no error metric moved. Three signals would have caught it: a
sampled online judge score per prompt version, which would show a drop for the new version against
the old within a few hundred samples; behavioural proxies such as refusal rate, guardrail trigger rate
and the output-token distribution per prompt version, which shift immediately when a prompt changes
the model's behaviour; and user feedback rates (thumbs-down, escalations) as the slow ground truth.
The prompt-version label is what makes the comparison possible. For alerting, I would not page on a
single bad judge score; I would define a quality SLO (say, judge pass rate over a rolling day) and
alert on burn rate with two windows, a fast one for sudden collapses and a slow one for gradual
drift, and I would raise the sample rate for the first day after any prompt deploy so the signal
arrives sooner.

## Quiz
### Q: Why must latency be recorded as a histogram rather than as a running average?
- [ ] Averages cannot be computed across multiple server instances
- [x] Percentiles such as p95 and p99 need the distribution, and the slow tail is what users experience and what you alert on
- [ ] Histograms use less memory than a single average
- [ ] Monitoring systems do not support averages
> An average of a long-tailed distribution looks healthy while a meaningful fraction of users wait many
> times longer. Buckets preserve the shape so tail percentiles can be computed and aggregated later.

### Q: Which of these is the most dangerous label to put on a request latency histogram?
- [ ] model
- [ ] route
- [x] conversation_id
- [ ] provider
> Each distinct label value creates a new series, multiplied by every bucket. Identifiers are
> unbounded, so a conversation id turns a few series into millions and brings the metrics store down;
> ids belong in traces and logs.

### Q: Your provider's status page is green but one tenant's requests are failing. Which monitoring practice explains why you should still have known?
- [ ] Alerting on CPU utilisation of the orchestrator
- [x] Measuring provider error classes from your own calls, with tenant as a bounded dimension where the tenant count allows, rather than relying on the provider's view
- [ ] Logging full prompts at INFO
- [ ] Increasing the trace sampling rate
> Status pages report broad incidents. Rate limits, regional issues and tenant-specific quotas only
> show up in your own error-class metrics, which is why provider health must be measured from the
> caller's side.
