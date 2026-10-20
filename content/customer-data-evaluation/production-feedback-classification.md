---
id: production-feedback-classification
module: customer-data-evaluation
name: Production feedback classification
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/error-analysis
  - evals/llm-as-a-judge
  - ml-fundamentals/precision-recall-f1
  - evals/inter-annotator-agreement
  - observability/tracing
  - adoption/feature-request-triage
  - adoption/usage-analytics
  - customer-data-evaluation/feedback-loops-to-product
---

## TL;DR
Production feedback classification turns raw signals (thumbs-down, comments, edits, escalations,
tickets) into labelled failure modes, each with a severity and an owning component. It exists because a
thumbs-down rate tells you something is wrong but not what or who should fix it. The classifier labels
the whole trace, not just the comment, uses a taxonomy grown from real failures, and is checked against
human labels.

## Why it exists
A live deployment produces far more feedback than anyone can read. Explicit signals are sparse and
terse: a thumbs-down with no comment, or "wrong again". Implicit signals are plentiful but ambiguous: a
user regenerating an answer, heavily editing a draft, abandoning a session, or escalating to a human.
Tickets from the customer's support desk mix model failures with login problems and feature requests.

The naive approaches fail. **Tracking one satisfaction rate** gives a trend line with nothing to act on:
a rise in thumbs-down could be retrieval missing a newly uploaded policy, a guardrail over-blocking, or
latency. **Reading feedback ad hoc** gives vivid anecdotes and a priority list set by whoever complained
most memorably. **Classifying only the comment text** misattributes cause: "this is wrong" says nothing
about whether the retriever missed the document, the model ignored it, or the source document itself
was out of date. Those three have three different owners and three different fixes.

Classification makes feedback countable and routable: how many failures of each kind, for which
customer, in which component, and whether that is rising. It is the input to the regression suite (which
failures to capture as tests), to the product roadmap, and to the customer conversation.

## How it works
### 1. Build the taxonomy bottom-up
Start with error analysis: pull a few hundred negative traces, read each, and write a free-text note on
what went wrong (open coding). Group the notes into categories. This matters because categories invented
upfront ("hallucination", "bad answer") are too vague, while categories from real traces map to fixes. A
typical first taxonomy for a RAG assistant: retrieval miss, ignored context, unsupported claim, wrong
format, over-refusal or over-blocking, outdated source, latency or timeout, out of scope, missing
capability (a feature request), user error, and other. Each category gets a definition, positive and
negative examples, and an owning component.

### 2. Add severity and owner as separate fields
Category says what happened; severity says how bad (cosmetic, wrong but harmless, wrong and acted upon,
safety or compliance); owner says who fixes it (retrieval, prompt, guardrails, ingestion, product,
customer content). Keeping them separate lets you sort by severity within a category and route by owner.

### 3. Classify the trace, not the comment
The classifier gets the user input, retrieved passages, tool calls, final output, guardrail events and
the user's feedback. With retrieval visible it can tell "the answer was in no retrieved passage" (retrieval
miss) from "it was in passage 2 and the answer contradicts it" (ignored context). An LLM classifier with
a structured output schema works well here; small fine-tuned classifiers are cheaper once you have
enough labels.

### 4. Calibrate against humans, per class
Have SMEs label a sample using the same codebook, measure their agreement first (if humans cannot agree,
the definition is the problem), then measure classifier precision and recall per class. Report per class
because overall accuracy hides that, for instance, "unsupported claim" is rarely detected. Re-check after
any taxonomy, prompt or model change.

### 5. Route by confidence
High-confidence labels flow straight to dashboards and owners' queues. Low-confidence or high-severity
items go to a human review queue, because those are where a wrong label is most costly.

Be careful where "confidence" comes from. A number an LLM writes into its own JSON is a weak signal and
is often poorly calibrated, so do not trust the 0.7 cut-off because it looks like a probability. Either
calibrate it (on the SME-labelled sample, find the score above which labels are, say, 90% correct, and
use that as the threshold) or derive confidence from agreement: classify each item two or three times,
or with two different models, and send disagreements to humans. Agreement costs more tokens but is
measurable.

### 6. Watch for taxonomy drift
Track the share of "other" and of low-confidence items. When it rises, a new failure mode has appeared
(a new document type, a new user population): do another round of open coding and revise the taxonomy,
with a version number so trends across versions are interpretable.

### Know the sampling bias
Feedback comes from users who bother to give it, skewed negative and toward heavy users. Use it to find
and rank failure modes, then estimate their true prevalence on a random sample of traffic before claiming
"12% of answers have retrieval misses".

Even on a random sample, the classifier's own errors distort the rate. For one category, with recall
(true-positive rate) *r* and false-positive rate *f* measured against SME labels, the observed share
*p_obs* relates to the true share *p* by `p_obs = r·p + f·(1 − p)`, so `p ≈ (p_obs − f) / (r − f)`.
If the classifier flags 3% of a random sample as "unsupported claim" but only catches half of the real
ones (*r* = 0.5) with a 1% false-positive rate, the true share is about (0.03 − 0.01) / 0.49 ≈ 4%: the
dashboard understates the category by a quarter. The correction is only as good as the *r* and *f*
estimates, which is one more reason to keep a labelled calibration sample per customer.

### Handle it as customer data
Comments and traces contain personal and confidential information. Classification runs under the same
data agreement as production, and stored labels inherit the trace's retention and access rules.

## Example
A trace-level classifier with a fixed schema and confidence routing. `llm_json` stands for any client call
that returns JSON validated against the schema.

```python
from enum import Enum
from pydantic import BaseModel, Field

class Category(str, Enum):
    retrieval_miss = "retrieval_miss"; ignored_context = "ignored_context"
    unsupported_claim = "unsupported_claim"; wrong_format = "wrong_format"
    over_refusal = "over_refusal"; outdated_source = "outdated_source"
    missing_capability = "missing_capability"; user_error = "user_error"; other = "other"

class Label(BaseModel):
    category: Category
    severity: int = Field(ge=1, le=4)         # 1 cosmetic ... 4 safety/compliance
    evidence: str                              # quote from the trace supporting the label
    confidence: float = Field(ge=0, le=1)

OWNER = {"retrieval_miss": "retrieval", "ignored_context": "prompt", "unsupported_claim": "prompt",
         "wrong_format": "prompt", "over_refusal": "guardrails", "outdated_source": "customer_content",
         "missing_capability": "product", "user_error": "enablement", "other": "triage"}

def classify(trace: dict, codebook: str, llm_json) -> dict:
    prompt = (f"{codebook}\n\nUSER INPUT:\n{trace['input']}\n\nRETRIEVED:\n" +
              "\n".join(f"[{i}] {p}" for i, p in enumerate(trace["passages"])) +
              f"\n\nTOOL CALLS:\n{trace['tool_calls']}\n\nGUARDRAIL EVENTS:\n{trace['guardrail_events']}" +
              f"\n\nOUTPUT:\n{trace['output']}\n\nUSER FEEDBACK:\n{trace['feedback']}")
    label = Label.model_validate(llm_json(prompt, schema=Label.model_json_schema()))
    needs_human = label.confidence < 0.7 or label.severity >= 3 or label.category == Category.other
    return {"trace_id": trace["id"], **label.model_dump(mode="json"),
            "owner": OWNER[label.category.value], "queue": "human" if needs_human else "auto"}
```

The routing rule is deliberately asymmetric: severity 3 and above goes to a human whatever the
confidence, because a confidently wrong label on a compliance failure costs far more than a reviewer's
minute.

Illustrative week (numbers invented for the walkthrough): 1,400 negative signals for one customer. About
150 are not model failures at all (login problems, feature requests) and route straight to product or
support. Of the rest, retrieval misses and outdated sources are the largest groups, and roughly
two-thirds of the outdated-source items cite one policy document the customer replaced last month.
Ranking by count alone would put that first anyway, but rank by count × severity and a smaller group of
40 "unsupported claim" items about refund eligibility (severity 3: users acted on them) jumps the queue,
and every one of them was sent to human review by the severity rule. Two fixes follow: re-ingest the
replaced policy (an ingestion and customer-content fix, not a model fix) and add the 40 refund cases to
the customer's regression suite before changing the prompt.

## Trade-offs
- An LLM classifier handles free text and full traces with little setup, but costs tokens per item and
  drifts with model changes; a small trained classifier is cheaper once you have a few thousand labels.
- A fine-grained taxonomy points directly at fixes but lowers agreement between annotators; merge
  categories whose distinction does not change who fixes the problem.
- Auto-routing high-confidence labels saves reviewer time, but low-frequency severe failures can be
  mislabelled confidently; always send the top severities to humans regardless of confidence.
- Implicit signals such as edits and regenerations give far more volume than thumbs, but are noisier;
  weight them lower and validate their meaning on a sample before using them as targets.

## Pitfalls
- Classifying only the user's comment; the symptom is most items labelled "inaccurate answer" with no way to
  tell retrieval failures from generation failures.
- Inventing the taxonomy upfront; the symptom is a large "other" bucket and categories that do not map to
  any team's backlog.
- Reporting overall classifier accuracy only; the symptom is a dashboard where a rare but critical class
  never appears because the classifier almost never predicts it.
- Trusting the classifier's self-reported confidence as a probability; the symptom is a human-review
  queue that rarely catches errors while confidently mislabelled items flow into owners' backlogs.
- Treating feedback rates as prevalence; the symptom is alarming numbers that a random-sample audit of
  traffic does not reproduce.
- Changing the taxonomy without versioning; the symptom is a trend chart with a sudden jump that is a
  relabelling artifact, not a real change.

## When to use
Start as soon as a deployment produces more feedback than one person reads in a day; before that, read
everything yourself and build the first taxonomy from it. Re-run open coding after major launches or new
customer onboarding. Do not automate classification before humans agree on the codebook. Measure per-
class precision and recall against SME labels, the "other" share, and time from feedback to routed owner.
In interviews, trace-level classification and bottom-up taxonomies distinguish real practice from
dashboard talk.

## Role lens
- **Engineer:** Builds the trace-level classifier with a strict schema, the routing by confidence and
  severity, and per-class evaluation against human labels.
- **Architect:** Ensures traces capture retrieval, tool and guardrail data with stable ids so feedback
  can be joined to them, and that labels inherit data retention rules.
- **FDE:** Runs the open-coding sessions with customer SMEs, agrees severity definitions with the
  customer, and brings the weekly classified summary to the customer review.

## Check yourself
**Q:** Thumbs-down rate for one customer doubled after a release. Your classifier says most new items
are "unsupported claim". How do you confirm and act?
**A:** First check the classifier itself: sample the new items and have an SME label them, because a
release can also change what the classifier sees. If confirmed, compare the traces: are the claims absent
from retrieved passages (retrieval changed) or present but contradicted (prompt or model changed)? Diff
the release for retrieval, prompt and model changes, run the customer regression suite, and add the
confirmed failures as cases. Estimate prevalence on a random traffic sample, since feedback is biased,
then roll back for that tenant if the regression is real and severe.

## Quiz
### Q: Why should the classifier see retrieved passages and tool calls, not only the user's comment?
- [ ] Comments are too short to embed
- [ ] It reduces the classifier's token cost
- [x] Cause and owner depend on whether the information was retrieved and then ignored, or never retrieved at all
- [ ] Users' comments are not allowed to be stored
> "This is wrong" is the same comment for a retrieval miss and for ignored context, but they have different
> owners and fixes. Only the trace shows which one happened.

### Q: A feedback taxonomy shows a growing share of items labelled "other". What does it most likely indicate?
- [ ] The classifier's temperature is too high
- [x] A new failure mode has appeared that the taxonomy does not cover, so open coding should be repeated
- [ ] Users are giving less feedback overall
- [ ] The severity scale has too many levels
> A rising "other" share means real items do not fit existing definitions. Reading a sample and revising
> the versioned taxonomy keeps categories mapped to actual fixes.

### Q: Why is the rate of a category in feedback not a good estimate of its prevalence in all traffic?
- [ ] Feedback is always anonymised, so it cannot be counted
- [ ] LLM classifiers cannot estimate rates
- [ ] Prevalence is only defined for classification tasks
- [x] Feedback comes from a self-selected, negatively skewed group of users, so it over- or under-represents failures
> Feedback is excellent for discovering and ranking failure modes, but its sample is biased. A random
> traffic sample labelled with the same codebook gives the true rate.
