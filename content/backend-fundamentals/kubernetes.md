---
id: kubernetes
module: backend-fundamentals
name: Kubernetes
kind: tool
difficulty: core
est_minutes: 16
related:
  - backend-fundamentals/docker
  - backend-fundamentals/ci-cd
  - backend-fundamentals/secrets-management
  - backend-fundamentals/rbac
  - deployment/health-checks
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/ray-kuberay
  - infra-capacity-economics/gpu-infrastructure
sources:
  - Official docs: Kubernetes
  - Burns, Grant, Oppenheimer, Brewer, Wilkes, 2016 — Borg, Omega, and Kubernetes
  - Official docs: NVIDIA GPU Operator
---

## TL;DR
Kubernetes runs containers across a fleet of machines by continuously reconciling what you declared
(this image, three replicas, 8 GiB and one GPU each, this health check) with what is actually running.
It exists because once containers outnumber hosts, placement, restarts, rollouts, discovery and scaling
become a control problem Docker alone does not solve. It is powerful and heavy: many teams are better
served by a managed platform until they need GPU pools, custom scheduling or a uniform way to run dozens
of services.

## Why it exists
One host running `docker run` is fine until the host dies at 3 a.m. and nothing restarts the
containers. Add a second host and you need to decide which containers go where; add a load balancer and
it needs to learn where they went; ship a new version and you need to replace containers one at a time
without dropping requests; add a GPU host and only some workloads may land there. Teams at this point
build an orchestrator out of shell scripts, Compose files, SSH and a spreadsheet, and it is fragile in
proportion to how clever it had to be.

The naive scalable alternative is one cloud autoscaling group per service with a health check and a
load balancer. It works, but every service re-implements deployment, discovery, configuration and
secrets slightly differently, and a GPU-backed model server, a queue worker and a cron-style eval job
each need their own operational playbook.

Kubernetes grew out of Google's Borg and took one lesson above all: express the desired state
declaratively and let controllers reconcile reality toward it forever. Failure handling stops being an
exception path, because "a pod disappeared" and "I asked for one more replica" are the same event to the
controller. For AI teams the draw is concrete: GPU scheduling through device plugins, node pools with
taints, and operators such as KubeRay and KServe that turn a cluster into a serving and training
platform. Honest alternatives remain: ECS or Cloud Run for a handful of CPU services, Nomad for
simplicity, a managed inference endpoint when you only have one model.

## How it works
### Architecture
The control plane is an API server (the single door; every object is a REST resource validated and
persisted here), etcd (a consistent key-value store holding all state), a scheduler (assigns pending
pods to nodes using requests, affinities and taints) and a controller manager (the reconciliation
loops). Each node runs a kubelet (makes the pods assigned to it match their spec by driving a container
runtime such as containerd over CRI), kube-proxy or an eBPF equivalent (implements Service virtual IPs)
and a CNI plugin that gives every pod its own IP on a flat network.

### The reconciliation loop
Every controller runs the same loop: read desired state from the spec, observe actual state, compute
the difference, act, repeat. It is level-triggered, comparing states rather than reacting to events,
*because* a missed event must not leave the system wrong forever. A node dies, its pods vanish, the
ReplicaSet controller sees two of three, creates a pod, the scheduler places it, the kubelet starts it.
Nobody is paged for that. Every action is idempotent, so controllers can retry freely.

### The objects you touch daily
- **Pod:** one or more containers sharing a network namespace (they talk over `localhost`) and
  volumes. Ephemeral; you almost never create pods directly.
- **Deployment → ReplicaSet → Pods:** a Deployment holds a pod template and a replica count. Changing
  the template creates a new ReplicaSet and rolls pods across according to `maxSurge` and
  `maxUnavailable`, moving on only as new pods become ready. Rollback points the Deployment back at the
  previous ReplicaSet.
- **Service:** a stable DNS name and virtual IP that load-balances to pods selected by labels. Only
  *ready* pods are endpoints, which is what makes readiness probes matter. **Ingress** or the Gateway
  API bring HTTP in from outside.
- **ConfigMap and Secret:** configuration injected as environment variables or files. A Secret is
  base64, not encryption; see secrets management.
- **Namespace** scopes names, RBAC and quotas. **PersistentVolumeClaim** requests durable storage.
  **StatefulSet** gives stable identities for databases, **Job** and **CronJob** run batch work such as
  evaluation runs, **DaemonSet** runs one pod per node (the GPU device plugin, a log shipper).

### Requests, limits and GPUs
`requests` are what the scheduler reserves for placement; `limits` are what the kernel enforces through
cgroups. CPU above its limit is throttled; memory above its limit is OOMKilled (exit 137) and restarted.
Because memory cannot be throttled, set memory request equal to limit so a pod is never scheduled onto
RAM it cannot actually get; many teams set no CPU limit at all to avoid throttling latency. GPUs are
extended resources exposed by the NVIDIA device plugin as `nvidia.com/gpu`; they are requested as whole
devices (sharing via time-slicing or MIG exists on supporting hardware), request must equal limit, and
GPU nodes are normally tainted so only pods that tolerate the taint land there.

### Probes
A **startup** probe gives a model server minutes to load weights before anything else judges it; a
**readiness** probe controls whether the pod receives traffic and should fail while loading or when
overloaded; a **liveness** probe restarts the container and should fail only when it is genuinely stuck.
Get these wrong and a healthy-but-slow model server lives in CrashLoopBackOff.

### Scaling
The HorizontalPodAutoscaler scales replicas on CPU, memory or custom metrics such as in-flight
requests or queue depth; KEDA scales on event sources, including to zero. The cluster autoscaler or
Karpenter adds nodes, but a GPU node takes minutes to boot and pull a multi-gigabyte image, so capacity
is provisioned ahead of demand and warm pools are kept.

### Access and isolation
Kubernetes RBAC (Role, RoleBinding, ClusterRole) governs who may do what to which objects.
Namespaces plus ResourceQuota and NetworkPolicy isolate teams or tenants. Workload identity lets a pod
assume a cloud role without a stored key.

## Example
A Deployment and Service for a GPU-backed embedding API. The image is pinned by digest, the pod only
lands on the GPU pool, memory request equals limit, a generous startup probe covers model loading, and
the rollout strategy never drops below full capacity.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: embed-api }
spec:
  replicas: 3
  strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }
  selector: { matchLabels: { app: embed-api } }
  template:
    metadata: { labels: { app: embed-api } }
    spec:
      nodeSelector: { accelerator: nvidia-l4 }                 # GPU node pool only
      tolerations: [{ key: nvidia.com/gpu, operator: Exists, effect: NoSchedule }]
      terminationGracePeriodSeconds: 60                        # time to drain in-flight requests
      containers:
      - name: api
        image: registry.example.com/embed-api@sha256:3f9c0e...  # digest, never a mutable tag
        ports: [{ containerPort: 8000 }]
        env:
        - { name: MODEL_NAME, valueFrom: { configMapKeyRef: { name: embed-cfg, key: model } } }
        - { name: HF_TOKEN,   valueFrom: { secretKeyRef:    { name: embed-secrets, key: hf_token } } }
        resources:
          requests: { cpu: "2", memory: 8Gi, nvidia.com/gpu: 1 }
          limits:   { memory: 8Gi, nvidia.com/gpu: 1 }        # memory = request; no CPU limit
        startupProbe:   { httpGet: { path: /healthz, port: 8000 }, periodSeconds: 5, failureThreshold: 60 }
        readinessProbe: { httpGet: { path: /ready,   port: 8000 }, periodSeconds: 5 }
        livenessProbe:  { httpGet: { path: /healthz, port: 8000 }, periodSeconds: 20 }
---
apiVersion: v1
kind: Service
metadata: { name: embed-api }
spec:
  selector: { app: embed-api }
  ports: [{ port: 80, targetPort: 8000 }]
```

`kubectl apply -f embed-api.yaml` then `kubectl rollout status deployment/embed-api` waits until the
new ReplicaSet is fully ready. When something is wrong, `kubectl describe pod <name>` shows scheduling
events ("0/12 nodes available: insufficient nvidia.com/gpu") and `kubectl logs <name> --previous`
shows why the last container died.

## Trade-offs
- Self-healing, a uniform deployment model and portability across clouds and customer sites, paid for
  with a steep learning curve and real cluster operations; it flips to a managed platform when you run
  fewer than a handful of CPU-only services.
- Declarative reconciliation makes recovery automatic, but "why has my change not applied" becomes a
  trace through controllers, events and conditions rather than a stack trace.
- Bin-packing many pods per node raises utilisation but invites noisy neighbours; requests, limits and
  QoS classes contain it only if they are set honestly.
- Managed control planes (EKS, GKE, AKS) remove etcd and API-server operations, but node pools,
  upgrades, the GPU operator and autoscalers remain yours to run.
- Fractional GPU sharing through time-slicing or MIG raises utilisation for small models but weakens
  memory isolation between tenants; whole-GPU allocation is simpler and safer.

## Pitfalls
- An aggressive liveness probe on a model server; the symptom is CrashLoopBackOff while weights are
  still loading. Add a startup probe with a long failure threshold.
- Pods without resource requests; the symptom is everything packed onto one node, followed by evictions
  and OOMKilled containers with exit code 137.
- A mutable `latest` tag with `imagePullPolicy: IfNotPresent`; the symptom is nodes running different
  versions of "the same" image. Deploy by digest.
- A readiness probe that only checks the process is up; the symptom is 5xx during every rollout as
  traffic reaches pods still loading.
- A GPU pod stuck Pending; the events say no node has `nvidia.com/gpu`, the toleration is missing, or
  the device plugin is not installed. `kubectl describe pod` names which.
- No PodDisruptionBudget; the symptom is a node upgrade draining every replica at once and a short
  outage during routine maintenance.
- Running a database in a Deployment without a PersistentVolumeClaim; the symptom is data gone after a
  reschedule.
- Treating Kubernetes Secrets as encrypted; the symptom is any namespace reader dumping credentials.

## When to use
Reach for Kubernetes when you run many services, need GPU node pools and custom scheduling, serve
several teams or tenants from shared infrastructure, or must deploy into customers' clusters, which
enterprise buyers increasingly require. Avoid it for a single API plus a worker; Cloud Run, ECS or a
PaaS will cost less in attention. Measure rollout duration, pod restart counts, time pods spend Pending,
and node and GPU utilisation. Day to day you write manifests or Helm charts, read events, and tune
probes and resources. In interviews, be ready to explain the reconciliation loop, requests versus limits,
the three probes, how a rolling update and a Service interact, and how to diagnose a Pending or
crash-looping pod.

## Role lens
- **Engineer:** Writes Deployments with honest requests, startup probes sized to model load time and
  digest-pinned images, and reads events and previous logs to debug restarts.
- **Architect:** Decides whether a cluster is warranted, designs node pools and GPU allocation,
  namespace and quota boundaries, and the autoscaling policy that keeps warm GPU capacity.
- **FDE:** Deploys the product into the customer's existing cluster within the namespace and RBAC they
  grant, adapts to their ingress, storage classes and GPU operator, and explains Pending pods to their
  platform team.

## Check yourself
**Q:** A new version of a model server is rolled out; the rollout stalls, some pods show
CrashLoopBackOff, and the old pods keep serving. Walk through how you would diagnose it and what the
usual causes are.
**A:** `kubectl rollout status` confirms the stall, and `kubectl get pods` shows the new ReplicaSet's
pods restarting while the old ones remain because `maxUnavailable: 0` protects capacity. `kubectl
describe pod` on a new pod reveals which probe is failing and the restart reason; `kubectl logs
--previous` shows the container's last output. The common causes are a liveness probe firing before the
model has loaded (fix with a startup probe and a larger failure threshold), an OOMKilled container
because the new model needs more memory than the limit (raise request and limit together), a missing
ConfigMap or Secret key referenced in the new template (CreateContainerConfigError), or a GPU request
that no node can satisfy (Pending rather than crashing). After fixing, `kubectl rollout undo` is the
fast path back if users are affected.

## Quiz
### Q: Why does a readiness probe matter during a rolling update?
- [ ] It restarts containers that fail so the update can retry
- [x] A Service only routes to ready pods, so the rollout waits for new pods to pass readiness before removing old ones and traffic never reaches a pod still loading
- [ ] It tells the scheduler which node has enough memory for the new pod
- [ ] It controls how many pods may be unavailable at once
> Readiness gates both Service endpoints and rollout progress. Without it, Kubernetes considers a pod
> ready as soon as its container starts, and requests hit a server whose model is still loading.

### Q: A pod requests 4 GiB of memory with an 8 GiB limit and is OOMKilled on a node that has free RAM. What is the most likely explanation?
- [ ] The node's kubelet reserved the memory for system daemons
- [ ] The limit is ignored when it is larger than the request
- [x] The container exceeded its 8 GiB limit; the cgroup enforces the limit regardless of free memory on the node
- [ ] The scheduler placed the pod on a node with less memory than requested
> Limits are enforced by cgroups on the container, not by node pressure. Free RAM on the node does not
> help a process that passes its own limit; this is why memory request and limit are usually set
> equal.

### Q: What does the reconciliation model mean for how you should think about a Deployment?
- [ ] It is an imperative command that runs once and reports success or failure
- [x] It is a declared desired state that a controller keeps enforcing, so a crashed pod is recreated without any action on your part
- [ ] It is a script executed on each node by the kubelet
- [ ] It is a snapshot of the cluster that must be re-applied after any change
> Controllers compare spec with status in a loop and act on the difference, forever. Recovery and
> rollout are the same mechanism, which is why `kubectl apply` of the same file is always safe.
