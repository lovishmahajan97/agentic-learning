---
id: graceful-shutdown
module: node-ts-for-ai
name: Graceful shutdown
kind: practice
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - deployment/health-checks
  - fastapi-for-ai/health-readiness-endpoints
  - backend-fundamentals/kubernetes
  - node-ts-for-ai/bullmq-and-durable-jobs
  - node-ts-for-ai/abortcontroller-and-cancellation
  - node-ts-for-ai/docker-ci-cd-deployment
  - node-ts-for-ai/opentelemetry-instrumentation
sources:
  - Official docs: Node.js (process, http)
  - Official docs: Kubernetes — Pod Lifecycle
  - Official docs: BullMQ
---

## TL;DR
Processes are killed constantly: deploys, autoscaling, node drains, out-of-memory kills. Graceful
shutdown is the sequence a Node service runs on SIGTERM so nothing in flight is lost: stop advertising
readiness, stop accepting new requests and jobs, let in-flight work finish within a deadline shorter
than the orchestrator's grace period, close queues and pools, flush telemetry, then exit. Without it, every deploy
truncates streamed LLM responses, re-runs queue jobs and leaks half-done writes.

## Why it exists
Kubernetes replaces a pod by sending SIGTERM and, after `terminationGracePeriodSeconds` (30 s by
default), SIGKILL. `docker stop` does the same with a 10 s default. In a Node process with no handler,
SIGTERM either terminates it immediately or, when Node is PID 1 in the container, is ignored outright
(the kernel drops signals that PID 1 has no handler for), so the pod sits until SIGKILL. Either way
nothing drains: an SSE stream halfway through a 2,000-token answer closes without a
final event, a BullMQ worker mid-way through a 500-chunk embedding loses its place and the job stalls
and re-runs, a Postgres transaction is rolled back by the server when the socket drops, and the last
batch of OpenTelemetry spans, which are precisely the ones covering the deploy, is never exported.

The naive fix, `process.on('SIGTERM', () => server.close(() => process.exit(0)))`, is better and still
wrong in three ways. First, Kubernetes removes the pod from the Service's endpoints asynchronously with
sending SIGTERM, so for a short window new requests still arrive at a server that has stopped accepting;
you see connection errors at the load balancer on every deploy. Second, `server.close()` waits for every
open connection, and on older Node versions that includes idle keep-alive connections from the load
balancer, so the process can hang until SIGKILL. Third, closing the HTTP server says nothing about the
worker, the pool, the Redis client or the telemetry exporter, each of which has its own flush and close
semantics and must be closed in the right order. Graceful shutdown is the explicit choreography of all
of that against a deadline.

## How it works
### What the orchestrator does
1. The pod is marked terminating; endpoint removal propagates to kube-proxy and ingress controllers
   with a delay.
2. The container's `preStop` hook runs, if any; a common one is `sleep 5` to let endpoint removal
   propagate before the signal arrives (the exec form needs a `sleep` binary in the image; recent
   Kubernetes versions also offer a built-in `sleep` preStop action). The hook's time counts against
   the grace period.
3. SIGTERM is sent to PID 1 of the container.
4. After the grace period, SIGKILL.

Two consequences: the process must actually receive the signal (use the exec form
`CMD ["node", "dist/server.js"]`; the shell form `CMD npm start` leaves a shell as PID 1 that does not
forward signals, and `docker run --init` or `tini` is the safe default), and the application's own
deadline must be shorter than the grace period minus the `preStop` sleep, *because* otherwise SIGKILL
wins and the work is lost anyway.

### The sequence inside Node
1. **Flip readiness.** Make `/ready` return 503 immediately so any health-checked balancer stops
   routing; keep `/live` healthy, *because* a failing liveness probe during shutdown would make the
   orchestrator restart the container mid-drain.
2. **Stop intake.** `server.close()` stops accepting connections. Since Node 19, `close()` also closes
   idle keep-alive connections; on older versions call `server.closeIdleConnections()` yourself. Keep
   `keepAliveTimeout` short so balancers reconnect elsewhere. For BullMQ, `await worker.close()` stops
   taking new jobs and waits for active ones; `worker.pause()` is the softer form. Cancel scheduled
   timers and polling loops.
3. **Drain in flight.** Requests complete naturally. Streaming responses need a product decision:
   finish them if they will end within the deadline, otherwise send a final event that tells the client
   to resume or retry, and abort the upstream model call with an `AbortController` so you stop paying
   for tokens nobody will receive.
4. **Close resources in reverse dependency order.** Queue and Redis clients (`queue.close()`,
   `redis.quit()`, which waits for pending replies, unlike `disconnect()`), then `pool.end()`, which
   must come after every worker and request that might still query. Telemetry goes last
   (`sdk.shutdown()` flushes the batch span processor): the exporter depends on none of the other
   resources, and flushing last also exports the spans and errors of the close steps themselves. Give
   the flush its own short timeout.
5. **Exit explicitly.** Once a SIGTERM listener is registered, Node no longer exits on the signal by
   default, so call `process.exit(0)` at the end (or set `process.exitCode` and let the loop drain).
   Wrap the sequence in a deadline timer (`setTimeout(..., 20_000).unref()` for a 30 s grace period with a 5 s `preStop`) that exits non-zero if
   draining hangs, *because* a hung shutdown wastes the whole grace period and still ends in SIGKILL.
6. **Second signal means force.** A second SIGTERM or SIGINT during draining should exit immediately;
   operators expect it.

### Crashes
`uncaughtException` and `unhandledRejection` leave the process in an unknown state. Log, then either
flush telemetry and exit non-zero within a second or two, or run the normal drain under a short
deadline (as the example does); either way stop accepting work and exit non-zero, never keep serving. The orchestrator
starts a fresh instance, and the queue's stall detection re-runs the job, which is why jobs must be
idempotent in the first place.

### What "done" looks like
Deploys produce zero 5xx at the load balancer, zero stalled jobs, no truncated streams in client logs,
and the last trace before shutdown is present in the backend.

## Example
A shutdown orchestrator for an API process that also runs a BullMQ worker. Readiness fails first, HTTP
intake and the worker close in parallel, then Redis and Postgres close, and telemetry is flushed last
under its own 3-second cap. The 20-second deadline leaves margin under a 30-second grace period minus a
5-second `preStop` sleep; the timer is `unref()`'d so it cannot keep an otherwise-finished process
alive, and a second signal forces exit.

```ts
import http from "node:http";
import { Pool } from "pg";
import { Worker } from "bullmq";
import { sdk } from "./instrumentation";          // the OpenTelemetry NodeSDK instance
import { redis } from "./redis";                  // an ioredis client

let ready = true;
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const server = http.createServer((req, res) => {
  if (req.url === "/ready") { res.writeHead(ready ? 200 : 503); return res.end(); }
  if (req.url === "/live")  { res.writeHead(200); return res.end(); }
  handle(req, res);                                 // normal routing
});
server.keepAliveTimeout = 5_000;
const workerConn = redis.duplicate({ maxRetriesPerRequest: null });   // BullMQ workers require null
const worker = new Worker("embed", processEmbed, { connection: workerConn, concurrency: 4 });
server.listen(8080);

let shuttingDown = false;
async function shutdown(signal: string, code = 0) {
  if (shuttingDown) { console.warn("second signal, forcing exit"); process.exit(1); }
  shuttingDown = true; ready = false;               // 1. fail readiness first
  const deadline = setTimeout(() => { console.error("drain timeout"); process.exit(1); }, 20_000).unref();
  console.log(`${signal}: draining`);
  try {
    await Promise.all([
      new Promise<void>((resolve) => server.close(() => resolve())),  // 2. stop intake (closes idle keep-alive on Node 19+)
      worker.close(),                                                   //    stop taking jobs, wait for active ones
    ]);
    await Promise.all([redis.quit(), workerConn.quit()]);              // 3. waits for pending replies
    await pool.end();                                                   //    only after nothing can still query
    await Promise.race([sdk.shutdown(), new Promise((r) => setTimeout(r, 3_000))]);  // 4. flush spans last, capped
    clearTimeout(deadline);
    process.exit(code);
  } catch (err) {
    console.error("shutdown error", err); process.exit(1);
  }
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT",  () => void shutdown("SIGINT"));
process.on("unhandledRejection", (e) => { console.error(e); void shutdown("unhandledRejection", 1); });
```

## Trade-offs
- Draining in-flight work avoids lost requests and duplicate jobs, but every second of drain delays the rollout; with long LLM streams the deadline has to be a product decision (finish, or cut with a resume token), not just a timeout value.
- Failing readiness before closing the server gives balancers time to stop routing, but only if they probe frequently; where endpoint removal is slower than the probe interval, a `preStop` sleep is the cruder but more reliable fix.
- A hard internal deadline guarantees the process exits before SIGKILL and reports a non-zero code, but it cuts whatever is still running; keep it comfortably below the grace period and raise the grace period for worker pods rather than removing the deadline.
- Closing BullMQ workers gracefully prevents stalls on every deploy, but with `concurrency: 10` and ten-minute jobs no sensible grace period suffices; the fix is per-chunk checkpoints so a re-run resumes, not a longer wait.
- Flushing telemetry on shutdown preserves the most interesting spans, but a slow or unreachable collector can eat the whole deadline; give the flush its own short timeout.

## Pitfalls
- Shell-form `CMD npm start` or a wrapper script as PID 1: the symptom is containers that always take the full grace period and die by SIGKILL, with no shutdown log line ever appearing.
- Registering a SIGTERM handler that forgets to call `process.exit`: the symptom is the process lingering after draining because an open handle (a timer, a Redis socket) keeps the event loop alive.
- `server.close()` on Node 18 or older without `closeIdleConnections()`: the symptom is shutdown hanging until SIGKILL while the balancer holds idle keep-alive sockets.
- Liveness probe wired to the same flag as readiness: the symptom is the container being restarted by the orchestrator in the middle of its own drain.
- Closing the Postgres pool before the worker finishes: the symptom is "Cannot use a pool after calling end on the pool" errors in the last jobs of every deploy.
- Deadline timer not `unref()`'d: the symptom is a process that finished draining but waits the full timeout before exiting.

## When to use
Every long-running Node process needs this: API servers, BullMQ workers, MCP servers, schedulers. Do it
on day one of containerising the service, because the symptoms (5xx spikes, duplicate jobs, truncated
streams, missing traces) are easy to blame on everything else. Set the internal deadline from the
orchestrator's grace period and the longest acceptable in-flight unit; raise
`terminationGracePeriodSeconds` for worker deployments with long jobs, and shorten jobs with checkpoints
where that is not enough. Measure 5xx and connection errors during rollouts, stalled job counts, and
whether shutdown logs show the sequence completing. In interviews, walk through the SIGTERM-to-SIGKILL
timeline and the ordering of readiness, intake, drain, flush, close, exit.

## Role lens
- **Engineer:** Implements the shutdown orchestrator, wires readiness to it, closes the worker, telemetry, Redis and pool in order, and tests it by sending SIGTERM to a loaded local process.
- **Architect:** Sets grace periods, `preStop` policy and the rule for in-flight streams across services, and makes idempotent, checkpointed jobs a requirement so shutdown can afford to be strict.
- **FDE:** Verifies the customer's cluster settings (grace period, probe intervals, PID 1 handling in their base image) and demonstrates a deploy with zero dropped requests in their environment.

## Check yourself
**Q:** After every deploy your Node API shows a burst of 502s at the load balancer and BullMQ reports a
handful of stalled jobs, even though you added `process.on('SIGTERM', () => server.close(() =>
process.exit(0)))`. Explain both symptoms and the full fix.
**A:** The 502s come from the window between SIGTERM and the balancer learning the pod is gone: the
server stopped accepting instantly while traffic was still routed to it, so fail readiness first and
either wait for probes to notice or add a short `preStop` sleep, and keep the liveness probe passing.
The stalled jobs come from the worker never being closed: `server.close` only drains HTTP, so
`process.exit` killed active jobs whose locks then expired and were re-run by the stall checker. The
full sequence is: readiness to 503, `server.close()` (closing idle keep-alive connections) in parallel
with `await worker.close()`, `redis.quit()`, `pool.end()`, flush OpenTelemetry, then `process.exit(0)`,
all under a deadline below the grace period, with a non-zero exit if the deadline fires. Verify the
signal reaches Node at all: exec-form `CMD` so Node (or a forwarding init such as `tini`) is PID 1,
not a shell.

## Quiz
### Q: Why should readiness be failed before calling `server.close()` rather than relying on `close()` alone?
- [ ] Because `server.close()` is synchronous and blocks the event loop
- [x] Endpoint removal propagates asynchronously, so new connections may still arrive after the server stops accepting them, producing balancer errors
- [ ] Because Kubernetes only sends SIGTERM after readiness fails
- [ ] Because `server.close()` cannot be called while the readiness probe is passing
> There is a window where traffic is still routed to a terminating pod; signalling not-ready (and optionally a `preStop` sleep) lets routing catch up before the listener closes, which is what removes the 502 burst.

### Q: Once you register a `process.on('SIGTERM', ...)` handler in Node, what changes?
- [ ] Node forwards the signal to child processes automatically
- [ ] The grace period is extended by the orchestrator
- [x] The default exit on SIGTERM is disabled, so the process keeps running until you exit explicitly or the event loop empties
- [ ] Subsequent SIGTERMs are ignored
> Installing a signal listener replaces the default behaviour; if the handler drains but never calls `process.exit` and some handle keeps the loop alive, the process lingers until SIGKILL.

### Q: After readiness has been flipped to 503, which shutdown order is right?
- [ ] End the Postgres pool so no new writes can start, then close the worker, then flush telemetry, then exit
- [x] Close the HTTP server and the worker, then Redis and the Postgres pool, then flush telemetry, then exit
- [ ] Flush telemetry, then exit and let the orchestrator close the remaining sockets
- [ ] Close the worker, then exit, because the stall checker will finish any remaining jobs
> The pool must outlive every request and job that may still query, so it closes only after the server and worker have drained; ending it first produces "pool after end" errors in the last jobs. Telemetry goes last, under a short cap, so the close steps' own spans are exported before exit.
