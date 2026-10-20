---
id: serverless-gpus
module: infra-capacity-economics
name: Serverless GPUs
kind: pattern
difficulty: core
est_minutes: 14
related:
  - infra-capacity-economics/keda-autoscaling
  - infra-capacity-economics/break-even-analysis
  - infra-capacity-economics/cost-modeling
  - infra-capacity-economics/capacity-planning
  - infra-capacity-economics/latency-budgets
  - serving-inference/ttft
  - serving-inference/model-serving
sources:
  - Official docs: Modal
  - Official docs: RunPod Serverless
  - Official docs: Google Cloud Run (GPU support)
---

## TL;DR
Serverless GPUs bill per second of actual use and scale to zero, so a bursty or low-volume workload
pays only for busy seconds instead of for a card that idles most of the day. The price is a cold start
of tens of seconds to minutes (pull the image, load weights into VRAM, warm the engine) and a
per-second rate above on-demand hourly pricing. Decide on utilisation break-even shifted by cold-start
overhead, gated by whether your latency SLO can absorb a cold start at all.

## Why it exists
A dedicated GPU costs the same per hour whether it serves ten thousand requests or none. Most
internal tools, prototypes, batch jobs and early-stage products have traffic that is bursty, diurnal
or simply small: a document-processing pipeline that runs for two hours each night, an internal
assistant used during working hours in one time zone, a feature with a hundred requests a day. Paying
for 730 hours a month to use 20 of them is the single largest waste line in many AI budgets, and it
is invisible because the card shows up as "in use".

The naive fix, "autoscale the Kubernetes deployment to zero", runs into the fact that a GPU node
takes minutes to provision, pull a multi-gigabyte image and load weights; by the time the pod is
Ready the user has gone, and the cluster autoscaler may not even have the quota to add a node. The
other naive fix, "keep one replica warm", is just a dedicated GPU with extra steps. Serverless GPU
platforms exist to make the scale-from-zero path fast enough to be usable — pre-provisioned fleets,
cached images, weight caches on fast storage, process snapshots — and to bill at the granularity
(seconds) that makes bursty use cheap. They do not remove the cold start; they shrink it and hide the
node layer. Knowing what remains, and what it costs, is the engineering content of this topic.

## How it works
### The request path
A request arrives at the platform's router. If a warm container for your function exists and has
spare concurrency, it runs there immediately. Otherwise the platform takes a GPU from its pooled
fleet (it keeps nodes and often images pre-warmed *because* node provisioning is the slowest step and
amortises across tenants), starts your container, runs your initialisation, and only then serves the
request. After the container has been idle for a configurable scale-down window, it is stopped and
billing ends. Scaling out is the same path repeated: when in-flight requests exceed per-container
concurrency, another container is started.

### Anatomy of a cold start
Each stage has its own mitigation, and the total is the sum:
1. **Scheduling and container start.** Seconds on a pooled fleet; minutes if a node must be created.
2. **Image pull.** A multi-gigabyte CUDA image can take tens of seconds; lazy-loading image formats
   and keeping weights *out* of the image reduce this.
3. **Process start and imports.** Importing PyTorch, CUDA initialisation and an inference engine's
   setup take several seconds. Memory snapshots (checkpointing the process after imports, offered by
   some platforms) skip this on later starts; GPU-state snapshots are newer and provider-specific.
4. **Weight load.** Storage to host RAM to VRAM. 16 GB of bf16 weights from a local NVMe cache takes
   seconds; from a remote bucket it can take minutes. Platforms offer volumes or model caches on fast
   storage precisely to move this term from minutes to seconds; a network bucket on every start is
   the classic mistake.
5. **Engine warm-up.** CUDA graph capture, kernel compilation, `torch.compile`: tens of seconds for
   some engines. Either accept it, disable it for serverless, or snapshot after it completes.
Order of magnitude, illustrative: a tuned small-model function cold-starts in 10–30 seconds; an
untuned 70B deployment can take ten minutes. Measure yours; it is the key input to every decision
below.

### The knobs
- **Scale-down window (idle timeout).** Longer keeps containers warm through traffic gaps at the cost
  of paying for idle seconds; shorter saves money and causes more cold starts.
- **Minimum warm containers.** Removes cold starts for the base load, billed continuously at the
  serverless rate, which is usually above on-demand; this is where "serverless" quietly becomes
  "expensive dedicated".
- **Concurrency per container.** Letting one container handle several requests at once lets the
  engine batch them, raising throughput per GPU-second; set it from the batching lesson, not to 1.
- **GPU type.** Most platforms let you pick L4/A10-class through H100; the selection lesson's
  arithmetic applies unchanged, except that per-second billing makes low utilisation free.

### Billing shape
You pay per second of container lifetime (not per request), often for CPU and memory as well, at a
GPU rate above on-demand hourly pricing: the premium funds the idle pool and the fast storage.
Cold-start seconds are billed. Egress, storage volumes and request overhead may be separate lines.

### What it is not
Serverless GPUs are *your container on their pooled GPUs*. Per-token model APIs are a different
layer: no container, no GPU choice, billed per token; they are the right answer for standard open
models at modest scale. And not every "serverless" product has GPUs: AWS Lambda does not, and
SageMaker's serverless inference option has been CPU-only; Google Cloud Run and Azure Container Apps
have added GPU options, with constraints that change, so check current support before designing
around any of them.

## Example
An 8B model on an L4-class GPU. Illustrative prices, ballpark only: dedicated on-demand $0.80 per
hour ($584 for a 730-hour month); serverless $1.60 per hour-equivalent, billed per second, a 2×
premium. Measured cold start after tuning: 45 seconds. Scale-down window: 5 minutes.

*Workload A, internal document tool:* 1,000 requests per day at ~3 GPU-seconds each is 3,000 busy
seconds a day, ~25 hours a month, $40. Requests cluster in working hours with gaps, producing about
40 cold starts a day: 40 × 45 s = 30 minutes a day, ~15 hours a month, $24. Idle time inside the
5-minute windows adds perhaps as much again. Serverless total: roughly $90 against $584 dedicated,
a six-fold saving, and the 45-second cold start is tolerable for a tool that already takes seconds.

*Workload B, steady product traffic at 40% busy:* 292 busy hours × $1.60 = $467, cold starts
negligible because containers stay warm. Serverless still wins by 20%. The break-even is where the
rates cross: 0.80 ÷ 1.60 = 50% utilisation, minus whatever cold starts and idle windows add. At 60%
busy, serverless costs $700 against $584 and dedicated wins.

*Workload B with a latency SLO:* p95 response under 2 seconds. Even a 3% cold-start rate puts
45-second responses inside the p95, which fails the SLO. Keeping one container always warm costs
730 × $1.60 = $1,168, double the dedicated card. The right design is a dedicated card (or
Kubernetes replica) for the base load and serverless only for bursts above it, where the cold start
affects a small tail of requests during ramps. The arithmetic, not the branding, picks the layout.

## Trade-offs
- Per-second billing makes low and bursty utilisation cheap, but the rate premium makes steady high
  utilisation dearer than on-demand; the flip is near the ratio of the two prices.
- Scale-to-zero removes idle cost but introduces cold starts; it is right when the SLO tolerates
  them or traffic gaps are rare, wrong for tight tail-latency targets.
- Warm minimums and long idle windows buy latency with money, and past a point you are paying
  dedicated prices for a platform you do not control.
- Managed pooling hides nodes, drivers and images, at the cost of vendor-specific SDKs, less control
  over GPU topology and limits on multi-GPU or multi-node models.

## Pitfalls
- Loading weights from a remote bucket on every start; symptom is cold starts of minutes that no
  scale-down tuning fixes, visible as a bimodal latency histogram.
- Baking weights into the container image; symptom is image pulls that take longer than the model
  load and rebuilds for every checkpoint change.
- Setting per-container concurrency to one; symptom is many containers each running a batch of one,
  so GPU-seconds per request are several times higher than on a batched dedicated server.
- Forgetting that cold-start seconds are billed; symptom is a bill where start-up time rivals
  serving time, which happens whenever the scale-down window is short relative to request gaps.
- Promising a tight p95 on a scale-to-zero deployment; symptom is an SLO breach on the first request
  after every lull.

## When to use
Reach for serverless GPUs when utilisation would be low or bursty (internal tools, nightly batch,
prototypes, long-tail models that are rarely hit), when latency can absorb a cold start or the
platform's warm pool makes it rare, and when you want no node operations. Prefer dedicated capacity
or a Kubernetes deployment when sustained utilisation exceeds the price ratio, when tail latency is
contractual, or when the model spans multiple GPUs. Measure your cold start by stage, your busy
seconds per day and your p95 with cold starts included; those three numbers decide. In day-to-day
work this is the "why did the first request take a minute" ticket; in interviews, the expected
answer walks the cold-start anatomy and the break-even calculation.

## Role lens
- **Engineer:** Structures the function so imports and weight loading happen once at container start,
  keeps weights on a cached volume, tunes concurrency and idle windows, and measures cold-start stages.
- **Architect:** Decides which workloads live on serverless, which on dedicated capacity, and where
  the hybrid boundary sits, using measured utilisation and the SLO rather than vendor claims.
- **FDE:** Prototypes a customer pilot on serverless to avoid procurement delays, then presents the
  utilisation data that justifies moving to dedicated hardware when traffic becomes steady.

## Check yourself
**Q:** A customer's serverless LLM endpoint shows p50 latency of 1.2 s and p95 of 48 s. They ask
whether to raise the minimum warm containers to five. What do you investigate and recommend?
**A:** A 48-second p95 against a 1.2-second p50 is a cold-start signature: at least 5% of requests
start a container. First measure the cold start by stage (image pull, imports, weight load, warm-up)
and fix the cheap ones: weights on a cached volume instead of a bucket, snapshotting after imports,
disabling or pre-capturing warm-up. Then look at traffic: if gaps are frequent, a longer scale-down
window may cut cold starts more cheaply than warm containers. If base load is steady, one warm
container removes most cold starts; five warm containers is dedicated pricing for five cards and
probably exceeds an on-demand deployment. Recommend tuning first, then one warm container, and move
the base load to dedicated capacity if the utilisation arithmetic says so.

## Quiz
### Q: Why do serverless GPU platforms charge a per-second rate above on-demand hourly pricing?
- [ ] Per-second billing is more expensive to meter
- [x] The premium pays for the idle pooled fleet, cached images and fast storage that make scale-from-zero fast enough to use
- [ ] GPUs run at higher clocks in serverless mode
- [ ] Serverless containers get larger VRAM allocations
> The platform must keep provisioned GPUs and warm infrastructure waiting for tenants' bursts; that
> idle capacity is shared and amortised through the rate premium. Customers accept it because they
> pay only for busy seconds.

### Q: Which change most reduces cold-start time for a function whose 16 GB weights currently download from object storage on every start?
- [ ] Increasing per-container concurrency
- [ ] Choosing an H100 instead of an L4
- [x] Keeping the weights on a platform volume or local cache so they load from fast storage
- [ ] Shortening the scale-down window
> Weight loading from remote storage is usually the dominant cold-start stage; moving it to cached
> NVMe-class storage cuts minutes to seconds. A faster GPU does not speed a network download, and a
> shorter idle window causes more cold starts, not faster ones.

### Q: At what utilisation does serverless stop being cheaper than a dedicated card, ignoring cold starts?
- [ ] Always above 10%, because of the premium
- [ ] Never; per-second billing is always cheaper
- [x] Roughly where busy fraction equals the dedicated hourly rate divided by the serverless hourly-equivalent rate
- [ ] At 100%, when both bill the same hours
> With a 2× premium the crossover is 50% busy; cold starts and idle windows are billed too, so the
> real crossover is lower. Above it the dedicated card's constant price covers more tokens.
