---
id: golden-dataset-strategy
module: llmops-platform
name: Golden dataset strategy
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - evals/golden-dataset
  - evals/dataset-contamination
  - evals/error-analysis
  - evals/inter-annotator-agreement
  - llmops-platform/evaluation-frameworks
  - llmops-platform/evaluation-gates-for-production-releases
  - training-adaptation/data-labeling
  - data-architecture/model-and-dataset-provenance
sources:
  - Gebru et al., 2021 — Datasheets for Datasets
  - Ribeiro et al., 2020 — Beyond Accuracy: Behavioral Testing of NLP Models with CheckList
  - Breck et al., 2017 — The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction
---

## TL;DR
The `evals/golden-dataset` lesson covers what a golden item is and how it is labeled. This is the
platform view: a golden dataset is a portfolio of tiered, versioned sets (smoke, full, slice, regression,
adversarial, sealed holdout), each with an owner, an admission workflow fed from production failures, and
a size derived from the smallest regression it must detect. The strategy exists because a single static
set goes stale, leaks into prompts, and is either too small to trust or too expensive to run on every
change.

## Why it exists
Most teams start with one file of fifty examples the first engineer wrote in an afternoon. It works for a
quarter, then fails in four predictable ways. It goes *stale*: the product adds a refunds flow and a
second language, traffic shifts, and the set still measures last quarter's product, so the eval stays
green while users complain. It *leaks*: someone copies the best examples into the few-shot prompt, then
into the fine-tuning data, and from then on the set measures memorisation; the score climbs toward 100% and
stops carrying information. It has the *wrong size*: fifty items cannot distinguish a two-point regression
from noise, so engineers learn that the number wobbles and ignore it; the fix, growing it to five thousand
items, makes a full run cost real money and an hour of wall-clock, so the run is skipped on busy days,
which are the days it matters. And it has *no owner*: either nobody is allowed to say "this expected
answer is wrong", so stale labels block legitimate changes, or everyone edits it, so the baseline moves
silently and no two runs are comparable.

The naive alternatives do not solve this. "Just use production logs" gives inputs without agreed outputs,
skewed toward the common case, with customer PII inside. "Just use a public benchmark" measures the model
rather than your prompt, retrieval and tools. "Just make it bigger" trades noise for cost without fixing
staleness or leakage. The strategy treats the golden data as a managed platform asset with tiers,
versions, owners and a refresh loop, the same way a mature team treats its test suite rather than a
folder of scripts.

## How it works
### A portfolio, not a file
Each tier has a purpose, a size and a cadence, and the tiers exist *because* cost multiplied by run
frequency must stay affordable while the release gate stays powerful (sizes below are illustrative):
- **Smoke set** (tens to a couple of hundred items, stratified): runs on every PR; catches catastrophic
  breakage such as a broken template or a parser that no longer matches, not two-point drifts.
- **Full set** (hundreds to low thousands, per-slice quotas): runs on merge, nightly and on release
  candidates; this is the set gates are computed on.
- **Slice sets**: per intent, language, tenant or document type; small, but each carries its own floor
  because the aggregate hides a collapsed slice.
- **Regression set**: one item per fixed production incident, never pruned, expected at 100% pass; "every
  bug becomes a test" is the cheapest coverage you will ever buy.
- **Adversarial and safety set**: prompt injection, jailbreak attempts, PII extraction; gated by absolute
  floors, not relative to the champion.
- **Sealed holdout**: a subset prompt authors never see and that never enters few-shot or training data,
  run only at release; its job is to detect overfitting to the development set. The gap between full-set
  and holdout scores is the overfitting signal.

### The statistics that set the sizes
For a pass-rate metric with true rate *p*, one run's standard error is √(p(1−p)/n). With *p* = 0.8 the
95% half-width is about ±11 points at n = 50, ±5.5 at n = 200 and ±2.5 at n = 1,000. So a smoke set
can only detect large breaks, and anyone claiming a two-point improvement on a hundred items is reading
noise. Pairing tightens this considerably: when candidate and champion run on the *identical* items,
item difficulty cancels in the per-item difference, which is why both must run on the same dataset
version (the `evaluation-gates-for-production-releases` lesson does the arithmetic). Size each slice from
the smallest regression you must be able to detect there, not from a round number.

The sizing rule follows directly. For a target 95% half-width *h* on the candidate-minus-champion
difference, an unpaired comparison of two pass rates needs n ≈ 1.96² · 2p(1−p) / h² items, while a paired
one needs n ≈ 1.96² · f / h², where *f* is the fraction of items whose verdict differs between the two
bundles. At p = 0.8 and h = 2 points, unpaired needs about 3,070 items; paired with f = 10% needs about
960. Detecting a regression of size *h* reliably rather than just bounding it costs more again (roughly
double the items for 80% power), and *f* is unknown until you have run the champion against a few
candidates, so measure it and resize.

### The sourcing pipeline
Items come from four places, and the mix matters. *Production traces*, sampled with per-slice quotas and
stratified by outcome (thumbs-down, escalation, abandoned session) rather than uniformly, *because*
uniform sampling drowns rare intents in the head of the distribution. *Failure mining* from error
analysis of incidents. *Synthetic generation* for slices with too little real traffic, admitted only after
a human labels and filters them, *because* a generator shares the model's blind spots and tends to write
easier cases than users do. *Expert-written* items for policy-critical behaviour that traffic rarely
exercises. What an item stores as "expected" depends on the task: a label for
classification, executable tests for code, and for free text usually a short list of must-include facts
and must-not behaviours rather than one reference phrasing, *because* a judge grading against criteria
penalises legitimate rewording less, and a policy change then edits a named criterion instead of a
paragraph nobody can diff. Every item passes an admission workflow: proposed, labeled by the task owner, checked by a
second reviewer (measure inter-annotator agreement; a slice with persistent disagreement has an
under-specified task, not bad reviewers), then merged into the *next* version. PII is scrubbed or
replaced with synthetic values before storage, *because* a golden set lives for years and gets copied to
laptops.

### Versioning and ownership
A version is immutable and identified by a content hash, with a changelog recording items added, removed
or relabeled and why. Relabeling is a version bump, never an edit, *because* a baseline recorded against
version A is meaningless once A's labels change. The owner of "what counts as correct" is the role that
owns the task definition (product or domain), while engineering owns the pipeline; the person who wrote
the prompt under test does not label the holdout. A datasheet (purpose, sources, slices, known gaps)
travels with each set so a new team can judge what a score on it means.

### Lifecycle
Refresh is triggered, not calendar-driven: when production slice mix shifts (monitoring tells you), when
a policy changes an expected answer, when a slice has sat at 100% for every candidate for months (move
those items to smoke or regression, they no longer discriminate), and after every incident. Contamination
defence is mechanical: the registry checks that no golden input's hash or near-duplicate appears in prompt
templates, few-shot banks or fine-tuning corpora, and the sealed holdout is excluded from any export that
engineers can read.

## Example
A support assistant with six intents whose production traffic splits roughly 60/20/10/5/3/2 percent.
Uniform sampling of 300 traces yields about 180/60/30/15/9/6 items; the rarest intent, with six items,
cannot be measured at all. Drawing a quota of 40 per slice from the full trace pool instead gives 240
items where every slice is measurable to roughly ±12 points at p = 0.8, and the aggregate is reported both
traffic-weighted and as per-slice floors. The code shows the size arithmetic, the quota sampler and how a
version is cut.

```python
import hashlib, json, math, random
from collections import defaultdict

def half_width_points(p, n):            # 95% half-width in percentage points for a pass-rate metric
    return 100 * 1.96 * math.sqrt(p * (1 - p) / n)

for n in (40, 50, 200, 1000):
    print(f"n={n:5d}: ±{half_width_points(0.8, n):.1f} points")   # ±12.4, ±11.1, ±5.5, ±2.5

def items_for_paired_gate(h, flip_rate):  # n so the paired 95% half-width on the delta is h (fractions)
    return math.ceil(1.96**2 * flip_rate / h**2)          # items_for_paired_gate(0.02, 0.10) -> 961

def stratified_sample(candidates, quota_per_slice, seed=7):
    """candidates: labeled production traces; a fixed quota per slice keeps rare intents measurable."""
    by_slice = defaultdict(list)
    for c in candidates:
        by_slice[c["slice"]].append(c)
    rng = random.Random(seed)
    chosen = []
    for slice_name, items in sorted(by_slice.items()):
        rng.shuffle(items)
        chosen += items[:quota_per_slice]           # a slice with fewer than quota items is a coverage gap to log
    return chosen

def cut_version(items, parent, changelog, tier="full", sealed=False):
    body = json.dumps(sorted(items, key=lambda i: i["id"]), sort_keys=True).encode()
    slices = {i["slice"] for i in items}
    return {"version": hashlib.sha256(body).hexdigest()[:12],   # content hash: an edit is a new version
            "parent": parent, "n": len(items), "tier": tier, "sealed": sealed,
            "per_slice": {s: sum(i["slice"] == s for i in items) for s in sorted(slices)},
            "changelog": changelog}                            # e.g. "relabeled 15 refund items after policy change 2025-06"
```

## Trade-offs
- Per-slice quotas make rare cases measurable, but the aggregate no longer reflects traffic; report both a
  traffic-weighted score and per-slice floors, and drop the quota only for slices nobody cares about.
- A sealed holdout detects overfitting to the development set, but you cannot error-analyse what you are
  not allowed to read; a small team without capacity for two sets should rotate items into the holdout
  instead.
- Mining failures from production keeps the set current, but a set made only of past failures makes every
  candidate look worse than traffic would; keep a randomly sampled, stratified core alongside it.
- Synthetic items cover rare slices cheaply, but share the generator's blind spots and run easier than
  real users; the trade flips in their favour only once humans have labeled and filtered them.
- Larger sets tighten confidence intervals and raise cost per run; tiering and content-addressed caching
  are what reconcile the two, and without caching a large full set gets skipped.

## Pitfalls
- Golden items copied into few-shot prompts or fine-tuning data; the symptom is a development-set score
  near 100% while the sealed holdout and production CSAT do not move.
- Editing expected outputs in place; the symptom is a champion baseline that shifts without any deploy, so
  last month's runs are no longer comparable.
- Uniform sampling from logs; the symptom is a flat aggregate while a rare but high-value intent regresses
  all the way to production.
- A set that is only a museum of past failures; the symptom is every candidate "regressing" ten points
  relative to what users actually experience.
- Nobody owning correctness; the symptom is label disputes settled by whoever ran the eval and an
  inter-annotator agreement that has never been measured.
- Items stale after a policy change; the symptom is the gate blocking a legitimately correct new behaviour,
  after which engineers start overriding the gate and it stops meaning anything.

## When to use
Adopt the portfolio the moment an eval gates anything or more than one person edits the set; before that,
one stratified file with a content hash is enough. Measure the asset itself: coverage and confidence-interval
width per slice, median item age, the fraction of production incidents that have a regression item, agreement
on relabels, and cost per run per tier. Day to day, every bug-fix PR adds a regression item and every
product change triggers a version bump with a changelog. In interviews, expect to be asked how big a set
must be to detect a given regression, how you keep it out of the prompt, and who is allowed to change a
label.

## Role lens
- **Engineer:** Writes the quota sampler and the contamination check, adds a regression item with every
  incident fix, and cuts content-hashed versions rather than editing files in place.
- **Architect:** Defines the tiers, the ownership model and the holdout policy, and decides which slices
  carry floors in the release gate and what cost per run the platform will fund.
- **FDE:** Builds the customer's first golden set from their real tickets with their domain experts labeling,
  because acceptance criteria argued from the customer's own data survive procurement reviews.

## Check yourself
**Q:** A team's 120-item golden set has read 94% for six months while production satisfaction has fallen.
What has most likely gone wrong, and what is your plan for the next two sprints?
**A:** Three candidates, checked in order. Leakage: grep prompts, few-shot banks and fine-tuning data for
golden inputs; if found, the 94% measures memorisation. Staleness: compare the set's slice mix with current
traffic and the product's recent changes; a set built before the refunds flow cannot see refunds failing.
Size: at n = 120 and p ≈ 0.94 the 95% half-width is roughly ±4 points (it would be about ±7 near p = 0.8),
and each slice holds only a fraction of the 120 items, so a real drop on one slice can hide in the noise. Plan: cut a new version
with per-slice quotas sampled from recent traces stratified by negative outcomes, add regression items
from the complaints, carve out a sealed holdout the prompt authors never see, record the new baseline
for the champion on that version, and put slice floors into the gate.

## Quiz
### Q: Why keep a sealed holdout that prompt authors are never allowed to read?
- [ ] To reduce the cost of the per-PR smoke run
- [x] To detect overfitting of prompts and few-shot examples to the development set
- [ ] To satisfy data-residency rules for customer traces
- [ ] To measure inter-annotator agreement on hard items
> Anything authors can read eventually shapes the prompt, so development-set scores drift upward without
> real improvement. A gap between full-set and holdout scores is the overfitting signal; cost and residency
> are solved by other mechanisms.

### Q: On a 100-item set a candidate scores 84% against the champion's 82%. What should you conclude?
- [ ] The candidate is better and can be promoted
- [ ] The candidate is worse because two points is within rounding
- [x] Nothing yet: at n = 100 the noise is several points, so run a paired comparison on a larger set
- [ ] The golden set is contaminated
> With p around 0.8 and n = 100 the 95% half-width is roughly ±8 points, far wider than the observed
> difference. A paired run on the same, larger dataset version is what can distinguish signal from noise.

### Q: A policy change makes 15 expected answers wrong. What is the correct way to update the golden set?
- [ ] Edit the 15 expected outputs in place so the next run is correct
- [ ] Delete the 15 items and shrink the set
- [x] Cut a new version with the relabels in its changelog and re-baseline the champion on that version
- [ ] Move the 15 items into the sealed holdout
> Baselines are recorded against a dataset version; editing in place silently invalidates every earlier
> comparison. A new content-hashed version with a changelog keeps history comparable, and re-running the
> champion gives the gate a valid reference.
