---
id: health-checks
module: deployment
name: Health checks
kind: practice
difficulty: core
est_minutes: 14
related:
  - fastapi-for-ai/health-readiness-endpoints
  - backend-fundamentals/kubernetes
  - observability/monitoring
  - reliability/backpressure-and-load-shedding
  - reliability/slas-slos
  - deployment/runbooks
  - deployment/rollout-criteria-and-rollback-triggers
sources:
  - Official docs: Kubernetes (liveness, readiness and startup probes)
---

## TL;DR
Health checks are endpoints or probes that let infrastructure and operators ask "should this instance
get traffic, and should it be restarted?". They answer different questions, so they are separate checks:
liveness (is the process stuck, restart it), readiness (can it serve right now, route around it if not),
startup (has slow initialisation such as loading a model finished) and end-to-end synthetic checks (does
the whole system produce a correct answer). Mixing these up turns a degraded dependency into a total
outage.

## Why it exists
A load balancer or orchestrator cannot tell a healthy instance from a broken one unless the instance
tells it. Without checks, traffic keeps flowing to a process that has deadlocked, run out of GPU memory,
or is still loading a multi-gigabyte model, and users see errors while every infrastructure dashboard
shows "running". Rolling deployments also depend on them: a new version is only safe to shift traffic to
once it says it is ready.

The naive check is a single `/health` endpoint that returns 200 if the web server responds. It fails in
both directions. It is too shallow, because a server can answer HTTP while its model failed to load or
its connection pool is exhausted. And when teams make it deeper by checking every dependency (database,
vector store, LLM provider) and wire it to the restart probe, it becomes dangerous: if the shared LLM
provider has a blip, every instance fails its check at once, the orchestrator restarts all of them, and
a brief upstream problem becomes a fleet-wide restart storm with cold model loads on top.

The fix is to recognise that "healthy" is not one question. Whether to restart, whether to route traffic
and whether the product works end to end have different answers and different consequences, so each
gets its own check with its own scope.

## How it works
### Liveness: should this process be restarted?
Liveness answers only "is this process irrecoverably stuck?". It should check things a restart would fix:
the event loop responds, a worker thread is not deadlocked. It must **not** check external dependencies,
*because* restarting your service does not fix someone else's outage and restarts at fleet scale add
load exactly when the system is weakest. In Kubernetes, repeated liveness failures make the kubelet
restart the container.

### Readiness: should this instance receive traffic right now?
Readiness answers "can I serve a request successfully at this moment?". It checks local conditions: the
model is loaded, the GPU is usable, the instance is not overloaded, the instance is not shutting down. In
Kubernetes a failing readiness probe removes the pod from the Service's endpoints without restarting it,
so traffic flows to other replicas. Two design choices matter here:
- **Shared dependencies are risky in readiness.** If every replica checks the same LLM provider and it
  fails, every replica goes unready and the service has zero endpoints, so even a fallback path cannot be
  served. Prefer to keep serving and degrade (fallback model, cached answer, clear error) and report the
  dependency's state through metrics and alerts instead.
- **Drain before exiting.** On shutdown the instance must stop receiving new requests, then finish
  in-flight ones, *because* long LLM streams cut mid-answer are user-visible errors during every deploy.
  Behind a plain load balancer that means failing readiness first and waiting before exit. Kubernetes
  removes a terminating pod from endpoints itself, but asynchronously, so a short pre-stop delay plus a
  graceful-shutdown timeout longer than the longest stream is the usual pattern.

### Startup: has slow initialisation finished?
Loading model weights, warming a GPU or building an in-memory index can take minutes. A startup probe
covers that window; in Kubernetes, liveness and readiness probes are not run until the startup probe
succeeds. Without it, teams either set a huge liveness delay (slow to detect real hangs later) or watch
pods get killed mid-load in a restart loop.

### Synthetic end-to-end checks
Probes tell infrastructure about one instance. They do not tell you the product works. A synthetic check
runs from outside on a schedule: send a fixed golden request through the real entry point (auth,
retrieval, model, guardrails) and assert on the answer, for example that it cites the expected document.
This catches failures probes never see: a stale index, an expired credential to a customer system, a
model silently changed behind an alias. Synthetic results feed alerts and dashboards, not restarts.

### Making checks cheap and honest
Probes run often, so they must be fast and must not call the LLM. Deeper checks run on a background
schedule and the endpoint returns the cached result, *because* a probe that waits on a slow dependency
times out and reports failure for the wrong reason. Return a body explaining which condition failed, so
operators following a runbook can see why an instance is unready.

## Example
A FastAPI service that loads a model at startup, separates liveness from readiness and reports
overload. The model loader is a stand-in for real initialisation.

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response

state = {"model": None, "in_flight": 0}
MAX_IN_FLIGHT = 32                       # illustrative per-replica concurrency budget

async def load_model():
    await asyncio.sleep(5)               # stand-in for loading weights onto the GPU
    return object()

@asynccontextmanager
async def lifespan(app):
    state["model"] = await load_model()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/livez")                      # restart only if the process itself is stuck
async def livez():
    return {"ok": True}                 # reaching this handler proves the event loop runs

@app.get("/startupz")                   # gates liveness and readiness until the model is loaded
async def startupz(resp: Response):
    if state["model"] is None:
        resp.status_code = 503
    return {"model_loaded": state["model"] is not None}

@app.get("/readyz")                     # route traffic here only if a request would succeed now
async def readyz(resp: Response):
    reasons = []
    if state["model"] is None: reasons.append("model not loaded")
    if state["in_flight"] >= MAX_IN_FLIGHT: reasons.append("at capacity")
    if reasons:
        resp.status_code = 503
    return {"ready": not reasons, "reasons": reasons}
```

Note that no check calls the LLM provider or the vector store. Their health is reported by metrics and a
separate synthetic check, so an upstream outage degrades the service instead of emptying the load
balancer. A real deployment increments and decrements `in_flight` around each generation (including
the whole duration of a streamed response) and configures draining in the platform, for example a
Kubernetes pre-stop delay, rather than overriding the server's own signal handling.

## Trade-offs
- Deep readiness checks catch more broken instances, but couple every replica to shared dependencies; the
  balance flips toward deep checks only for dependencies that are per-instance, such as a local GPU.
- Aggressive probe intervals and low failure thresholds detect problems fast, but cause flapping and
  needless restarts under load spikes; tolerate a few consecutive failures before acting.
- Readiness based on load ("at capacity") sheds traffic gracefully, but if every replica hits capacity at
  once the service has no endpoints; pair it with autoscaling and a front-door rate limit.
- Synthetic checks prove end-to-end correctness, but each one costs a real model call and can drift from
  real usage; keep them few, cheap and representative of the critical path.

## Pitfalls
- Checking the LLM provider or database in the liveness probe; the symptom is every pod restarting at
  once during a short upstream incident, followed by slow cold starts.
- No startup probe for a service that loads large weights; the symptom is pods killed by liveness during
  model loading and stuck in a restart loop.
- Exiting without draining, so new requests still arrive during shutdown; the symptom is streamed responses cut off mid-answer on
  every deploy or scale-down.
- Probes that are only shallow HTTP pings; the symptom is instances that report healthy while every
  request fails because the model never loaded or a GPU is in an error state.
- Treating green probes as proof the product works; the symptom is all-green dashboards while users get
  wrong answers from a stale index, which only a synthetic end-to-end check would have caught.

## When to use
Every deployed service needs liveness and readiness; any service with slow initialisation needs a startup
probe; any customer-facing AI system needs at least one synthetic end-to-end check on its critical path.
Decide check scope by asking what action the check triggers: restart, route away, or alert. Watch probe
failure counts, restart counts, ready replica count and synthetic pass rate, and link each health alert
to a runbook. In interviews, the expected depth is explaining why liveness must not include dependencies,
how readiness interacts with draining and rolling deploys, and why a probe cannot tell you answers are
correct.

## Role lens
- **Engineer:** Implements separate liveness, readiness and startup endpoints, keeps them fast and
  dependency-free, and writes the golden-request synthetic check with a meaningful assertion.
- **Architect:** Decides which failures cause restarts, which cause routing changes and which only alert,
  so a shared dependency outage degrades the service rather than taking it down.
- **FDE:** Adapts checks to the customer's load balancers and monitoring, which may only support one
  endpoint, and makes synthetic checks run inside their network against their real integrations.

## Check yourself
**Q:** After a 90-second outage at the LLM provider, the whole fleet restarted and the service was down
for twelve minutes. What went wrong in the health check design, and how would you change it?
**A:** The provider was almost certainly checked by the liveness probe, or by readiness with no fallback.
When the provider failed, every replica failed at once; liveness restarted them all, and each restart
meant reloading model weights or warming caches, so recovery took far longer than the upstream outage.
I would make liveness local only (process responsive), keep readiness to per-instance conditions such as
model loaded, capacity and draining, and report provider health through metrics, alerts and a synthetic
check. The request path should degrade with a fallback model or clear error, and add a startup probe so
slow loads are not killed.

## Quiz
### Q: What happens in Kubernetes when a pod fails its readiness probe but passes liveness?
- [ ] The container is restarted
- [x] The pod is removed from Service endpoints and stops receiving traffic, without a restart
- [ ] The pod is rescheduled onto another node
- [ ] Nothing, readiness is informational only
> Readiness controls routing: an unready pod is taken out of the Service's endpoints until it passes
> again. Restarts are driven by liveness failures, and rescheduling is a scheduler decision, not a probe result.

### Q: Why should a liveness probe avoid checking the external LLM provider?
- [ ] Provider calls are too expensive to run every few seconds
- [ ] Liveness probes cannot make outbound network calls
- [ ] The provider's own status page already reports its health
- [x] Restarting your instances cannot fix the provider, and simultaneous restarts turn a brief upstream issue into a fleet-wide outage
> Cost is a real concern, but the decisive reason is the action liveness triggers. A restart only helps
> for failures inside the process; for shared dependencies it multiplies the damage.

### Q: Which failure would a synthetic end-to-end check catch that liveness and readiness probes would not?
- [ ] A deadlocked worker thread
- [ ] A pod that has not finished loading model weights
- [x] A retrieval index that stopped updating, so answers cite outdated documents
- [ ] A pod that is shutting down
> Probes check one instance's local condition. A stale index leaves every instance healthy while
> answers are wrong, which only a check asserting on real end-to-end output can detect.
