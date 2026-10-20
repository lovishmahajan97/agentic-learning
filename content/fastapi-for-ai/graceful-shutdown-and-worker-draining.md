---
id: graceful-shutdown-and-worker-draining
module: fastapi-for-ai
name: Graceful shutdown & worker draining
kind: practice
difficulty: advanced
est_minutes: 15
related:
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/health-readiness-endpoints
  - fastapi-for-ai/server-sent-events
  - fastapi-for-ai/job-queues-and-durable-workers
  - fastapi-for-ai/cancellation-on-client-disconnect
  - node-ts-for-ai/graceful-shutdown
  - backend-fundamentals/kubernetes
sources:
  - Official docs: Kubernetes
  - Official docs: Uvicorn
  - Official docs: Gunicorn
---

## TL;DR
Graceful shutdown means that when a worker is told to stop (SIGTERM on every deploy, scale-down or node
drain), it stops receiving new traffic, lets in-flight requests finish, closes its resources, then exits.
AI services make this hard because a single request can stream tokens for minutes while orchestrators
give a default 30 seconds, so draining needs an explicit budget, a readiness flip, and a plan for work
that cannot finish in time.

## Why it exists
Processes are stopped far more often than they crash: every rolling deploy, autoscaler scale-down, spot
instance reclaim and node upgrade terminates workers that are busy. If a worker exits immediately, every
open request dies mid-flight. For a CRUD API that is a few failed 50 ms calls which clients retry. For an
LLM service it is a user watching an answer stop halfway after 40 seconds, an agent run losing several
paid tool calls, or a document-ingestion request that already spent money on embeddings and now has to
start over.

The naive approaches fail in opposite directions. Killing immediately loses work. "Wait for everything to
finish" with no bound blocks deploys forever when one client holds a stream open, and the orchestrator
eventually sends SIGKILL anyway, which loses the work *and* skips cleanup. A third trap is ordering: on
Kubernetes, the pod receiving SIGTERM and the load balancer removing it from its endpoints happen in
parallel, so a worker that stops accepting connections at once still receives new requests for a short
window and refuses them. Graceful shutdown is the protocol that sequences these steps deliberately.

## How it works
### The signals
- **SIGTERM:** "please stop". Uvicorn and Gunicorn treat it as a graceful shutdown.
- **SIGKILL:** sent by the orchestrator when the grace period expires. It cannot be caught; nothing after
  it runs, including lifespan shutdown.
- On Kubernetes, `terminationGracePeriodSeconds` (default 30) is the whole budget from SIGTERM to SIGKILL,
  and it includes the `preStop` hook.

### The sequence
1. **Stop new traffic first.** A `preStop` hook (often a short sleep of a few seconds) delays SIGTERM so
   endpoint controllers and load balancers stop routing to the pod before the process stops listening.
   Kubernetes removes a terminating pod from Service endpoints on its own, but an external balancer
   that polls `/readyz` does not know, and the app itself does not learn about SIGTERM until lifespan
   shutdown, which runs only *after* draining. So the preStop hook can also tell the app it is draining
   (for example a local `POST /internal/drain` that sets a flag): readiness then fails, and long streams
   know a cutoff is coming.
2. **Stop accepting, keep serving.** On SIGTERM, Uvicorn closes the listening socket, closes idle
   keep-alive connections, and waits for in-flight requests to finish. `--timeout-graceful-shutdown`
   bounds that wait; without it Uvicorn waits indefinitely and leaves the bound to SIGKILL. Under
   Gunicorn, the master forwards the signal to workers and `--graceful-timeout` bounds their drain.
3. **Run lifespan shutdown.** After requests drain, the code after `yield` in lifespan runs: close the
   HTTP client pool, dispose the DB engine, flush telemetry exporters, release GPU memory.
4. **Exit before SIGKILL**, with margin, so step 3 actually happens.

### The budget
```
terminationGracePeriodSeconds ≥ preStop delay + longest request you intend to finish + cleanup time
```
Set the server's own drain timeout to slightly less than the grace period minus preStop, so the server,
not SIGKILL, decides what gets cut off and lifespan cleanup still runs.

### Work that cannot finish
Some requests should not be waited for: a 10-minute agent run or a stream that the client keeps open.
Three strategies, in order of robustness:
- **Move it out of the request.** Long jobs go to a durable queue; the API returns a job id and the
  client polls or subscribes. The queue survives the worker.
- **Make it resumable.** Persist progress (tokens generated, steps completed) and let the client reconnect
  with an id, so a cut stream can continue elsewhere.
- **End it cleanly.** Once the drain flag is set, a stream that will not finish inside the budget sends
  a final event that says "reconnect" and closes, so the client retries rather than rendering a truncated
  answer. This must happen before the server's drain timeout; after that the connection is just closed.

### Containers
The process must actually receive the signal. A Dockerfile `CMD` in shell form runs the server under
`/bin/sh`, which by default does not forward SIGTERM to the child, so the server is SIGKILLed after the
grace period with no drain at all. Use the exec form (`CMD ["uvicorn", ...]`) or an init process.

## Example
A timeline for an SSE chat service whose answers take up to 50 seconds, on a rolling deploy.

Defaults first: grace 30 s, no preStop, Uvicorn waits indefinitely. At t=0 SIGTERM arrives while 12
streams are open, the longest 10 s into a 50 s answer. Uvicorn stops listening, but the endpoint update
lags, so a few new requests in the first second get connection refused. At t=30 the kubelet sends
SIGKILL: streams with more than 30 s remaining are cut mid-sentence, and lifespan shutdown never runs, so
buffered spans in the telemetry exporter are lost too, which is why the incident is hard to see.

Fixed configuration (illustrative values):

```yaml
spec:
  terminationGracePeriodSeconds: 75
  containers:
    - name: api
      command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
                "--timeout-graceful-shutdown", "60"]
      lifecycle:
        preStop:
          exec:
            command: ["sh", "-c", "curl -s -X POST localhost:8000/internal/drain; sleep 5"]
```

Now: t=0 preStop sets the drain flag (readiness now returns 503) and the pod is removed from
endpoints while it still serves. t=5 SIGTERM reaches Uvicorn (exec form, PID 1), which stops accepting
and drains. Streams that started before t=0 finish by about t=50. A rare outlier still running near its
deadline sees the flag and sends `event: reconnect` before t=65, when Uvicorn would close it anyway. Lifespan closes clients and flushes telemetry by about t=67, eight seconds before SIGKILL.
Answers longer than the budget are the signal to move that workload to a job queue.

## Trade-offs
- A long grace period lets long streams finish, but it slows rollouts and scale-downs proportionally;
  past a minute or two, durable jobs or resumable streams are cheaper than waiting.
- A `preStop` sleep prevents refused connections during endpoint propagation, but it adds its duration
  to every pod termination and eats into the grace budget.
- Bounding the server's drain timeout guarantees cleanup runs, but it deliberately cuts the longest
  requests; flip to an unbounded wait only for workers with no streaming or long requests.
- Making streams resumable avoids lost work on any shutdown, but it requires persisted generation state
  and client support, which is only worth building when long generations are core to the product.

## Pitfalls
- Using shell-form `CMD` in the Dockerfile; the symptom is every shutdown taking exactly the grace period
  and ending in SIGKILL, with no shutdown log lines from the app.
- Leaving the server drain unbounded with a grace period shorter than the longest stream; the symptom is
  lifespan cleanup that never runs, so telemetry from the last seconds is missing.
- Skipping the preStop delay; the symptom is a burst of connection-refused or 502 errors at the start of
  every rolling deploy, even though shutdown itself looks clean.
- Leaving readiness green during shutdown behind a balancer that polls it; the symptom is new requests
  landing on a draining worker and failing or being cut a few seconds later.
- Treating agent runs as ordinary requests; the symptom is half-completed side effects (tickets created,
  emails sent) with no record, because the run was killed between tool calls.

## When to use
Every production service needs a deliberate shutdown, because deploys guarantee terminations. Calculate
the budget from your actual request-duration distribution: if p99 is under a few seconds, defaults plus a
preStop sleep and exec-form CMD are enough. If you stream LLM output, size the grace period to the longest
answer you will wait for and bound the server's drain below it. If requests can run for minutes or
perform side effects, move them to durable workers. Watch for 5xx and truncated streams around deploy
times; in interviews, walking through the SIGTERM-to-SIGKILL timeline is the expected depth.

## Role lens
- **Engineer:** Uses exec-form CMD, sets the drain timeout, flips readiness on shutdown, and writes the
  stream cleanup path that tells clients to reconnect.
- **Architect:** Decides which workloads may live in the request path at all, and sets grace budgets
  that trade rollout speed against interrupted work.
- **FDE:** Diagnoses "answers cut off during deploys" in the customer's cluster by reading their pod spec,
  load balancer drain settings and container entrypoint.

## Check yourself
**Q:** After moving to Kubernetes, users report that roughly once a day chat answers stop mid-sentence,
always around deploy time, and the traces for those requests are missing. Explain the likely mechanism and
the fix.
**A:** Deploys terminate pods with SIGTERM and a 30-second default grace period. Streams longer than the
remaining budget are still open when SIGKILL arrives, so they are cut mid-token; SIGKILL also prevents
lifespan shutdown, so the telemetry exporter never flushes its buffered spans, which is why traces are
missing. I would check the entrypoint uses exec form so the signal reaches Uvicorn, add a short preStop
sleep for endpoint propagation, raise `terminationGracePeriodSeconds` to cover the longest answer plus
cleanup, bound Uvicorn's `--timeout-graceful-shutdown` below that, and send a reconnect event when a stream
must be cut. Very long generations should become resumable or move to a job queue.

## Quiz
### Q: Why do new requests sometimes fail at the very start of a rolling deploy even though the app drains correctly?
- [ ] Uvicorn rejects requests until lifespan startup completes
- [x] SIGTERM and endpoint removal happen in parallel, so traffic still arrives after the server stops listening
- [ ] Kubernetes sends SIGKILL before SIGTERM on rolling updates
- [ ] Readiness probes are disabled during deploys
> Endpoint and load balancer updates propagate asynchronously after the pod starts terminating. A short
> preStop delay keeps the server listening until traffic has been rerouted.

### Q: What happens to code after `yield` in lifespan if the grace period expires while requests are still in flight?
- [ ] It runs immediately, in parallel with the remaining requests
- [ ] It is deferred until the next pod starts
- [x] It never runs, because SIGKILL cannot be caught
- [ ] It runs, but only the first line executes
> SIGKILL terminates the process without running any handlers, so lifespan cleanup is skipped. Bounding the
> server's own drain below the grace period is what preserves cleanup.

### Q: A service streams answers lasting up to 3 minutes. Which approach is most robust for deploys?
- [ ] Set `terminationGracePeriodSeconds` to one hour
- [ ] Disable rolling deploys during business hours
- [x] Make generation resumable or run it as a durable job the client subscribes to
- [ ] Increase Gunicorn's `--timeout` to 300 seconds
> Very long grace periods stall every rollout and scale-down, and still fail on node loss. Durable or
> resumable work survives any termination. Gunicorn's `--timeout` is the heartbeat limit, not a drain setting.
