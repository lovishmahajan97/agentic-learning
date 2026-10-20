---
id: health-readiness-endpoints
module: fastapi-for-ai
name: Health / readiness endpoints
kind: pattern
difficulty: core
est_minutes: 13
related:
  - fastapi-for-ai/lifespan
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - fastapi-for-ai/uvicorn-gunicorn-workers
  - fastapi-for-ai/async-endpoints
  - backend-fundamentals/kubernetes
  - deployment/health-checks
  - system-design/graceful-degradation
sources:
  - Official docs: Kubernetes
  - Official docs: FastAPI
---

## TL;DR
A liveness endpoint answers "is this process stuck and worth restarting?"; a readiness endpoint answers
"should this instance receive traffic right now?"; a startup probe gives slow starters (model loading)
time before liveness counts. They are separate because the right reaction differs: restarting fixes a
deadlock but makes a database outage worse, so dependency checks belong in readiness, never liveness.

## Why it exists
Load balancers and orchestrators need a cheap, automatic way to decide two things about each instance:
send it requests or not, and kill it or not. Without health endpoints they can only see whether the port
accepts TCP connections, which stays true while the event loop is wedged, while a 4 GB model is still
loading, or after the worker lost its database pool. Users get timeouts from instances that look healthy.

The naive fix, one `/health` that checks everything (DB, Redis, vector store, LLM provider) and is used
for both decisions, is worse than none. When the shared database has a 20-second blip, every replica's
health fails at once. As readiness, that removes all instances from rotation, turning a partial
degradation into a total outage. As liveness, it restarts every pod simultaneously; the restarts reload
models and reopen connection pools, stampeding the database as it recovers, and the service stays down
longer than the original blip. AI services add a third failure: a model that takes 90 seconds to load
gets killed by a liveness probe at 30 seconds, forever, in a crash loop. Separate probes with separate
semantics prevent all three.

## How it works
### Three questions, three probes
- **Liveness (`/livez`):** can this process make progress? It should check only things that a restart
  would fix: the event loop is responsive, the worker is not deadlocked. A trivial `async def` that
  returns 200 is a good liveness check, because if the event loop is blocked it cannot answer, which is
  exactly the condition you want to detect. Failure means: restart the container.
- **Readiness (`/readyz`):** should traffic come here now? It reflects local state the instance needs to
  serve: lifespan finished, model loaded, connection pools created, not draining for shutdown. Failure
  means: stop routing new requests, but leave the process running so it can recover.
- **Startup:** has initialisation finished? While the startup probe has not yet succeeded, Kubernetes does
  not run liveness or readiness, so a slow model load is not mistaken for a hang. Size it as
  `failureThreshold × periodSeconds ≥ worst-case startup time`.

### Kubernetes mechanics worth knowing
Each probe has `periodSeconds`, `timeoutSeconds` and `failureThreshold`; the defaults are 10 s, 1 s and 3.
So with defaults a liveness failure triggers a restart after roughly 30 seconds of consecutive failures,
and a probe that takes longer than 1 second counts as failed. That second fact matters: a readiness check
that awaits a remote dependency inherits that dependency's latency.

### What readiness should check
Prefer state over calls. Lifespan sets flags (`model_loaded`, `pools_ready`) and the probe reads them in
microseconds. If you check a dependency, check one this instance *alone* can lose (its own pool's ability
to hand out a connection), with a tight timeout and a cached result, so probes do not themselves become
load on the database. Shared external dependencies, especially the LLM provider, should usually not gate
readiness: if the provider is down for everyone, removing every replica removes your ability to serve
cached answers, fallback models or a clear error message.

### Deep health for humans
Keep a separate, authenticated `/health/details` that reports each dependency's status and latency for
dashboards and on-call, without wiring it to any automated restart or routing decision.

## Example
A service that loads an embedding model in lifespan, with liveness, readiness and a draining flag.

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response

class State:
    model = None
    pools_ready = False
    draining = False

state = State()

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.model = await asyncio.to_thread(load_embedding_model)   # ~60-90 s; off the event loop
    state.pools_ready = True
    yield
    state.draining = True            # in case anything polls during cleanup

app = FastAPI(lifespan=lifespan)

@app.get("/livez", include_in_schema=False)
async def livez():
    return {"ok": True}              # answers only if the event loop is responsive

@app.get("/readyz", include_in_schema=False)
async def readyz(response: Response):
    ready = state.model is not None and state.pools_ready and not state.draining
    response.status_code = 200 if ready else 503
    return {"model": state.model is not None, "pools": state.pools_ready, "draining": state.draining}

@app.post("/internal/drain", include_in_schema=False)     # called by preStop
async def drain():
    state.draining = True
    return {"draining": True}

def load_embedding_model():
    ...
```

Probe configuration to match: `startupProbe` on `/livez` with `periodSeconds: 5, failureThreshold: 30`
(150 s for model load), `livenessProbe` on `/livez` with defaults, `readinessProbe` on `/readyz` with
`periodSeconds: 5`. Note the model is loaded inside lifespan, so Uvicorn does not serve any route,
including `/livez`, until it finishes; that is why the startup probe must cover the whole load.

## Trade-offs
- Checking dependencies in readiness removes instances that genuinely cannot serve, but it couples all
  replicas to shared failures; it is right only for dependencies one instance can lose independently.
- A trivial liveness endpoint never restarts a pod for someone else's outage, but it misses subtle
  hangs such as a deadlocked thread pool; add a targeted check only for a hang you have actually observed.
- Short probe periods detect failure quickly, but every probe is a request; with many replicas and
  dependency checks, aggressive probing becomes measurable load on the things being checked.
- Failing readiness when the LLM provider is down protects users from slow errors, but it forfeits
  fallbacks and cached answers; flip to gating only when the instance truly has nothing useful to return.

## Pitfalls
- Using the same endpoint for liveness and readiness; the symptom is all pods restarting together during
  a database blip and a recovery slower than the outage itself.
- No startup probe for a slow model load; the symptom is a CrashLoopBackOff where each container is
  killed at about the same age, before it ever logs "model loaded".
- Liveness that calls the database or the provider; the symptom is restart storms correlated with
  upstream incidents, with restart counts climbing on every replica at once.
- Running synchronous inference on the event loop; the symptom is liveness timeouts and restarts under
  load only, because probes queue behind the blocking work, killing pods that were merely busy.
- Health routes going through auth, rate-limit or logging middleware; the symptom is probes failing with
  401 or 429, or logs dominated by probe requests every few seconds.

## When to use
Every deployed service behind a balancer or orchestrator needs at least liveness and readiness, and AI
services that load models need a startup probe too. Decide for each dependency whether losing it means
"this instance is broken" (readiness), "the whole system is degraded" (serve a fallback, alert, keep
routing) or "restart fixes it" (rare; liveness). Measure probe latency and failure counts, restarts per
pod, and time from deploy to ready. In interviews, the strong answer explains why dependency checks in
liveness cause cascading failures and why readiness is the lever for draining.

## Role lens
- **Engineer:** Implements `/livez` and `/readyz` from lifespan state, keeps them off auth and logging
  middleware, and moves model loading off the event loop.
- **Architect:** Decides which dependencies gate readiness versus trigger degraded modes, and sets probe
  timings against startup time and recovery behaviour.
- **FDE:** Translates the probes to whatever the customer's platform uses (an on-prem balancer, ECS,
  OpenShift) and tunes startup timing to their slower hardware.

## Check yourself
**Q:** Your RAG service's single `/health` endpoint checks Postgres, the vector database and the LLM
provider, and it is configured as both liveness and readiness. The provider has a 5-minute partial outage.
Describe what happens and how you would redesign.
**A:** Every replica's health fails at once. As readiness, all pods leave rotation, so users get balancer
errors instead of any degraded answer. As liveness, after about three failed probes every pod restarts;
restarts reload models and reopen pools, stampeding Postgres and the vector store just as traffic returns,
and the service may stay down after the provider recovers. Redesign: liveness becomes a trivial
event-loop check; readiness reads local lifespan state plus this instance's own pool health, with a tight
timeout; the provider is not probed at all but handled in the request path with timeouts, a fallback model
or a clear 503, plus an alert. A startup probe covers model load, and a separate authenticated details
endpoint serves dashboards.

## Quiz
### Q: Why should a liveness probe usually not check the database?
- [ ] Database queries cannot run inside async endpoints
- [x] Restarting the app does not fix a database outage, and simultaneous restarts amplify it
- [ ] Kubernetes forbids network calls in liveness probes
- [ ] Liveness probes run before the database driver is imported
> Liveness failure means restart. A shared database problem fails every replica's probe at once, so all
> pods restart together, reconnecting in a stampede, without fixing the cause.

### Q: A pod loads a model for 100 seconds and is killed repeatedly before it is ready. What is the fix?
- [ ] Increase the readiness probe's `failureThreshold`
- [ ] Remove the liveness probe entirely
- [x] Add a startup probe whose `failureThreshold × periodSeconds` exceeds the load time
- [ ] Load the model lazily on the first request instead
> While a startup probe is pending, liveness and readiness are not run, so slow initialisation is not
> mistaken for a hang. Lazy loading only moves the delay onto the first user's request.

### Q: What should `/readyz` return while a worker is draining for shutdown?
- [ ] 200, because the process is still alive
- [x] 503, so balancers that poll readiness stop sending new requests while in-flight ones finish
- [ ] 500, so the orchestrator restarts the pod faster
- [ ] A redirect to another replica
> Draining means "alive but should not get new traffic", which is exactly readiness semantics. In-flight
> requests continue; a 500 or restart would cut them off.
