---
id: ai-use-case-discovery
module: scoping
name: AI use-case discovery
kind: practice
difficulty: core
est_minutes: 15
related:
  - scoping/customer-discovery
  - scoping/workflow-analysis
  - scoping/roi-model
  - scoping/rapid-prototyping-poc
  - security-governance-compliance/ai-inventory-and-use-case-risk-classification
  - agents/workflows-vs-agents
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
---

## TL;DR
AI use-case discovery turns a long list of "we could use AI for…" ideas into a short, ranked list of
candidates that are valuable, actually suited to a probabilistic model, feasible with reachable data, and
safe to get wrong sometimes. It exists because most candidate tasks are either better solved by plain
automation or too risky or data-starved to start with, and choosing the first use case badly sinks
credibility for the whole programme.

## Why it exists
Once an organisation decides to "do AI", ideas arrive faster than anyone can evaluate them: every team
has a list, vendors pitch their own, and the loudest executive's idea tends to win by default. Without a
discipline for choosing, three failure patterns repeat.

First, **AI for a deterministic problem**: a task that is really "apply these five rules" or "join these
two tables" gets an LLM, which is slower, costlier and less reliable than twenty lines of code or a
report. Second, **high value, impossible first step**: the most valuable idea (say, autonomous
underwriting decisions) is also the one with the highest error cost, the strictest regulation and the
least labelled data, so it spends a year in review and never ships. Third, **feasible but worthless**: a
team builds a neat summariser for a document nobody reads.

The naive approach, ranking ideas by value alone or by enthusiasm, fails because value is only one of four
independent axes. A use case is a good *first* candidate only when it is valuable, the task genuinely
needs pattern-matching over unstructured input, the data and integration point are reachable now, and
the consequence of an occasional error is bounded by review or reversibility. Discovery is the process of
checking all four before committing engineers.

## How it works
### 1. Build the candidate inventory from work, not wishes
Collect candidates by walking real workflows, reading ticket queues and asking teams where time goes
(see customer discovery and workflow analysis). For each, write one line in the form *"[role] spends
[time] doing [task] on [input] to produce [output]"*. This format forces each idea to name an input and
an output, *because* a use case without both cannot be built or evaluated.

### 2. Gate on AI fit
Ask whether the task needs a model at all. Signs it does: inputs are unstructured (free text, documents,
images, speech); the mapping from input to output is patterned but too varied for hand-written rules;
there is tolerance for imperfect output. Signs it does not: the logic is a stable set of rules, the
answer is a database lookup, or every output must be exactly correct with no review. A useful test is
*verification asymmetry*: tasks where checking an answer is much cheaper than producing it (drafting a
reply a human approves, extracting fields a human confirms) are strong fits, *because* the human review
turns a probabilistic component into a reliable system at low cost.

### 3. Estimate value
Use volume × time per item × loaded cost, plus error and delay costs, and any revenue effect. Keep it
order-of-magnitude at this stage; the ROI model refines the winners later. Note whether the value needs
full automation or only assistance, *because* assisted value (say, half the time saved) is reachable far
sooner than full automation.

### 4. Assess feasibility
Can you get representative data this month? Is there an integration point where output can land (an
API, a queue, a UI people already use)? Are there examples of correct outputs to evaluate against? Is
latency or cost per item compatible with the volume? A candidate blocked on a six-month data-access
approval is not a first use case regardless of value.

### 5. Classify risk
Consider consequence of error, who is affected (internal staff vs customers vs the public),
reversibility, regulatory exposure and the sensitivity of the data. This maps onto the organisation's
AI risk classification; high-risk use cases need governance work that should run in parallel, not be
discovered at launch.

### 6. Rank and pick a portfolio
Plot surviving candidates on value vs feasibility, with risk as a gate or a penalty. Pick a first use
case that is frequent, measurable against a known baseline, human-reviewed, and reachable with current
data: it buys trust and builds reusable plumbing (connectors, evaluation harness, access patterns). Keep
one larger strategic bet in discovery alongside it so the programme is not only quick wins.

### 7. Choose the technique last
Only after a candidate survives do you decide prompt, RAG, fine-tuning, a workflow or an agent. Choosing
the technique first ("we want an agent") is how deterministic problems end up with LLMs in them.

## Example
A minimal screening pass. Gates remove candidates that do not need a model or cannot start now; the
rest are scored, with high-risk ones penalised rather than banned so they stay visible as strategic bets.
All figures are illustrative inputs a team would estimate in workshops.

```python
from dataclasses import dataclass

@dataclass
class Candidate:
    name: str
    hours_per_year: float      # volume x time per item, from workflow interviews
    unstructured_input: bool   # free text, documents, images, speech
    rule_solvable: bool        # a stable rule set or lookup already answers it
    data_reachable: bool       # representative samples obtainable this month
    human_review: bool         # a person checks output before it takes effect
    error_cost: int            # 1 = trivial, 3 = moderate, 5 = regulatory or customer harm

def screen(c: Candidate):
    if c.rule_solvable or not c.unstructured_input:
        return None, "automate without a model"
    if not c.data_reachable:
        return None, "blocked: start the access request now"
    risk = c.error_cost - (2 if c.human_review else 0)   # review absorbs some error cost
    return c.hours_per_year / max(risk, 1), "candidate"

ideas = [
    Candidate("Draft replies to supplier emails", 3000, True, False, True, True, 2),
    Candidate("Flag invoices over approval limit", 1500, False, True, True, False, 3),
    Candidate("Auto-decide loan applications", 9000, True, False, True, False, 5),
    Candidate("Extract fields from scanned contracts", 2500, True, False, False, True, 3),
]
for c in ideas:
    score, verdict = screen(c)
    print(f"{c.name:40} {verdict:40} {'' if score is None else round(score)}")
```

The supplier-email drafts score 3,000 (risk 0 floors at 1); the loan decisions, despite three times the
hours, score 1,800 because they are unreviewed and high-consequence. Invoice limits are a rule, and
contract extraction is blocked on data, which tells you what to start in parallel.

## Trade-offs
- Starting with a low-risk, reviewed use case builds trust and reusable plumbing quickly, but its value
  may be modest; when the sponsor needs a headline result to keep funding, pair it with a strategic bet.
- A formal scoring matrix makes prioritisation transparent and harder to hijack, but its weights are
  judgement dressed as numbers; when two candidates score close, decide on qualitative factors instead.
- Gating out rule-solvable tasks keeps LLMs where they add value, but some messy rule sets are cheaper
  to approximate with a model than to codify; that flips when rules change often or are undocumented.
- Penalising high-risk use cases speeds the first delivery, but defers the governance learning the
  organisation will eventually need; run the risk review early when the strategic case depends on it.

## Pitfalls
- Choosing the technique before the problem ("find us agent use cases"); the symptom is a shortlist where
  every item is an agent and several are just scheduled scripts with an LLM bolted on.
- Ranking by value alone; the symptom is a first project stuck in legal, security or data-access review
  for months while stakeholders lose patience.
- Ignoring where output lands; the symptom is a model that works in a notebook but has no system, screen
  or queue to deliver into, so nobody uses its results.
- Not checking for a measurable baseline; the symptom is a finished pilot where nobody can say whether
  it improved anything, because the current time or error rate was never recorded.
- Treating the candidate list as fixed; the symptom is a stale backlog that ignores what the first
  deployment taught about data quality and user adoption.

## When to use
Use this whenever a customer or organisation has more AI ideas than capacity, at the start of an AI
programme, and when choosing the next use case after a first deployment. Run it as a short workshop
series: collect candidates from real workflows, screen them together with the people who own the work,
and leave with two or three shortlisted candidates, each with an input, output, value estimate, baseline
and risk class. Do not run it when the problem is already fixed by contract; go straight to
decomposition. In interviews, expect "the customer has twenty AI ideas, which do you start with?" and
answer with the four axes, not with the most exciting idea.

## Role lens
- **Engineer:** Sanity-checks feasibility of shortlisted candidates by pulling sample data, trying a quick
  prompt and estimating per-item latency and cost before anyone commits.
- **Architect:** Owns the portfolio view: which use cases share connectors, evaluation tooling and data
  access, and therefore which sequence builds a reusable platform.
- **FDE:** Facilitates the use-case workshop with the customer's teams, keeps executive pet projects
  honest against the screening criteria, and starts access requests for blocked candidates.

## Check yourself
**Q:** A bank's innovation team brings you two candidates: AI that writes first drafts of responses to
customer complaints for agents to edit, and AI that decides whether to refund disputed card transactions.
The second is worth more. Which do you recommend first, and why?
**A:** The complaint drafts. Both involve unstructured text, but the drafts have strong verification
asymmetry: an agent reads and edits each one, so occasional errors are caught cheaply and the value
(time saved per complaint) is measurable against current handling time. Refund decisions act directly on
customer money, are likely to be regulated and auditable, need explanation for each decision, and
probably lack clean labelled outcomes. They belong in the portfolio as a strategic bet with a parallel
risk review, perhaps starting as decision support for a human. Starting there would likely stall in
governance and risk the programme's credibility.

## Quiz
### Q: Which property most strongly suggests a task is a good first AI use case?
- [ ] It has the highest estimated annual value in the inventory
- [x] Checking an output is much cheaper than producing it, and a person reviews outputs before they take effect
- [ ] It can be fully automated with no human involvement from day one
- [ ] It uses the newest model capabilities the vendor has released
> Verification asymmetry plus human review turns a probabilistic component into a reliable system at low
> cost. Highest value often comes with the highest risk and access hurdles, which stall first projects.

### Q: A candidate is "flag purchase orders above the approval limit for the department". What is the right screening outcome?
- [ ] A strong AI candidate, because it is high-volume and repetitive
- [ ] A candidate for fine-tuning, because approval limits are company-specific
- [x] Solve it with a rule or a query, because the logic is a stable threshold check on structured data
- [ ] Block it until the team can label thousands of historical purchase orders
> The task is a deterministic comparison on structured fields. A model would add cost, latency and error
> to a problem that a rule answers exactly; repetitiveness alone does not make a task need AI.

### Q: Why choose the implementation technique (RAG, fine-tuning, agent) only after a use case survives screening?
- [ ] Because techniques change too fast to plan for
- [ ] Because the vendor contract usually determines the technique
- [x] Because choosing the technique first biases the inventory toward problems that fit it, including ones that need no model
- [ ] Because screening criteria are different for each technique
> Starting from "we want an agent" turns discovery into a search for agent-shaped problems and lets
> deterministic tasks sneak in. The task's input, output, error cost and data should decide the technique.
