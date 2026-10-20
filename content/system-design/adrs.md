---
id: adrs
module: system-design
name: ADRs (architecture decision records)
kind: practice
difficulty: intro
est_minutes: 12
related:
  - system-design/trade-off-analysis
  - system-design/nfrs
  - system-design/c4-diagrams
  - system-design/build-vs-buy
  - system-design/model-selection
  - delivery-ownership/technical-documentation
  - deployment/security-and-architecture-reviews
  - strategy/stakeholder-alignment
sources:
  - Nygard, 2011 — Documenting Architecture Decisions
  - Official docs: MADR (Markdown Any Decision Records)
---

## TL;DR
An architecture decision record is a short, numbered, immutable document that captures one significant
decision: the context that forced it, the options considered, the choice, and its consequences. It
exists because decisions outlive the people, meetings and chat threads that made them, and without the
record teams either cargo-cult old choices or reverse them blindly. In AI systems, where model, vendor
and retrieval choices churn monthly, a good ADR also records the evidence at decision time and the
trigger for revisiting.

## Why it exists
Every system is the sum of its decisions, and almost none of them are written down. Six months later a
new engineer finds that the vector store is PostgreSQL with pgvector rather than a dedicated database,
that the context window is capped at 32k tokens, and that every LLM call goes through a gateway. Two bad
things can happen. **Blind acceptance:** nobody dares change the choice because nobody knows what it was
protecting, so the system calcifies around constraints that may no longer exist. **Blind reversal:** the
engineer "fixes" it, and the incident that caused the original decision happens again, because the
reason lived in someone's head and they left.

Wiki pages do not solve this. They describe the current state, not the decision, and they are edited in
place, so the reasoning and the alternatives vanish. Design documents are closer but are proposals,
long, and written before the decision; they rarely record what was finally chosen or why the other
options lost. Commit messages record *what* changed, not what was weighed.

AI systems make the problem acute because the landscape moves faster than the system. "We chose model X
because it scored best on our eval" is true on the day and unknowable a year later unless the eval
version, the scores, the prices and the assumptions were captured. Without that, the team cannot tell
whether a decision is still right or merely old. The ADR is the cheapest artefact that makes decisions
inspectable, reversible on purpose, and reviewable like code.

## How it works
### What deserves one
The test is architectural significance: would reversing this be expensive, or would someone later need
to know why it was made? Choosing the vector store, the tenancy model, the gateway, the retrieval
strategy, the model tier for a workload, the evaluation gate for releases, the data-residency approach:
yes. Choosing a logging library's formatter: no. A team producing an ADR a week is probably recording
tasks; a team with three ADRs after a year is probably not recording decisions.

### Anatomy
Nygard's original template has five parts, and each exists for a reason:
- **Title and number.** `ADR-0007: Serve v1 through a hosted model API behind our gateway`. Numbers give
  a stable reference; the title states the decision, not the topic.
- **Status.** Proposed, Accepted, Deprecated, or Superseded by ADR-NNNN. Status is the only field that
  changes after acceptance, *because* the record must stay an honest snapshot of what was decided.
- **Context.** The forces at play, stated neutrally: the non-functional requirements, constraints, team
  size, deadlines, costs, risks, and what is uncertain. This section is what lets a future reader judge
  whether the decision still applies, so it must describe conditions, not advocate.
- **Decision.** "We will …", in full sentences, including what is explicitly *not* being done.
- **Consequences.** What becomes easier, what becomes harder, new risks, follow-up work. Every real
  decision has negative consequences; a record without them was written to justify, not to decide.

MADR (Markdown Any Decision Records) extends this with **decision drivers**, **considered options**
with pros and cons, a **decision outcome** with its justification, and a **confirmation** section
describing how compliance will be checked. The extra structure is worth it when the decision had real
alternatives, which for architecture is nearly always. A one-line **Y-statement** ("In the context of
…, facing …, we decided for … to achieve …, accepting …") is a useful summary at the top.

### Lifecycle and mechanics
ADRs live in the repository, typically `docs/adr/NNNN-title.md`, next to the code they govern, because
that is where engineers look and where pull-request review already happens. One is opened as
*Proposed* in a pull request, argued in the review, and becomes *Accepted* when merged. After that it
is never edited except for status; changing a decision means writing a new ADR that supersedes the old
one and linking both ways. This immutability is the feature: the history of why the system looks the
way it does is the chain of records. Keep each to one or two pages and write it at decision time; an
ADR reconstructed months later is a story, not a record. Link ADRs from the C4 diagrams and README so
the "why" is one click from the "what".

### What AI systems add to the template
- **Evidence snapshot.** The evaluation set version and the scores, latency percentiles and cost per
  request for each option at decision time, so the decision can be re-run, not just re-argued.
- **Assumptions with expiry.** Provider pricing, rate limits, context limits, licence terms, and
  expected traffic, each labelled with the date it was checked.
- **Revisit triggers.** Concrete conditions under which the decision must be re-evaluated: a volume
  threshold, an open-weight model reaching eval parity, a residency requirement, a price change. Without
  a trigger, an ADR becomes a reason never to look again.
- **Reversibility.** Whether this is a two-way door (swap a model behind the gateway) or a one-way door
  (train on customer data, choose an embedding model that fixes the index).
- **Compliance and data flow implications.** Where customer data goes under this decision, and which
  approvals it needed.

### How it fits with the rest of the design toolkit
Non-functional requirements define what matters; trade-off analysis scores the options against them;
the ADR records the outcome and the reasoning; the C4 diagrams show the resulting structure. When any
one of these is missing, the others degrade: an ADR with no NFRs behind it is an opinion, and a diagram
with no ADRs behind it is a snapshot nobody can question.

## Example
A MADR-style record for a decision almost every AI team makes early. The figures are illustrative
scenario values, not benchmarks; the point is the structure: neutral context, real options, named
drivers, honest negative consequences, an evidence snapshot and explicit revisit triggers.

```markdown
# ADR-0007: Serve v1 through a hosted model API behind our own gateway

**Status:** Accepted (2026-03-12)  **Supersedes:** none  **Deciders:** platform lead, security, CTO

**Context.** Six engineers, launch in ten weeks. Expected launch volume ~20M tokens/month, highly
uncertain (illustrative). NFRs: p95 time-to-first-token <= 1.5 s, cost <= $0.04 per conversation,
EU data residency, provider must be swappable without client changes.

**Decision drivers.** Time to launch; engineering capacity; residency; cost at uncertain volume;
ability to switch models as quality and prices change.

**Considered options.**
1. Hosted model API (EU region) behind an internal gateway.
2. Self-hosted open-weight model on two GPUs with vLLM.
3. Hybrid: hosted for chat, self-hosted for embeddings.

**Decision.** Option 1. All model calls go through the gateway, which owns routing, quotas, prompt
caching and per-tenant metering. We will not self-host any generation model in v1.

**Consequences.**
- Easier: launch on time; model swaps are a gateway config change; no GPU operations.
- Harder: per-token cost grows linearly with usage; dependence on provider rate limits and uptime;
  residency relies on the provider's EU endpoint and DPA.
- Follow-up: contract tests against the gateway interface; monthly cost and quality review.

**Evidence at decision time.** Golden set v3 (312 cases): hosted model faithfulness 0.91 vs best
open-weight candidate 0.84; p95 TTFT 0.9 s vs 1.3 s on a single GPU under load test. Illustrative.

**Revisit when:** sustained volume > 2B tokens/month for three months; an open-weight model reaches
>= 0.90 on golden set v3 or later; the provider cannot meet a customer's residency requirement; or
provider price changes by more than 30%.
```

## Trade-offs
- Writing an ADR costs an hour or two at decision time, but it saves the repeated re-litigation of the
  same choice; the cost is not worth paying for decisions that are cheap to reverse and nobody will ask
  about.
- Immutability keeps the history honest, but it produces chains of superseding records that a reader
  must follow; an index page with current status keeps this navigable.
- A rich template (MADR) forces real alternatives and evidence into the open, but it discourages people
  from writing anything for medium-sized decisions; many teams use the full template for one-way doors
  and the short Nygard form otherwise.
- Keeping ADRs in the repository puts them where engineers look and under review, but makes them
  invisible to non-engineering stakeholders unless they are also published or summarised.

## Pitfalls
- Writing the ADR after the decision to justify it; the symptom is a record with one option, no negative
  consequences and a context section that reads like a sales pitch.
- Editing an accepted ADR when the decision changes; the symptom is `git blame` showing the decision text
  rewritten with no superseding record and no explanation of what changed.
- Recording decisions about trivia; the symptom is eighty ADRs in a year and no one able to find the
  five that matter.
- Omitting the evidence and assumptions in AI decisions; the symptom is a team still on a model two
  generations old because "there is an ADR", with no way to tell whether its reasons still hold.
- No revisit trigger; the symptom is the same, discovered only when a competitor's cost per conversation
  is a third of yours.
- Storing ADRs in a wiki away from the code; the symptom is that nobody updates status, and engineers
  learn about a decision from the code rather than the record.

## When to use
Write an ADR whenever a choice shapes structure, cost, risk or data flow and would be expensive to
reverse: tenancy model, vector store, gateway, retrieval strategy, model tier per workload, evaluation
gates, residency approach, build versus buy. Write it as the decision is being made, in the pull request
where it is argued, and link it from the diagram and the README. Measure the practice by whether new
engineers cite ADR numbers in design discussions and whether superseding records exist for the decisions
that changed. In interviews, being able to describe a past decision in ADR shape, context, options,
choice, consequences, and what would have made you choose differently, is one of the clearest signals of
architectural maturity.

## Role lens
- **Engineer:** Opens an ADR in the same pull request as a significant change, keeps the evidence
  section honest with real eval and latency numbers, and reads the existing chain before proposing a
  reversal.
- **Architect:** Owns the ADR log, decides what is architecturally significant, enforces supersede-not-edit,
  and makes sure every ADR traces to the NFRs and trade-off analysis behind it.
- **FDE:** Writes customer-specific ADRs for deviations from the reference architecture (residency,
  SSO, on-prem model serving) so the next engineer on the account inherits the reasoning, not just the
  config.

## Check yourself
**Q:** An ADR from fourteen months ago says "use provider X's model Y because it scored best on our
evaluation set". A new engineer wants to switch to a cheaper model this sprint. How should the team
handle it, and what does the answer reveal about how the original ADR should have been written?
**A:** Do not edit the old record and do not switch on a hunch. Read its context and evidence: which
eval set version, what scores, what cost and latency targets, which assumptions. Re-run the current
golden set against both models, measure cost per conversation and p95 latency under the same NFRs, and
check whether any assumption (price, rate limits, residency) has changed. If the cheaper model meets the
drivers, write a new ADR that supersedes ADR-N, records the new evidence, and marks the old one
*Superseded*. The exercise shows what the original should have contained: an evidence snapshot with the
eval version, dated assumptions, and a revisit trigger such as "re-evaluate when a model within 10% of
the cost achieves parity", which would have made this review routine rather than a debate.

## Quiz
### Q: Why are accepted ADRs treated as immutable, with changes made by superseding records?
- [ ] Because version control cannot track edits to Markdown files reliably
- [x] So the record stays an honest snapshot of what was decided and why, and the history of changes is preserved as a chain
- [ ] Because only architects are permitted to edit them
- [ ] To keep the number of files in the repository stable
> Editing in place destroys the context and reasoning of the original decision, which is exactly what a
> future reader needs to judge whether it still applies. Superseding keeps both the old and the new
> reasoning.

### Q: Which element of an ADR is most often missing when the record was written to justify a decision after the fact?
- [ ] A number and title
- [ ] The names of the deciders
- [x] Genuine alternative options and negative consequences of the chosen one
- [ ] A link to the related C4 diagram
> A real decision weighs options and accepts costs. A record with a single option and "no downsides" is
> advocacy, and it gives a future team nothing to re-evaluate when conditions change.

### Q: What should an ADR about choosing an LLM provider include that a classic database-choice ADR usually would not need?
- [ ] A sequence diagram of the request path
- [x] An evidence snapshot (eval set version, scores, cost, latency), dated assumptions and explicit revisit triggers
- [ ] The names of all competing vendors in the market
- [ ] A rollback script
> Model quality, pricing and limits change far faster than database characteristics, so the decision must
> carry the data to be re-run and the conditions under which it should be. Without them the ADR becomes a
> reason never to re-evaluate.
