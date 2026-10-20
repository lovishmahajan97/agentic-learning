---
id: requirements-traceability
module: scoping
name: Requirements traceability
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - scoping/statement-of-work
  - scoping/success-metrics-kpis
  - evals/task-specific-acceptance-criteria
  - evals/golden-dataset
  - evals/regression-tests
  - customer-data-evaluation/customer-specific-regression-suites
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - security-governance-compliance/audit-trails
sources:
  - Gotel, Finkelstein, 1994 — An Analysis of the Requirements Traceability Problem
---

## TL;DR
Requirements traceability is the maintained link from each business objective to the requirements
that serve it, to the components and prompts that implement them, to the eval cases that verify them,
and to the latest evidence. You need it because "is this requirement met?" and "what does this change
break?" both need those links. With AI systems, a prompt or model change can silently affect
requirements nobody re-tested.

## Why it exists
Halfway through an AI project, the requirements live in three places: the SOW, a few Slack threads,
and the heads of two SMEs. The eval suite has 800 cases, but nobody can say which requirement each
case checks. When the customer asks "does it meet requirement 7, never discussing individual claims?",
the honest answer is "probably". When someone switches to a cheaper model, the eval average moves
from 91% to 90%, everyone shrugs, and nobody notices that every refusal case for requirement 7 now
fails. Those were eight cases, about 1% of the suite, so their collapse moved the average by a single
point.

The naive approach treats the eval suite as one undifferentiated score, plus a requirements document
that is never updated. That breaks in both directions. Going forward, from requirements to tests,
there are requirements with no test at all, especially non-functional ones like "never reveal another
employee's data". Going backward, from tests and features to requirements, there are cases and
features that trace to nothing. Those are gold-plating, stale behaviour, or scope creep nobody approved.
Aggregate scores hide both. Regulated buyers make this concrete: in safety-critical software domains,
traceability from requirement to verification evidence is an expected part of certification. AI
governance reviews increasingly ask the same question of model-based systems.

## How it works
### The chain
Give every element a stable ID and record links between adjacent levels:

1. **Objective** (from the metric tree), for example O1: reduce benefits-desk contacts.
2. **Requirement**: a testable statement with a priority and an acceptance threshold. Include
   functional requirements (answer from the current policy), behavioural ones (escalate medical-claim
   questions), and non-functional ones (p95 latency, data isolation, retention).
3. **Implementation elements**: the components, prompt templates, tools, retrieval filters and
   guardrails that realise each requirement, recorded with their version.
4. **Verification**: eval cases tagged with the requirement IDs they check, plus deterministic tests,
   load tests and security tests for the non-functional requirements.
5. **Evidence**: the run ID, the model and prompt versions, the date, and the per-requirement result.

### Why per-requirement thresholds
Each requirement gets its own acceptance threshold on its own slice of cases. The slice score, not
the global average, decides the result *because* requirements differ in criticality and in volume. A
100% requirement such as "never reveal another employee's data" can be outvoted by hundreds of
routine cases in an average. Slicing also gives each requirement a sample size, so you can tell
whether a slice has enough cases for its result to mean anything.

Sample size matters most for zero-tolerance requirements. If a slice of n cases has no failures, the
95% upper bound on the true failure rate is about 3/n (the "rule of three"). Thirty clean cases show
only that the rate is below roughly 10%; three hundred show below roughly 1%. A 100% threshold on a
small slice is therefore evidence of "not frequent", not of "never". A requirement that truly must
never fail should also trace to a deterministic control, such as a permission filter or a hard
validator with its own unit tests, with the eval slice as a second line of evidence.

### The two queries that matter
- **Coverage (forward):** which requirements have no verification, or too few cases to judge? Fill
  these gaps before acceptance, not after an incident.
- **Orphans (backward):** which tests, prompts or features trace to no requirement? Either a
  requirement is missing from the document, and should be added and approved, or the element is
  unneeded and should be removed.

### Change impact analysis
When something changes, the links say what to re-verify. A new model version touches every
requirement, so re-run everything and compare per-requirement results. A change to the retrieval
filter touches the requirements linked to that component. A requirement change touches its linked
cases, which may need relabelling. This is the payoff that justifies the bookkeeping: the cost of
change becomes proportional to what changed, rather than "re-test everything and hope".

### Keep it lightweight
The matrix does not need a requirements-management tool. Tags on eval cases (a `reqs` field in the
JSONL), a requirements file in the repo with IDs and thresholds, and a CI step that prints the
per-requirement report cover most AI projects. A traceability matrix that is not generated from the
artefacts themselves goes stale within weeks.

## Example
An HR benefits assistant. Eval cases carry requirement tags, and CI prints a per-requirement report
that catches untested requirements and orphan cases. A real suite would have tens to hundreds of cases
per requirement. This one is cut down to show the mechanics.

```python
from collections import defaultdict

REQS = {   # from the SOW: id -> (statement, acceptance threshold on its slice)
    "R1": ("Answer benefits questions from the current policy only", 0.95),
    "R2": ("Refuse and escalate questions about individual medical claims", 1.00),
    "R3": ("Cite the policy section for every factual answer", 0.90),
    "R4": ("Never reveal another employee's data", 1.00),
}
CASES = [  # eval cases tagged with the requirements they verify, plus latest result
    {"id": "c001", "reqs": ["R1", "R3"], "passed": True},
    {"id": "c002", "reqs": ["R1"],       "passed": True},
    {"id": "c003", "reqs": ["R2"],       "passed": False},
    {"id": "c004", "reqs": ["R1", "R3"], "passed": False},
    {"id": "c005", "reqs": ["R9"],       "passed": True},   # tag points at no requirement
    {"id": "c006", "reqs": [],           "passed": True},   # untagged case
]
MIN_CASES = 30   # below this, a slice result is too noisy to accept on

by_req, orphans = defaultdict(list), []
for c in CASES:
    known = [r for r in c["reqs"] if r in REQS]
    if not known:
        orphans.append(c["id"])
    for r in known:
        by_req[r].append(c["passed"])

for rid, (text, threshold) in REQS.items():
    res = by_req.get(rid, [])
    if not res:
        print(f"{rid} UNTESTED  {text}")
        continue
    rate = sum(res) / len(res)
    note = "  (too few cases)" if len(res) < MIN_CASES else ""
    print(f"{rid} {'PASS' if rate >= threshold else 'FAIL'}  {rate:.0%} of {len(res)} "
          f"(needs {threshold:.0%}){note}  {text}")
print("orphans:", orphans)   # -> ['c005', 'c006']
```

The output shows R4, the data-isolation requirement, as UNTESTED. That would never show up in an
aggregate pass rate, and it is exactly the requirement a security reviewer will ask about. R2 fails
outright, and its 100% threshold means a single failure blocks acceptance. Both orphans need a
decision: is c005's "R9" a typo, or a requirement nobody wrote down? And when R4 does get cases, a
30-case slice passing at 100% only bounds the leak rate below about 10%. That is why R4 should also
trace to the retrieval layer's per-employee access filter and its deterministic tests, not to the
eval slice alone.

## Trade-offs
- Fine-grained tagging gives precise impact analysis but costs time on every new case. Tag at the
  requirement level, not the sub-clause level, unless the domain is regulated.
- A dedicated requirements tool gives audit trails and approval workflows but drifts away from the
  code. Prefer IDs in repo files and eval data unless the customer's compliance process mandates a tool.
- Per-requirement thresholds are stricter than a global average and can block releases over small
  slices. Pair them with minimum case counts so noise does not decide acceptance.
- Tracing every prompt template to requirements makes refactoring slower. It earns its cost once the
  system has more than a handful of prompts or more than one team editing them.

## Pitfalls
- Reporting a single aggregate eval score as proof of acceptance. The symptom is a release that
  passes on average while a small, critical requirement fails every case.
- Non-functional requirements left out of the matrix. The symptom is a security review finding that
  data isolation or retention was never tested at all.
- A hand-maintained traceability spreadsheet. The symptom is a matrix that lists cases which no longer
  exist and misses the ones added last sprint.
- Not recording model and prompt versions with the evidence. The symptom is nobody being able to say
  which configuration actually passed acceptance once something regresses in production.
- Treating orphan cases as harmless. The symptom is behaviour customers rely on that is undocumented,
  untested against a threshold, and removed by the next refactor.

## When to use
Set up traceability as soon as there is an SOW with numbered requirements and more than a few dozen
eval cases. In practice that means at the start of the pilot build, because that is when tagging is
cheapest. It is essential when acceptance is contractual, when several teams change prompts or models,
or when the buyer is regulated and will audit. For a throwaway PoC, a short list of requirements
mapped to test names is enough. Day to day, it shows up as a per-requirement CI report and as the
first thing you check when a model upgrade is proposed. In interviews, it answers "how do you know a
model swap didn't break anything?"

## Role lens
- **Engineer:** Tags eval cases with requirement IDs, writes the per-requirement CI report, and records
  model and prompt versions with every run's evidence.
- **Architect:** Defines the ID scheme and the links between requirements, components and evals, so
  change impact analysis works across teams and releases.
- **FDE:** Keeps the customer's requirements and the matrix aligned on site, and uses the report as
  acceptance evidence with the customer's QA or compliance team.

## Check yourself
**Q:** The team wants to switch to a cheaper model. The overall eval pass rate drops from 92% to 91%.
Why is that not enough to approve the change, and what would you look at?
**A:** An aggregate hides how the drop is distributed. One point overall could be noise spread evenly,
or it could be every case of a small, critical requirement failing, such as refusals for regulated
questions or data isolation. I would run the per-requirement report from the traceability matrix.
That means checking each requirement's slice against its own threshold, flagging slices with too few
cases to judge, and diffing per-requirement results between the two models. A model change touches
every requirement, so all of them need to be re-verified. I would approve only if every critical
requirement still meets its threshold, and record the new model version with the evidence.

## Quiz
### Q: What does a backward trace (from tests and features to requirements) mainly reveal?
- [ ] Requirements that have no test coverage
- [x] Tests, prompts or features that serve no agreed requirement, such as gold-plating or undocumented scope
- [ ] The model version that produced each result
- [ ] Which requirements have the highest business value
> Forward tracing finds untested requirements. Backward tracing finds elements that trace to nothing.
> Those indicate missing requirements to approve, or unneeded work and unapproved scope to remove.

### Q: Why should each requirement be accepted on its own slice and threshold rather than the global eval score?
- [ ] Slices run faster in CI than the full suite
- [ ] Global scores are not statistically valid
- [ ] It lets the team drop hard cases from the average
- [x] A small but critical requirement can fail completely while the average barely moves
> Requirements differ in criticality and case count. A per-requirement threshold means a
> zero-tolerance behaviour such as data isolation decides acceptance on its own, rather than being
> outvoted by many routine cases.

### Q: Which change requires re-verifying every requirement, not just a subset?
- [ ] Editing the citation format prompt for one answer type
- [ ] Adding ten new eval cases to the escalation slice
- [x] Upgrading the underlying model to a new version
- [ ] Renaming a requirement ID in the requirements file
> The model affects every behaviour, so impact analysis links it to all requirements. A prompt edit
> touches only the requirements linked to that template, and adding cases or renaming IDs changes
> verification or bookkeeping, not behaviour.
