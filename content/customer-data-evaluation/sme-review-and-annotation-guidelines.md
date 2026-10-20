---
id: sme-review-and-annotation-guidelines
module: customer-data-evaluation
name: SME review & annotation guidelines
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - evals/rubrics
  - evals/inter-annotator-agreement
  - training-adaptation/data-labeling
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - customer-data-evaluation/labeling-sessions
  - customer-data-evaluation/eval-review-loops
  - evals/judge-calibration-and-bias
---

## TL;DR
Annotation guidelines are the written contract for how a customer's SMEs judge model outputs: the label
set, an operational definition of each label, a decision order, worked boundary examples and an explicit
"unsure" path. SME review is the protocol around them: blind, randomised, independently labelled, with
agreement measured and disagreements fed back into the guideline. Without both, every eval number
inherits the unmeasured noise of experts applying different private standards.

## Why it exists
When you ask three senior experts "is this answer good?", you get three answers shaped by three private
standards. One fails an answer for a missing caveat, one for a slightly wrong tone, one passes anything
factually correct. Each is a reasonable expert; together they produce labels whose disagreement is
indistinguishable from model error. If the eval set is labelled this way, a prompt change can move the
score just by changing which reviewer saw which items.

The naive approach is to trust expertise: "they are the SMEs, they know what good looks like". Expertise
tells you what an individual expert believes, but an eval needs a *shared* standard that can be applied
consistently, audited later, taught to a new reviewer and eventually encoded into an LLM judge. The
second naive approach is a one-line rubric ("rate 1 to 5 for quality"), which invites each reviewer to
fill in their own meaning of 3 versus 4. Guidelines exist to turn tacit, individual standards into an
explicit, shared one; review protocol exists to prevent the process itself (seeing which model produced
an output, reviewing in a fixed order, discussing before labelling) from biasing the result.

## How it works
### What goes in the guideline
1. **Scope.** What the reviewer is judging and, just as important, what they are not: "judge whether the
   answer is correct for this policy; do not judge writing style unless it misleads".
2. **Label set.** Prefer binary or small categorical labels per criterion over a single 1–5 score,
   *because* narrow questions get consistent answers and broad scales hide which criterion failed.
3. **Operational definitions.** For each label, the observable conditions: "Fail on completeness if the
   answer omits an eligibility condition stated in the source policy".
4. **Decision order.** Which criteria to check first and which are disqualifying, so reviewers do not
   weigh a tone problem against a factual error differently.
5. **Boundary examples.** Pairs of real cases just either side of each line, with the reason.
6. **Unsure and out-of-scope paths.** A legitimate "cannot judge" label with a reason field, *because*
   forcing a choice on ambiguous items turns policy gaps into silent noise.
7. **Rationale requirement.** A short free-text reason on every fail, citing the guideline section.

### The review protocol
- **Blind.** Hide which system, prompt version or model produced the output, and mix in reference
  answers, *because* reviewers who know which is the "new" version rate it differently.
- **Randomised order.** Shuffle per reviewer, so fatigue and anchoring effects spread evenly.
- **Independent first, discuss second.** Labels are committed before any group discussion; otherwise
  the most senior voice becomes the label.
- **Measure agreement.** On a double-labelled subset, compute raw agreement and a chance-corrected
  statistic such as Cohen's κ = (p_obs − p_chance)/(1 − p_chance). Raw agreement alone is inflated when
  one label dominates.
- **Attribute disagreement.** Use the cited guideline sections to see which criterion causes splits; fix
  that section, not the reviewers.
- **Version the guideline.** Store guideline version per label, because a definition change can
  invalidate earlier labels in that category.

### When to stop iterating
Iterate calibration rounds until agreement is stable and adequate for the decision the labels support.
There is no universal threshold; κ bands such as Landis and Koch's are conventions, and a high-stakes
acceptance set warrants more agreement than an exploratory error analysis.

## Example
A benefits-policy assistant for an HR customer. Two HR SMEs review 50 answers independently against
guideline v0.3, labelling pass or fail and citing the section behind each fail.

```python
from collections import Counter

def cohen_kappa(a, b):
    """a, b: equal-length label lists from two reviewers on the same items."""
    n = len(a)
    p_obs = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    p_chance = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return (p_obs - p_chance) / (1 - p_chance), p_obs

# 30 both pass, 10 both fail, 6 A-pass/B-fail, 4 A-fail/B-pass (illustrative)
a = ["pass"] * 30 + ["fail"] * 10 + ["pass"] * 6 + ["fail"] * 4
b = ["pass"] * 30 + ["fail"] * 10 + ["fail"] * 6 + ["pass"] * 4
# Guideline section cited by whichever reviewer failed the item, on the 10 disagreements
cited = ["G3-completeness"] * 7 + ["G1-accuracy"] * 2 + ["G5-tone"]

k, po = cohen_kappa(a, b)
print(f"raw agreement {po:.2f}, kappa {k:.2f}")   # raw agreement 0.80, kappa 0.52
print(Counter(cited).most_common())              # G3 drives most disagreement
```

Raw agreement of 80% looks fine, but chance agreement here is about 58% because both reviewers pass most
answers, so κ is only about 0.52. Seven of ten disagreements cite completeness: one SME fails answers
that omit the waiting-period rule even when the question did not ask about it. The fix is in G3 ("an
answer is incomplete only if it omits a condition that changes the answer to the question asked"), plus
two boundary examples, then a fresh calibration batch, not more reviewer training.

## Trade-offs
- Binary per-criterion labels give higher agreement and clear diagnosis, but lose gradations of quality;
  when choosing between two good systems, add pairwise preference judgements alongside them.
- Detailed guidelines raise consistency, but long documents go unread; past a few pages, move rarely
  needed detail into an appendix and keep a one-page decision card for reviewers.
- Blind review removes version bias, but hides context reviewers sometimes need, such as which data
  source a system had; provide the input context, never the system identity.
- An "unsure" label surfaces policy gaps honestly, but some reviewers overuse it; when its rate climbs,
  review those items in session and add boundary examples.

## Pitfalls
- Using a single holistic 1–5 score; the symptom is reviewers agreeing on the average but rarely on any
  individual item.
- Discussing items as a group before labelling; the symptom is suspiciously high agreement that drops
  sharply when the same reviewers label a new batch alone.
- Reporting raw agreement only; the symptom is "90% agreement" on a set where 90% of items are passes and
  reviewers almost never agree on the failures.
- Editing the guideline without versioning labels; the symptom is a metric jump between dataset versions
  that nobody can attribute to the model or the definition change.
- Letting reviewers see which model produced each output; the symptom is the new version winning by
  more in SME review than in blind or automated checks.

## When to use
Write guidelines before any SME labels more than a calibration batch, and before you build an LLM judge,
since the judge's prompt should be derived from the same document. Use the full protocol (blind,
randomised, double-labelled subset, κ) for acceptance sets and judge calibration; a lighter version is
fine for exploratory error analysis. Track agreement per criterion, unsure rate and guideline version.
In interviews, the strong signal is treating low agreement as a guideline defect and showing how
disagreement is attributed to a specific criterion.

## Role lens
- **Engineer:** Builds the review tool with blinding, per-reviewer shuffling, rationale fields and
  guideline version capture, and computes per-criterion agreement automatically.
- **Architect:** Makes the guideline the single source for human labels, judge prompts and acceptance
  criteria, so all three evolve together under version control.
- **FDE:** Drafts the guideline with the customer's SMEs from real cases, runs calibration rounds, and
  takes recurring splits to the policy owner for a decision.

## Check yourself
**Q:** Two SMEs show 88% raw agreement on a pass/fail review. Your manager says the labels are reliable.
What would you check before agreeing?
**A:** I would check the label balance and compute a chance-corrected statistic. If, say, 85% of items are
passes for both reviewers, chance agreement alone is high and κ may be low, meaning they rarely agree on
which items fail, which are exactly the items the eval is about. I would look at agreement restricted to
items either reviewer failed, and group disagreements by the guideline criterion cited. If one criterion
dominates, the guideline definition is ambiguous. I would also confirm reviewers labelled independently
and blind to system identity, since discussion or version knowledge inflates agreement.

## Quiz
### Q: Why are binary per-criterion labels usually preferred over one 1–5 quality score for SME review?
- [ ] Binary labels are faster to store and query
- [x] Narrow questions get more consistent answers and show which criterion failed
- [ ] Five-point scales cannot be used with chance-corrected agreement
- [ ] LLM judges can only output binary labels
> A holistic score lets each reviewer weight criteria privately, so the same output gets different
> scores. Per-criterion binary labels are easier to define operationally and pinpoint the failure.

### Q: Raw agreement is 80% but Cohen's kappa is about 0.5. What does that indicate?
- [ ] The kappa calculation is wrong because agreement is high
- [ ] The reviewers are careless and should be replaced
- [x] Much of the agreement is expected by chance because one label dominates
- [ ] The dataset is too small for kappa to be meaningful
> Kappa subtracts the agreement expected from each reviewer's label rates. When most items pass, two
> reviewers agree often by chance alone; the residual agreement on the hard items is what kappa shows.

### Q: Why should SMEs commit labels independently before discussing disagreements?
- [ ] Discussion takes too long in a labelling session
- [ ] Independent labels are required for blinding
- [ ] Discussion is never useful for annotation
- [x] Otherwise the most senior or vocal reviewer's view becomes the label and real ambiguity is hidden
> Independent labels reveal where the guideline is genuinely unclear. Discussion afterwards is valuable
> for fixing the guideline, but before labelling it manufactures agreement.
