---
id: audit-trails
module: security-governance-compliance
name: Audit trails
kind: pattern
difficulty: core
est_minutes: 17
status: reviewed
related:
  - security-governance-compliance/compliance-architecture
  - security-governance-compliance/ai-incident-response
  - security-governance-compliance/pii-protection-dlp
  - observability/logging
  - observability/tracing
  - data-architecture/data-retention-and-deletion-architecture
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/audit-logs-execution-traces
sources:
  - NIST SP 800-92 — Guide to Computer Security Log Management
  - Official docs: Amazon S3 Object Lock
---

## TL;DR
An audit trail is an append-only, tamper-evident record of who (or what) did what, when, with which
inputs and which versions, kept so that a decision can be reconstructed and defended later. AI systems
need a richer one than ordinary apps because the "what" includes a non-deterministic model: without the
prompt, model version, retrieved context and tool calls recorded at the time, you cannot explain or
reproduce an output months later.

## Why it exists
Sooner or later someone asks "why did the system do that?": a customer disputing a denied refund, a
regulator asking how an applicant was screened, a security team investigating an agent that emailed a
file outside the company. The answer has to come from evidence written at the time, because nothing
else survives. You cannot re-run the request: the model may have been updated by the provider, the
prompt template has changed twice, the retrieved document was edited, and sampling is not deterministic
even with the same inputs.

Application logs are the naive answer and they fail as evidence. They are written for debugging, so they
are sampled, rotated after days, missing the fields that matter (which model version? which document
chunks?), and editable by the same engineers whose actions might be in question. A log line that an
administrator can delete proves nothing to an auditor. Observability traces have the right shape but the
wrong guarantees: they are optimized for recent, aggregate debugging and are often sampled at a few
percent.

Compliance regimes formalize the need: HIPAA's Security Rule requires audit controls on systems holding
health data, SOC 2 auditors test that security-relevant events are logged and reviewed, and the EU AI Act
requires high-risk AI systems to support automatic recording of events over their lifetime. An audit
trail is the component built to meet those guarantees on purpose.

## How it works
### What goes in an AI audit event
An event answers who, what, when, on what basis and with what result:
- **Actor**: authenticated user or service identity, tenant, and for agents the human on whose behalf it
  acted. Both, *because* "the agent did it" is not an accountable answer.
- **Action and resource**: e.g. `claim.decision`, `tool.send_email`, with the target object ID.
- **Versions**: model ID as returned by the provider, prompt template version, system config hash, index
  or embedding version, guardrail policy version. These are what make reconstruction possible *because*
  every one of them changes behaviour.
- **Inputs by reference**: request ID, retrieved document IDs and their content hashes, tool arguments.
  Store references and hashes, not necessarily full text, *because* full prompts often contain personal
  data and turn the audit store into the largest PII copy you own. A hash only proves *which* text was
  used if you can still produce that text, so references must point at a versioned document store (or a
  retained payload store) whose own retention is at least as long as the trail's.
- **Output and decision**: the output (or its hash and a pointer to a retained copy), the action taken,
  any human approval with approver identity, and overrides.
- **Time** from a trusted clock, plus the trace ID linking to the debugging trace.

### Tamper evidence
Two mechanisms, usually combined:
1. **Hash chaining.** Each record stores `h_i = SHA256(h_{i-1} || canonical(record_i))`. Changing or
   deleting any past record breaks every later hash, so tampering is detectable by re-walking the chain.
   Two attacks survive a bare chain. Someone with write access can recompute every hash after an edit, and
   someone can silently drop the most recent records, since a truncated chain still verifies. Periodically
   anchoring the latest hash and a monotonically increasing sequence number somewhere the writer cannot
   alter (a separate account, a signed timestamp, a customer-visible digest) defeats both: the anchored
   head no longer matches a rewritten chain, and a sequence number below the anchored one exposes truncation.
2. **WORM storage.** Write-once-read-many storage such as object-lock buckets in compliance mode refuses
   deletion or overwrite until the retention period ends, even for administrators. This *prevents*
   tampering rather than merely detecting it.

### Scaling the chain
A single chain serializes every write through one "previous hash", which becomes a bottleneck when many
services emit events. The usual answer is per-partition chains (per service or tenant) plus a periodic
Merkle tree over the latest heads or over each batch of events, whose root is the only thing anchored.
This is the structure of transparency logs such as Certificate Transparency, and it has a second benefit:
you can prove a single record is included by handing an auditor a short Merkle path, without exposing
other tenants' events.

### Separation of duties
The audit store lives in a different account or project from the application, writes go through an
append-only path, and the application's credentials cannot delete. Readers (auditors, investigators) get
read-only access that is itself logged. This exists *because* the threat model includes insiders and a
compromised application.

### Retention vs erasure
Audit trails want to keep everything; privacy law wants personal data minimized and deletable. Resolve it
by design: keep identifiers pseudonymous in the trail, store content payloads separately under their own
retention, and use crypto-shredding (per-subject encryption keys you destroy on erasure) where content must
be retained but later made unreadable. Retention periods come from the strictest applicable rule for each
record type, decided with legal, not by default bucket settings; for example, the EU AI Act requires
deployers of high-risk systems to keep the automatically generated logs under their control for at least
six months unless other law says otherwise, while a credit or employment dispute may need records for years.

### Write path reliability
An audit event that is lost when the system is busy is worse than useless, *because* gaps look like
concealment. Write audit events with the same transaction as the business action (or via a transactional
outbox) so either both happen or neither does. For agents, each tool call is its own event carrying the
same run ID and the step number, *because* the question after an incident is usually "which step, on
which retrieved text, led to this action", not just "what was the final answer".

### Answering a reconstruction query
The test of the design is a question such as "show everything that led to decision D". The path is:
look up the event by object ID, verify its chain position against the latest anchor, follow the version
fields to the exact prompt template and guardrail policy in their registries, follow the document
references to the versioned store and check the content hashes match, and join any later override events
on the same object. If any hop requires a person's memory or a system that has since been rotated, the
trail has a gap.

## Example
A minimal hash-chained audit log with verification. In production the chain would be appended to WORM
storage and the head hash anchored elsewhere.

```python
import hashlib, json, time

GENESIS = "0" * 64

def canonical(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()

def append(log: list, event: dict) -> dict:
    prev = log[-1]["hash"] if log else GENESIS
    body = {**event, "ts": time.time(), "prev": prev}
    body["hash"] = hashlib.sha256(prev.encode() + canonical(body)).hexdigest()
    log.append(body)
    return body

def verify(log: list):                    # index of first broken record, or None
    prev = GENESIS
    for i, rec in enumerate(log):
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec["prev"] != prev or hashlib.sha256(prev.encode() + canonical(body)).hexdigest() != rec["hash"]:
            return i                      # first broken record
        prev = rec["hash"]
    return None

log = []
append(log, {"actor": "svc:refund-agent", "on_behalf_of": "user:4821", "action": "refund.decide",
             "model": "provider-model-2026-05", "prompt_version": "refund-v14",
             "retrieved": ["policy-77#sha256:9f1c..."], "decision": "deny", "approver": None})
append(log, {"actor": "user:ops-17", "action": "refund.override", "decision": "approve",
             "reason": "policy-77 outdated"})
anchor = (len(log), log[-1]["hash"])      # copied to a store the writer cannot alter
print(verify(log))                         # None: chain intact
dropped = log[:1]                          # an insider deletes the override record
print(verify(dropped), len(dropped) < anchor[0])   # None True: chain still verifies, anchor exposes it
log[0]["decision"] = "approve"             # an insider edits history
print(verify(log))                         # 0: tampering detected at record 0
```

Note the middle case: deleting the newest record leaves a perfectly valid chain, which is why the head
must be anchored outside the writer's reach. The second record also shows why the trail records
overrides: the dispute can be answered with the original model decision, its inputs, and the human who
reversed it.

## Trade-offs
- Logging full prompts and outputs makes reconstruction easy but creates a large sensitive-data store;
  references plus hashes flip the balance when inputs contain personal or regulated data.
- Synchronous, transactional audit writes guarantee completeness but add latency and a dependency;
  asynchronous writes via an outbox are fine as long as delivery is guaranteed, not best-effort.
- WORM retention prevents tampering but also prevents correcting mistakes and honouring erasure; it fits
  metadata and decisions better than raw personal content.
- Long retention helps disputes and audits years later, but every extra year raises storage cost, breach
  impact and discovery exposure; set it per record type from actual obligations.

## Pitfalls
- Recording "model: gpt" or an alias instead of the exact version returned; the symptom is an incident
  review that cannot tell whether a provider model update caused the change.
- Using the debugging log pipeline with sampling as the audit trail; the symptom is a disputed decision
  whose event simply is not there.
- Letting the application role delete or rewrite audit records; the symptom is an auditor finding that
  integrity of the trail cannot be demonstrated.
- Logging the agent's action but not the human principal; the symptom is an investigation that ends at
  "the service account did it".
- Storing raw prompts with personal data indefinitely; the symptom is an erasure request that cannot be
  fulfilled without breaking the audit chain.

## When to use
Build a real audit trail whenever an AI system makes or informs decisions about people or money, takes
actions through tools, touches regulated data, or must pass SOC 2, HIPAA or EU AI Act scrutiny. For a
low-risk internal assistant, structured logs with longer retention may be enough. Decide the event schema
at design time, alongside the threat model, because retrofitting version and provenance fields is hard.
Measure completeness (share of decisions with an audit event), integrity checks passing, and time to
answer a reconstruction query. In interviews, distinguish audit trails from observability: different
retention, access, integrity and completeness guarantees.

## Role lens
- **Engineer:** Emits a structured audit event in the same transaction as each consequential action,
  including model, prompt and index versions and retrieved document hashes.
- **Architect:** Owns the audit store's isolation, integrity mechanism, retention per record type and the
  balance between reconstruction and data minimization.
- **FDE:** Maps the customer's audit and retention requirements to the event schema and shows their
  compliance team how to run a reconstruction query for a disputed case.

## Check yourself
**Q:** A regulator asks you to explain an automated loan pre-screen decision made eight months ago. Your
provider has since retired that model version and your prompt has changed. What must your audit trail
have captured for you to answer?
**A:** I cannot re-run the request, so the answer must come entirely from recorded evidence. The trail
needs the applicant's pseudonymous ID and the request ID; the exact model version, prompt template version
and configuration hash; the inputs used, as references to the application fields and retrieved policy
documents with content hashes so I can show which text the model saw; the raw output and the decision
derived from it; any thresholds applied; and any human review with reviewer identity. Integrity matters
too: hash chaining or WORM storage lets me show the record was not altered after the fact.

## Quiz
### Q: Why are ordinary application debug logs usually insufficient as an AI audit trail?
- [ ] They are stored in text rather than JSON
- [x] They are often sampled, short-lived, missing version and provenance fields, and editable by operators
- [ ] They cannot record timestamps precisely enough
- [ ] They are too expensive to query compared with an audit database
> Evidence must be complete, retained for the required period, integrity-protected and rich enough to
> reconstruct the decision. Debug logs fail on most of those; format and cost are secondary concerns.

### Q: What does hash chaining an audit log provide on its own?
- [ ] Prevention of any deletion of records
- [ ] Encryption of personal data in the records
- [x] Detection of modification or deletion of past records when the chain is re-verified
- [ ] Guaranteed delivery of every event to storage
> Each hash depends on the previous one, so altering history breaks verification. It detects tampering but
> does not prevent it; WORM storage provides prevention, and anchoring the head hash stops full rewrites.

### Q: How do you reconcile long audit retention with a data subject's right to erasure?
- [ ] Exclude AI systems from erasure requests because audit law overrides privacy law
- [ ] Delete the entire audit chain whenever an erasure request arrives
- [x] Keep pseudonymous identifiers and hashes in the trail, store content separately, and crypto-shred where needed
- [ ] Store all audit records in a different country
> Separating identity and content from the integrity chain lets you erase or render personal content
> unreadable while the trail of decisions and versions stays intact. Blanket exemptions are not a safe legal
> assumption and must be confirmed with counsel.
