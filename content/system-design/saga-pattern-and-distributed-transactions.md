---
id: saga-pattern-and-distributed-transactions
module: system-design
name: Saga pattern & distributed transactions
kind: pattern
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - system-design/outbox-inbox-patterns
  - system-design/event-driven-architecture
  - system-design/data-consistency
  - system-design/workflow-orchestration
  - reliability/durable-workflow-execution
  - agent-reliability/idempotent-tool-execution
  - agents/approval-gates-for-consequential-actions
  - backend-fundamentals/postgresql-transactions-and-isolation-levels
sources:
  - Garcia-Molina & Salem, 1987 — Sagas
  - Gray & Reuter, 1992 — Transaction Processing: Concepts and Techniques
  - Richardson, 2018 — Microservices Patterns
  - Kleppmann, 2017 — Designing Data-Intensive Applications
---

## TL;DR
A business operation spanning several services cannot be one ACID transaction, and two-phase commit
blocks under coordinator failure and is unavailable across external APIs. A saga replaces it with a
sequence of local transactions, each paired with a compensating action that undoes it. Sagas give
atomicity and durability but not isolation, so you must order steps around the irreversible pivot, mark
in-flight state and make every step idempotent. An agent calling several tools in sequence is a saga.

## Why it exists
Consider "place order": reserve stock in the inventory service, charge the card through a payment
provider, create a shipment in logistics, and record the order. Each system has its own database and its
own transaction. If the card charge succeeds and shipment creation fails, the customer has paid for
nothing. Inside one database you would wrap all four in a transaction and roll back; across four systems
there is no shared transaction to roll back.

The textbook answer is **two-phase commit (2PC)**. A coordinator asks every participant to *prepare*
(write the change durably and hold locks, promising it can commit), and only when all vote yes does it
tell them to *commit*. 2PC does give atomicity, and it still lives inside many database clusters. It fails
as a general tool for two reasons. First, it is **blocking**: if the coordinator crashes after participants
have prepared, they must hold their locks until it returns, because committing or aborting unilaterally
could disagree with the coordinator's decision. Rows stay locked for as long as the outage lasts.
Second, it requires every participant to implement the prepare protocol; Stripe, Slack, Jira, a vector
database and an LLM provider do not expose "prepare", so the step with the most consequential side
effect is exactly the one that cannot participate. Availability also compounds: a 2PC commit needs every
participant up at the same moment, which is the temporal coupling that event-driven designs try to
remove.

2PC is not dead, though; it has moved inside systems that can make its weak point safe. Spanner-style
databases run 2PC across shards, but each participant and the coordinator is itself a replicated
consensus group, so "the coordinator crashed" means a new leader of that group takes over with the
decision already in its log and nobody blocks for long. The XA standard exposes 2PC across a database
and a broker that both implement it, which works inside one organisation's infrastructure and still
excludes every SaaS API. Three-phase commit adds a pre-commit round to avoid blocking under crash
failures, but it can decide inconsistently under network partitions, so it is rarely deployed. The
lesson: atomic commit across participants is available when you own all of them and replicate the
coordinator; a business process across vendors is not that case.

The saga, proposed in 1987 for long-lived database transactions, accepts that there is no global
rollback and designs the undo explicitly instead. That design work, deciding what "undo a charge" means
and when it is no longer possible, is the real content of the pattern.

## How it works
### Structure
A saga is a sequence of local transactions T₁ … Tₙ. Each Tᵢ commits on its own, in its own service.
For every Tᵢ that may need undoing there is a compensating transaction Cᵢ. If Tₖ fails, the saga runs
Cₖ₋₁, Cₖ₋₂, … C₁ in reverse order. The end state is either "all Tᵢ committed" or "every committed Tᵢ
compensated", which is the saga's version of atomicity. Durability comes from each local commit plus a
persisted saga log that records which step the saga has reached, *because* the coordinator process may
crash between steps and must resume rather than restart. The log records intent before each step
(`started charge_card`) and the outcome after (`done charge_card`): on recovery, a step with `started`
but no `done` is exactly the unknown-outcome case below, and it is resolved by querying the participant
with the step's idempotency key, never by assuming either answer.

### Compensation is semantic, not physical
A rollback restores bytes; a compensation is a new business action that cancels the effect. "Charge
card" is compensated by "refund", not by deleting the charge, and the refund is visible on the
statement. "Reserve stock" is compensated by "release reservation". "Send email" has no compensation; at
best you send a correction. This is why step ordering matters more in sagas than in transactions.

A step can be made cheaper to undo by splitting it into a reservation and a confirmation, the
**Try-Confirm-Cancel (TCC)** shape: *try* places a hold that expires on its own (a seat hold, a card
authorisation), *confirm* turns it into the real effect at the end of the saga, and *cancel* releases it.
Holding instead of acting means most compensations are "let the hold lapse or release it", which is
invisible to the customer, *because* nothing irreversible happened until confirm. The cost is that the
participant must support holds, and a hold's expiry must outlast the slowest path to confirm.

### Compensatable, pivot and retriable steps
Classify each step. **Compensatable** steps can be undone. The **pivot** is the step after which the saga
can no longer be abandoned: the first action with no undo, such as a non-refundable booking or a message
already delivered to a customer. Many payment flows treat the successful charge as the pivot even though
refunds exist, because a refund is visible on the statement and costs fees. **Retriable** steps come after
the pivot and are guaranteed to eventually succeed if retried (record the order, send the confirmation). The rule that falls out: put every step that might fail for
business reasons (validation, stock check, fraud score) *before* the pivot, so compensation is rarely
needed, and make everything after the pivot idempotent and retried forever, because you have promised
the customer the outcome.

### No isolation
Local transactions commit immediately, so other sagas and readers observe intermediate state. Garcia-Molina
and Salem's original paper already states that other transactions can see a saga's partial results;
Richardson summarises this as sagas being "ACD": atomicity, consistency and durability without
isolation. Concrete anomalies: a **lost update** when
another saga modifies the same order between a step and its compensation; a **dirty read** when a
dashboard shows an order as paid that is about to be refunded. Countermeasures named in Richardson's
treatment: a **semantic lock** (write `status = PENDING` so other logic treats the record as in flight),
**commutative updates** (debit/credit rather than set-balance), the **pessimistic view** (reorder steps to
shrink the window), **re-reading values** before acting, and a **version** check that rejects stale writes.

### Coordination styles
In **choreography**, each service listens for the previous step's event and emits its own; compensation
is triggered by failure events. It needs no central component but scatters the saga's logic across
services. In **orchestration**, a saga orchestrator holds the state machine, sends commands, waits for
replies and runs compensations. Beyond a handful of steps, or whenever someone must answer "which step is
order 42 stuck on", orchestration wins. Durable-execution engines (Temporal and similar) are orchestrators
with the saga log built in: your code is replayed from the log after a crash and the compensation list is
ordinary code.

### Unknown outcomes
A step that times out has an unknown result: the charge may or may not have happened. Compensating
blindly can refund a charge that never occurred. The correct move is to query the participant with the
step's idempotency key before deciding, which means every step must be issued with such a key. Timeouts
turn into a reconciliation step, not an error branch.

### Agents are sagas
An agent that creates a Jira ticket, posts to Slack and emails a customer is executing T₁, T₂, T₃ with
no transaction. Tool adapters should expose compensations where they exist (close ticket, delete
message), the orchestrator should place irreversible tools after a pivot, and approval gates belong
exactly at the pivot.

## Example
A small orchestrator: steps run forward, each with an idempotency key derived from the saga id and step
name, and committed steps are compensated in reverse on failure. The persisted log is a list here; in
production it is a table or a durable-execution engine's history.

```python
import logging
log = logging.getLogger("saga")
NOOP = lambda ctx, idempotency_key: None      # step had no side effect worth undoing
IRREVERSIBLE = None                            # step cannot be undone: this is the pivot

class Step:
    def __init__(self, name, action, compensate):
        self.name, self.action, self.compensate = name, action, compensate

def run_saga(saga_id, steps, ctx, saga_log):
    done = []                                        # steps whose action committed
    for step in steps:
        key = f"{saga_id}:{step.name}"               # same key on every retry of this step
        saga_log.append(("started", step.name))      # intent first: recovery sees in-flight steps
        try:
            ctx[step.name] = step.action(ctx, idempotency_key=key)
            saga_log.append(("done", step.name)); done.append(step)
        except Exception as e:   # production: on a timeout, query the participant by key first
            if any(s.compensate is IRREVERSIBLE for s in done):   # pivot passed
                raise RuntimeError(f"cannot abandon saga; retry {step.name} forward") from e
            log.warning("step %s failed: %s; compensating", step.name, e)
            for prev in reversed(done):
                prev.compensate(ctx, idempotency_key=f"{saga_id}:{prev.name}:undo")
                saga_log.append(("undone", prev.name))
            return "compensated"
    return "completed"

def fraud_check(ctx, idempotency_key):
    if not fraud.passes(ctx["customer"]):            # flights, fraud, payments, hotel, mail: service clients
        raise ValueError("declined")

steps = [   # business failures first, the irreversible step as late as possible
    Step("hold_seat",   lambda c, idempotency_key: flights.hold(c["flight"], key=idempotency_key),
                        lambda c, idempotency_key: flights.release(c["hold_seat"], key=idempotency_key)),
    Step("fraud_check", fraud_check, NOOP),
    Step("charge_card", lambda c, idempotency_key: payments.charge(c["amount"], key=idempotency_key),
                        lambda c, idempotency_key: payments.refund(c["charge_card"], key=idempotency_key)),
    Step("book_hotel",  lambda c, idempotency_key: hotel.book(c["stay"], key=idempotency_key), IRREVERSIBLE),
    Step("send_itinerary", lambda c, idempotency_key: mail.send(c["employee"], key=idempotency_key), NOOP),
]
```

If `charge_card` is declined, the seat hold is released and the saga ends as `compensated`. If
`send_itinerary` fails after the non-refundable hotel is booked, the orchestrator refuses to compensate
and the caller re-runs the saga from the log; the idempotency keys make the hold, charge and booking
no-ops on the second pass, so only the email is actually retried.

## Trade-offs
- Sagas keep every service available independently and need no shared lock manager, but they expose
  intermediate state to other readers; when an invariant must never be observably violated, keep those
  writes inside one database and one aggregate instead.
- 2PC gives true atomicity with no application-level compensation code, yet it blocks on coordinator
  failure and needs every participant to speak the protocol, which excludes external APIs and most
  SaaS systems.
- Orchestration centralises visibility and compensation logic at the cost of a component that every
  service depends on; choreography removes that dependency but spreads the state machine across teams,
  which flips in favour of orchestration as step count or compensation complexity grows.
- Designing explicit compensations forces the business to decide what "undo" means, which is valuable
  clarity, but some actions have no undo, and for those the only lever is ordering and approval gates.
- Forward-only retry after the pivot guarantees completion without refunds, provided post-pivot steps
  are idempotent and the retry budget is effectively unbounded, which requires a dead-letter path to a
  human when a participant stays down.

## Pitfalls
- Compensations that are not idempotent; a retried refund refunds twice, discovered when finance
  reconciles payouts.
- Compensating on timeout without querying the participant; refunds issued for charges that never
  happened, or shipments created twice, visible as unexplained negative balances.
- An irreversible step placed early; when a later validation fails there is nothing to compensate it
  with, and the customer has an email or a ticket for an order that does not exist.
- Ignoring isolation; a second saga (or a human in the admin panel) edits the record while the first is
  mid-flight, and the compensation overwrites their change, surfacing as "the system reverted my edit".
- No saga log or heartbeat; the orchestrator crashes between steps, nobody resumes, and orders sit in
  `PENDING` until a customer complains.
- Holds that expire before the saga confirms them; sagas that wait on a human approval or a slow
  partner fail at confirm with "hold not found", and the seat or authorisation is gone.
- Treating the LLM as a step that can be redone freely; re-running a non-deterministic step on retry
  produces different tool arguments, so the compensation keyed to the first attempt no longer matches.

## When to use
Use a saga whenever one business operation must update more than one service, database or external
system and the operation can legitimately fail part-way. Do not use one when all the data can live in a
single database and a local transaction suffices, which is the better design more often than microservice
enthusiasm suggests. Before choosing, write down each step's compensation and its idempotency key; if a
step has neither, it is your pivot and belongs as late as possible, possibly behind a human approval. In
practice the pattern appears as a workflow definition in an orchestrator, as a `compensate` method on a
tool adapter, or as a `status` column with `PENDING` values that other code must respect. Measure saga
completion rate, compensation rate, time stuck per step and the count of sagas needing manual repair.
In interviews, state why 2PC is unsuitable across heterogeneous services, define compensation versus
rollback, and bring up the lack of isolation and the pivot step without prompting.

## Role lens
- **Engineer:** Implements steps and compensations with idempotency keys, persists saga state, and
  writes reconciliation queries for steps whose outcome is unknown after a timeout.
- **Architect:** Decides aggregate boundaries so that most invariants stay inside one transaction,
  classifies steps as compensatable, pivot or retriable, and picks orchestration versus choreography.
- **FDE:** Maps a customer's cross-system process (CRM, billing, ticketing) onto a saga, identifies which
  of their systems cannot be undone, and places approval gates before those actions.

## Check yourself
**Q:** An agent books travel for an employee: it holds a flight seat, charges the corporate card, books a
non-refundable hotel through a partner API, and files an expense record. Order the steps, identify the
pivot, and describe what happens if the hotel API times out.
**A:** Everything that can fail for business reasons goes first: validate policy, hold the seat
(compensation: release the hold). The hotel booking is non-refundable, so it is the pivot and must come as
late as possible; the card charge is refundable, so it can precede the hotel. Order: policy check, seat
hold, card charge, hotel booking, then retriable steps: confirm the seat and file the expense. If the
hotel call times out, the outcome is unknown; the orchestrator must query the partner with the booking's
idempotency key before deciding. If the booking exists, the saga continues forward and retries the
remaining steps until they succeed. If it does not, the agent may retry the booking with the same key,
or compensate by refunding the charge and releasing the hold. Because the hotel is irreversible and
costly, an approval gate belongs immediately before it, and the saga log must persist each step so a
crashed worker resumes rather than rebooking.

## Quiz
### Q: Why is two-phase commit rarely used to coordinate a payment provider, a ticketing SaaS and an internal database?
- [ ] 2PC cannot guarantee atomicity across more than two participants
- [x] External services do not expose a prepare phase, and 2PC blocks participants if the coordinator fails after prepare
- [ ] 2PC requires all participants to use the same database engine
- [ ] 2PC is only defined for read-only transactions
> 2PC needs every participant to vote in a prepare phase and hold locks until the decision arrives; SaaS
> APIs offer no such phase, and a coordinator crash leaves prepared participants blocked. Atomicity across
> many participants is exactly what 2PC does provide where it applies.

### Q: In a saga, what does "no isolation" mean in practice?
- [ ] Steps cannot run on different machines
- [ ] Compensations must run in the same order as the original steps
- [x] Other transactions can observe and act on the intermediate state between a step and its compensation
- [ ] Each local transaction must use READ UNCOMMITTED
> Each local transaction commits immediately, so a saga's partial results are visible system-wide until it
> completes or compensates. Countermeasures such as semantic locks and version checks exist precisely
> because of this; compensations run in reverse order, not the same order.

### Q: A step after the pivot fails repeatedly. What should the orchestrator do?
- [ ] Run compensations for all earlier steps, including the pivot
- [ ] Abort the saga and leave the current state as is
- [x] Keep retrying the step forward, because post-pivot steps must be idempotent and eventually succeed
- [ ] Restart the saga from the first step with a new saga id
> Once the pivot has committed the saga has promised its outcome; earlier steps may be uncompensatable.
> Post-pivot steps are designed to be retriable and idempotent so the saga completes forward, with a
> dead-letter path to a human only when a participant stays unavailable.
