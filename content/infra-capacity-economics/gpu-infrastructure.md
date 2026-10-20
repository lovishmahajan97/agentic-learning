---
id: gpu-infrastructure
module: infra-capacity-economics
name: GPU infrastructure
kind: concept
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - infra-capacity-economics/gpu-sizing
  - infra-capacity-economics/gpu-utilization-and-memory-bandwidth
  - infra-capacity-economics/a100-h100-l4-selection
  - infra-capacity-economics/inference-clusters
  - infra-capacity-economics/hybrid-on-prem
  - backend-fundamentals/kubernetes
  - serving-inference/engines
sources:
  - Official docs: NVIDIA GPU Operator
  - Official docs: NVIDIA Container Toolkit
  - Official docs: Kubernetes (device plugins and extended resources)
---

## TL;DR
A GPU deployment is a stack, not a card: the accelerator and its memory, the host around it, the
driver and CUDA libraries, the container runtime that exposes the device, the scheduler that assigns
it, and the procurement model that pays for it. Each layer fails differently, and most "the GPU is
slow" tickets are really host, driver or scheduling problems. This lesson is the map; sizing,
bandwidth, card selection, serverless and autoscaling each have their own lesson.

## Why it exists
CPU servers are interchangeable enough that most engineers never think about the box: any node runs
any container, the OS hides the hardware, and a scheduler can place work anywhere. GPUs break every one
of those assumptions. A GPU is a separate computer attached over PCIe with its own memory, its own
instruction set and its own driver, and the host kernel does not virtualise it the way it virtualises
CPU time and RAM. An inference container therefore only works if the driver on the node, the CUDA
toolkit inside the image, the device plugin that advertised the card, and the scheduler's placement
all line up.

The naive approach, "install the driver, `pip install torch`, run it", works on one developer machine
and collapses at the second node: driver versions drift, images pull in CUDA builds the driver cannot
run, pods land on nodes without a card, two pods silently share one card and OOM each other, and nobody
can say whether the fleet is 30% or 90% busy because nothing exports GPU metrics. Add the price — one
high-end accelerator costs more per hour than a rack of web servers — and the stack has to be
deliberate: every idle hour is wasted money and every misplaced pod is a capacity bug. GPU
infrastructure is the set of layers that turn a card into a reliable, schedulable, observable, billable
resource.

## How it works
Walk the stack bottom-up. Each layer answers one question, and each has a lesson that goes deeper.

### 1. The accelerator: what does the compute look like?
A data-centre GPU is thousands of simple cores grouped into streaming multiprocessors (SMs), each with
tensor cores for matrix maths, fed by on-package memory (HBM on A100/H100, GDDR6 on L4). Two numbers
dominate everything downstream: memory capacity, which decides whether the model fits (see *GPU
sizing*), and memory bandwidth, which bounds how fast decode can run (see *GPU utilization & memory
bandwidth*). Form factor matters too: SXM modules sit on a baseboard with NVLink/NVSwitch between GPUs
and run at higher power; PCIe cards slot into ordinary servers, draw less power, and talk to each other
only over PCIe unless bridged. Which card to buy is the subject of *A100 / H100 / L4 selection*.

### 2. The node: what feeds the card?
A GPU node is a host with one to eight accelerators plus the CPU, RAM, storage and network that keep
them busy. Each can be the bottleneck:
- **CPU and host RAM.** Tokenisation, request handling, data loading and the serving engine's scheduler
  run on the CPU. Host RAM must stage weights on the way to the device, so a 64 GB host struggles to
  load a 140 GB checkpoint.
- **Storage.** Weights are read from disk at start-up. Pulling 140 GB over a 100 MB/s network volume
  takes about 23 minutes; from local NVMe at a few GB/s it takes under a minute. Cold-start time is
  mostly a storage decision.
- **Intra-node interconnect.** Tensor parallelism exchanges activations between GPUs every layer.
  NVLink gives hundreds of GB/s per GPU (NVIDIA quotes 900 GB/s for H100 SXM); a PCIe Gen4 x16 link
  gives roughly 32 GB/s per direction. Sharding across PCIe-only cards works but leaves bandwidth on the
  table.
- **Network.** Multi-node training needs RDMA (InfiniBand or RoCE) so gradient all-reduce does not
  stall in the host networking stack. Serving needs it only when one model replica spans machines (a
  model too large for one eight-GPU node, or disaggregated prefill and decode that ship KV caches
  between nodes); otherwise each request stays inside one node and ordinary data-centre Ethernet is
  enough, *because* only prompts and tokens cross the network.

### 3. Driver and CUDA: will the code run at all?
The kernel-mode NVIDIA driver is installed once per node and is the only piece that touches hardware.
Everything else — CUDA runtime, cuBLAS, cuDNN, NCCL, the PyTorch build — ships inside the container
and must be a version the node's driver supports. Drivers are backward compatible with older CUDA
runtimes, not forward compatible, so "CUDA driver version is insufficient for CUDA runtime version"
means the image is newer than the node. Two escape hatches exist but neither is a plan: CUDA minor-version
compatibility lets, say, a 12.x runtime run on an older 12.x driver with some features (PTX JIT) missing,
and the `cuda-compat` forward-compatibility package works only on data-centre GPUs. Pin both, and upgrade
drivers fleet-wide *before* bumping base images.

### 4. Container runtime: how does a container see the device?
Containers do not include the driver. The NVIDIA Container Toolkit hooks the container runtime so
that, when a container requests GPUs, the device files (`/dev/nvidia*`) and the driver's user-space
libraries are mounted in at start. This is why a GPU image is portable across driver versions and why
`nvidia-smi` works inside a container that never installed it.

### 5. Scheduling: which workload gets which card?
On Kubernetes a device plugin advertises GPUs as an extended resource, `nvidia.com/gpu`, in integer
units. Extended resources cannot be overcommitted and requests must equal limits. The reason is that
the kubelet treats an extended resource as an opaque count it cannot enforce: there is no cgroup-style
controller that gives a container a guaranteed fraction of a GPU or caps its device memory, the way CFS
quotas and memory cgroups do for CPU and RAM. (The GPU itself does time-slice between processes by
default; what is missing is enforced shares and isolation.) The NVIDIA GPU Operator
packages driver, toolkit, device plugin, feature discovery (node labels such as the card model) and the
DCGM metrics exporter as one install. Taints keep ordinary pods off expensive nodes; labels let a pod
target the right tier. Sharing one card between small workloads has three mechanisms with different
isolation: MIG hardware-partitions an A100 or H100 into up to seven instances with separate memory;
time-slicing lets the plugin advertise N virtual GPUs that share memory with no isolation; MPS lets
processes run kernels concurrently on one card and, on Volta and later, can set an opt-in per-client
memory cap, but clients still share one fault domain. Only MIG gives hardware-enforced memory and fault
isolation, so only MIG reliably protects one tenant from another's OOM.

### 6. Procurement: who pays, and how?
The same card arrives as on-demand instances, reserved or committed-use discounts, spot/preemptible
capacity, bare-metal leases, GPU-specialist clouds, or serverless per-second billing. Quotas gate all
of them; a fresh cloud account often cannot launch a single H100 without a request. The economics live
in *cost modeling*, *TCO* and *serverless GPUs*; the operational rule is that procurement latency
(days for quota, weeks for reservations) is part of capacity planning.

### 7. Observability: is it actually busy?
The DCGM exporter publishes per-GPU utilisation, memory use, temperature, power, ECC and XID error
counts to Prometheus. Without it the fleet is a black box; with it you discover that "100% utilised"
often means one kernel at a time on an otherwise idle chip, which the utilization lesson unpacks.

GPUs also fail in ways the host OS does not hide or recover from: uncorrectable ECC errors, retired
memory rows, a card that drops off the PCIe bus. The driver records these as XID events in the kernel
log and DCGM exports them. They matter for scheduling *because* Kubernetes still sees the device
plugin's advertised count: unless a health check cordons the node, the scheduler keeps placing pods on
a card that will crash them. Automated cordon-on-XID (or the GPU Operator's health checks) is part of
the stack, not an optional extra.

## Example
A minimal Kubernetes Deployment that asks for one GPU on a node labelled with a card model, tolerates
the GPU taint, and fails fast on a mis-provisioned node. The init container runs `nvidia-smi`, which
only succeeds if the toolkit mounted the driver libraries; because official `nvidia/cuda` images also set
`NVIDIA_REQUIRE_CUDA`, the toolkit refuses to start the container on a driver too old for that CUDA
version. Use the same CUDA version as the server image, and a bad node fails the pod before the server
tries to load 16 GB of weights.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: embedder
spec:
  replicas: 1
  selector: { matchLabels: { app: embedder } }
  template:
    metadata: { labels: { app: embedder } }
    spec:
      nodeSelector:
        nvidia.com/gpu.product: NVIDIA-L4        # label written by GPU feature discovery
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      initContainers:
      - name: gpu-check
        image: nvidia/cuda:12.4.1-base-ubuntu22.04   # same CUDA version as the server image
        command: ["nvidia-smi"]                   # fails the pod if the device is not visible
        resources: { limits: { nvidia.com/gpu: 1 } }
      containers:
      - name: server
        image: ghcr.io/example/embedder:1.4       # ships its own CUDA runtime, never the driver
        resources:
          limits: { nvidia.com/gpu: 1 }           # integer; requests are forced equal to limits
          requests: { cpu: "4", memory: 16Gi }    # CPU for tokenisation, RAM to stage weights
        volumeMounts:
        - { name: weights, mountPath: /models, readOnly: true }
      volumes:
      - name: weights
        persistentVolumeClaim: { claimName: model-weights }   # NVMe-backed, not object storage
```

## Trade-offs
- SXM/NVLink nodes make multi-GPU sharding fast but come in fixed eight-card blocks; PCIe cards are
  cheaper and available singly, which wins until a model must be split across cards.
- A GPU Operator or managed node pool makes the driver stack someone else's problem but ties you to
  their driver cadence; bare metal gives control and costs an ops team.
- Sharing a card (MIG, time-slicing, MPS) raises utilisation for small models but caps per-tenant
  performance and complicates scheduling; it stops paying off once one model saturates the card.
- Spot and preemptible capacity is far cheaper but can vanish mid-job; fine for checkpointed training
  and batch inference, wrong for latency-SLO serving unless on-demand fallback exists.

## Pitfalls
- Image CUDA runtime newer than the node driver; symptom is CrashLoopBackOff on exactly the nodes
  that have not had the driver upgrade, with "driver version is insufficient" in the logs.
- Loading weights from object storage or a slow network disk on every start; symptom is pods stuck in
  Running-but-not-Ready for tens of minutes while the readiness probe fails.
- Time-slicing two models onto one card without checking combined memory; symptom is CUDA OOM errors
  in one deployment that correlate with traffic to another.
- Judging fleet health by `nvidia-smi` GPU-Util; symptom is "95% busy" dashboards while tokens per
  second sit at a fraction of what the card can deliver.
- Leaving a node with a faulty GPU in the pool; symptom is pods failing with CUDA "unknown error" or
  illegal-memory-access on one node while identical pods elsewhere run, with XID entries in that node's
  kernel log.
- Treating quota and node lead time as instant; symptom is the autoscaler requesting a node, the cloud
  answering "insufficient capacity", and the request queue growing for twenty minutes.

## When to use
You do not choose whether to have GPU infrastructure; you choose how many layers to own. Managed node
pools with the GPU Operator, or a serverless GPU platform, are right when the team is small and the
workload is standard inference: the driver stack, device plugin and metrics come pre-wired. Own more
layers when you need a specific NVLink topology, custom kernels or drivers, bare-metal pricing, or
on-prem placement. Whatever the split, measure three things: pod ready-time (storage and image size),
GPU hours used versus purchased (procurement), and DCGM SM activity (whether "busy" is real). In
day-to-day work this shows up as a CrashLoopBackOff you must attribute to the right layer; in
interviews, as "walk me through what happens between `kubectl apply` and the first token", where
naming each layer and its failure mode is the expected answer.

## Role lens
- **Engineer:** Writes the pod spec, pins CUDA and driver versions, mounts weights from fast storage,
  and reads DCGM metrics to tell a real bottleneck from an idle card.
- **Architect:** Decides which layers to own versus buy, the node shapes (SXM vs PCIe, sharing policy),
  and the procurement mix of reserved, on-demand and spot capacity.
- **FDE:** Audits a customer's cluster layer by layer — driver, toolkit, device plugin, taints, metrics
  — before promising that a model will run there on day one.

## Check yourself
**Q:** After a base-image bump, new inference pods crash on half the cluster with "CUDA driver version
is insufficient" and run fine on the other half. What happened, and how would you structure the fleet
so it cannot recur?
**A:** The image now carries a CUDA runtime newer than the kernel driver on the older half of the
nodes; drivers support older runtimes, not newer ones, so only un-upgraded nodes fail. Immediate fix:
roll the image back or pin the pods to upgraded nodes with a label. Structural fix: manage drivers with
the GPU Operator or node-pool images so every node runs one known version, label nodes with the driver
version, roll driver upgrades fleet-wide before base-image upgrades, and add an init container that
runs `nvidia-smi` so a mismatch fails fast and visibly instead of after a long weight load. Capacity
also matters: if half the fleet is unusable, the autoscaler should be told, not left to discover it.

## Quiz
### Q: Why can `nvidia.com/gpu` not be overcommitted on Kubernetes the way CPU can?
- [ ] GPUs are too expensive for the scheduler to risk contention
- [x] There is no cgroup-style mechanism to enforce a fractional share or memory cap on a GPU, so whole devices are handed out
- [ ] The NVIDIA driver forbids more than one process per card
- [ ] Extended resources are only counted at node level, not pod level
> CPU is overcommittable because CFS quotas and cgroups can enforce each container's share. A GPU has no
> such host-side enforcement, so Kubernetes exposes it as an opaque integer extended resource with
> requests equal to limits. Multiple processes per card are possible (MPS, time-slicing) but without
> enforced shares or hard memory isolation; the driver does not forbid them.

### Q: Which sharing mechanism protects one tenant from another tenant's out-of-memory error on the same card?
- [ ] Time-slicing configured in the device plugin
- [ ] CUDA MPS
- [x] MIG hardware partitions
- [ ] Setting memory requests equal to limits on both pods
> MIG partitions an A100 or H100 into instances with their own memory and compute slices. Time-slicing
> shares one memory pool with no limits; MPS shares the pool and the fault domain (its per-client memory
> cap is opt-in), so one process can starve or crash another. Pod memory limits govern host RAM only.

### Q: A GPU container image contains the CUDA runtime but no driver. Where does the driver come from at run time?
- [ ] It is downloaded from the registry as a hidden image layer
- [ ] PyTorch bundles a user-space driver in its wheel
- [x] The NVIDIA Container Toolkit mounts the host node's driver libraries and device files into the container
- [ ] Kubernetes injects it through the device plugin's gRPC socket
> The kernel driver lives on the node and must match the kernel; the toolkit's runtime hook bind-mounts
> its user-space libraries and `/dev/nvidia*` into any container that requests GPUs. The device plugin
> only tells the scheduler which devices exist and which ones a container was allocated.
