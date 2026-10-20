---
id: nfrs
module: system-design
name: NFRs (non-functional requirements)
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - system-design/trade-off-analysis
  - system-design/adrs
  - reliability/slas-slos
  - infra-capacity-economics/latency-budgets
  - infra-capacity-economics/capacity-planning
  - observability/latency-percentiles
  - scoping/success-metrics-kpis
  - scoping/requirements-traceability
sources:
  - ISO/IEC 25010 — Systems and software quality models
  - Bass, Clements, Kazman, 2012 — Software Architecture in Practice (3rd ed.)
---

## TL;DR
Non-functional requirements (quality attribute requirements) state how well a system must do what it
does: latency, availability, cost, security, privacy, maintainability and, for AI systems, answer
quality, safety and freshness. They exist because architecture is shaped by these constraints rather
than by the feature list, and because adjectives like "fast" and "accurate" cannot be tested. A usable
NFR is a measurable scenario, a stimulus under a stated condition with a numeric response measure,
traced to a test, an SLO or an evaluation gate.

## Why it exists
Two systems can implement the same feature list and be completely different architectures. One serves
ten internal users from a single process; the other serves ten thousand tenants across regions with a
99.9% availability target. Nothing in the functional requirements distinguishes them. What does is the
set of quality attributes they must satisfy, which is why experienced architects say the NFRs *are* the
architecture.

The naive way to state them is with adjectives: "the assistant must be fast, accurate, secure and
scalable." Nobody can design to that, test it, or tell when it has been violated. Fast for whom, at what
load, at which percentile? Accurate on which inputs, measured how, against what threshold? The cost of
leaving these vague is paid late and in production: a pilot that works for five users streams its first
token in eight seconds at five hundred; a customer's security review surfaces a residency requirement the
chosen model provider cannot meet; the cost per conversation turns out to exceed the price per seat.

AI systems add a category classic engineering never had to specify. Answer quality is probabilistic and
can only be stated statistically, over a defined evaluation set, with a threshold and a tolerance. Safety
properties (harmful output rate, prompt-injection resistance) are the same. And the three that matter
most, quality, latency and cost, pull against each other, so an NFR list without priorities cannot
resolve the first real design conflict. Writing NFRs properly is how the trade-offs become explicit
before the code does.

## How it works
### From adjective to scenario
The quality attribute scenario format from Bass, Clements and Kazman turns an adjective into something
testable. It has six parts: a **source** of the stimulus, the **stimulus** itself, the **environment** in
which it arrives, the **artifact** it hits, the expected **response**, and the **response measure**. For
example: a logged-in support agent (source) submits a chat message (stimulus) during normal operation
with 50 concurrent sessions (environment) to the chat API (artifact); the first token is streamed back
(response) within 1.5 seconds at the 95th percentile over any five-minute window (measure). Each part
exists to close a loophole: the environment stops "it was fast in the demo", the artifact stops arguments
about where latency is counted, and the measure's percentile and window stop averages hiding tail pain.

### The categories, and what AI adds
ISO/IEC 25010 organises product quality into characteristics: functional suitability, performance
efficiency, compatibility, usability, reliability, security, maintainability and portability (a later
revision adds safety and renames some). The list is a checklist for discovery, not a template to fill in
full. For an AI system, add the attributes that classic software never needed to specify:

- **Answer quality.** Faithfulness or groundedness, task success rate, citation correctness, measured on
  a versioned golden set with a threshold and a minimum sample size.
- **Safety.** Rate of harmful or policy-violating outputs, refusal correctness, resistance to a
  prompt-injection test suite.
- **Freshness.** Maximum lag between a source document changing and the index reflecting it.
- **Consistency.** How much output may vary for the same input, and whether a run can be reproduced.
- **Cost.** Cost per successful task or per conversation, including retries and tool calls.
- **Data handling.** Residency, retention, no-training clauses, what may appear in logs and traces.
- **Operability.** Time to roll back a prompt or model version, and to re-index after an embedding change.

### Making each one measurable and owned
Every NFR gets five fields: the metric, the target, the measurement method, the condition (load, data,
time window) and the place it is enforced. That last field is the one most often missing, and it exists
because an NFR with no enforcement point is a wish. Runtime attributes (latency, availability, freshness)
become SLOs with alerts. Quality and safety attributes become evaluation gates in CI and release
pipelines. Security and residency attributes become review checklist items and automated tests. Cost
attributes become a per-tenant, per-feature dashboard with budgets. Use percentiles, never averages, for
anything latency-shaped, and state the window over which a percentile is computed.

Statistical targets need one more field: the uncertainty of the measurement. A proportion measured on
*n* cases has a standard error of about √(p(1−p)/n). At p = 0.90 and n = 300 that is ≈ 0.017, so the 95%
interval is roughly ±3.4 points, and a run scoring 0.91 cannot be told apart from one scoring 0.88. Write
the gate accordingly: either "the lower bound of the interval is at least 0.90", or "no statistically
significant regression against the current release on the same cases". The paired form needs far fewer
cases to detect a small drop, because each case is compared with itself.

### Decomposing a target into budgets
An end-to-end NFR is not actionable for one team until it is split along the request path. Latency
budgets add: a 1.5 s p95 time-to-first-token might be allotted as 50 ms for auth and tenant resolution,
100 ms for query embedding, 150 ms for vector search, 200 ms for guardrails and prompt assembly, leaving
about 1 s for the model's own first token (illustrative). Summing component p95s usually overestimates the
end-to-end p95, because components rarely hit their tails on the same request, so budgets set this way
are conservative; the end-to-end measurement is still the one that counts. Availability multiplies
instead: a chat path that depends serially on your stack at 99.95% and a provider at 99.9% delivers about
0.9995 × 0.999 ≈ 99.85%, below a 99.9% target before your own bugs. That one multiplication is why the
availability row in the example below forces a provider fallback rather than a better runbook. A 99.9%
monthly target is about 43 minutes of allowed unavailability in a 30-day month; stating the error budget
in minutes makes the cost of each dependency concrete.

### Prioritising, because they conflict
Not every NFR drives structure. The **architecturally significant requirements** are the handful whose
satisfaction forces a design choice: a 1.5-second time-to-first-token forces streaming and a model-tier
decision; EU residency forces region pinning and provider selection; a cost ceiling forces routing and
caching. ATAM's utility tree ranks scenarios by business importance and architectural difficulty so the
team argues about the top five rather than the full list. Then the conflicts must be ordered explicitly:
if quality and latency collide, which gives? That written priority is what trade-off analysis uses as a
tie-breaker, and without it every design review reopens the question.

### Traceability
Each architecturally significant NFR should be traceable forward to the decision it drove (an ADR) and
to the test, SLO or eval gate that verifies it, and backward to the stakeholder and business reason it
came from. Traceability exists because requirements change: when the cost ceiling moves, you need to
know which decisions and which tests depended on the old number.

### Where they come from
Finance and product supply cost and availability targets; security and legal supply residency,
retention and access requirements; users supply latency tolerance (and it differs: an agent drafting
replies tolerates less than a batch summariser); operations supply observability and rollback needs;
regulation supplies audit and explainability demands. A baseline measurement of the current process
(how long a human takes, how often they are wrong) is often the most defensible source for a quality
target.

## Example
A worked NFR set for a multi-tenant support assistant that drafts replies for human agents. The figures
are illustrative targets chosen for the scenario, not benchmarks. The point is the shape: each row is
measurable, has an enforcement point, and visibly drives an architectural decision; the last column is
where the trade-off analysis and ADRs begin.

| Attribute | Scenario and measure (illustrative) | Enforced by | Architectural consequence |
|---|---|---|---|
| Latency | First token within 1.5 s at p95, 50 concurrent sessions, 5-min window | SLO + load test in staging | Streaming responses; model tier chosen for TTFT; gateway timeout budget |
| Answer quality | Faithfulness ≥ 0.90 and citation correctness ≥ 0.95 on golden set v3 (≥ 300 cases) | Eval gate in CI; blocks release | RAG with mandatory citations; reranking; no release without eval run |
| Safety | Harmful-output rate ≤ 0.5% on red-team suite; ≥ 99% refusal of injection probes | Eval gate + quarterly red team | Input/output guardrails as a separate component; tool allow-lists |
| Availability | 99.9% monthly for the chat API; degraded mode allowed | SLO, error budget policy | Provider fallback via gateway; graceful degradation to retrieval-only |
| Cost | ≤ $0.04 per conversation averaged weekly, per tenant | Meter + budget alert | Model routing by difficulty; prompt caching; context trimming |
| Residency | EU tenants' prompts, documents and logs never leave EU regions | Architecture review + automated egress test | Region-pinned deployment; EU provider endpoint; log pipeline per region |
| Freshness | Knowledge-base edits searchable within 15 minutes at p95 | SLO on index lag | Incremental ingestion with change feed, not nightly rebuild |
| Operability | Prompt or model rollback in under 10 minutes without a deploy | Runbook drill | Versioned prompt registry; gateway config as the switch |

Read the table against the conflicts. The quality row wants a large model and long context; the latency
and cost rows want a small one and short prompts. The written priority for this product was quality,
then latency, then cost, which is why the consequence column resolves to routing (large model only for
hard cases) rather than a single small model. Change the priority and the architecture changes with it.

## Trade-offs
- Precise, tight NFRs make design and testing unambiguous, but each tightened target costs real money
  (more capacity, bigger models, more regions); the right move is to negotiate the number with its
  cost attached rather than accept a round figure.
- Percentile-based latency targets protect the tail, but p99 on a low-traffic endpoint is noisy; use
  p95 or longer windows until volume justifies p99.
- A long NFR list is thorough, but only the architecturally significant few deserve scenario-level
  rigour; spreading effort evenly produces a document nobody reads.
- Treating NFRs as hard contracts (SLAs) gives customers certainty, but it removes the room to trade; keep
  internal SLOs tighter than external SLAs so the trade-off space stays yours.
- Quality thresholds set from the start make release gates possible, but a threshold set before a
  baseline exists is arbitrary; measure the human baseline first, then set the bar relative to it.

## Pitfalls
- Specifying latency as an average; the symptom is a green dashboard while a fifth of users wait ten
  seconds for a first token.
- A quality NFR with no named evaluation set or sample size; the symptom is "accuracy 92%" that cannot
  be reproduced and silently changes when someone edits the test cases.
- Gating releases on a point estimate from a small eval set; the symptom is a gate that flips between
  pass and fail on reruns of the same build, and a team that learns to rerun until green.
- A latency target with no load condition; the symptom is a staging pass followed by a launch-day
  collapse at ten times the concurrency.
- Copying "99.99% availability" from a template; the symptom is a multi-region design and on-call
  rotation for an internal tool whose users go home at six.
- Forgetting cost as an NFR; the symptom is a successful pilot whose unit economics make the product
  unprofitable at scale, discovered from the invoice.
- NFRs without an owner or an enforcement point; the symptom is a requirements document that nobody has
  opened since the kickoff.

## When to use
Write NFRs at the start of any system that will have real users, and revisit them at every scope change,
because they are the inputs to every architecture decision that follows. Spend the scenario-level rigour
on the five to eight that are architecturally significant and keep the rest as a checklist. Attach an
enforcement mechanism to each: an SLO, an eval gate, a security test, a budget alert. In an engagement,
getting the customer to state and prioritise NFRs early is how you avoid discovering residency or cost
constraints in week ten. In interviews, when asked to design a system, state the NFRs you are assuming
before drawing anything, with numbers and percentiles, and name which one is driving each structural
choice; that is the difference between a feature sketch and an architecture.

## Role lens
- **Engineer:** Turns each NFR into a concrete check, a load test, an eval gate, an SLO alert, and
  knows the numeric target behind the component being built.
- **Architect:** Owns the elicitation, prioritisation and traceability of NFRs, and uses them as the
  scoring dimensions for every trade-off and the justification for every ADR.
- **FDE:** Extracts the customer's real constraints (residency, latency tolerance, budget, audit) early,
  writes them as measurable NFRs, and uses them to set acceptance criteria for the pilot.

## Check yourself
**Q:** A product manager says the new assistant must be "fast, accurate and cheap". Turn that into
NFRs, explain why they conflict, and say what you would ask the business to decide.
**A:** I would write each as a measurable scenario with an enforcement point. Fast: first token within
1.5 seconds at p95 under the expected peak concurrency, enforced as an SLO and a staging load test.
Accurate: faithfulness and task success above a threshold on a versioned golden set of at least a few
hundred representative cases, enforced as a release gate, with the threshold set relative to the measured
human baseline. Cheap: a cost per successful conversation ceiling, metered per tenant with alerts. They
conflict because quality pushes toward larger models and longer context, which raise latency and cost,
while the cost ceiling pushes toward smaller models and shorter prompts, which lower quality. I would ask
the business to rank the three explicitly, so the design can resolve the conflict, typically by routing
only hard cases to the expensive model, and to attach a cost to any tightening of the targets.

## Quiz
### Q: Which of these is a well-formed non-functional requirement?
- [ ] The system must be highly available
- [x] The chat API returns its first token within 1.5 s at p95 under 50 concurrent sessions, measured over any 5-minute window
- [ ] The model should be as accurate as possible on customer questions
- [ ] Latency must average under one second
> A testable NFR names the artifact, the condition, the percentile and the window. An average hides tail
> latency, and "highly available" or "as accurate as possible" cannot be verified or designed to.

### Q: Why should an AI quality requirement name a versioned evaluation set and a minimum sample size?
- [ ] Because model providers require it for support contracts
- [ ] To make the requirement shorter and easier to read
- [x] Because a statistical threshold is meaningless without the population it is measured on, and the gate must be reproducible as cases change
- [ ] Because regulators mandate exactly 300 test cases
> Quality is a probabilistic property; "92% accurate" means nothing without the set, its version and
> enough cases for the estimate to be stable. Versioning lets a release gate be re-run and compared over
> time.

### Q: What makes a requirement "architecturally significant"?
- [ ] It appears in the customer contract
- [ ] It concerns security rather than performance
- [x] Satisfying it forces a structural choice, such as streaming, region pinning or model routing
- [ ] It has the largest numeric target
> Architectural significance is about consequences for structure, not about the source or the domain
> of the requirement. Many contractual or security requirements are satisfied by configuration and do
> not shape the architecture.
