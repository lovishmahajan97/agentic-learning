---
id: ray-kuberay
module: infra-capacity-economics
name: Ray / KubeRay
kind: tool
difficulty: advanced
est_minutes: 18
status: reviewed
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
Ray is a Python framework that turns functions and classes into distributed tasks and stateful actors,
scheduled across a cluster by declared resources such as GPUs. It exists because AI pipelines mix CPU and
GPU stages and need fine-grained, stateful parallelism that Spark or job queues handle poorly. KubeRay is
the Kubernetes operator that runs Ray clusters, jobs and Serve applications as custom resources.

## Why it exists
AI workloads rarely fit one box or one shape. Batch inference over ten million documents needs CPU workers
to read and tokenise, GPU workers that each load a model once and keep it resident, and back-pressure so
the GPUs are neither starved nor flooded. Hyperparameter search launches hundreds of short trials.
Multi-node serving needs a group of GPUs placed together and started as a unit. Reinforcement learning
interleaves simulators, learners and inference.

The obvious tools each break somewhere. A job queue (Celery, SQS workers) has no notion of "this worker
holds a 16 GB model and should keep it"; each task either reloads weights or you build actor management
yourself. Spark was built for CPU data-parallel stages with a barrier between them; feeding GPUs a
continuous stream and keeping a model warm per executor fights its execution model. Raw Kubernetes
gives you pods, not a way for one Python program to fan out ten thousand fine-grained calls and pass
large arrays between them without serialising through a database. Multiprocessing stops at one machine.

Ray's answer is a small set of primitives (tasks, actors, objects) with a scheduler that understands
resources, plus libraries built on them for data processing, training, tuning and serving. Running it
on Kubernetes was the remaining gap: someone has to create the head and worker pods, wire up autoscaling,
and upgrade a serving cluster without downtime. KubeRay fills that gap with CRDs, so platform teams keep
Kubernetes as the control plane while ML code stays Python.

## How it works
### Core primitives
- **Task:** a `@ray.remote` function. Calling `f.remote(x)` returns immediately with an `ObjectRef` (a
  future); the scheduler places the call on any node with the requested resources. Tasks are stateless,
  so they can be retried elsewhere on failure.
- **Actor:** a `@ray.remote` class. Instantiating it starts a dedicated worker process that keeps state
  between method calls, which is exactly how a model stays loaded on a GPU across thousands of batches.
- **Object:** task results live in a per-node shared-memory object store. Passing an `ObjectRef` to another
  task moves the bytes node-to-node only when needed and lets same-node readers share memory without
  copies. Results that do not fit spill to disk, which is slow but avoids crashes.

### Scheduling by declared resources
Each task or actor states `num_cpus`, `num_gpus`, memory or custom resources, and the scheduler only
places it where those are free. These are *logical* reservations: `num_gpus=1` sets `CUDA_VISIBLE_DEVICES`
for the worker but does not stop it using memory it was not granted, and `num_gpus=0.5` lets two actors
share a card with no memory isolation. **Placement groups** reserve bundles atomically (gang scheduling)
with strategies such as `STRICT_PACK` (all bundles on one node, for tensor parallelism over NVLink;
`PACK` is only a best-effort preference for that) or `SPREAD` (fault isolation). Engines like vLLM use Ray for multi-node tensor and pipeline parallelism for exactly this.

### Cluster anatomy
A Ray cluster has one **head node** running the Global Control Service (GCS, cluster metadata; the
original paper called it the Global Control Store), the dashboard,
the job-submission API and the autoscaler, plus **worker nodes** each running a raylet (local scheduler
and object store). The head is a single point of failure: lose it and, by default, the cluster's state
goes with it. The **autoscaler** scales on *pending logical demand*, not utilisation: if tasks are queued
asking for GPUs that no node has free, it adds workers; idle workers are removed after a timeout.

### Failure semantics
Ray's recovery model follows from the primitives. A failed task is retried (by default a few times) on
any node with resources, *because* it is stateless. A lost object is rebuilt by re-running the tasks
that produced it (lineage reconstruction), which works for task outputs but not for objects created
with `ray.put`. An actor can be restarted (`max_restarts`), but its in-memory state is gone, so a
model actor reloads its weights and any per-actor progress must be checkpointed by your code. Every
object has an owner, the worker that created the reference; if the owner dies, its objects are lost
even if copies survive elsewhere. Design batch pipelines so a retried batch is idempotent and long runs
checkpoint their output, and treat the head node as the one component whose loss is not automatically
survivable.

### Libraries
**Ray Data** streams datasets through CPU and GPU stages with back-pressure, the usual tool for offline
batch inference and embedding. **Ray Train** and **Ray Tune** wrap distributed training and
hyperparameter search. **Ray Serve** runs deployments as replica actors with request-based autoscaling
(a target of ongoing requests per replica), model composition and multiplexing.

### KubeRay
KubeRay adds three CRDs. **RayCluster** declares a head group and worker groups (each with pod template,
`minReplicas`, `maxReplicas`); with in-tree autoscaling enabled, an autoscaler sidecar in the head pod
edits worker-group replica counts, and a node autoscaler (cluster autoscaler or Karpenter) supplies GPU
nodes. Scale-down is why this is an operator rather than a plain Deployment: Ray must choose *which*
worker to remove (an idle one holding no actors or primary object copies), so the autoscaler names
specific pods for KubeRay to delete instead of decrementing a replica count and letting Kubernetes pick
one at random. **RayJob** creates a cluster, submits an entrypoint and can tear the cluster down when it
finishes, so a nightly batch run pays for GPUs only while it runs. **RayService** runs a Serve application
and handles cluster-spec upgrades by bringing up a new cluster, waiting until it is healthy, then
switching traffic; changes to only the Serve config are applied in place on the running cluster.

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

## Example
Embedding 10 million documents with Ray Data. CPU stages read and clean; four GPU actors each load the
model once and receive batches as they are ready. Under the RayJob above, the autoscaler sees demand for
four GPUs, raises the worker group to four, and the job deletes the cluster when done.

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

Rough cost reasoning (illustrative): if one GPU actor sustains about 1,000 documents per second, four
actors finish 10 million in roughly 40 minutes plus cluster start-up, and `shutdownAfterJobFinishes`
means you pay for about that long, not for an always-on pool. The argument names (`concurrency`,
`num_gpus`) follow recent Ray Data releases and have shifted between versions; check yours.

## Trade-offs
- One Python program can express a whole CPU-to-GPU pipeline with stateful model workers, but you take on
  a second scheduler inside Kubernetes; for a single stateless model server, a Deployment plus KEDA is simpler.
- Ray Serve gives request-aware autoscaling, composition and multi-node replicas in one framework, but
  ties serving to Ray's versioning and head node; a dedicated engine behind a gateway wins when one model per pod suffices.
- Fractional GPUs pack small models densely and raise utilisation, but sharing is unenforced; it flips to
  harmful when one actor's memory grows and OOMs its neighbour.
- Ephemeral RayJob clusters cost nothing while idle, but every run pays start-up and image pull; for
  frequent short jobs a long-lived RayCluster with autoscaling to zero workers is cheaper.

## Pitfalls
- Mismatched Ray or Python versions between the client, head image and worker images; the symptom is
  cryptic connection or deserialisation errors at job submission rather than a clear version message.
- Exposing the dashboard or job-submission port beyond the cluster; these endpoints have historically had no
  authentication by default and accept arbitrary code (check what your Ray version offers), and the symptom is unknown jobs (often crypto-mining) on your GPUs.
- Declaring `num_gpus` that do not match what the code uses; the autoscaler provisions by declarations,
  so the symptom is idle GPU nodes held by actors or, conversely, two processes OOMing one card.
- Running GCS on the head without fault tolerance for a RayService; the symptom is a head-pod restart
  taking every Serve replica with it. KubeRay supports external-Redis GCS fault tolerance for this.
- Returning huge objects from tasks and holding the refs; the symptom is object-store spilling to disk
  and a pipeline that slows by orders of magnitude without erroring.

## When to use
Reach for Ray when the workload is Python, distributed, and heterogeneous: batch inference or embedding
over large datasets with GPU actors, hyperparameter sweeps, RL, multi-node model serving, or pipelines
that mix CPU and GPU stages with state. Reach for KubeRay when that runs on Kubernetes and you want the
cluster lifecycle declared in Git. Do not adopt it for a single stateless model endpoint (an inference
engine Deployment with KEDA is less to operate) or for SQL-shaped ETL (Spark or a warehouse). Measure
GPU-actor busy time and object-store pressure in the dashboard. In interviews, explain tasks versus actors,
logical-resource scheduling, and why the autoscaler reacts to pending demand.

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
