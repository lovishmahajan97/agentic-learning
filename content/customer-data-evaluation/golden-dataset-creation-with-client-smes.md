---
id: golden-dataset-creation-with-client-smes
module: customer-data-evaluation
name: Golden dataset creation with client SMEs
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - evals/golden-dataset
  - evals/inter-annotator-agreement
  - customer-data-evaluation/representative-test-case-sampling
  - customer-data-evaluation/sme-review-and-annotation-guidelines
  - customer-data-evaluation/labeling-sessions
  - customer-data-evaluation/acceptance-testing-against-customer-kpis
  - scoping/data-access-and-permissions-discovery
---

## TL;DR
A golden dataset built with client SMEs is a versioned set of the customer's real inputs with reference
answers their own experts agreed on, so "is it good enough?" is settled against the customer's judgement
rather than the vendor's. The hard parts are not the file format: getting scarce expert time, sampling
the right cases, resolving expert disagreement as policy, handling data governance, and getting a formal
sign-off that makes the set authoritative.

## Why it exists
In a customer deployment the question that decides the project is "do your outputs meet our standard?"
Only the customer's experts can define that standard, and they define it case by case. If the vendor
writes the reference answers, the eval measures agreement with the vendor's guesses, and the first time
an SME reviews production output the score is revealed as fiction. The general idea of a golden dataset
is covered in the evals module; this lesson is about the engagement mechanics of building one inside a
client.

Three things break without a deliberate process. First, SME time is the scarcest resource on the
project: senior adjusters, clinicians or lawyers are billing or seeing patients, so an ad-hoc request
for "some labels" produces a few dozen easy cases and then silence. Second, experts disagree more than
anyone expects, and an unmanaged disagreement silently becomes label noise that caps every metric.
Third, the data itself is governed: it may contain personal or regulated information that cannot leave
the customer's environment, so "export a CSV and label it in our tool" may be forbidden. The naive path,
engineers labelling from the documentation and asking SMEs to "spot check", fails on all three, and it
leaves no artefact the customer will sign as the acceptance standard.

## How it works
### 1. Anchor the dataset to a decision
Write down what the dataset will be used to decide: prompt iteration, model choice, go-live acceptance.
This sets the size, the error tolerance and who must sign off. An acceptance dataset needs the business
owner's signature; an iteration dataset only needs the working SMEs.

### 2. Clear data access first
Agree where the data lives, who may see it, and whether de-identification is needed before anyone
labels. Labelling tools often must run inside the customer's environment. Doing this first matters
*because* discovering a governance blocker after SMEs have labelled 200 exported records means throwing
the work away.

### 3. Sample from production, stratified
Draw candidate inputs from real recent traffic, stratified by the segments the business cares about,
with deliberate oversampling of rare high-stakes cases (see representative test-case sampling).
Synthetic or hand-picked items are allowed only as a labelled minority, *because* experts write
cleaner inputs than the world does.

### 4. Write guidelines, then calibrate
Draft annotation guidelines with one or two SMEs, run a calibration batch that several experts label
independently, measure agreement, and revise the guideline where they split. Only then scale up.
Calibrating first is cheap; discovering disagreement after 500 labels is not.

### 5. Label for reference and rationale
Each item gets the reference answer (or acceptable-answer criteria for open-ended outputs), a short
rationale, and a difficulty or "ambiguous" flag. The rationale is what lets an engineer later tell a
model error from a label error.

### 6. Double-label a subset and adjudicate
Have a fixed fraction labelled by two experts, compute agreement per category, and send disagreements to
an adjudicator, normally the policy owner. Persistent disagreement in a category is a policy gap; resolve
it as a written rule rather than by majority vote, *because* the model will be judged against the
rule in production.

### 7. Freeze, version and sign off
Freeze a version with a changelog, record guideline version and labeller per item, split off a holdout
that the team never inspects during iteration, and get written sign-off from the business owner that
this set, at this threshold, is the acceptance standard.

### 8. Refresh on a schedule
Production drifts. Add newly sampled cases each cycle, retire items that no longer reflect policy, and
never edit a label silently; changes go through the same review and bump the version.

## Example
A worked plan for a commercial-insurance claims triage pilot, with numbers stated as illustrative.

The customer offers three senior adjusters for two hours a week each, six expert-hours weekly. The
target is a 400-item acceptance set. Week 1: the data team confirms claims can be labelled only inside
the customer's tenant, so the labelling tool is deployed there and policyholder names are masked. A
stratified sample of 600 candidates is drawn from the last quarter: by line of business, claim size
band, and an oversample of the roughly 3% of claims with litigation flags so they get 40 of the 400
final slots rather than the 12 a proportional sample would give.

Week 2: a 40-item calibration batch is labelled by all three adjusters independently. Agreement is high
on routing to fast-track, but they split on "potential subrogation" in 11 of 40 cases. The claims
director rules that subrogation applies only when a third party is named in the first notice, and that
rule goes into the guideline and the prompt.

Weeks 3 to 8: at an assumed 4 minutes per item, 400 items take about 27 hours single-labelled; 20%
double-labelled adds about 5 hours, so roughly 32 expert-hours, which is between five and six weeks of
the offered time. The FDE shows this to the sponsor up front rather than promising three weeks.
Adjudication takes one 45-minute session per week. Week 9: version 1.0 is frozen with 80 items held out,
and the claims director signs that 90% routing accuracy overall and 85% on litigation-flagged claims is
the acceptance bar.

## Trade-offs
- More SMEs per item gives cleaner labels and measurable agreement, but multiplies scarce expert time;
  double-label a subset, and label everything twice only when errors are very costly.
- Using the customer's labelling environment satisfies governance, but you lose your tooling and
  automation; when data can be de-identified safely, an external tool may be faster and still compliant.
- A larger set narrows confidence intervals, but every extra item costs expert minutes; once intervals
  are tighter than the decision needs, spend the time on rare strata instead.
- Freezing early gives a stable target, but freezes early mistakes too; keep a short revision window
  after the first full eval run before declaring the acceptance version.

## Pitfalls
- Engineers writing reference answers and SMEs only spot-checking; the symptom is a high eval score
  followed by SMEs rejecting a large share of production outputs.
- Letting the most available SME label everything; the symptom is a second expert disagreeing with a
  third of the labels when brought in late.
- Resolving disagreement by majority vote; the symptom is the same category flip-flopping between
  dataset versions because no policy was ever written.
- Reusing golden items as prompt examples or retrieval content; the symptom is a score that collapses on
  freshly sampled production cases.
- Skipping the formal sign-off; the symptom is the acceptance bar being renegotiated after results come
  in.

## When to use
Build a client-SME golden dataset for any deployment where the customer's experts, not a public
benchmark, decide whether outputs are acceptable, which is nearly every enterprise deployment. Start in
the first two weeks of a pilot, in parallel with the first prototype, because SME scheduling and data
access are the long poles. A small iteration set from the vendor team is fine for day-one debugging, but
it must never be the acceptance standard. Measure agreement on the double-labelled subset, coverage per
stratum, and label churn between versions. In interviews, emphasise sign-off, governance and policy
resolution, not just "collect examples".

## Role lens
- **Engineer:** Builds the sampling and labelling pipeline, stores labeller, guideline version and
  rationale per item, and keeps the holdout physically separate from iteration data.
- **Architect:** Decides where labelling runs given data residency and access constraints, and how
  datasets are versioned per customer so they feed regression suites later.
- **FDE:** Negotiates SME time with the business owner, facilitates calibration, routes disagreements
  to the policy owner, and secures written sign-off on the acceptance set.

## Check yourself
**Q:** The customer offers one SME for one hour a week and wants go-live in six weeks. What do you do?
**A:** I would show the arithmetic: at a few minutes per item, one hour a week yields at most about
ninety labels by go-live, single-labelled, with no agreement estimate, which cannot support an
acceptance decision with useful confidence. Then I would offer options: add a second SME for the
calibration and double-labelled subset; have the vendor team pre-label so SMEs only review and correct,
while measuring how often they change the pre-label to detect anchoring; narrow the scope to the
highest-volume segments; or move the date. I would also ask the business owner to sign off on the
reduced confidence explicitly, because the risk is theirs to accept.

## Quiz
### Q: Two adjusters disagree on a category in a quarter of calibration cases. What should happen?
- [ ] Take the majority label once a third adjuster votes
- [x] Escalate to the policy owner, write the ruling into the guideline, and relabel the affected cases
- [ ] Drop the category from the dataset because it is ambiguous
- [ ] Keep both labels and score the model correct if it matches either
> A consistent split signals a policy gap. Voting hides it and it resurfaces in production; a written
> ruling from the owner fixes labels, guideline and prompt together.

### Q: Why should data-access and governance questions be settled before labelling starts?
- [ ] Labelling tools cannot read customer data formats
- [ ] Governance reviews always take longer than labelling
- [ ] SMEs refuse to label without a signed contract
- [x] If labelled data later turns out to be unusable outside the customer's environment, the labelling work is lost
> The expensive asset is expert time. A late governance blocker can invalidate labels done in the wrong
> place, so where data lives and who may see it must be agreed first.

### Q: What makes a client golden dataset "authoritative" for go-live?
- [ ] It has more than a thousand items
- [ ] It was labelled by the vendor's most experienced engineers
- [x] The customer's business owner signed off on the set and the threshold as the acceptance standard
- [ ] It matches the distribution of a public benchmark
> Authority comes from the customer's agreement, not size or vendor expertise. Without sign-off, the
> bar tends to be renegotiated after results are seen.
