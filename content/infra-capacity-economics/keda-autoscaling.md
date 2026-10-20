---
id: keda-autoscaling
module: infra-capacity-economics
name: KEDA autoscaling
kind: tool
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - infra-capacity-economics/serverless-gpus
  - infra-capacity-economics/inference-clusters
  - infra-capacity-economics/queueing-theory
  - infra-capacity-economics/capacity-planning
  - backend-fundamentals/kubernetes
  - serving-inference/batching-and-concurrency-limits
  - serving-inference/ttft
  - reliability/backpressure-and-load-shedding
sources:
  - Official docs: KEDA
  - Official docs: Kubernetes Horizontal Pod Autoscaler
---

## TL;DR
KEDA (Kubernetes Event-Driven Autoscaling) lets a Deployment or Job scale on the signal that actually
measures demand, such as queue depth, in-flight requests or a Prometheus query, and lets it scale to zero.
GPU inference needs this because CPU and GPU utilisation are poor demand signals for LLM servers. KEDA
moves pod counts only; nodes still come from the cluster autoscaler, and GPU cold starts still take minutes.

## Why it exists
The built-in Horizontal Pod Autoscaler (HPA) was designed for stateless web services whose CPU usage
rises with load. An LLM server breaks that assumption twice. First, its CPU barely moves: the work
happens on the GPU, and the Python process mostly waits on CUDA kernels. Second, GPU utilisation (the
`DCGM_FI_DEV_GPU_UTIL`-style "percent of time a kernel was running") reads near 100% as soon as one
request is decoding, because a single sequence keeps kernels busy while using a sliver of the card's
arithmetic. So a CPU-based HPA never scales, and a GPU-utilisation-based one scales at the first request
and then cannot tell 4 requests from 40.

The quantity that actually tracks pain is demand: requests waiting in the engine's queue, requests in
flight per replica, or messages piling up in a broker for batch workers. The HPA can consume such
"external" metrics, but only if something serves them through the Kubernetes external metrics API, and
it cannot scale below one replica in a stock cluster (scale-to-zero is behind an alpha feature gate).
Teams used to bolt on a Prometheus adapter, write custom controllers for zero, and duplicate
credentials for every queue. KEDA packages that: dozens of ready-made "scalers" for queues, databases,
cloud services and Prometheus, an external-metrics server, and its own 0↔1 activation logic. For GPU
workloads, where an idle replica can cost dollars per hour, both halves matter: the right signal and the
ability to release the card.

## How it works
### Components
KEDA runs as an operator plus a metrics API server in the cluster. You declare intent with custom
resources:
- **ScaledObject** targets anything with a `/scale` subresource (usually a Deployment or StatefulSet).
- **ScaledJob** creates Kubernetes Jobs in proportion to pending work; each Job runs to completion,
  which suits batch inference where a pod should not be killed mid-item.
- **TriggerAuthentication / ClusterTriggerAuthentication** hold references to secrets or workload
  identity for the scalers, so the queue password is not pasted into every manifest.

### Two control loops, split at one replica
KEDA does not replace the HPA; it drives it. The split is the most important thing to understand:
1. **Activation (0↔1), owned by KEDA.** Every `pollingInterval` seconds (default 30) the operator asks each
   trigger whether it is active, meaning above `activationThreshold`. If any is active and the target is at
   zero, KEDA sets replicas to `minReplicaCount` or 1. When all triggers have been inactive for
   `cooldownPeriod` seconds (default 300), it scales back to zero. `cooldownPeriod` governs *only* this
   last step.
2. **Scaling (1↔N), owned by an HPA that KEDA creates.** KEDA registers each trigger as an external
   metric and generates an HPA pointing at it. The HPA then applies its usual rule: for an `AverageValue`
   target (the default for most scalers), `desired = ceil(total_metric / threshold)`, clamped to
   `[minReplicaCount, maxReplicaCount]`, with a default tolerance of about 10% to avoid flapping.
   Scale-down is damped by the HPA `behavior` stabilisation window (default 300 s); scale-up has no
   stabilisation window by default but is rate-limited by the default policy to the larger of +4 pods
   or +100% every 15 seconds. You tune both through `advanced.horizontalPodAutoscalerConfig.behavior`.

With several triggers, the HPA computes a replica count per metric and takes the maximum, so you can
combine "queue depth" with a `cron` trigger that pre-warms capacity before the morning peak.

### Why queue depth beats latency as a trigger
Scaling on p95 latency is tempting because latency is what the SLO says. But latency is a lagging,
saturating signal: it barely moves until the server is nearly full and then explodes (the queueing
knee), and adding replicas changes it only after the new pods are serving, which for a GPU pod can be
several minutes. A controller acting on that signal over-reacts and oscillates. In-flight plus waiting
requests is a leading, roughly linear signal: by Little's law, concurrency = arrival rate × time in
system, so it rises the moment traffic does, and dividing it by a measured per-replica capacity gives a
replica count directly.

### What KEDA does not do
KEDA changes a number in a Deployment. If the new pod requests `nvidia.com/gpu: 1` and no node has a free
GPU, the pod sits Pending until the cluster autoscaler or Karpenter adds a node, subject to cloud quota
and stock. Then the image is pulled and the weights loaded. KEDA cannot shorten any of that; it only
decides earlier. For plain HTTP scale-to-zero, something must hold requests while the first pod starts;
the separate KEDA HTTP add-on provides an interceptor proxy for that, and serverless GPU platforms exist
because doing it well is hard.

## Example
A vLLM Deployment serving an 8B model. A load test showed one replica holds its TTFT target up to about 16
concurrent sequences (illustrative: measure your own). The trigger sums running and waiting requests
across replicas and targets 12 per replica, keeping headroom for the minutes a new pod takes to arrive.

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: chat-llm
spec:
  scaleTargetRef:
    name: vllm-chat                 # the Deployment
  minReplicaCount: 1                # 0 only if a cold start fits the SLO
  maxReplicaCount: 8                # match the GPU quota you actually have
  pollingInterval: 15
  cooldownPeriod: 900               # only for the final 1 -> 0 step
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:   {stabilizationWindowSeconds: 0}
        scaleDown:
          stabilizationWindowSeconds: 600
          policies: [{type: Pods, value: 1, periodSeconds: 120}]
  fallback:                         # if Prometheus is unreachable, hold 4 replicas
    failureThreshold: 3
    replicas: 4
  triggers:
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring:9090
        query: >
          sum(vllm:num_requests_running{model_name="chat-8b"})
          + sum(vllm:num_requests_waiting{model_name="chat-8b"})
        threshold: "12"
        activationThreshold: "0"
```

Walkthrough: at 09:00 traffic reaches 70 concurrent requests. The HPA computes ceil(70 / 12) = 6 replicas
and gets there within one or two 15-second policy periods (no stabilisation window; the default scale-up
policy allows +4 pods or +100% per period, and the `scaleUp` block above keeps those default policies). If only two GPU nodes are free, four pods go Pending until
new nodes join, which is why a cron trigger setting 4 replicas at 08:45 is often cheaper than SLO
misses. At 18:00 load falls to 10; the HPA removes at most one pod every two minutes after a ten-minute
window, so a brief lull does not throw away warm replicas that cost minutes to rebuild. Note the vLLM
metric names follow its Prometheus exporter and can change between versions; check `/metrics`.

## Trade-offs
- Scaling on queue depth tracks demand linearly and early, but it needs a measured per-replica capacity;
  when prompt lengths shift (longer RAG contexts), the same request count means more load and the threshold drifts.
- Scale-to-zero saves the full idle cost of a GPU, but the first request after idle waits for node
  provisioning, image pull and weight loading; it flips to worthwhile only when idle hours dominate and callers tolerate minutes.
- Slow scale-down keeps warm replicas through short lulls at the price of paying for idle GPUs; with
  per-second billing and fast-loading small models, a shorter window becomes the better bet.
- One KEDA layer over standard Deployments is simpler than adopting Ray Serve or a serverless platform,
  but you own cold-start mitigation, request buffering and node provisioning yourself.

## Pitfalls
- Scaling a scaled-to-zero vLLM Deployment on vLLM's own metrics; with zero pods the series vanish, the
  query returns nothing, and the service never wakes. Activate from a signal that exists without pods (gateway queue, broker depth).
- Writing a per-pod average query while the trigger uses `AverageValue`; the HPA divides an
  already-averaged number again, and the symptom is a deployment stuck near one replica under heavy load.
- Setting `maxReplicaCount` above real GPU quota; the symptom is pods Pending for hours while dashboards
  claim the autoscaler "scaled up", hiding a capacity problem behind a scaling one.
- Expecting `cooldownPeriod` to slow scale-down from 6 to 2 replicas; it only applies to the last
  replica, and the symptom is replicas dropping after five minutes despite a long cooldown.
- Letting the HPA remove a pod mid-generation without a long `terminationGracePeriodSeconds` and a drain
  hook; the symptom is truncated streamed responses every time load falls.

## When to use
Reach for KEDA when a Kubernetes workload's demand is visible in some external signal and CPU is not it:
LLM servers, embedding workers draining a queue, nightly batch inference as ScaledJobs, or anything that
should sit at zero outside working hours. Do not reach for it to fix cold starts (it cannot), for
sub-second bursty HTTP traffic that needs request buffering (look at serverless GPUs), or when you already
run Ray Serve, whose own autoscaler fills the same role. Measure per-replica capacity under realistic
prompt lengths before choosing a threshold, then watch the replica count against queue depth, Pending
pods, and TTFT during ramps. In interviews, the signal is explaining why GPU utilisation is the wrong
metric and where the 0↔1 and 1↔N loops split.

## Role lens
- **Engineer:** Writes the ScaledObject, chooses the Prometheus query and threshold from a load test,
  and adds graceful drain so scale-down never cuts off a streaming response.
- **Architect:** Decides which services may scale to zero, the scaling signal per workload class, and how
  pod autoscaling couples to node provisioning, GPU quota and warm-pool policy.
- **FDE:** Installs KEDA inside a customer's existing cluster, maps their queue or gateway metrics to
  triggers, and explains why the first morning request is slow unless a cron pre-warm is added.

## Check yourself
**Q:** A team autoscales its vLLM pods with an HPA on GPU utilisation targeting 70%. The deployment
sits at its maximum replica count even overnight, when traffic is a fraction of the daytime peak, and
the GPU bill is huge.
Explain what is happening and design a better setup.
**A:** GPU "utilisation" reports the fraction of time any kernel is running, so it reads near 100% as
soon as one or two requests are decoding on each replica and says nothing about how many more could fit. The
HPA sees every replica above target, scales up, and never finds a reason to scale down; the signal
carries no proportional information, so capacity is set by the cap rather than by demand. Better: a
KEDA ScaledObject on running plus waiting requests summed across
replicas, threshold set from a load test at below-knee concurrency, fast scale-up, slow damped
scale-down, `maxReplicaCount` equal to GPU quota, a cron trigger to pre-warm before peaks, and a node
autoscaler with GPU node pools. If scale-to-zero is wanted, activate from a gateway or queue metric.

## Quiz
### Q: In a KEDA ScaledObject, which mechanism controls scaling from 5 replicas down to 3?
- [ ] The `cooldownPeriod` field
- [x] The HPA that KEDA generates, damped by its scale-down behavior and stabilisation window
- [ ] The `pollingInterval` field alone
- [ ] The cluster autoscaler removing GPU nodes
> KEDA itself handles only activation between zero and one replica, and `cooldownPeriod` applies to that
> final step. Between one and N, the generated HPA decides, using its behavior policies and window.

### Q: Why is in-flight plus waiting request count a better trigger for an LLM server than p95 latency?
- [ ] Latency metrics cannot be exported to Prometheus
- [ ] KEDA does not support latency-based triggers
- [x] Concurrency rises linearly and immediately with demand, while latency lags and spikes near saturation, causing oscillation
- [ ] Request counts are unaffected by prompt length
> By Little's law concurrency tracks arrival rate times service time, so it is a leading signal. Latency
> stays flat until the queueing knee, then jumps, and reacts only after slow GPU pods arrive.

### Q: A ScaledObject scales a vLLM Deployment to zero using a Prometheus query over vLLM's own metrics. What happens when traffic returns?
- [ ] KEDA buffers the requests and replays them once a pod is ready
- [x] The service may never wake, because with zero pods the metric series do not exist to cross the activation threshold
- [ ] The HPA scales from zero to one on its own
- [ ] The cluster autoscaler detects pending requests and starts a pod
> A trigger must be observable while nothing is running. Activate from a gateway, queue or broker metric,
> or use the HTTP add-on; the stock HPA does not scale from zero, and nothing buffers requests by default.
