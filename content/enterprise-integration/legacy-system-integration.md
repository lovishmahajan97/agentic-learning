---
id: legacy-system-integration
module: enterprise-integration
name: Legacy system integration
kind: pattern
difficulty: core
est_minutes: 16
related:
  - enterprise-integration/enterprise-connectors
  - enterprise-integration/incremental-synchronization-and-cdc
  - enterprise-integration/data-mapping-and-canonical-schemas
  - enterprise-integration/retry-deduplication-reconciliation
  - system-design/domain-driven-design-and-bounded-contexts
  - system-design/outbox-inbox-patterns
  - agent-reliability/idempotent-tool-execution
sources:
  - Hohpe & Woolf, 2003 — Enterprise Integration Patterns
  - Evans, 2003 — Domain-Driven Design
  - Fowler, 2004 — StranglerFigApplication (bliki)
---

## TL;DR
Legacy systems (mainframes, AS/400s, old ERPs, in-house databases) hold the data and transactions an AI
feature needs, but they offer no modern API, run on batch windows and fixed record formats, and cannot be
changed or overloaded. You integrate by wrapping them behind an anti-corruption layer that picks the least
invasive access path (file drop, replica, message queue, existing service, or screen automation) and
translates their model into yours, so the legacy quirks stop at one boundary.

## Why it exists
In most large enterprises the system of record for policies, accounts, claims or inventory is decades old.
It works, it is audited, and nobody will rewrite it for your assistant. Yet "what is the status of claim
4471?" or "raise a return for this order" can only be answered by that system.

The naive approaches each break something. Querying the production database directly with ad-hoc SQL
competes with the batch jobs and online transactions it was sized for, and on a mainframe, CPU consumption
can translate directly into the customer's software bill. Writing to its tables bypasses the business rules
that live in application code, corrupting state in ways nobody notices until month-end. Copying its data
model straight into your service (cryptic eight-character field names, status code `07`, dates as
`YYDDD`) spreads legacy semantics everywhere, so every later change touches every consumer. And treating it
like a cloud API (retry instantly, call it per user request, expect sub-second latency) collides with
nightly batch windows, single-threaded transaction monitors and maintenance freezes. Legacy integration is
the discipline of extracting value without destabilizing the thing that runs the business.

## How it works
### 1. Choose the access path, least invasive first
Ordered roughly from safest to riskiest, *because* each step down couples you more tightly to internals:
- **Existing interface.** A SOAP service, an MQ queue (IBM MQ is common on mainframes), a published batch
  extract, or an ESB/iPaaS flow someone already maintains. Reusing it inherits its rules and its support.
- **Batch file exchange.** Fixed-width or delimited files over SFTP on a schedule. Crude but decoupled: the
  legacy side controls load, and files are replayable.
- **Read replica or CDC.** Read from a replica, or capture changes from the database log, so reads never touch
  the primary. Writes still go through the application.
- **Transaction wrappers.** Expose an existing transaction program (for example a CICS transaction) through a
  gateway, so business rules still run on writes.
- **Screen automation (RPA).** Drive the green-screen or desktop UI. It is the last resort *because* any UI
  change breaks it, but sometimes it is the only path that runs the full validation logic.

### 2. Build an anti-corruption layer
A translation service owned by your side converts legacy records into your canonical model and back. It
decodes encodings (EBCDIC, COBOL `COMP-3` packed decimal, implied decimal points), maps status codes to
named states, normalizes dates and time zones, and hides access-path details behind domain operations such
as `get_claim(id)`. Consumers, including LLM tools, see only that interface, *because* when the legacy
system is eventually replaced, only this layer changes.

### 3. Protect the legacy system
Put a cache or replica in front of reads, a queue in front of writes, and explicit concurrency limits
everywhere. Respect batch windows: schedule bulk extracts when the system says, and make agent tools return
"system unavailable, try after 06:00" rather than hammering it.

### 4. Make writes safe
Legacy transactions are rarely idempotent. Attach a client reference to every write, record it before
sending, and check for it before retrying, so a timeout followed by a retry does not create two orders.

### 5. Migrate incrementally if replacing
The strangler fig pattern routes one capability at a time from the legacy system to the new one behind a
facade, *because* big-bang rewrites of systems with decades of undocumented rules routinely fail.

## Example
A nightly SFTP extract contains fixed-width 30-byte records in EBCDIC: customer id `PIC X(10)`, name
`PIC X(16)`, balance `PIC S9(5)V99 COMP-3` (seven digits plus a sign nibble = 4 bytes, two implied decimals).
The anti-corruption layer decodes it into a typed record.

```python
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class Customer:
    customer_id: str
    name: str
    balance: Decimal

def unpack_comp3(raw: bytes, scale: int) -> Decimal:
    """COBOL packed decimal: two digits per byte, last nibble is the sign (C/F positive, D negative)."""
    nibbles = []
    for b in raw:
        nibbles += [b >> 4, b & 0x0F]
    sign = nibbles.pop()                       # final nibble
    if any(n > 9 for n in nibbles):
        raise ValueError(f"corrupt packed field {raw.hex()}")
    value = int("".join(map(str, nibbles)))
    if sign == 0x0D:
        value = -value
    return Decimal(value).scaleb(-scale)

def parse_record(rec: bytes) -> Customer:
    assert len(rec) == 30, len(rec)
    return Customer(
        customer_id=rec[0:10].decode("cp037").strip(),   # cp037 = EBCDIC US/Canada
        name=rec[10:26].decode("cp037").strip(),
        balance=unpack_comp3(rec[26:30], scale=2),
    )

# 12345.67 packs to 12 34 56 7C; -1.50 packs to 00 00 15 0D
assert unpack_comp3(bytes.fromhex("1234567C"), 2) == Decimal("12345.67")
assert unpack_comp3(bytes.fromhex("0000150D"), 2) == Decimal("-1.50")
```

Reading the file as UTF-8 would produce garbage names and, worse, treating the packed bytes as text would
silently produce plausible-looking wrong balances, which is why the layout (the COBOL copybook) is the first
artifact to request from the customer.

## Trade-offs
- Batch file exchange is decoupled and replayable, but data is as stale as the last extract; it flips to
  CDC or a queue when users need answers about changes made in the last hour.
- Reading a replica protects the primary, but replication lag means a just-submitted transaction may be
  invisible; read-your-writes flows must query the primary or wait.
- Screen automation reaches logic no API exposes, but breaks on any UI change and is slow; it is acceptable
  only when volumes are low and a proper interface is on the roadmap.
- An anti-corruption layer costs a service to build and run, but without it every consumer learns legacy
  codes; it stops paying off only for a single throwaway consumer.

## Pitfalls
- Querying the production database during the batch window; the symptom is the nightly job overrunning
  and the operations team asking who is running unplanned queries.
- Decoding with the wrong code page or ignoring packed decimals; the symptom is mojibake in names or
  balances off by orders of magnitude.
- Retrying non-idempotent legacy writes on timeout; the symptom is duplicate orders or payments
  discovered at reconciliation.
- Exposing raw legacy codes to the LLM as tool output; the symptom is the model guessing what status
  `07` means and confidently telling a customer the wrong thing.
- Assuming 24/7 availability; the symptom is agent tools failing every night during maintenance with
  unhelpful error messages.

## When to use
Use these patterns whenever an AI feature must read from or write to a system you cannot change. Start by
asking for the existing interfaces, the batch schedule, the record layouts and the capacity constraints,
before writing code. Prefer read-only integration for a first release; add writes only through a path that
runs the system's own business rules, with idempotency references. Measure data freshness, load placed on
the legacy system and reconciliation mismatches. In interviews, explaining why you would not point an agent
at the mainframe database directly, and what you would do instead, is the core signal.

## Role lens
- **Engineer:** Writes the anti-corruption layer: copybook-driven parsers, code-to-state mappings,
  idempotent write wrappers and concurrency limits around legacy calls.
- **Architect:** Chooses the access path per capability, sets the boundary where legacy semantics stop,
  and plans strangler-style replacement rather than coupling new AI features to internals.
- **FDE:** Finds the person who knows the batch schedule and copybooks, gets SFTP or MQ access approved,
  and validates parsed records against what the legacy screens show.

## Check yourself
**Q:** An insurer wants an agent that can update a claim's contact address. The claims system is a mainframe
with no API, a nightly batch and a green-screen UI. How do you approach it?
**A:** First look for an existing interface: an MQ-fed transaction, a CICS transaction exposed through a
gateway, or a batch update file the system already ingests. Any of these runs the system's own validation.
Prefer them over direct table writes, which bypass rules, and over screen automation, which is brittle.
Wrap the chosen path in an anti-corruption layer with a domain operation like `update_contact(claim_id,
address, ref)`, record the reference before sending so retries are idempotent, and queue requests so the
agent never overloads the system or calls during batch. If only a nightly file exists, make the agent say
the change is scheduled, not done.

## Quiz
### Q: Why is screen automation usually the last-resort integration path?
- [ ] It cannot read data, only write it
- [x] Any UI change breaks it and it is slow, even though it does run the application's business rules
- [ ] Mainframes do not allow terminal sessions
- [ ] It requires rewriting the legacy application
> Driving the UI does pass through full validation logic, which is its appeal, but it couples you to screen
> layouts that change without notice and runs at human interaction speed, so it suits low volumes only.

### Q: What is the main purpose of an anti-corruption layer?
- [ ] To encrypt legacy data before it leaves the data centre
- [ ] To replace the legacy system with a modern one
- [x] To translate the legacy model into your own so legacy semantics do not spread into every consumer
- [ ] To filter malicious prompts before they reach the mainframe
> The layer decodes formats, maps codes to named states and exposes domain operations, so only it changes
> when the legacy system changes or is replaced; replacement itself is the strangler fig's job.

### Q: A packed-decimal balance field is read as text. What is the most likely outcome?
- [ ] An exception is always raised immediately
- [ ] The value is correct but loses its sign
- [x] Garbled or plausible-but-wrong values flow through unless the parser validates the field
- [ ] The database rejects the record
> Packed decimal stores two digits per byte plus a sign nibble, so text decoding yields nonsense characters
> or numbers that look valid; explicit COMP-3 decoding with nibble validation is required.
