---
id: kubernetes
module: backend-fundamentals
name: Kubernetes
kind: tool
difficulty: core
est_minutes: 17
status: reviewed
related:
  - backend-fundamentals/docker
  - backend-fundamentals/ci-cd
  - backend-fundamentals/secrets-management
  - fastapi-for-ai/health-readiness-endpoints
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/ray-kuberay
  - infra-capacity-economics/gpu-infrastructure
sources:
  - Official docs: Kubernetes
  - Burns, Grant, Oppenheimer, Brewer, Wilkes, 2016 — Borg, Omega, and Kubernetes
  - Verma et al., 2015 — Large-scale cluster management at Google with Borg
---

## TL;DR
Kubernetes runs containers across a fleet of machines by reconciling declared desired state ("three
replicas of this image, one GPU each, ready when `/ready` returns 200") against what is actually
running. It exists because once there are more containers than one person can place by hand,
scheduling, self-healing, rolling updates, service discovery and scaling have to be a platform rather
than scripts. For AI services, its contract of resource requests, probes and graceful termination is
what makes GPU-bound, slow-starting, long-streaming workloads deployable without dropping requests.

## Why it exists
Docker gives you a reproducible unit. It does not answer the questions that appear the moment you have
more than one: which machine should run this container, given its CPU, memory and GPU needs; what
happens when it crashes at 3 a.m. or its node dies; how a new version replaces the old without
downtime; how service A finds the current IP of service B when replicas come and go; how to go from
two replicas to twenty when traffic arrives; how configuration and secrets reach the process.

The naive answer is SSH plus `docker run` plus a hand-edited load balancer, or Compose on a few large
hosts. It fails in a predictable order. Nothing reconciles: a dead container stays dead unless you
wrote the watchdog yourself. The real state of the system lives in people's heads and shell history,
so no one can say what is running where. Placement is manual, so expensive GPU nodes sit half empty
while others are oversubscribed; bin-packing is what makes GPUs affordable, and humans do not bin-pack.
Every scaling action, update and rollback is a bespoke procedure that differs the second time it runs.

Google's Borg solved this at scale with one idea: operators declare the desired state of jobs, and
controllers converge the cluster toward it, forever. Kubernetes is the open-source generalisation of
that model. The important shift is from *commands* ("start this here") to *declarations* ("this should
exist"), because declarations can be reconciled after failures, versioned in git, and reasoned about
without knowing the current state of every machine.

## How it works
### Control plane and reconciliation
The API server is the only component that reads and writes etcd, where every object lives. Controllers
each watch one kind of object and act to make reality match its spec. The scheduler assigns Pods that
have no node to a node whose free resources satisfy their requests. On every node, the kubelet starts
containers through the container runtime and reports status; kube-proxy and the CNI plugin provide
networking. When you `kubectl apply` a manifest, nothing executes a command: you changed desired state
in the API server, and the controllers converge toward it, repeatedly. That is why a deleted Pod comes
back and why a `docker run` on a node is invisible to the cluster.

Every controller is the same loop, run on every change event and on a periodic resync:

```
loop:
    desired = read spec from API server        # e.g. replicas: 3, image: v2
    actual  = observe status                   # e.g. 2 Pods running v1, 1 Pending
    diff    = desired - actual
    act on diff (create, delete, update)       # one step toward desired, not a script to the end
    write status back
```

The loop is *level-triggered*, acting on the current difference rather than on the event that caused
it, *because* events are lost when components restart; a controller that wakes after a crash simply
recomputes the diff. It also means you never get a "done" signal from `apply`; you watch status
(`kubectl rollout status`) to learn when reality has caught up.

### The objects you actually use
- **Pod**: one or more containers sharing a network namespace and volumes; the unit of scheduling;
  ephemeral, and almost never created directly in production (a controller owns it).
- **Deployment → ReplicaSet → Pods**: a replica count, a Pod template and a rollout strategy. Changing
  the image creates a new ReplicaSet and scales it up while the old one scales down, bounded by
  `maxSurge` and `maxUnavailable`. Old ReplicaSets are kept at zero replicas, which is what makes
  `kubectl rollout undo` fast. Kubernetes does not roll back on its own: a rollout whose new Pods never
  become ready simply stalls (with `maxUnavailable: 0`, harmlessly) and is marked failed after
  `progressDeadlineSeconds`; reverting is a human, pipeline or progressive-delivery tool (Argo Rollouts,
  Flagger) decision.
- **Service**: a stable virtual IP and DNS name that load-balances to Pods matching a label selector,
  with membership gated by readiness. An Ingress or Gateway brings HTTP in from outside.
- **ConfigMap** and **Secret**: configuration injected as environment variables or mounted files.
- **Job** and **CronJob** for run-to-completion work (evals, batch embedding, fine-tuning);
  **StatefulSet** for stable identity and storage (vector databases); **DaemonSet** for one Pod per
  node (the GPU device plugin, a log shipper).
- **Namespace**, RBAC and **ResourceQuota** to share a cluster across teams.

### Scheduling: requests, limits and GPUs
Requests are what the scheduler reserves on a node; limits are what the cgroup enforces at run time.
Exceeding a CPU limit throttles the container; exceeding a memory limit gets it OOM-killed (exit code
137). GPUs are exposed by the NVIDIA device plugin as the extended resource `nvidia.com/gpu`: whole
integers, request equal to limit, no overcommit, because a GPU cannot be time-shared transparently the
way CPU can. GPU node pools are usually tainted so that only Pods with a matching toleration land on
them, keeping CPU-only work off expensive hardware. Fractional sharing needs MIG partitions or
time-slicing configured on the node.

Requests and limits together also set a Pod's quality-of-service class, which decides who dies first
when a node runs short of memory. **Guaranteed**: every container has CPU and memory requests equal to
limits. **BestEffort**: no requests or limits at all. **Burstable**: anything in between. Under memory
pressure the kubelet evicts BestEffort Pods first, then Burstable Pods using more than they requested,
and Guaranteed Pods last, *because* a Pod that reserved exactly what it uses has made no claim on
anyone else's memory. The trade is that Guaranteed requires a CPU limit, which reintroduces
throttling; the example below is Burstable on purpose.

### Probes: the contract that makes rollouts safe
- **Startup probe**: for slow starts such as loading model weights; while it has not passed, the other
  probes are paused, *because* otherwise the liveness probe would kill the Pod mid-load and produce a
  CrashLoopBackOff.
- **Readiness probe**: whether to send traffic; failing removes the Pod from Service endpoints without
  restarting it, because overload or a dependency outage should shed traffic, not restart the process.
- **Liveness probe**: whether to restart; it should be cheap and must not depend on downstream systems,
  or an incident in the database becomes a restart storm in the API.

### Termination
When a Pod is deleted it is marked Terminating, removed from Service endpoints (asynchronously, so
traffic can still arrive for a moment), its `preStop` hook runs, PID 1 receives SIGTERM, and after
`terminationGracePeriodSeconds` (30 by default, and the clock starts before `preStop`, so the hook's
time counts against it) it receives SIGKILL. The server must stop accepting,
drain in-flight requests, including streams, and exit. For LLM responses that can run for minutes,
raise the grace period and add a short `preStop` sleep so endpoint removal propagates first.

### Scaling
The Horizontal Pod Autoscaler changes replica counts on CPU, memory or custom metrics. For GPU-bound
services CPU is a poor signal; scale on queue depth, in-flight requests or tokens waiting, through KEDA
or custom metrics. The Cluster Autoscaler or Karpenter adds nodes, but a GPU node takes minutes to boot
and pull a multi-gigabyte image, so reactive scaling needs headroom or a warm pool.

### AI workloads specifically
Model weights can be baked into the image (simple, slow to pull), on a shared read-only volume, or
fetched by an init container from object storage into an `emptyDir` with a node-local cache. Serving
stacks such as vLLM or KServe run as Deployments with the pattern above; distributed training or
inference runs on Ray via KubeRay; a PodDisruptionBudget stops node maintenance from evicting every
replica at once.

## Example
A Deployment and Service for a GPU-backed inference API. The rollout never drops below three ready
replicas, the startup probe allows up to ten minutes of model loading before liveness checks begin,
the readiness endpoint gates traffic, and the grace period plus `preStop` sleep let long streaming
responses finish before SIGKILL. The image is referenced by digest so every node runs the same bytes.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: llm-api}
spec:
  replicas: 3
  strategy: {rollingUpdate: {maxSurge: 1, maxUnavailable: 0}}   # never below 3 ready
  selector: {matchLabels: {app: llm-api}}
  template:
    metadata: {labels: {app: llm-api}}
    spec:
      terminationGracePeriodSeconds: 120       # let long streaming responses finish
      nodeSelector: {accelerator: nvidia-l4}
      tolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]
      containers:
      - name: api
        image: registry.example.com/llm-api@sha256:3f9c...   # immutable digest, not :latest
        ports: [{containerPort: 8000}]
        envFrom: [{secretRef: {name: llm-api-secrets}}]   # frozen at start; mount as files for hot rotation
        resources:
          requests: {cpu: "2", memory: 8Gi, nvidia.com/gpu: 1}
          limits:   {memory: 8Gi, nvidia.com/gpu: 1}          # no CPU limit: avoids throttling
        startupProbe:                            # model load may take minutes
          httpGet: {path: /health, port: 8000}
          periodSeconds: 10
          failureThreshold: 60                   # up to 10 min before giving up
        readinessProbe:
          httpGet: {path: /ready, port: 8000}    # 200 only after the model is loaded and warm
          periodSeconds: 5
        livenessProbe:
          httpGet: {path: /health, port: 8000}   # cheap, no downstream calls
          periodSeconds: 30
        lifecycle:
          preStop: {exec: {command: ["sleep", "5"]}}   # let endpoint removal propagate
---
apiVersion: v1
kind: Service
metadata: {name: llm-api}
spec:
  selector: {app: llm-api}
  ports: [{port: 80, targetPort: 8000}]
```

## Trade-offs
- Declarative reconciliation gives self-healing and repeatable deploys, but you pay in a large object
  model and a control plane to operate; for one service on one box a managed container service is less
  to learn, and the balance flips once you have several services, GPUs, batch jobs and more than one
  team.
- `maxUnavailable: 0` keeps capacity during rollouts but requires a spare GPU for the surge Pod; when
  GPUs are scarce you choose between a brief capacity dip and permanently reserved headroom.
- CPU limits protect neighbours from a runaway process but throttle latency-sensitive Pods through the
  CFS quota; the common compromise is to always set requests and memory limits and rarely set CPU
  limits.
- Autoscaling on queue depth tracks load correctly, but GPU node provisioning takes minutes, so
  reactive scaling needs idle headroom that costs money every hour it is not used.

## Pitfalls
- No resource requests; the symptom is nodes overloaded and Pods evicted, or, with requests set too
  high, Pods stuck Pending with `Insufficient nvidia.com/gpu` in the events.
- A liveness probe that calls the model or the database; the symptom is a restart storm during an
  incident that turns a slow dependency into a full outage.
- No startup probe on a service that loads weights for minutes; the symptom is CrashLoopBackOff: the
  liveness probe kills the container mid-load (with default settings after about 30 seconds of failed
  checks), and the restart backoff grows toward its five-minute cap while the model never finishes
  loading.
- Readiness returning 200 before the model is loaded; the symptom is a burst of 5xx responses right
  after every deploy.
- The default 30-second grace period with no drain logic; the symptom is streaming responses cut
  mid-token on every rollout and node drain.
- Deploying a `:latest` tag; the symptom is Pods on different nodes running different code depending on
  when each node last pulled.
- OOMKilled with no application log; the symptom is restarts under load because the memory limit was
  set below the real peak of tokenizer plus model plus batch.
- Treating `emptyDir` or local disk as durable for an index; the symptom is data gone after the Pod is
  rescheduled.
- HPA on CPU for a GPU-bound service; the symptom is a growing queue while the autoscaler sees low CPU
  and never adds replicas.

## When to use
Reach for Kubernetes when you run several services, need GPU scheduling, run batch jobs beside serving,
share infrastructure across teams, or deploy into enterprises that already run it, which is most of
them. Avoid it for a single container with modest load, where a managed serverless container platform
(Cloud Run, AWS App Runner) or a simpler orchestrator (ECS, Nomad) costs less to operate; those
give you scheduling and restarts but a smaller object model, less portability and, for serverless,
limited GPU and long-request support. Even on a managed control plane (EKS, GKE, AKS) you still own
node pools, upgrades, add-ons and every manifest, which is where most of the operating cost lives. Measure time-to-ready for a new Pod, rollout duration, restart counts, Pending
Pods, and GPU allocation versus actual utilization. Day to day you live in `kubectl get`, `describe`,
`logs` and `exec`, read events, write Deployments, Jobs and autoscalers, and package them with Helm or
Kustomize. In interviews, explain reconciliation, how a Deployment rolls out, the three probes,
requests versus limits, GPU scheduling and graceful termination.

## Role lens
- **Engineer:** Writes manifests or Helm charts, sets probes, requests and grace periods correctly, and
  debugs CrashLoopBackOff, OOMKilled and Pending Pods from `describe` output and events.
- **Architect:** Designs the cluster topology (GPU node pools, taints, namespaces, quotas), chooses
  autoscaling signals, decides where weights live, and picks managed versus self-run control planes.
- **FDE:** Deploys into the customer's cluster with their CNI, ingress, Pod Security Standards,
  registry mirrors, GPU operator version and restricted RBAC, and sizes node pools with their platform
  team.

## Check yourself
**Q:** After each deploy of your vLLM-based service, users see a burst of errors and some streaming
responses are cut off, even though the Deployment uses a rolling update. What is happening and how do
you fix it?
**A:** Two separate failures. The errors at the start come from new Pods reporting ready before the
engine has loaded, so the Service routes to them; fix it by making `/ready` return 200 only after the
model is loaded and a warm-up request succeeded, and add a startup probe with a generous
`failureThreshold` so liveness does not kill Pods during load. The cut-off streams come from old Pods
receiving SIGTERM and either exiting immediately or hitting the 30-second default grace period
mid-stream; fix it by handling SIGTERM to stop accepting and drain in-flight requests, raising
`terminationGracePeriodSeconds` to cover the longest stream, and adding a `preStop` sleep so endpoint
removal propagates before shutdown begins. Set `maxUnavailable: 0` with `maxSurge: 1` so capacity never
dips, accepting one spare GPU during rollout, and verify by watching 5xx and stream-abort metrics
across a rollout.

## Quiz
### Q: What does `kubectl apply` of a Deployment manifest actually do?
- [ ] Starts the containers on the node your kubeconfig points at
- [x] Records desired state in the API server; controllers then create ReplicaSets and Pods and keep converging reality toward that spec
- [ ] Builds the image and pushes it to the cluster's registry
- [ ] Sends the manifest to every kubelet, which executes it once
> Kubernetes is declarative: `apply` changes the stored spec and the Deployment and ReplicaSet
> controllers do the work, repeatedly, which is why Pods are recreated after failures. Nothing builds
> images, and kubelets only run Pods the scheduler has bound to their node.

### Q: Which probe should decide whether a Pod receives traffic, without restarting it?
- [ ] livenessProbe
- [x] readinessProbe
- [ ] startupProbe
- [ ] the preStop hook
> A failing readiness probe removes the Pod from Service endpoints and nothing else, which is the right
> response to overload or a dependency outage. Liveness failure restarts the container, the startup
> probe only gates the other probes during initialisation, and preStop runs at termination.

### Q: Why do GPU Pods often sit Pending while CPU Pods on the same cluster schedule immediately?
- [ ] GPU Pods must be created with `kubectl run` rather than a Deployment
- [ ] The scheduler only honours GPU requests in the kube-system namespace
- [x] `nvidia.com/gpu` is an integer, non-overcommittable resource exposed by the device plugin, and GPU nodes are usually tainted, so the Pod needs a free whole GPU plus a matching toleration
- [ ] GPU nodes do not run the kubelet
> CPU is requested in millicores on plentiful, cheap nodes, so a CPU Pod usually fits somewhere. A GPU
> request needs an entire unallocated device on a node whose taint the Pod tolerates; if none is free the Pod waits
> until one is released or a new node is provisioned.
