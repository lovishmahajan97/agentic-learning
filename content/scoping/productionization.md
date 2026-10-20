---
id: productionization
module: scoping
name: Productionization
kind: practice
difficulty: advanced
est_minutes: 15
status: fact-checked
related:
  - scoping/pilot-to-production-plan
  - scoping/rapid-prototyping-poc
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/canary-shadow-deployments
  - observability/tracing
  - reliability/slas-slos
  - deployment/runbooks
  - llmops-platform/model-lifecycle-and-deprecation-policy
---

## TL;DR
Productionization turns a prototype that works on the happy path into a system that keeps working under
real traffic, bad inputs, provider outages, model changes, adversaries and audits. The model call is
usually a small fraction of the work; the rest is reliability, evaluation gates, observability, security,
cost control and operational ownership. Plan it as a risk-ordered list of hardening work, not a final
polish step.

## Why it exists
A prototype is built to answer a question quickly, so it makes every assumption that saves time: one user,
clean inputs, a provider that always responds, a prompt edited by hand, no adversary, unlimited budget,
the builder watching the output. Each assumption is fine for a PoC and false in production.

Consider what changes. Inputs arrive from thousands of users, so the long tail of strange inputs, which
the PoC sample barely touched, becomes a daily occurrence. Concurrency hits provider rate limits. The
provider has partial outages, returns malformed JSON, or slows down at peak. The model version behind an
alias changes, or is deprecated on a schedule you do not control, and quality shifts silently. Users paste
documents containing instructions that try to hijack the system. Someone asks why the system gave a
particular answer last Tuesday, and there is no record. The monthly inference bill arrives at several
times the estimate because a retry loop or a few heavy users consumed it.

The naive approach is to "just deploy the prototype" and fix problems as they appear. That works for
deterministic code with good tests; for an LLM system it means problems appear as quality degradation that
nobody detects, cost overruns, or a security incident, all discovered by users. The opposite approach,
waiting until everything is perfect, never ships. Productionization is the disciplined middle: enumerate
the failure sources, harden against the ones that matter most for this use case, and make the rest
observable.

## How it works
Organize the work by what can go wrong, *because* that makes gaps visible and lets you prioritize by risk.

### 1. Reliability of the model call
Set explicit timeouts, *because* a hung provider call otherwise holds a worker indefinitely. Retry only
transient errors (timeouts, rate limits, server errors) with exponential backoff and jitter, and cap total
attempts, *because* unbounded retries multiply cost and load during an outage. Validate structured output
against a schema and handle failure; a schema failure is a middle case, since outputs are sampled and one
bounded re-try (or a repair prompt) often succeeds, but count it separately so a prompt regression is not
hidden by retries. Define a fallback: a secondary model, a cached answer, or a graceful
"cannot answer, routing to a person".

### 2. Quality as a release gate
Turn the PoC eval set into a regression suite that runs on every change to prompts, models, retrieval or
tools. Version prompts and pin model versions where the provider allows, *because* an unpinned alias can
change behavior with no deployment on your side. Roll out changes through canary or shadow traffic.

### 3. Observability
Trace each request end to end: inputs, retrieved context, prompt version, model, outputs, latency, token
counts and cost, tied to a request ID. Without traces, debugging a bad answer means guessing. Monitor
latency percentiles, error rates, cost per request, and online quality signals such as user feedback or
sampled human review.

### 4. Security and data handling
Authenticate users and enforce authorization on retrieved data and tool calls with the user's own
permissions, *because* the model will happily summarize documents the user should not see. Treat retrieved
and user-supplied text as untrusted input that may contain prompt injection. Decide what gets logged, how
PII is redacted, and how long data is kept.

### 5. Cost and capacity
Set per-user and per-tenant quotas, maximum input and output tokens, and budget alerts. Estimate cost at
expected volume, *because* PoC cost per call times production volume is often the first surprise.

### 6. Operations and ownership
Define SLOs, write runbooks for known failure modes (provider outage, quality regression, cost spike),
set up on-call, and name the owning team. Plan for model deprecation, *because* the model you shipped on
has a finite lifetime.

## Example
The same model call in prototype form and productionized form. The provider call is abstracted; the
hardening around it is the point.

```python
import json, logging, random, time, uuid
from pydantic import BaseModel, ValidationError

log = logging.getLogger("triage")
PROMPT_VERSION = "triage-v7"      # versioned, reviewed, covered by the regression suite
MODEL = "provider-model-2025-xx"  # pinned snapshot, not a floating alias (placeholder name)

class Triage(BaseModel):
    category: str
    priority: int

class TransientError(Exception): ...   # map provider timeouts / 429 / 5xx onto this

def call_llm(model, prompt_version, text, timeout_s=None, with_usage=False):
    raise NotImplementedError("wrap your provider SDK here")

def prototype(ticket: str) -> dict:
    return json.loads(call_llm(MODEL, PROMPT_VERSION, ticket))   # happy path only

def production(ticket: str, user_id: str, max_attempts: int = 3) -> Triage | None:
    req_id = str(uuid.uuid4())
    ticket = ticket[:20_000]                                     # bound input size and cost
    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            raw, usage = call_llm(MODEL, PROMPT_VERSION, ticket, timeout_s=15, with_usage=True)
            result = Triage.model_validate_json(raw)             # schema check, not blind json.loads
            log.info("ok", extra={"req": req_id, "user": user_id, "prompt": PROMPT_VERSION,
                                  "attempt": attempt, "ms": int((time.monotonic() - t0) * 1000),
                                  "tokens_in": usage["in"], "tokens_out": usage["out"]})
            return result
        except TransientError:
            log.warning("transient", extra={"req": req_id, "attempt": attempt})
            if attempt < max_attempts:                            # no pointless sleep before giving up
                time.sleep(min(8, 2 ** attempt) * random.uniform(0.5, 1.0))  # backoff with jitter
        except ValidationError:                                   # re-sample once more, counted separately
            log.warning("bad_schema", extra={"req": req_id, "attempt": attempt})
    log.error("fallback_to_human", extra={"req": req_id})
    return None                                                   # caller routes to manual queue
```

Each added line answers a production question the prototype ignored: what if it hangs, rate-limits,
returns broken output, or costs too much, and how will we debug it next week?

## Trade-offs
- Retries improve success rates during transient faults, but they add latency and cost and can amplify an
  outage; this flips toward fewer retries when the call sits on a latency-critical user path.
- Pinning model versions gives reproducible behavior, but it forces planned migrations before
  deprecation dates; floating aliases avoid that work at the price of unannounced behavior change.
- Logging full prompts and outputs makes debugging and audits far easier, but it creates a store of
  sensitive data; redact or sample when the inputs contain regulated or personal data.
- A fallback to a human or a simpler path keeps service available, but it needs staffing and a queue;
  for low-stakes features, returning a clear error may be the cheaper fallback.
- Strict regression gates prevent quality backsliding, but a slow or flaky eval suite will be bypassed;
  keep the gating suite fast and run the expensive one nightly.

## Pitfalls
- Deploying the prototype as-is because it worked in the pilot; the symptom is the first provider outage
  taking the feature down with hung requests and no fallback.
- Retrying every error, including invalid requests and content refusals; the symptom is a cost spike and
  rate-limit errors during incidents, as retries pile onto an overloaded provider.
- Using an unpinned model alias with no regression suite; the symptom is a quality drop users report weeks
  before anyone on the team notices.
- Retrieving with a service account instead of the end user's permissions; the symptom is a user receiving
  content from documents they cannot open directly.
- No per-request traces or cost attribution; the symptom is a large bill or a bad answer that nobody can
  explain or reproduce.

## When to use
Start productionization when a pilot shows the system is worth running, and scope it by risk: a
customer-facing, regulated or tool-using system needs every section above; an internal summarizer with
easy fallback may need only timeouts, quotas, basic tracing and a regression set. Begin with the items
whose failure is silent (quality regression, data exposure, cost), *because* loud failures get fixed
anyway. Measure readiness with a checklist reviewed at a deployment readiness review, and track error
rate, latency percentiles, cost per request and sampled quality after launch.

## Role lens
- **Engineer:** Adds timeouts, scoped retries, schema validation, fallbacks, tracing and cost logging
  around every model call, and wires the eval set into CI as a regression gate.
- **Architect:** Decides the platform pieces shared across systems, such as gateways, prompt registry,
  tracing and quotas, so each team does not productionize the same concerns separately.
- **FDE:** Maps production requirements onto the customer's environment: their identity provider, logging
  and retention rules, network constraints and the team that will own operations.

## Check yourself
**Q:** A pilot of a document Q&A assistant was a success. The prototype calls a model alias directly,
retrieves with an admin service account, logs nothing, and has no tests. You have four weeks before
company-wide launch. What do you do first, and why that order?
**A:** Order by severity and silence of failure. First, fix authorization: retrieval must run with the
end user's permissions, because a data-exposure incident is the most damaging and hardest to undo. Second,
turn the pilot's eval examples into a regression suite and pin the model version, so quality changes
become visible. Third, add request tracing, with redaction, plus cost logging. Fourth, timeouts, scoped
retries, schema validation and a fallback. Then quotas, runbooks, on-call and an owner. Roll out by canary
so remaining gaps surface on a small population first.

## Quiz
### Q: Why should retries be limited to transient errors such as timeouts and rate limits?
- [ ] Because providers charge double for retried requests
- [x] Because retrying permanent errors cannot succeed and only adds cost, latency and load during incidents
- [ ] Because retries break schema validation
- [ ] Because transient errors are always caused by the client
> A malformed request, an auth error or an oversized input will fail the same way again, and a policy refusal usually does. Retrying it wastes tokens and,
> during an outage, piles extra load onto an already struggling provider.

### Q: What is the main risk of calling a floating model alias in production without a regression suite?
- [ ] The alias will stop resolving after a fixed number of requests
- [ ] Latency always doubles when the alias updates
- [x] The underlying model can change without a deploy on your side, shifting quality with nothing to detect it
- [ ] Aliases cannot return structured output
> Aliases may point to new snapshots over time. Without pinned versions or a regression suite, behavior
> changes silently, and the first signal is user complaints.

### Q: Which productionization item should usually come first for a system that retrieves internal documents?
- [ ] Adding a caching layer to cut latency
- [ ] Switching to a cheaper model to reduce cost
- [x] Enforcing the end user's permissions on retrieval
- [ ] Building a dashboard of daily active users
> Data exposure through retrieval is severe, often silent and hard to reverse. Latency, cost and usage
> analytics matter, but they do not leak data to the wrong people.
