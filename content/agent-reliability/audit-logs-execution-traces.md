---
id: audit-logs-execution-traces
module: agent-reliability
name: Audit logs / execution traces
kind: practice
difficulty: core
est_minutes: 14
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
of failed or escalated runs and a fraction of successful ones is a common policy.

### Audit log
An audit record is written at each authority-bearing moment: a permission decision, an approval, a
side-effecting tool execution, a credential issuance, a human override. The record is small and
fixed-shape:

```
ts, run_id, step_id, actor (user), principal (agent identity),
action (tool name), target (resource id), args_hash,
policy_version, decision (allow|ask|deny), approval_id, credential_id,
outcome (ok | error class), prev_hash, hash
```

Each design choice has a reason. It stores an `args_hash` and a reference rather than the arguments
themselves, so the log can be retained for years without holding PII, while the referenced payload can be
deleted on schedule. It is append-only in a separate store with narrower write permissions than the
application, *because* the whole point is that the people and systems being audited cannot edit it.
`prev_hash` and `hash` chain the records so deletion or modification is detectable; this is cheap and
turns the log into evidence. It is complete, never sampled, *because* "we dropped that one" is not an
acceptable answer in an incident review. And it is queryable by actor, action, target and time, which
means a table with indexes, not a text file.

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
A tool-execution wrapper that emits a trace span and an append-only, hash-chained audit record from the
same call. Arguments are hashed into the audit row and stored by reference for the trace.

```python
import hashlib, json, time
from opentelemetry import trace

tracer = trace.get_tracer("agent")

def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()

class AuditLog:
    """Append-only, hash-chained. The writer role has INSERT only; nobody has UPDATE or DELETE."""
    def __init__(self, store):
        self.store, self.prev = store, "0" * 64
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
        t0 = time.time()
        result = tools[name](**args) if decision == "allow" else {"ok": False, "error": decision}
        span.set_attributes({"agent.ok": bool(result.get("ok")),
                             "agent.error": result.get("error", ""),
                             "agent.latency_ms": int((time.time() - t0) * 1000)})
        audit.write(ts=time.time(), run_id=ctx.run_id, step_id=ctx.step_id,
                    actor=ctx.user_id, principal=ctx.agent_id, action=name,
                    target=args.get("id"), args_hash=_h(args), policy_version=policy.version,
                    decision=decision, credential_id=ctx.credential_id,
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
user, agent identity, tool, target id, argument hash, policy version, decision, approval id, credential id,
outcome, and a hash chain linking rows; it is append-only, written with an insert-only role, retained for
the two years, and contains no free text. The trace holds the prompt reference, model id, token counts
and tool results by reference; its payloads live in a blob store with a shorter retention and per-record
deletion. To prove "why", the audit row gives the prompt hash and registry version, which resolve to the
exact template text kept in the prompt registry (not PII) and the policy that allowed the call; the
customer-specific inputs are provable by hash even after the content is purged. To prove integrity,
recompute the chain. The identifiers are shared, so a row leads to the trace while it exists.

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
- [x] So the long-lived audit record carries no PII while still proving which exact arguments were used
- [ ] Because arguments are non-deterministic and cannot be stored
> The hash proves the identity of the arguments without holding their content; the content lives under a
> separate, shorter retention policy with per-record deletion.

### Q: A team samples traces at 10% to control cost. Which runs should be exempt from sampling?
- [ ] Runs shorter than one second
- [ ] Runs that completed successfully on the first attempt
- [x] Failed, denied and escalated runs, which are the ones investigations will ask about
- [ ] Runs from internal users only
> The purpose of a trace is to debug what went wrong; sampling out the failures defeats it. Successful
> runs are the safe place to save cost.
