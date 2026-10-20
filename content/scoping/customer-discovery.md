---
id: customer-discovery
module: scoping
name: Customer discovery
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - scoping/ai-use-case-discovery
  - scoping/workflow-analysis
  - scoping/success-metrics-kpis
  - scoping/baseline-measurement-before-deployment
  - strategy/stakeholder-alignment
  - adoption/stakeholder-management
sources:
  - Rob Fitzpatrick, 2013 — The Mom Test
  - Steve Blank, 2005 — The Four Steps to the Epiphany
---

## TL;DR
Customer discovery is the structured work of finding out what problem a customer actually has, who feels
it, what it costs today and what evidence would prove it solved, before anyone builds. It exists because
the request ("build us a chatbot") is a proposed solution from one stakeholder, and AI projects fail far
more often from solving the wrong problem than from picking the wrong model.

## Why it exists
A customer engagement usually starts with a solution-shaped request: an executive saw a demo and wants
"an agent for procurement" or "GPT over our documents". That request compresses three different things
into one sentence: a pain someone feels, a guess about what would relieve it, and a political mandate.
Building the literal request is fast and feels responsive, and it is the most common way an AI project
ends up as a polished demo nobody uses.

The naive alternatives fail in predictable ways. Taking the request at face value means you inherit the
requester's guess, which was made without knowing what the technology is good at. Asking users "would
you use a tool that does X?" produces polite yeses, because hypothetical questions about the future cost
nothing to answer. Building a quick prototype first and "seeing what they think" anchors everyone on
your first design and skips the questions that decide success: how often the task happens, what an error
costs, who must approve a change, and whether the data is even reachable.

AI raises the stakes on discovery because the system is probabilistic. A conventional feature either
works or it does not; an AI feature works on some fraction of cases. Whether 85% is a triumph or a
liability depends entirely on facts only discovery surfaces: the cost of a wrong answer, whether a human
reviews the output, and how accurate the current manual process really is.

## How it works
### 1. Map the people before the problem
Every enterprise problem has several roles, and they are rarely the same person:
- **Economic buyer** — controls the budget and decides whether this continues past a pilot.
- **Champion** — wants it to succeed and will spend political capital; often the original requester.
- **End users** — do the work today; their adoption decides whether value is realised.
- **Data and system owners** — can grant or block access to the sources you need.
- **Risk functions** — security, legal, compliance, works councils; they have veto power late if
  ignored early.

You map these first *because* each sees a different problem. The VP sees headcount cost, the user sees a
tedious lookup, compliance sees audit exposure. A solution that satisfies only one of them stalls.

### 2. Interview about past behaviour, not future intentions
The core technique, popularised by *The Mom Test*, is to ask about specific recent events: "Walk me
through the last claim you processed. What did you open first? Where did you get stuck?" Past behaviour
is evidence; opinions about hypothetical products are not. Useful follow-ups: "How often does that
happen?", "What did you do about it?", "What have you tried before?", "What happens if you get it
wrong?". If someone has never tried to solve the problem, it is probably not painful enough to fund.

### 3. Quantify the pain
Turn anecdotes into a rough cost: frequency × time per occurrence × cost of time, plus the cost of
errors (rework, refunds, regulatory findings) and of delay. You need this *because* it bounds the value
of any solution and feeds the ROI model; a problem worth 200 hours a year does not justify a six-month
integration.

### 4. Separate the problem from the proposed solution
Restate each request as the outcome it serves. "A chatbot over policy documents" might really be
"adjusters need the applicable coverage clause, with a citation, inside the claims screen". Asking "why"
two or three times usually moves from the solution to the job. Keep the requester's idea as one
hypothesis, not the specification.

### 5. Collect artifacts, not descriptions
Ask for real samples: 30 to 50 actual tickets, documents, emails or records, including the ugly ones. A
description of the data is always cleaner than the data. Samples also become the seed of the golden
dataset and reveal access problems immediately.

### 6. Find the decision process and success evidence
Ask: "If this worked, how would you know? Who decides whether it goes to production, and what would they
need to see?" This produces the success criteria and baseline before you build, *because* criteria
defined after a demo are negotiated to fit what was built.

### 7. Synthesise and play it back
Write a one-page problem statement: who, what job, current cost, constraints, success evidence, open
risks, and what you deliberately are not solving. Read it back to the champion and users. Disagreement
here is cheap; disagreement after a build is expensive.

### Commitment is the real signal
Enthusiasm is free. Customers who give you time with real users, sample data, a named sponsor and an
access request in flight are committed; those who only say "this is great" are not yet.

## Example
A worked scenario (all figures illustrative). An insurer's VP of Claims asks for "a ChatGPT-style
assistant over our policy wordings". The FDE runs six 45-minute interviews: the VP, a claims team lead,
three adjusters and the claims-system admin.

Each adjuster is asked to walk through their last three claims. A pattern emerges: a typical claim takes
about 40 minutes of hands-on work, and roughly 15 of those go to finding which clauses apply, because
wordings live in a document store organised by product version and adjusters must match the policy's
issue date to the right version. The team handles about 120 claims a day, so lookup alone is about
120 × 15 = 1,800 minutes, or 30 person-hours, daily. Errors matter more than time: the team lead says
wrong-version lookups are a known cause of payment leakage, and supervisors spot-check 10% of claims.

Nobody asked for a chat interface; adjusters live in the claims screen and do not want another tab. The
admin confirms the policy issue date and product code are available through the claims system's API.

The played-back problem statement becomes: "Given an open claim, surface the applicable clauses from the
correct wording version, with citations, inside the claims screen. Success: on 50 historical claims
labelled by a senior adjuster, the correct clause appears in the top three for at least the share a
senior adjuster agrees is acceptable, and median lookup time drops." That is a retrieval-and-citation
problem with a hard metadata filter (version by date), not a chatbot, and it now has a baseline, a
metric and an integration point.

## Trade-offs
- Deeper discovery lowers the risk of building the wrong thing, but delays visible progress; when the
  sponsor needs momentum, run a narrow prototype in parallel rather than skipping interviews.
- Interviewing end users surfaces the real job, but can bypass the champion politically; when the
  organisation is hierarchical, get the champion to introduce you and share findings with them first.
- Quantifying pain precisely makes the ROI case defensible, but costs time; when the problem is
  obviously large or small, an order-of-magnitude estimate is enough to decide.
- Playing back a written problem statement forces alignment, but exposes disagreements early; that
  discomfort is the point, unless stakeholders are already aligned, when a short confirmation suffices.

## Pitfalls
- Asking hypothetical questions like "would you use this?"; the symptom is unanimous enthusiasm in
  interviews followed by near-zero usage in the pilot.
- Interviewing only the requester and their managers; the symptom is a solution that fits the org chart
  but not the screen users actually work in, and users route around it.
- Accepting a description of the data instead of samples; the symptom is a prototype that works on clean
  demo files and collapses on the first real batch of scanned or inconsistent records.
- Skipping the decision process; the symptom is a successful pilot that stalls because security, legal
  or the budget holder were never asked what they needed to see.
- Letting the first solution idea become the spec; the symptom is every later conversation being about
  features of the chatbot rather than whether claims get processed faster or more accurately.

## When to use
Run discovery at the start of every engagement and again whenever scope changes materially, such as a
new department or a new data source. Keep it proportional: a two-week pilot might need five interviews
and one sample dataset; a multi-quarter programme needs a stakeholder map and a written problem statement
signed off by the buyer. Measure your discovery by whether you can state the job, the current cost, the
cost of an error, the success evidence and the decision-maker in a page. If you cannot, you are not
ready to build. In interviews for FDE and architect roles, expect a scenario where a customer asks for a
specific AI solution; the strong answer reframes it into the underlying problem before proposing anything.

## Role lens
- **Engineer:** Joins user interviews to see real inputs and edge cases, and turns collected samples into
  the first evaluation set before writing pipeline code.
- **Architect:** Owns the problem statement and constraints that shape the design, and refuses to commit
  to an architecture until error cost and integration point are known.
- **FDE:** Runs the interviews on site, maps stakeholders and politics, and converts a solution-shaped
  executive request into a measurable problem the customer agrees to.

## Check yourself
**Q:** A customer's COO asks you to "build an agent that automates our procurement approvals" and gives you
two weeks for discovery. What do you do first, and what finding would make you push back on building an
agent at all?
**A:** First map the stakeholders: the COO as buyer, the procurement lead as likely champion, approvers and
requesters as users, the ERP owner for data, and finance controls and audit for risk. Then interview
approvers about their last several approvals: what they checked, where they waited, how often they
rejected and why. Quantify volume, time per approval and the cost of a wrong approval. I would push back
on an agent if the interviews show most delay is waiting in an approver's queue rather than judgement
work, or if approvals are mostly deterministic policy checks (thresholds, approved vendors), because then
routing rules or a policy engine fix it more cheaply and auditably, and the AI role shrinks to
summarising the exceptions.

## Quiz
### Q: Which interview question produces the most reliable evidence about a user's pain?
- [ ] "Would you use a tool that summarised these documents automatically?"
- [x] "Walk me through the last time you had to find this information. What did you do?"
- [ ] "On a scale of 1 to 10, how painful is document search for you?"
- [ ] "What features would your ideal AI assistant have?"
> Questions about specific past events reveal actual behaviour, frequency and workarounds. Hypothetical
> and rating questions invite polite, cheap answers, and feature wish-lists describe solutions, not problems.

### Q: Why does discovery for an AI system need to establish the cost of a wrong output, more than for a conventional feature?
- [ ] Because AI systems are more expensive to host than conventional software
- [ ] Because regulators require error costs for every software project
- [x] Because AI output is correct only some of the time, so whether a given accuracy is acceptable depends on error cost and review
- [ ] Because error cost determines which model provider to choose
> A probabilistic system succeeds on a fraction of cases. The same accuracy can be excellent or
> unacceptable depending on what a mistake costs and whether a human checks, so discovery must surface both.

### Q: A customer is enthusiastic in every meeting but has not provided sample data or user access after three weeks. What is the best reading?
- [ ] The project is low-risk because stakeholder sentiment is strongly positive
- [ ] The team should build with synthetic data so as not to lose momentum
- [x] Commitment is not yet real; escalate the access request and the sponsor's role before building
- [ ] The data is probably clean, so it can be integrated later without risk
> Enthusiasm costs nothing; giving time, data and access does. Missing samples also hide the data
> problems that most often sink AI projects, so building on synthetic data defers the real risk.
