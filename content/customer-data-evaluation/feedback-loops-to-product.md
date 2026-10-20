---
id: feedback-loops-to-product
module: customer-data-evaluation
name: Feedback loops to product
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - customer-data-evaluation/production-feedback-classification
  - customer-data-evaluation/customer-specific-regression-suites
  - delivery-ownership/productizing-repeated-customer-solutions
  - adoption/feature-request-triage
  - adoption/continuous-improvement
  - evals/eval-driven-development
  - adoption/usage-analytics
  - customer-data-evaluation/eval-review-loops
---

## TL;DR
A feedback loop to product carries what is learned in customer deployments back into the shared
product: classified failures and requests become evidence-backed issues, a regular triage decides
fix-in-config, fix-in-product or roadmap, every fix lands with a regression case, and the reporting
customer hears what changed. Without the loop, field teams patch the same problem separately in each
deployment, and the product never learns from its users.

## Why it exists
Forward-deployed teams sit closest to real usage. They see that the date parser fails on one
customer's European formats, that the retriever cannot handle scanned tables, that users at three banks
all want the same approval step. Under delivery pressure the rational move is a local fix: a prompt
override, a pre-processing script, a customer-specific branch. The customer is happy this week.

Repeated across customers, that rationality turns into a mess. The same date-parsing problem is solved
four times, four slightly different ways, in four deployments. The product team, which owns the shared
roadmap, never sees the pattern, because each instance was closed locally. When the platform changes,
every local patch is a potential break. And customers notice something else: they reported a problem,
it was patched for them, but the next release reintroduced it, or they reported a gap and never heard
back. After a few rounds they stop reporting, which removes the signal entirely.

The naive fix, "FDEs should file tickets", fails because a ticket per complaint is noise: a product
manager cannot weigh 300 one-line tickets against each other. What works is a deliberate loop with a
unit of evidence a product team can act on, a cadence, decision rules, and a closing step back to the
customer.

## How it works
### 1. The unit is an issue cluster with evidence
Classified feedback (see production feedback classification) is grouped into clusters: one failure
mode or request, with linked traces, counts, the customers affected, severity, the current workaround,
and its cost (hours spent, deals blocked, workaround maintenance). A cluster lets the product team
compare items on the same terms, which raw tickets never do.

### 2. Aggregate across customers
The same cluster is merged across deployments, with per-customer counts kept. This is where patterns
appear: a problem that is a quirk for one customer is a product gap when it appears in four. Because
customer data cannot be pooled freely, clusters carry redacted or synthetic examples plus links to the
traces held under each customer's data rules.

### 3. Triage on a cadence with decision rules
A regular (for example weekly) triage between field and product leads decides each cluster:
- **Customer configuration:** the behaviour is customer-specific and legitimate; express it as tenant
  config, not code.
- **Product fix:** the cause is in shared code, prompts or models; it goes on the product backlog.
- **Roadmap candidate:** a missing capability, ranked with the roadmap rather than squeezed in.
- **No change:** user error or out of scope, answered with documentation or training.
Decision rules make triage predictable, for example: anything severe is a product fix; anything seen in
several customers is reviewed for productisation; a local workaround is allowed only with a tracking issue
and an expiry.

The config-versus-product line is the decision people get wrong most. The test is whether the
behaviour is *correct but different* or *wrong*. "Dates in this market are day-first" is a legitimate,
stable customer difference, so it is configuration. "The parser guesses the order and is sometimes
wrong" is a defect, and hiding it behind a per-customer flag leaves it live for every customer who has
not set the flag yet.

### Rank clusters on comparable terms
Triage needs a way to compare clusters that does not reward volume of complaint. One reasonable scheme
(a convention to adapt, not a standard) scores each cluster as:

`priority = Σ over affected customers of (affected share of that customer's traffic × severity weight) + workaround cost`

with severity weights such as 1 cosmetic, 3 wrong-but-harmless, 10 acted-upon, 30 compliance, and
workaround cost expressed on the same scale (for example one point per engineer-day per quarter).
Using *share of traffic* rather than *number of tickets* is the point: a customer whose users complain
loudly about a cosmetic issue on 20% of requests scores 0.2 × 1 = 0.2, while a silent acted-upon error on
3% of requests at each of three customers scores 3 × 0.03 × 10 = 0.9. Whatever the exact weights, writing
them down makes the trade-off arguable, and strategic-account weight becomes an explicit term rather than
a side conversation.

### 4. Every fix closes three things
A resolved cluster: (a) adds regression cases to each affected customer's suite and, if general, to the
shared suite, so it cannot silently return; (b) retires the local workarounds, which is how the deployments
converge back onto the product; (c) is communicated to the customers who reported it, with the release it
shipped in. The third step is what keeps customers reporting.

### 5. Measure the loop itself
Track time from first report to decision and to shipped fix; recurrence rate (clusters that reopen);
number of open local workarounds and their age; share of field patches upstreamed; and feedback volume
per active user over time. Falling feedback with flat usage often means people gave up, not that quality
improved.

### Watch the loop's own failure modes
Loud customers dominate unless clusters are weighted by breadth and severity. Optimising a single signal
such as thumbs-up rate invites changes that please users without helping them (Goodhart's law). And
using accepted outputs as training data needs both permission and care: users often accept plausible
but wrong answers, so the model can drift toward its own errors.

## Example
Worked scenario. Four FDE teams run a document-extraction product at four insurers. Over a quarter, the
classified feedback contains a cluster "dates parsed with month and day swapped" in three deployments.
Each team wrote its own fix: one a prompt line ("dates are DD/MM/YYYY"), one a regex post-processor, one
a customer-specific parser branch. The fourth insurer has the same problem undetected, because its users
correct dates by hand and never complain.

At triage, the cluster shows three customers, linked traces, a severity of "wrong and acted upon" (dates
drive claim deadlines) and roughly a week of combined engineering time spent on three workarounds
(illustrative). The rule "severe and seen in more than one customer" makes it a product fix: the
extraction schema gains a locale field in tenant config, and the parser uses it. Each of the four
customers' regression suites gets day-first date cases; the shared suite gets cases for both orders.
The three local workarounds are deleted, one of which, the regex, had been mangling ISO dates. The fourth
insurer's edit-rate data, checked because the cluster prompted it, confirms the silent problem, and their
config is set too. All four receive a release note naming the fix. The next quarter, the cluster stays
closed and the number of open workarounds goes down by three.

## Trade-offs
- Upstreaming fixes removes duplicate work and patch drift, but is slower than a local fix; allow local
  workarounds when a customer is blocked, provided each has a tracking issue and an expiry.
- Weighting clusters by breadth protects the roadmap from the loudest customer, but can starve a single
  strategic customer's real need; give account strategy an explicit, visible weight instead of a backdoor.
- Rich clusters with traces and cost make decisions easy, but take FDE time to assemble; automate
  clustering from the feedback classifier so FDEs only add context and cost.
- Expressing customer differences as tenant config instead of branches keeps one codebase, but each flag
  multiplies the combinations that must be tested; once flags interact, prefer a few vetted presets and
  retire flags nobody has changed in months.
- Closing the loop with every reporter builds trust and keeps feedback flowing, but adds communication
  work; batch it into release notes per customer rather than individual replies.

## Pitfalls
- Fixing locally and closing the ticket; the symptom is the same failure mode appearing as "new" in each
  new customer deployment.
- Sending raw tickets to product instead of evidence clusters; the symptom is a product backlog nobody
  triages and field teams saying "product never listens".
- Shipping product fixes without regression cases; the symptom is a customer reporting the same problem
  again after a later release.
- Never retiring workarounds after the product fix; the symptom is local patches conflicting with the new
  behaviour, like a post-processor double-correcting fixed output.
- Turning a defect into a per-customer config flag because that ships fastest; the symptom is a new
  customer hitting the "already fixed" bug on day one because nobody set their flag.
- Reading falling feedback volume as rising quality; the symptom is churn or low renewal from a customer
  whose dashboards looked quiet.

## When to use
Set up the loop once a product has more than one customer deployment or field team, and well before
local workarounds become a maintenance burden. A single pilot needs only a shared list between the FDE
and the product owner. Measure time to decision and to fix, recurrence, open workaround count and age,
and feedback per active user. In interviews for FDE and architect roles, describing how field learning
reaches the product, and how you avoid forking the product per customer, is a strong signal.

## Role lens
- **Engineer:** Upstreams fixes behind tenant config instead of customer branches, adds the regression
  cases, and removes the local workaround once the product fix ships.
- **Architect:** Designs the product so customer differences are configuration, owns the triage decision
  rules, and tracks workaround debt as a platform risk.
- **FDE:** Assembles evidence clusters from their deployment, argues for them in triage, and closes the
  loop with the customer when the fix ships.

## Check yourself
**Q:** Your FDE teams ship fast because they patch locally, and the product team complains that
deployments have drifted. How would you set up a loop without slowing the field teams down?
**A:** Keep local patches allowed, but make each one register a tracking issue with an expiry date and
link it to a feedback cluster. Clusters are built automatically from classified feedback across customers,
and a weekly triage with field and product leads applies explicit rules: severe or multi-customer clusters
become product fixes, customer-specific behaviour becomes tenant config. Each product fix must add
regression cases and delete the matching workarounds. I would track open workaround count and age and time
to upstream, and send customers release notes when their issue ships so they keep reporting.

## Quiz
### Q: What is the most useful unit for passing field feedback to a product team?
- [ ] One ticket per user complaint, filed as soon as it arrives
- [x] A cluster per failure mode or request with traces, affected customers, severity and workaround cost
- [ ] A monthly average satisfaction score per customer
- [ ] A list of the customers who complained the most
> A cluster lets the product team compare items on breadth, severity and cost. Raw tickets are too noisy
> to weigh, and averages or complaint counts say nothing about what to fix.

### Q: After a product fix ships for a cross-customer problem, what should happen to the local workarounds?
- [ ] Keep them in place as a backup in case the fix regresses
- [ ] Leave them until the customer asks for removal
- [x] Remove them, with regression cases ensuring the product fix covers each customer
- [ ] Convert them into the default behaviour for new customers
> Leftover workarounds diverge and can conflict with the fix, for example double-correcting outputs.
> Regression cases, not stale patches, are the right guard against the problem returning.

### Q: Feedback volume per active user has fallen steadily while usage is flat. What is a plausible and worrying interpretation?
- [ ] The feedback classifier has become more accurate
- [ ] The model has certainly improved, so users have less to report
- [ ] Usage analytics are miscounting sessions
- [x] Users have stopped reporting because earlier reports produced no visible change
> Falling feedback can mean better quality, but it also matches users giving up on a loop that never
> closes. Check with a random traffic sample and ask whether reporters heard back.
