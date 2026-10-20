---
id: audit-logs-execution-traces
module: agent-reliability
name: Audit logs / execution traces
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - agent-reliability/structured-event-streams
  - agent-reliability/deterministic-replay-and-reproducibility
  - agent-reliability/tool-permissions-and-least-privilege
  - agent-reliability/conversation-execution-identifiers
  - observability/tracing
  - observability/spans
  - observability/opentelemetry
  - observability/langfuse
sources:
  - Official docs: OpenTelemetry (semantic conventions for generative AI)
  - NIST, 2006 — SP 800-92, Guide to Computer Security Log Management
---

## TL;DR
An execution trace answers "what did the agent do and why" for engineers debugging a run; an audit log
answers "who did what with whose authority" for security, compliance and incident response. They share
the same facts, best derived from one event stream, but differ in what they must contain, how long they
live, who can read them and whether they may ever be altered. Build both on purpose, not one as an
afterthought of the other.

## Why it exists
An agent refunds a customer 4,000 instead of 40. The questions arrive in a fixed order. Which user asked
for it? What did the model see when it decided? Which tool ran, with what arguments, under which
credential? Was it approved, and by whom? Could it happen again? Ordinary application logs answer none of
these well: they are unstructured, scattered across services, missing the prompt, and routinely rotated
after a week. The model's decision is non-deterministic and depends on the full context, so without the
exact prompt and tool results the failure is not reproducible, and without the policy decision and
credential it is not attributable.

The naive fix is "log everything to stdout". It fails because logging everything means logging PII,
secrets and customer documents into a system with no access control, because free text cannot be queried
("show me every refund over 1,000 in the last 30 days where approval was skipped"), and because a log that
engineers can edit is not evidence. Debugging and accountability have different needs. The trace wants rich
payloads, timings and sampling; the audit log wants completeness, immutability and long retention. One
artifact cannot be both without being bad at one of them.

## How it works
### Execution trace
A trace is a tree of spans, exactly as in distributed tracing, with the agent's structure mapped onto it:

```
run (trace root)
+- step 3
   +- llm.call    model=..., prompt_ref=sha256:..., in_tokens=4120, out_tokens=212, latency_ms=1830, cache_hit=true
   +- tool.call   name=lookup_order, args_ref=..., ok=true, latency_ms=140
   +- tool.call   name=refund, args_ref=..., permission=ask, approval_id=..., ok=true
```

Each span carries attributes: the model identifier, the prompt template version or content hash, token
counts, parameters, latency, cache hits, tool name, error class. OpenTelemetry's generative-AI semantic
conventions define attribute names for many of these (they are still evolving, so pin the version you
adopt), and tools such as Langfuse, LangSmith and Phoenix render the tree. Span inputs and outputs are
attached as references to a blob store, *because* a trace of a long run with inline payloads is megabytes
and the tracing backend is the wrong place for customer data. Traces are sampled under load; keeping 100%
of failed or escalated runs and a fraction of successful ones is a common policy. That policy requires
*tail-based* sampling: the keep-or-drop decision is made after the run ends, by a collector that buffers
its spans, *because* head-based sampling decides at the root span, before anyone knows the run will fail,
and so drops failures at the same rate as successes.

### Audit log
An audit record is written at each authority-bearing moment: a permission decision, an approval, a
side-effecting tool execution, a credential issuance, a human override. The record is small and
fixed-shape:

```
ts, run_id, step_id, actor (user), principal (agent identity),
action (tool name), target (resource id), args_hash,
policy_version, decision (allow|ask|deny), approval_id, credential_id,
phase (authorized | completed), outcome (ok | error class), prev_hash, hash
```

Each design choice has a reason. It stores an `args_hash` and a reference rather than the arguments
themselves, so the log can be retained for years without holding the content, while the referenced payload
can be deleted on schedule. The hash must be keyed (an HMAC with a secret held outside the log), *because*
arguments are often low-entropy (a customer id and an amount) and a plain SHA-256 of them can be recovered
by enumerating candidates; a keyed hash is pseudonymous data, not anonymous, so keep `actor` and `target`
as internal ids rather than names or emails. It is append-only in a separate store with narrower write permissions than the
application, *because* the whole point is that the people and systems being audited cannot edit it.
`prev_hash` and `hash` chain the records so deletion or modification is detectable; this is cheap and
turns the log into evidence, provided the latest hash is periodically anchored somewhere the writer cannot
touch (a WORM bucket, a separate account, a signed checkpoint), because anyone able to rewrite every row
could also recompute the whole chain. It is complete, never sampled, *because* "we dropped that one" is not an
acceptable answer in an incident review. And it is queryable by actor, action, target and time, which
means a table with indexes, not a text file.

Two write-ordering rules follow from "complete". The authorization record is written *before* the
side effect and the outcome record after, *because* a crash in between must leave evidence that an
action was authorized and may have run; an outcome-only log loses exactly the actions that crashed
mid-flight, which are the ones an investigation cares about. And for side-effecting tools the audit
write fails closed: if the audit store is unavailable the tool does not run. That costs availability, so
read-only tools are often allowed to proceed with a buffered record, but an unrecorded refund is worse
than a delayed one.

### One source, two sinks
Both artifacts are projections of the run's event stream. `tool.call.permission`, `tool.call.result`,
`input.received` and credential events feed the audit sink; every event with timing feeds the trace sink.
Deriving both from the stream, rather than instrumenting twice, guarantees they agree on what happened
and share the `run_id` that lets an investigator jump from an audit row to the full trace.

### What to capture at the decision point
The expensive-to-recover items are the ones to capture eagerly: the exact prompt (or its hash plus a
registry version that resolves to the exact text), the model identifier, the tool results that preceded
the decision, and the policy version. Everything else can be reconstructed; these cannot.

### Retention and access
Traces: short retention, engineer access, sampled. Audit: long retention set by the regulated domain you
operate in, restricted access, complete. Redaction rules differ too: a trace may be scrubbed of PII at
ingestion; an audit log should never have contained it.

## Example
A tool-execution wrapper that emits a trace span and append-only, hash-chained audit records from the
same call: one for the decision before anything runs, one for the outcome after. Arguments are hashed
into the audit rows and stored by reference for the trace. (In a stream-based runtime both would be
projections of the run's events; the direct writes keep the example short.)

```python
import hashlib, hmac, json, os, time
from opentelemetry import trace

tracer = trace.get_tracer("agent")
ARGS_KEY = os.environ["AUDIT_HMAC_KEY"].encode()      # held outside the audit store

def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, default=str).encode()

def _h(obj) -> str:                                    # chain hash: plain SHA-256 is fine
    return hashlib.sha256(_canon(obj)).hexdigest()

def _args_hash(args) -> str:                           # keyed, so low-entropy args cannot be guessed
    return hmac.new(ARGS_KEY, _canon(args), hashlib.sha256).hexdigest()

class AuditLog:
    """Append-only, hash-chained, one writer per chain. The role has INSERT only, no UPDATE/DELETE."""
    def __init__(self, store):
        self.store, self.prev = store, store.last_hash() or "0" * 64    # resume the chain on restart
    def write(self, **rec) -> str:
        rec["prev_hash"] = self.prev
        rec["hash"] = _h(rec)
        self.store.insert(rec)
        self.prev = rec["hash"]
        return rec["hash"]

def run_tool(name, args, *, ctx, policy, tools, blobs, audit: AuditLog):
    with tracer.start_as_current_span(f"tool.call {name}") as span:
        decision = policy.check(ctx.run_id, name, args)             # allow | ask | deny
        args_ref = blobs.put(args, ttl_days=30)                     # payload lives in the blob store
        span.set_attributes({"agent.tool.name": name, "agent.args_ref": args_ref,
                             "agent.permission": decision, "agent.policy_version": policy.version})
        base = dict(run_id=ctx.run_id, step_id=ctx.step_id, actor=ctx.user_id,
                    principal=ctx.agent_id, action=name, target=args.get("id"),
                    args_hash=_args_hash(args), args_ref=args_ref, policy_version=policy.version,
                    decision=decision, credential_id=ctx.credential_id)
        audit.write(ts=time.time(), phase="authorized", **base)    # before the effect; raises -> no run
        t0 = time.time()
        # "ask" would go to an approval flow (omitted); here only "allow" executes
        result = tools[name](**args) if decision == "allow" else {"ok": False, "error": decision}
        span.set_attributes({"agent.ok": bool(result.get("ok")),
                             "agent.error": result.get("error", ""),
                             "agent.latency_ms": int((time.time() - t0) * 1000)})
        audit.write(ts=time.time(), phase="completed", **base,
                    outcome="ok" if result.get("ok") else result.get("error", "error"))
        return result
```

## Trade-offs
- Deriving both artifacts from the event stream guarantees they agree, but it means the audit log is only
  as complete as the stream; any effect that bypasses the runtime (a tool that calls another service
  directly) is invisible to both.
- Hash-chaining makes tampering detectable at almost no cost, yet it serializes writes per chain; shard
  the chain per run or per day when write volume is high.
- Sampling traces controls cost, but a bug that only appears in sampled-out runs is a bug you cannot see;
  keep failed, denied and escalated runs at 100%.
- Storing payloads by reference keeps audit rows PII-free and traces small, but an investigation older than
  the blob retention window has hashes and no content; set blob retention from the longest investigation
  window you must support, not from storage cost alone.

## Pitfalls
- Treating the trace as the audit log; the symptom is an auditor asking for a complete record of refunds
  and being told the tracing backend samples at 10% and retains for 14 days.
- Writing audit records from the application with the same database role that can delete them; the symptom
  is an incident review where the question "could this have been edited" has no good answer.
- Logging the full prompt and tool outputs into a shared log platform; the symptom is a data-protection
  request that requires purging customer content from a system that has no per-record delete.
- Missing the prompt version at the decision point; the symptom is a reproduction attempt that cannot tell
  whether the model changed or the prompt did.
- Audit entries that record the action but not the authority; the symptom is "the agent did X" with no way
  to say which user asked, which policy allowed it, or who approved it.
- Separate run identifiers in the trace and the audit log; the symptom is an investigator manually matching
  timestamps between two systems.
- Writing the audit record only after the tool returns; the symptom is an effect visible in the target
  system (a refund, an email) with no audit row, traced to a worker that crashed mid-call.
- Head-based trace sampling with a "keep all failures" policy on paper; the symptom is that failed runs
  are missing from the tracing backend at the same rate as successful ones.

## When to use
Every agent that acts on behalf of a user needs the trace from the first week, because it is how you
debug anything, and needs the audit log before the first side-effecting tool reaches production, because
retrofitting immutability onto a log that already exists is painful. Decide retention, access and
redaction per sink up front and measure: trace coverage of failed runs (target 100%), audit write latency
on the critical path, and time-to-answer for the standard incident questions. In interviews, the
distinction to draw is intent: traces serve engineers and are sampled and short-lived; audit logs serve
accountability and must be complete, attributable and tamper-evident.

## Role lens
- **Engineer:** Instruments steps and tool calls as spans, derives audit rows from permission and result
  events, and keeps payloads in the blob store by reference.
- **Architect:** Sets the two sinks' retention, access control and redaction policies, chooses the tracing
  backend, and decides that the audit store is append-only with its own credentials.
- **FDE:** Maps the audit record to the customer's compliance requirements and SIEM, and demonstrates that
  every agent action is attributable to a named user and policy.

## Check yourself
**Q:** A regulated customer says: "we need to prove, two years from now, exactly why your agent issued a
specific refund, and prove nobody altered that record". Design the logging to satisfy them without
retaining customer PII for two years.
**A:** Separate the artifacts. The audit log keeps a small, fixed-shape record per authority-bearing event:
internal user id, agent identity, tool, target id, keyed argument hash, policy version, decision, approval id, credential id,
outcome, and a hash chain linking rows; it is append-only, written with an insert-only role, retained for
the two years, and contains no free text. The trace holds the prompt reference, model id, token counts
and tool results by reference; its payloads live in a blob store with a shorter retention and per-record
deletion. To prove "why", the audit row gives the prompt hash and registry version, which resolve to the
exact template text kept in the prompt registry (not PII) and the policy that allowed the call; the
customer-specific inputs remain verifiable against a candidate by recomputing the keyed hash even after
the content is purged. To prove integrity, recompute the chain and compare it with the externally anchored
head hashes. The identifiers are shared, so a row leads to the trace while it exists.

## Quiz
### Q: What most distinguishes an audit log from an execution trace?
- [ ] Audit logs are stored as JSON while traces use binary formats
- [x] Audit logs must be complete, attributable and tamper-evident with long retention; traces are rich, sampled debugging views with short retention
- [ ] Traces include tool calls while audit logs only include model calls
- [ ] Audit logs are generated by the model and traces by the runtime
> They serve different consumers: accountability needs completeness and immutability, debugging needs
> payloads and timings. Treating one as the other fails whichever need you ignored.

### Q: Why store an argument hash and a payload reference in the audit row instead of the arguments themselves?
- [ ] Hashes are faster for the model to read
- [ ] Because audit rows have a fixed 256-byte size limit
- [x] So the long-lived audit record holds no argument content while still proving which exact arguments were used
- [ ] Because arguments are non-deterministic and cannot be stored
> The (keyed) hash proves the identity of the arguments without holding their content; the content lives
> under a separate, shorter retention policy with per-record deletion.

### Q: A team samples traces at 10% to control cost. Which runs should be exempt from sampling?
- [ ] Runs shorter than one second
- [ ] Runs that completed successfully on the first attempt
- [x] Failed, denied and escalated runs, which are the ones investigations will ask about
- [ ] Runs from internal users only
> The purpose of a trace is to debug what went wrong; sampling out the failures defeats it. Successful
> runs are the safe place to save cost.
