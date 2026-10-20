---
id: inter-annotator-agreement
module: evals
name: Inter-annotator agreement
kind: metric
difficulty: core
est_minutes: 14
status: reviewed
related:
  - evals/golden-dataset
  - evals/rubrics
  - evals/judge-calibration-and-bias
  - training-adaptation/data-labeling
  - customer-data-evaluation/labeling-sessions
  - customer-data-evaluation/sme-review-and-annotation-guidelines
sources:
  - Cohen, 1960 — A Coefficient of Agreement for Nominal Scales
  - Fleiss, 1971 — Measuring Nominal Scale Agreement Among Many Raters
  - Krippendorff, 2004 — Content Analysis: An Introduction to Its Methodology
  - Landis and Koch, 1977 — The Measurement of Observer Agreement for Categorical Data
---

## TL;DR
Inter-annotator agreement (IAA) measures how consistently independent humans label the same items. It
matters because labels are the ground truth every eval and every judge is scored against: if two experts
agree only 70% of the time, every score measured against those labels carries that noise, and the
guideline, not the model, is usually the problem. Chance-corrected statistics (Cohen's κ, Fleiss' κ, Krippendorff's α) are
used instead of raw agreement because raw agreement is inflated whenever one label dominates.

## Why it exists
You build a golden set of 500 support answers labeled "acceptable" or "not" by one subject-matter expert.
Your model scores 85% against it. Is the missing 15% model error or label error? With one labeler you
cannot know. Add a second expert and they agree with the first on 78% of items. Now at least a fifth of
the set is ambiguous enough that two experts read it differently, and the model's "85%" carries label
noise of that size. Every conclusion downstream, from "prompt B is 3 points better" to "the judge agrees
with humans 88% of the time", inherits it.

Agreement does three jobs. It sets the **reference point**: human–human agreement is what "as good as
a person" looks like on these labels. It is not a hard ceiling: a perfect judge would agree with each
annotator as often as that annotator is right, which is higher than two error-prone annotators agree with
each other, so a strong judge can land slightly above it. What the labels cannot do is *demonstrate*
that a judge is better than the people who wrote them. It **diagnoses guidelines**: low
agreement on one criterion means the definition is ambiguous, which is fixed by rewriting anchors, not
by replacing annotators. And it **sizes the process**: how many annotators per item, whether adjudication
is needed, how much overlap to keep during production labeling.

The naive measure, percent agreement, fails under class imbalance. If 95% of answers are acceptable, two
annotators who label everything acceptable agree 95% of the time while conveying nothing about the 5%
that matter. Chance-corrected statistics subtract the agreement you would expect from the label
frequencies alone and report only what is left.

## How it works
### Cohen's kappa: two raters, nominal labels
κ = (p_o − p_e) / (1 − p_e), where p_o is the observed agreement and p_e is the agreement expected by
chance, computed from each rater's marginal label distribution: p_e = Σ_c P_A(c) · P_B(c). The
denominator is the agreement available beyond chance and the numerator is how much of it was achieved,
so κ = 1 is perfect, 0 is chance level and negative values are worse than chance. For ordinal labels use
**weighted kappa**, which charges a one-level disagreement less than a three-level one (linear or
quadratic weights); quadratic-weighted κ is numerically close to an intraclass correlation.

### Fleiss' kappa: many raters, same count per item
Fleiss generalises p_o to the mean pairwise agreement within each item and p_e to the pooled label
proportions. It requires each item to be rated by the same *number* of raters, though not the same
raters. It is the right tool for a fixed panel of three SMEs labeling every item.

### Krippendorff's alpha: the general case
α = 1 − D_o / D_e, observed disagreement over expected disagreement, with a distance function chosen for
the scale: nominal, ordinal, interval or ratio. It tolerates any number of raters and missing ratings,
which is the normal situation in a real project where annotators overlap only partially. When in doubt,
report α.

### Continuous scores and rankings
For numeric scores, Pearson or Spearman correlation is tempting but ignores systematic offset: a rater
who is consistently one point higher gets r = 1 while the scores are not interchangeable. The intraclass
correlation coefficient (ICC) captures absolute agreement. For rankings, Kendall's τ.

### Interpreting the number
The Landis and Koch bands (0.41–0.60 moderate, 0.61–0.80 substantial, above 0.80 almost perfect) are a
convention, not a law; what is acceptable depends on how the labels are used. Gating labels for a
release need high agreement; preference labels for training tolerate less. Two **kappa paradoxes** catch
people: high p_o with low κ when one class dominates (most agreement was predictable), and *higher* κ
for raters whose marginals differ than for raters with matched marginals at the same p_o, because
mismatched label rates lower p_e (so a systematic difference between raters can make κ look better).
So report p_o, κ and the confusion table together,
with a bootstrap interval over items, because κ = 0.7 on 30 items has a wide interval.

### The process around the number
Pilot on 30–50 items with two or three annotators, compute agreement per criterion, read every
disagreement, rewrite the ambiguous anchors, and re-pilot. In production labeling keep a fraction of
items double-labeled for monitoring. Resolve disagreements by adjudication (a third rater or discussion),
and keep the "contested" flag on those items, because they are the ones a model will also get wrong for
defensible reasons. When you later compare an LLM judge with humans, compute judge–human κ the same way
and put it next to human–human κ: a judge at the human level is another annotator, not a better one.

## Example
Cohen's kappa by hand on two pilots of 100 support answers labeled pass or fail by two SMEs. The second
pilot has *higher* raw agreement and a much *lower* kappa, which is the paradox you need to recognise.

```python
from collections import Counter

def cohen_kappa(a, b):
    n = len(a)
    p_o = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum((ca[c] / n) * (cb[c] / n) for c in set(a) | set(b))
    return p_o, p_e, (p_o - p_e) / (1 - p_e)

# Pilot 1: both pass 50, both fail 30, A pass / B fail 12, A fail / B pass 8
A = ["pass"] * 50 + ["fail"] * 30 + ["pass"] * 12 + ["fail"] * 8
B = ["pass"] * 50 + ["fail"] * 30 + ["fail"] * 12 + ["pass"] * 8
print("pilot 1: p_o=%.2f p_e=%.3f kappa=%.2f" % cohen_kappa(A, B))   # 0.80 0.519 0.58

# Pilot 2: both pass 90, both fail 2, A pass / B fail 4, A fail / B pass 4
A2 = ["pass"] * 90 + ["fail"] * 2 + ["pass"] * 4 + ["fail"] * 4
B2 = ["pass"] * 90 + ["fail"] * 2 + ["fail"] * 4 + ["pass"] * 4
print("pilot 2: p_o=%.2f p_e=%.3f kappa=%.2f" % cohen_kappa(A2, B2))  # 0.92 0.887 0.29
```

Pilot 1: A marks 62 pass, B marks 58, so chance agreement is 0.62 × 0.58 + 0.38 × 0.42 ≈ 0.519 and
κ = (0.80 − 0.519) / (1 − 0.519) ≈ 0.58. Pilot 2 looks better at 92% raw agreement, but both raters
pass 94%, so chance agreement is already 0.887 and κ drops to 0.29. Look at the failures, which are what
the eval cares about: the two raters flagged ten distinct items between them and agreed on only two. A
golden set built from pilot 2 would have a "fail" label that is mostly one person's opinion.

## Trade-offs
- Chance-corrected statistics are harder to explain to stakeholders than "they agreed 92%", but raw
  agreement misleads under imbalance; report both, and show the confusion table when the two disagree.
- More annotators per item improve label quality and make agreement measurable, but multiply cost; once
  agreement is established, drop to a monitoring overlap of a fraction of items.
- Adjudication by discussion produces cleaner gold labels but imports the most senior annotator's bias;
  for subjective criteria, a majority of independent labels or an explicit "contested" tag is safer.
- Tightening guidelines until agreement is high can define away the genuinely ambiguous cases; if the
  product must handle those, keep them labeled as ambiguous rather than forcing a verdict.

## Pitfalls
- Reporting only percent agreement; the symptom is 95% agreement on the pilot while model accuracy on the
  minority class turns out to be near random.
- One global kappa pooled across criteria or categories; the symptom is an acceptable aggregate hiding a
  criterion that sits at chance.
- Pearson correlation for score agreement; the symptom is r = 0.9 while one rater is consistently two
  points higher, so the two raters' scores cannot be mixed in one dataset.
- Annotators who are not independent (one sees the other's labels, or they confer during the pilot); the
  symptom is a high pilot kappa followed by a much lower production kappa.
- Comparing an LLM judge with labels from a single annotator; the symptom is a judge that "beats humans",
  which often means it matches one person's habits and is unverified against everyone else.
- Small pilots without intervals; the symptom is κ = 0.7 on 30 items followed by 0.45 on the next batch
  with no change in process.

## When to use
Measure agreement at the start of every labeling effort, continuously on an overlap sample during
production labeling, and whenever you evaluate an LLM judge against humans. Compute it per criterion and
per annotator pair with bootstrap intervals; a single global number hides the one criterion that is
broken. The target depends on use: labels that gate a release need agreement at the high end, preference
labels for training can tolerate less. In practice this is a few lines of code in the labeling-tool
export; in interviews it answers "how do you know your golden set is good?" and "how do you compare a
judge to humans?".

## Role lens
- **Engineer:** Adds the kappa or alpha computation to the labeling export, routes disagreements to
  adjudication, and reuses the same code to score judge–human agreement.
- **Architect:** Sets the agreement threshold a golden set must meet before it can gate releases, and the
  overlap percentage budgeted for ongoing double-labeling.
- **FDE:** Runs the pilot with the customer's SMEs and shows them where they disagreed with each other;
  it turns "your model is wrong" into "we have not yet defined what right means".

## Check yourself
**Q:** Two SMEs label 200 answers pass/fail with 88% raw agreement, and your LLM judge matches SME-1 on
90% of items. The customer concludes the judge is better than a human. What do you say?
**A:** Start by computing κ: if most answers pass, much of the 88% is chance and the real agreement is
lower. Then note that matching one annotator at 90% while the two humans agree at 88% does not show the
judge is better: a two-point gap on 200 items is inside the noise (the standard error is about two
points), and even a merely human-level judge can match one SME slightly more often than the two SMEs
match each other. Check its agreement with SME-2 as well, since it may simply share SME-1's habits. The fair
comparison is the judge against the adjudicated or majority label on the items where the humans agree,
which are the only items with a defensible truth, reporting judge–human κ next to human–human κ with
bootstrap intervals. If the judge's κ is close to the humans', it is as reliable as another annotator and
can scale the labeling; the contested items still need humans, and the judge's errors there cannot be
measured at all.

## Quiz
### Q: Two annotators agree on 92% of items, yet Cohen's kappa is 0.29. What is the most likely explanation?
- [ ] There is a bug in the kappa computation
- [x] The labels are heavily skewed toward one class, so most of the agreement is expected by chance
- [ ] The annotators colluded during the pilot
- [ ] The sample is too small for kappa to be meaningful
> Kappa subtracts chance agreement, and when one label dominates, chance agreement is already very
> high; the leftover agreement on the rare class is what kappa measures, and it was small.

### Q: Five annotators label an ordinal 1–3 scale, with each item labeled by two or three of them. Which statistic fits?
- [ ] Cohen's kappa averaged over all annotator pairs
- [ ] Fleiss' kappa
- [x] Krippendorff's alpha with an ordinal distance function
- [ ] Pearson correlation between annotators
> Fleiss' kappa needs the same number of raters per item and ignores the ordinal structure; averaged
> Cohen's kappa mishandles the missing overlaps. Alpha handles both, and the ordinal metric charges
> near-misses less than far-misses.

### Q: An LLM judge agrees with human labels at κ = 0.62, while human–human agreement is κ = 0.65. What is the correct conclusion?
- [ ] The judge is worse than humans and should not be used
- [ ] The judge is better than humans because its interval overlaps theirs
- [x] The judge is roughly as reliable as another human annotator; the three-point gap is within noise, so check where its disagreements fall before letting it replace a person
- [ ] Nothing can be concluded without an accuracy figure
> Human–human agreement is the reference level for comparisons against human labels, so a judge near it
> has reached annotator quality. Calling it better or worse than humans overreads a three-point difference,
> and with no ground truth beyond the labels, agreement is the accuracy figure you have.
