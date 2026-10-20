---
id: deployment
title: Deployment
---

## Overview
This module covers what happens between "the AI system works in our environment" and "the customer's
users depend on it, and the customer's team can run it". Enterprise deployment is shaped by constraints
the builders do not control. Compliance constraints turn regimes such as HIPAA, GDPR or export control
into concrete rules about where data may be processed, which model providers qualify, and what logs
must contain and leave out. Those rules choose the deployment target: your SaaS, the customer's VPC,
their on-prem cluster or an air-gapped site. That target decides the network design: private endpoints
for one-way calls to managed services with no internet egress, and site-to-site VPNs for two-way access
to systems in the customer's data center. Terraform makes that infrastructure reproducible in every
customer account and auditable for every reviewer. Security questionnaires and security and
architecture reviews are how the customer checks your claims and your design before they let you near
their data. Health checks and runbooks make the system operable by people who did not build it. UAT
proves that it does the users' job against acceptance criteria agreed in advance. Rollout criteria and
rollback triggers set how exposure grows and what pulls it back. A deployment readiness review collects
all of that evidence into one go/no-go decision, and hypercare carries the system through its first
weeks of real traffic to a criteria-based handoff. Afterwards you can choose and justify a deployment
model, design its network path, pass a customer security review, and run a staged go-live you could
defend in a postmortem. It matters most for FDEs, who live this work on every engagement, and for
Architects, whose early design choices decide whether a deployment can pass review at all.

## Why it matters
- **Engineer:** You write the Terraform modules, private endpoint DNS overrides and VPN routes that let the same system install cleanly in each customer account. You implement separate liveness, readiness, startup and synthetic checks. You wire automated rollback triggers to error, latency, cost and sampled quality signals, and you keep the runbooks that every paging alert links to.
- **Architect:** You turn compliance regimes into data residency, provider and logging constraints before the design is fixed. You choose between SaaS, customer VPC, on-prem and air-gapped with the support cost priced in. You walk into the customer's architecture review with an accurate data-flow diagram, a threat model that covers prompt injection and tool permissions, and evidence for every control.
- **FDE:** You run the customer-facing sequence: answer the questionnaire honestly from one source of truth, steer the security review to dated findings, set acceptance criteria and sample sizes for UAT, chair the readiness review, and run hypercare with daily triage until the customer's team meets the exit criteria and owns support.

## Mental model
Picture deployment as a series of gates the system passes through, each one opened by evidence rather
than confidence. The first gate is permission: compliance constraints and the customer's policies decide
what is allowed at all, and that picks the deployment target. The second is reachability: private
endpoints and VPNs define exactly which network paths exist, and nothing else should. The third is
reproducibility: Terraform makes the environment a reviewed diff, not a set of console clicks. The
fourth is trust: questionnaires check your claims, and architecture reviews test your design. The fifth
is operability: health checks tell infrastructure what to do, and runbooks tell humans. The sixth is
acceptance: UAT shows that real users get their job done. The readiness review is the checkpoint where
all of that evidence is inspected together, with items proven, waived with an expiry, or blocking.
After it, rollout criteria widen exposure in stages, rollback triggers narrow it again, and hypercare
holds the system under close watch until ownership moves to the customer. Every gate produces an
artifact that the next one consumes. When a gate is skipped, you usually find out in production.

## Suggested order
1. compliance-constraints — start here, because regimes and customer policies set the limits that every later deployment choice must fit inside.
2. customer-vpc-on-prem-air-gapped — compliance and data custody pick the deployment target, which shapes the network, packaging and support model.
3. private-endpoints — the default way to reach managed services and model APIs from a customer network with no internet egress.
4. vpn — the alternative for two-way access to on-prem systems, best learned against private endpoints so the trade-off is clear.
5. terraform — once the target and network paths are known, IaC makes them reproducible and reviewable in every customer account.
6. security-questionnaires — the first trust check a buyer runs, and answering it needs the architecture and network design you now have.
7. security-and-architecture-reviews — goes deeper than the questionnaire, testing your data flows, trust boundaries and AI-specific threats against evidence.
8. health-checks — makes the deployed system observable to infrastructure, as the base for runbooks and rollout signals.
9. runbooks — gives the humans who respond to health alerts and known failure modes a tested way to mitigate.
10. uat — real users test the system against acceptance criteria agreed in advance, which needs a working, observable environment.
11. rollout-criteria-and-rollback-triggers — defines how exposure grows after acceptance and which signals pull it back, using the health and quality metrics from earlier lessons.
12. deployment-readiness-reviews — collects evidence from every earlier lesson into one go/no-go decision, so it only makes sense once you know what that evidence is.
13. hypercare — the last step, carrying the system through its first weeks of real traffic to a criteria-based handoff.

## Capstone
### Brief
You are the forward-deployed engineer taking a retrieval-augmented claims assistant from a successful
pilot to production at a regional health insurer. The assistant answers adjusters' questions from
policy documents and claim notes that contain protected health information. The insurer runs on AWS
in one region, permits no internet egress from data workloads, and keeps its claims database in an
on-prem data center. Its security team has sent a 120-question questionnaire and has scheduled an
architecture review. Go-live is planned for 300 adjusters across two regions in six weeks. Over a
week, using a sandbox cloud account and synthetic claim data, produce everything that deployment
needs. Turn the insurer's obligations into constraints and choose the deployment model. Write Terraform
for the VPC, the private endpoints to the model provider and storage, and the VPN to a simulated
on-prem network. Answer a representative slice of the questionnaire and prepare the architecture
review pack. Implement health checks and write runbooks for the top failure modes. Design and run a
small UAT. Write the staged rollout plan with rollback triggers. Chair a mock readiness review, and
plan hypercare through to handoff.

### Deliverables
- A constraints register that maps each regime and customer policy to concrete where, who, what, how-long, how and proof constraints, with a one-page justification of the chosen deployment model and the rejected alternatives with their costs.
- A Terraform repository that stands up the VPC, private endpoints with working DNS overrides, and a site-to-site VPN to a simulated on-prem network, with a reviewed plan output, remote state, and evidence that a clean apply into a second account succeeds.
- Answers to at least 25 questionnaire items drawn from a single answer library, plus an architecture review pack with a data-flow diagram, trust boundaries, a threat model covering prompt injection and tool permissions, and a findings tracker with owners and due dates.
- Liveness, readiness, startup and synthetic end-to-end checks running against the deployed service, and runbooks for at least five failure modes (provider outage, rate limiting, cost runaway, index staleness, an unsafe output report), each linked from its alert.
- A UAT plan and results with acceptance criteria, a scoring rubric, a justified sample size, the pass rate with a confidence interval, and defects classified by cause.
- A rollout ladder with stage criteria, minimum samples, hard and soft rollback triggers, a readiness review record with every item marked proven, waived with an expiry, or blocking, and a hypercare plan with a triage cadence and written exit criteria.

### Rubric
- VPC flow logs show zero bytes reaching the model provider or storage through NAT or the internet, and the services reject requests that do not come through the private endpoint.
- Liveness does not depend on downstream services: taking the model provider or vector store offline in a test removes instances from readiness without triggering restarts, and the synthetic check alerts.
- Every questionnaire answer is traceable to a control owner and evidence, and at least one answer is an honest "no" with a compensating control and a dated plan, not an aspirational "yes".
- Running each runbook in a rehearsal, by someone other than its author, mitigates the simulated failure without escalation, and any step that turns out wrong is recorded and fixed.
- The UAT pass rate is reported with its interval and per-slice results, and model-quality defects are separated from integration bugs, each with a different owner.
- Every rollback trigger has a numeric threshold, a minimum sample size and a named action. A replayed incident shows the trigger firing and exposure dropping to the previous stage.
- Hypercare exit criteria are things the customer's team demonstrates, such as resolving a set share of tickets alone and running one runbook unassisted, not a date on the calendar.
