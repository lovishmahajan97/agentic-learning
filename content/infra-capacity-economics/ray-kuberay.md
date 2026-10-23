---
id: ray-kuberay
module: infra-capacity-economics
name: Ray / KubeRay
kind: tool
difficulty: advanced
est_minutes: 18
status: typed
lesson_type: decision
agent_relevance: useful
related:
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/inference-clusters
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - backend-fundamentals/kubernetes
  - serving-inference/model-serving
  - serving-inference/tensor-parallelism-at-inference
  - training-adaptation/distributed-training
sources:
  - Moritz et al., 2018 — Ray: A Distributed Framework for Emerging AI Applications
  - Official docs: Ray
  - Official docs: KubeRay
---

## TL;DR
Ray turns Python functions and classes into tasks and stateful GPU actors scheduled by declared
resources; KubeRay runs them as Kubernetes custom resources. Agent teams use it to embed RAG corpora, fan
out agent evals, and serve models and rerankers behind tools.

## For agent builders
- **Why it matters for agents.** The agent loop itself is light; the heavy jobs around it are not.
  Re-embedding a knowledge base, running a 500-task eval suite, or hosting a reranker for a retrieval
  tool all need many workers, resident GPU models and back-pressure, which is exactly Ray's shape.
- **Where it shows up in your stack.** Ray Data for RAG ingestion and embedding, Ray tasks for parallel
  agent evals and trajectory generation, Ray Serve for model, embedder and reranker endpoints your tools
  call, and vLLM's multi-node mode, which uses Ray underneath.
- **What you control.** `num_gpus` per actor, actor count, retry policy (`max_retries`, `max_restarts`),
  worker-group bounds (`minReplicas`, `maxReplicas`), and whether a cluster is ephemeral (RayJob) or
  long-lived (RayService).
- **What you can skip.** RL libraries, GCS internals and lineage-reconstruction details. Know tasks versus
  actors, logical-resource scheduling and the failure table; that is enough to run agent workloads safely.

## Decision case
### Scenario
A customer-support platform runs a RAG agent on Kubernetes and needs a home for three GPU jobs around it.
Throughput figures are the illustrative ones from the examples.
- **Nightly re-embed:** 10 million documents in a 02:00-03:00 window, at about 1,000 documents per second
  per GPU actor, plus about 5 minutes of start-up.
- **Agent eval suite:** 500 episodes of about 2 minutes, twice a day, with mocked write tools. The model
  endpoint sustains 50 in-flight episodes.
- **Online reranker:** called on every retrieval step, so it must never cold-start inside a user's run.
- **Constraints:** GPU quota 8; the reranker keeps at least 1 GPU at night. Three Python ML engineers
  plus one SRE who already runs KEDA. The cluster sits in a regulated VPC, so no external platform.

### Options
| Option | How it works | Nightly re-embed | Eval fan-out | Reranker | Operating cost |
|---|---|---|---|---|---|
| A. KubeRay | RayJob with Ray Data GPU actors; Ray tasks capped by `ray.wait`; RayService for the reranker | 4 actors ≈ 47 min with start-up, about 3.1 GPU-hours | 50 in flight ≈ 20 min | Serve autoscaling, `min_replicas: 1` | A second scheduler, version pinning, GCS fault tolerance with external Redis |
| B. Kubernetes Jobs + KEDA | Indexed Job of 4 GPU pods on static shards; eval runner with a 50-thread pool; reranker Deployment on KEDA | ≈ 47 min only if shards are even; the slowest shard sets wall-clock | ≈ 20 min, but retries and result collection are hand-written | KEDA on in-flight requests, min 1 | Tools the SRE knows; the team writes sharding, back-pressure and retries |
| C. Serverless GPU platform | Functions per job, per-second billing | Fast fan-out | Fast fan-out | Warm minimum needed | Ruled out: data may not leave the VPC |

### Decision
Choose **A**.
- **Window:** 4 actors finish in about 47 minutes. Eight would take about 26, but 8 + 1 reranker GPU
  exceeds the quota of 8, so 4 (or at most 7) actors is the plan.
- **Streaming beats static shards:** Ray Data overlaps reads, filtering and GPU encoding with
  back-pressure, so one slow shard does not set wall-clock as in B.
- **One model for three shapes:** batch actors, retryable eval tasks and an online endpoint share one
  Python codebase the ML engineers can own.
- **Cost:** `shutdownAfterJobFinishes` and `minReplicas: 0` mean night jobs pay about 3 GPU-hours, not an
  always-on pool.
- **Why not C:** the residency constraint removes it before price is considered.

**What would change it:**
- **Only the reranker remains** (embedding moves to a vendor): a Deployment with KEDA is less to operate;
  take B.
- **Corpus triples to 30 million:** even 7 actors need about 76 minutes, past the window, so raise quota
  or widen the window.
- **No one can own the head node:** without GCS fault tolerance, keep the reranker on a plain Deployment
  and use Ray only for RayJobs.

### Trade-offs accepted
- **A second scheduler inside Kubernetes.** Pin one Ray and Python version across images; alert on
  job-submission failures after image changes.
- **Head-node risk for the reranker.** Run GCS fault tolerance with external Redis; give the retrieval
  tool a fallback to vector order.
- **Retries replay episodes.** Keep write tools mocked in evals; alert on retry spikes per run.
- **An exposed dashboard is remote code execution.** Keep the dashboard and job API cluster-internal.

### Review rubric
- Sizes actor count from measured per-actor throughput and the time window, and checks it against quota.
- Caps eval concurrency by the model endpoint, not by cluster CPUs.
- Compares the extra scheduler's operating cost with the code the team would write without it.
- Addresses head-node fault tolerance before putting an online agent tool on Ray Serve.
- Applies hard constraints (residency, quota) before cost, and names what would flip the choice.

## Why it exists
- **AI workloads rarely fit one box or one shape.** Batch inference over ten million documents needs CPU
  workers to read and tokenise, GPU workers that each load a model once and keep it resident, and
  back-pressure so GPUs are neither starved nor flooded. Hyperparameter search launches hundreds of short
  trials; multi-node serving needs GPUs placed together and started as a unit; RL interleaves simulators,
  learners and inference.
- **Agent platforms add the same shapes.** A RAG agent needs its corpus embedded and refreshed; an eval
  harness replays hundreds of multi-step episodes; fine-tuning on agent traces needs trajectory generation
  at scale. Each is a fan-out of Python work with a resident model somewhere in it.
- **Job queues have no notion of a resident model.** Celery or SQS workers cannot express "this worker holds
  a 16 GB model and should keep it"; each task either reloads weights or you build actor management yourself.
- **Spark fights the GPU pattern.** It was built for CPU data-parallel stages with a barrier between them;
  feeding GPUs a continuous stream and keeping a model warm per executor fights its execution model.
- **Raw Kubernetes and multiprocessing stop short.** Kubernetes gives pods, not a way for one Python program
  to fan out ten thousand fine-grained calls and pass large arrays without serialising through a database.
  Multiprocessing stops at one machine.
- **Ray plus KubeRay closes the gap.** Ray offers tasks, actors and objects with a resource-aware scheduler,
  plus libraries for data, training, tuning and serving. KubeRay creates head and worker pods, wires up
  autoscaling and upgrades serving clusters without downtime via CRDs, so Kubernetes stays the control plane
  while ML code stays Python.

## How it works
### Core primitives
| Primitive | What it is | Why it matters for agent workloads |
|---|---|---|
| **Task** | A `@ray.remote` function. Calling `f.remote(x)` returns immediately with an `ObjectRef` (a future); the scheduler places it on any node with the requested resources | Stateless, so it can be retried elsewhere on failure; one agent eval episode maps naturally to one task |
| **Actor** | A `@ray.remote` class. Instantiating it starts a dedicated worker process that keeps state between method calls | Exactly how an embedder, reranker or model stays loaded on a GPU across thousands of batches |
| **Object** | Task results in a per-node shared-memory object store | Passing an `ObjectRef` moves bytes node-to-node only when needed; same-node readers share memory without copies |

Results that do not fit the object store spill to disk, which is slow but avoids crashes.

### Scheduling by declared resources
- **Declare, then place.** Each task or actor states `num_cpus`, `num_gpus`, memory or custom resources,
  and the scheduler only places it where those are free.
- **Reservations are *logical*.** `num_gpus=1` sets `CUDA_VISIBLE_DEVICES` for the worker but does not stop
  it using memory it was not granted; `num_gpus=0.5` lets two actors share a card with no memory isolation.
- **Placement groups** reserve bundles atomically (gang scheduling). Strategies:
  - `STRICT_PACK`: all bundles on one node, for tensor parallelism over NVLink (`PACK` is only a
    best-effort preference for that);
  - `SPREAD`: fault isolation.
- **Engines like vLLM** use Ray for multi-node tensor and pipeline parallelism for exactly this, so a
  self-hosted model too large for one node behind your agent runs on a Ray cluster even if you never
  write Ray code yourself.

### Cluster anatomy
- **Head node:** runs the Global Control Service (GCS, cluster metadata; the original paper called it the
  Global Control Store), the dashboard, the job-submission API and the autoscaler.
- **Worker nodes:** each runs a raylet (local scheduler and object store).
- **The head is a single point of failure:** lose it and, by default, the cluster's state goes with it.
- **The autoscaler scales on *pending logical demand*, not utilisation.** If tasks are queued asking for GPUs
  that no node has free, it adds workers; idle workers are removed after a timeout.

### Failure semantics
Ray's recovery model follows from the primitives, and it matters more for agents because agent episodes
call tools with side effects:

| What fails | What Ray does | What you must do |
|---|---|---|
| Task | Retried (by default a few times) on any node with resources, *because* it is stateless | Make batches idempotent; for agent episodes, sandbox or mock side-effecting tools so a retry cannot repeat a real action |
| Lost object | Rebuilt by re-running the tasks that produced it (lineage reconstruction) | Works for task outputs, not for objects created with `ray.put` |
| Actor | Can be restarted (`max_restarts`), but its in-memory state is gone | A model actor reloads its weights; checkpoint per-actor progress (or agent memory) in your code |
| Owner worker | Every object has an owner, the worker that created the reference; if it dies, its objects are lost even if copies survive elsewhere | Avoid long-lived refs owned by short-lived workers |
| Head node | Not automatically survivable | Treat it as the one component that needs explicit fault tolerance |

Design batch pipelines so a retried batch is idempotent and long runs checkpoint their output.

### Libraries
- **Ray Data** streams datasets through CPU and GPU stages with back-pressure, the usual tool for offline
  batch inference and embedding, such as building or refreshing a RAG index.
- **Ray Train** and **Ray Tune** wrap distributed training and hyperparameter search, for example
  fine-tuning a small model on agent traces.
- **Ray Serve** runs deployments as replica actors with request-based autoscaling (a target of ongoing
  requests per replica), model composition and multiplexing. Agent tools call these deployments over HTTP.

### KubeRay
KubeRay adds three CRDs:

| CRD | What it does | Typical agent use |
|---|---|---|
| **RayCluster** | Declares a head group and worker groups (each with pod template, `minReplicas`, `maxReplicas`). With in-tree autoscaling enabled, an autoscaler sidecar in the head pod edits worker-group replica counts; a node autoscaler (cluster autoscaler or Karpenter) supplies GPU nodes | Shared dev cluster for eval and data work |
| **RayJob** | Creates a cluster, submits an entrypoint and can tear the cluster down when it finishes, so a nightly batch run pays for GPUs only while it runs | Nightly corpus re-embedding, scheduled eval suites |
| **RayService** | Runs a Serve application. Cluster-spec upgrades bring up a new cluster, wait until it is healthy, then switch traffic; changes to only the Serve config are applied in place on the running cluster | Online embedder, reranker or model endpoints behind agent tools |

- **Why an operator and not a Deployment:** scale-down. Ray must choose *which* worker to remove (an idle one
  holding no actors or primary object copies).
- **So the autoscaler names specific pods** for KubeRay to delete, instead of decrementing a replica count and
  letting Kubernetes pick one at random.

```yaml
apiVersion: ray.io/v1
kind: RayJob
metadata: {name: nightly-embed}
spec:
  entrypoint: python embed.py
  shutdownAfterJobFinishes: true
  rayClusterSpec:
    enableInTreeAutoscaling: true
    headGroupSpec: {rayStartParams: {}, template: {...}}         # CPU-only head
    workerGroupSpecs:
      - groupName: gpu
        minReplicas: 0
        maxReplicas: 8
        rayStartParams: {}
        template: {...}                                          # requests nvidia.com/gpu: 1
```

## Diagram
How a RayJob that refreshes an agent's RAG index turns pending GPU demand into running actors: Ray's
autoscaler reacts to unplaceable resource requests, KubeRay creates the named pods, and the node
autoscaler supplies the GPU machines.

```mermaid
flowchart TD
    J["RayJob custom resource"] --> O["KubeRay operator"]
    O --> H["Head pod: GCS, dashboard, autoscaler"]
    H --> Q["Pending actors ask for num_gpus 1"]
    Q --> A["Ray autoscaler: unmet logical demand"]
    A -->|"raise gpu worker group replicas"| O
    O --> W["GPU worker pods"]
    W -->|"no free GPU node"| N["Node autoscaler adds GPU node"]
    N --> W
    W --> R["Actors load embedder once, embed batches"]
    R -->|"job finishes"| T["shutdownAfterJobFinishes: cluster deleted"]
```

## Examples
### Batch embedding of 10 million documents for a RAG agent with Ray Data
CPU stages read and clean; four GPU actors each load the model once and receive batches as they are
ready. Under the RayJob above, the autoscaler sees demand for four GPUs, raises the worker group to four,
and the job deletes the cluster when done. The output feeds the vector index the agent's search tool
queries.

```python
import ray

class Embedder:
    def __init__(self):
        # Runs once per actor: the weights stay resident on that actor's GPU.
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("all-MiniLM-L6-v2", device="cuda")

    def __call__(self, batch: dict) -> dict:
        texts = [str(t)[:2000] for t in batch["text"]]
        batch["embedding"] = self.model.encode(texts, batch_size=128)  # (n, 384) array
        return batch

ray.init()  # inside a RayJob this connects to the cluster it created

ds = ray.data.read_parquet("s3://corpus/docs/")                    # streamed, CPU tasks
ds = ds.filter(lambda row: row["text"] is not None)                # CPU tasks
ds = ds.map_batches(Embedder, batch_size=512, num_gpus=1, concurrency=4)  # 4 GPU actors
ds.write_parquet("s3://corpus/embeddings/")                         # triggers execution

# Ray Data executes lazily and streams: reading, filtering and embedding overlap, and
# back-pressure stops readers from filling the object store when GPUs fall behind.
```

- **Rough cost reasoning (illustrative):** if one GPU actor sustains about 1,000 documents per second, four
  actors finish 10 million in roughly 40 minutes plus cluster start-up.
- **`shutdownAfterJobFinishes`** means you pay for about that long, not for an always-on pool.
- **Use the same embedder at query time.** The agent's retrieval tool must embed queries with the exact
  model and version used here, or similarity scores silently degrade.
- **Version note:** the argument names (`concurrency`, `num_gpus`) follow recent Ray Data releases and have
  shifted between versions; check yours.

### Worked sizing: actor count versus wall-clock and GPU-hours
Same illustrative 1,000 documents per second per GPU actor, 10 million documents. Start-up (pods, image
pull, model load) is a fixed cost per run, shown here as an assumed 5 minutes.

| GPU actors | Embedding time | Wall-clock with start-up | GPU-hours billed |
|---|---|---|---|
| 1 | 10,000 s ≈ 167 min | ≈ 172 min | ≈ 2.9 |
| 4 | 2,500 s ≈ 42 min | ≈ 47 min | ≈ 3.1 |
| 8 | 1,250 s ≈ 21 min | ≈ 26 min | ≈ 3.4 |

- **More actors buy wall-clock, not savings:** GPU-hours stay roughly flat because the work is the same;
  start-up overhead grows with actor count.
- **Check the CPU side first:** eight GPU actors only help if reads and filtering keep up; otherwise the
  dashboard shows idle actors and you pay for waiting GPUs.

### Agent scenario: fan out an eval suite as Ray tasks
Each eval case is one agent episode (a multi-step tool-calling loop), so it maps to one stateless Ray
task. `my_agent` stands in for your own agent code; tools that touch real systems are mocked so a retry
cannot repeat a real action.
- **Concurrency is capped in code,** because episodes are I/O-bound model and tool calls: one CPU each
  would let a large cluster run hundreds at once and flood the model endpoint.
- **`max_retries` covers worker and node failures only.** An exception raised inside the episode is not
  retried unless you also pass `retry_exceptions=True`.

```python
import ray

ray.init()
MAX_IN_FLIGHT = 50                               # what the model endpoint sustains, not the CPU count

@ray.remote(num_cpus=1, max_retries=2)          # re-run if the worker or node dies mid-episode
def run_episode(case: dict) -> dict:
    from my_agent import run_agent, grade       # your agent loop and grader (placeholders)
    result = run_agent(case["prompt"], tools="mocked", max_steps=20)
    return {"id": case["id"], "passed": grade(case, result), "steps": result.steps}

cases = [{"id": i, "prompt": f"task {i}"} for i in range(500)]
pending, results = [], []
for case in cases:
    if len(pending) >= MAX_IN_FLIGHT:            # back-pressure: wait for one episode to finish
        done, pending = ray.wait(pending, num_returns=1)
        results.extend(ray.get(done))
    pending.append(run_episode.remote(case))
results.extend(ray.get(pending))                 # drain the last wave
print(sum(r["passed"] for r in results) / len(results))
```

| Illustrative numbers | Serial | 50 concurrent tasks |
|---|---|---|
| 500 episodes at ~2 min each | ~1,000 min | ~20 min, if the model endpoint keeps up |
| Model endpoint saturated | n/a | extra concurrency only queues; wall-clock is set by endpoint throughput |

The binding constraint is usually the model behind the agent (a provider rate limit or your Ray Serve
replicas), so size task concurrency to that, not to the CPUs available.

### Ray Serve deployment with request-based autoscaling
A reranker that an agent's retrieval tool calls after vector search. Replicas are GPU actors and the
replica count follows ongoing requests, the Ray-native counterpart to a KEDA queue-depth trigger.
Parameter names follow recent Ray 2.x releases.

```python
from ray import serve

@serve.deployment(
    ray_actor_options={"num_gpus": 1},          # one card per replica actor
    autoscaling_config={
        "min_replicas": 1,
        "max_replicas": 4,                       # match the GPU worker group's maxReplicas
        "target_ongoing_requests": 8,            # in-flight requests per replica before scaling
    },
)
class Reranker:
    def __init__(self):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cuda")

    async def __call__(self, request):
        body = await request.json()
        pairs = [(body["query"], d) for d in body["docs"]]
        return {"scores": self.model.predict(pairs).tolist()}

app = Reranker.bind()       # deploy with `serve run module:app`, or from a RayService spec
```

- **Agent effect:** every retrieval step in every running agent hits this endpoint, so `min_replicas: 1`
  avoids a cold start inside a user-facing loop, and `max_replicas` caps GPU spend when many agents search
  at once.

## Production patterns
- **Pin one Ray and Python version everywhere.** Use on every cluster. Build head, worker and client images
  from the same Ray release and Python minor version, and pin the image tag in the RayCluster or RayJob
  spec. Monitor job-submission failures after any image change, since mismatches surface as cryptic
  deserialisation errors.
- **Ephemeral RayJob clusters for RAG refreshes and eval runs.** Use for nightly re-embedding or scheduled
  agent eval suites. Set `shutdownAfterJobFinishes: true`, GPU worker groups with `minReplicas: 0`, and an
  upper bound matching quota. Monitor GPU-hours per run against the sizing table, and cluster start-up
  time as a fixed overhead.
- **Mock side-effecting tools in fanned-out episodes.** Use whenever agent episodes run as retryable Ray
  tasks. Route email, payment or write tools to sandboxes or fakes, and give real writes an idempotency
  key. Monitor retry counts per run; a spike means flaky tools or a saturated model endpoint.
- **Bound episode concurrency by the model, not the cluster.** Use for eval and trajectory generation.
  Cap in-flight tasks (submit in waves or with `ray.wait`) to what the provider rate limit or Serve
  replicas sustain. Monitor 429s or queue time at the model endpoint.
- **GCS fault tolerance for RayService.** Use for any production Serve application behind agent tools.
  Enable KubeRay's GCS fault tolerance backed by an external Redis so a head-pod restart does not take
  every Serve replica with it. Monitor head-pod restarts and Serve availability during them.
- **Keep the dashboard and job API internal.** Use always. Expose the dashboard and job-submission
  service as cluster-internal only, behind a network policy, and reach it through port-forwarding or an
  authenticated proxy. Monitor for unknown job submissions.
- **Declare GPUs to match what code uses.** Use for every actor and task. Set `num_gpus` from what the
  code actually loads, and use fractional values only for models whose summed memory fits the card.
  Monitor actor busy time and GPU memory per process in the dashboard.
- **Separate worker groups per GPU tier.** Use when jobs need different cards, such as a small embedder
  and a large model. Define one worker group per card type with a node selector, plus custom resources so
  actors target the right group. Monitor pending demand per group to see which tier is the bottleneck.

## Trade-offs
- **One Python program vs a second scheduler.** It can express a whole CPU-to-GPU pipeline with stateful
  model workers, but you take on a second scheduler inside Kubernetes; for a single stateless model server, a
  Deployment plus KEDA is simpler.
- **Ray Serve's features vs coupling.** It gives request-aware autoscaling, composition and multi-node
  replicas in one framework, but ties serving to Ray's versioning and head node; a dedicated engine behind a
  gateway wins when one model per pod suffices.
- **Ray for evals vs a hosted eval runner.** Ray scales agent episodes cheaply when you already run it,
  but a small suite against a hosted model runs fine on a thread pool; the overhead pays off only at
  hundreds of episodes or when GPUs are involved.
- **Fractional GPUs vs isolation.** They pack small models densely and raise utilisation, but sharing is
  unenforced; it flips to harmful when one actor's memory grows and OOMs its neighbour.
- **Ephemeral RayJob clusters vs start-up cost.** They cost nothing while idle, but every run pays start-up
  and image pull; for frequent short jobs a long-lived RayCluster with autoscaling to zero workers is cheaper.

## Pitfalls
- **Mismatched Ray or Python versions** between the client, head image and worker images. Symptom: cryptic
  connection or deserialisation errors at job submission rather than a clear version message. Fix: pin one
  version everywhere.
- **Retrying agent episodes that call real tools.** Task retries re-run the whole episode. Symptom:
  duplicate tickets, emails or writes after a node failure. Fix: mock or sandbox side-effecting tools in
  eval runs, and use idempotency keys for real writes.
- **Exposing the dashboard or job-submission port beyond the cluster.** These endpoints have historically
  had no authentication by default and accept arbitrary code (check what your Ray version offers). Symptom:
  unknown jobs (often crypto-mining) on your GPUs. Fix: internal-only access.
- **Declaring `num_gpus` that do not match what the code uses.** The autoscaler provisions by declarations,
  so the symptom is idle GPU nodes held by actors or, conversely, two processes OOMing one card. Fix:
  declare what the code loads.
- **Running GCS on the head without fault tolerance for a RayService.** Symptom: a head-pod restart taking
  every Serve replica with it, and every agent's retrieval tool failing at once. Fix: KubeRay supports
  external-Redis GCS fault tolerance for this.
- **Returning huge objects from tasks and holding the refs.** Symptom: object-store spilling to disk and a
  pipeline that slows by orders of magnitude without erroring. Fix: write large outputs to storage, stream.

## When to use
- **Use Ray when:** the workload is Python, distributed and heterogeneous — batch inference or embedding over
  large datasets with GPU actors (RAG ingestion), large agent eval or trajectory runs, hyperparameter
  sweeps, RL, multi-node model serving, or pipelines that mix CPU and GPU stages with state.
- **Use KubeRay when:** that runs on Kubernetes and you want the cluster lifecycle declared in Git.
- **Avoid when:** it is a single stateless model endpoint (an inference engine Deployment with KEDA is less
  to operate), SQL-shaped ETL (Spark or a warehouse), or an agent that only calls hosted APIs with a small
  eval suite.
- **Measure:** GPU-actor busy time and object-store pressure in the dashboard, plus task retries and model
  endpoint saturation during eval runs.
- **Interviews:** explain tasks versus actors, logical-resource scheduling, and why the autoscaler reacts to
  pending demand.

## Role lens
- **Engineer:** Writes Ray Data pipelines and Serve deployments, sizes `num_gpus`, batch size and actor
  count, and reads the dashboard to find whether the CPU or GPU stage is the bottleneck.
- **Architect:** Decides whether Ray becomes a platform layer on Kubernetes or stays a per-job tool, and
  owns head-node fault tolerance, version pinning, network exposure and GPU quota per worker group.
- **FDE:** Deploys KubeRay into a customer's cluster, adapts worker-group templates to their GPU node
  pools and security policy, and locks the dashboard behind internal networking before handover.

## Check yourself
**Q:** A team runs nightly embedding of 20 million chunks with a Celery queue: each task loads the model,
embeds 100 chunks and exits. GPUs show low utilisation and the job takes all night. How would you
restructure it with Ray on KubeRay, and why would it be faster and cheaper?
**A:** The waste is reloading weights per task and idle GPUs between tasks, with CPU-side work (reads,
tokenisation) serialised in front of the GPU. With Ray Data, model loading moves into an actor
constructor, so each GPU loads once; `map_batches` with a fixed actor pool streams large batches,
overlapping S3 reads and filtering on CPU tasks with GPU encoding under back-pressure. A RayJob with an
autoscaling GPU worker group sized from measured per-actor throughput, minimum zero and
`shutdownAfterJobFinishes`, means GPUs exist only during the run. Validate by checking actor busy time,
object-store spilling, and per-GPU throughput before scaling out.

## Interview questions
### Explain tasks, actors and objects in Ray, and which you would use for GPU inference.
- **Tasks** are stateless remote functions returning `ObjectRef` futures, retried elsewhere on failure.
- **Actors** are long-lived worker processes that keep state across calls, so a model loads once and stays
  resident on its GPU; that makes them the right primitive for inference workers.
- **Objects** live in a per-node shared-memory store and move between nodes only when a consumer needs them.

### You need to run a 500-case agent eval suite nightly. How would you build it on Ray, and what can go wrong?
- **One episode per task:** each case runs the agent loop as a stateless `@ray.remote` task, collected with
  `ray.wait` as episodes finish, inside an ephemeral RayJob.
- **Concurrency is set by the model:** cap in-flight episodes to what the provider rate limit or Serve
  replicas sustain, or extra tasks only queue and time out.
- **Retries are the trap:** a retried task replays every tool call, so side-effecting tools must be mocked
  or sandboxed, and real writes need idempotency keys.

### Why does the Ray autoscaler react to pending demand rather than GPU utilisation, and what goes wrong because of it?
- **Ray schedules logical resources:** a task or actor declares `num_gpus` and waits until a node can satisfy
  it, so unmet declarations are the demand signal.
- **The failure mode:** declarations that do not match the code. Over-declaring holds idle GPU nodes;
  under-declaring with fractional GPUs lets two processes OOM one card.
- **So** sizing `num_gpus` correctly is a capacity decision, not a formality.

### A RayService head pod restarted and every Serve replica went down, taking your agents' reranker tool with it. What happened and how do you prevent it?
- **Cause:** the head runs the GCS, which holds cluster metadata; without fault tolerance its loss takes the
  cluster state, and the Serve application, with it.
- **Prevention:** enable KubeRay's GCS fault tolerance with an external Redis so workers and replicas
  survive a head restart.
- **Also** keep the head CPU-only and resource-guarded so GPU work cannot starve or evict it, and give the
  agent's tool a fallback (skip reranking, keep vector order) so one outage degrades answers instead of
  failing them.

### When would you choose a plain Deployment with KEDA over Ray Serve on KubeRay?
- **One stateless model per pod:** an inference engine Deployment scaled on queue depth is less to operate,
  with no second scheduler or head node.
- **Choose Ray Serve** when you need model composition, multiplexing, multi-node replicas with placement
  groups, or one Python codebase spanning batch and online.
- **Cost of Ray:** coupling to its versioning and head-node fault tolerance, which a platform team must own.

### How would you size a RayJob to embed 10 million documents overnight for a RAG agent, and what would you monitor?
- **Measure one actor's throughput,** for example an illustrative 1,000 documents per second, then pick actor
  count from the deadline: four actors take about 42 minutes, eight about 21.
- **GPU-hours stay roughly flat** as actors increase, so the choice trades wall-clock for start-up overhead
  and quota.
- **Monitor** actor busy time (CPU stages keeping up), object-store spilling, and per-GPU throughput before
  scaling out further.

## Quiz
### Q: What distinguishes a Ray actor from a Ray task?
- [ ] Actors run on GPUs while tasks run only on CPUs
- [x] An actor is a long-lived worker process that keeps state across method calls, while a task is a stateless function call
- [ ] Tasks are scheduled by Kubernetes while actors are scheduled by Ray
- [ ] Actors cannot return ObjectRefs
> Both can request any resources. The difference is state and lifetime: an actor holds, for example, a
> loaded model between calls, which is why GPU inference workers are actors rather than tasks.

### Q: On what signal does the Ray autoscaler (as used by KubeRay) add worker nodes?
- [ ] Average GPU utilisation across the cluster
- [ ] CPU usage of the head pod
- [x] Pending tasks and actors whose declared resource requests cannot be placed on existing nodes
- [ ] The number of HTTP requests reaching the dashboard
> Ray schedules logical resources, so the autoscaler reacts to unmet declared demand. Real utilisation is
> not consulted, which is why wrong `num_gpus` declarations directly cause over- or under-provisioning.

### Q: A team gives two model actors `num_gpus=0.5` each on one 24 GB GPU. What does Ray guarantee?
- [ ] Each actor is limited to 12 GB of GPU memory
- [ ] The GPU is time-sliced so each actor gets exactly half the compute
- [x] Only that both are scheduled onto the same GPU; memory and compute are not isolated
- [ ] Ray will refuse the placement because GPUs cannot be shared
> Fractional GPUs are bookkeeping for placement. The processes share the card freely, so if one grows its
> memory use, the other can hit out-of-memory errors; isolation needs MIG or framework-level memory caps.

## Use cases & projects
### Use cases
- **RAG index builds and refreshes.** Ray Data with GPU embedder actors re-embeds a knowledge base nightly
  in an ephemeral RayJob, so the agent's search tool sees fresh documents without an always-on GPU pool.
- **Large agent eval suites.** Hundreds of multi-step episodes run as parallel Ray tasks, turning a
  many-hour serial run into minutes, bounded by the model endpoint's throughput.
- **Tool backends for agents.** Rerankers, embedders and small classifiers run as Ray Serve deployments
  that agent tools call over HTTP, autoscaling on ongoing requests.
- **Self-hosted models too big for one node.** vLLM's multi-node tensor and pipeline parallelism runs on
  Ray, so a large open model behind your agent is placed and started as a unit.
- **Trajectory generation for fine-tuning.** Fan out agent runs to collect traces, then train a smaller
  model on them with Ray Train on the same cluster.

### Mini project
**Parallel agent eval on a local Ray cluster (2-3 hours).**
- **Goal:** run a 100-case agent eval suite in parallel with Ray tasks and show what concurrency and
  retries do to wall-clock and correctness.
- **Steps:**
  1. `pip install ray` and start it locally with `ray.init()`.
  2. Write a toy agent with two tools, one read-only and one that "writes" to a local file, and a fake
     model call that sleeps 1-3 seconds.
  3. Run the 100 cases serially, then as `@ray.remote` tasks with `max_retries=2` and
     `retry_exceptions=True` (without it, Ray does not retry Python exceptions).
  4. Raise an exception after the write in a random 10% of runs and count duplicate writes; then add an
     idempotency key per case and rerun.
- **Done when:** you have a table of serial versus parallel wall-clock, duplicate-write counts before and
  after the idempotency key, and a short note on how you would cap concurrency against a real model's rate
  limit.
