---
id: docker
module: backend-fundamentals
name: Docker
kind: tool
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - backend-fundamentals/kubernetes
  - backend-fundamentals/ci-cd
  - backend-fundamentals/secrets-management
  - node-ts-for-ai/docker-ci-cd-deployment
  - python-for-ai/lockfiles-and-dependency-pinning
  - python-for-ai/uv-poetry-pip
  - fastapi-for-ai/graceful-shutdown-and-worker-draining
  - infra-capacity-economics/gpu-infrastructure
sources:
  - Official docs: Docker
  - Open Container Initiative — Image Format Specification
  - Official docs: NVIDIA Container Toolkit
---

## TL;DR
Docker packages an application with its exact filesystem (OS libraries, interpreter, CUDA runtime,
your code) into an immutable, content-addressed image, and runs it as an isolated process on the
host's kernel using Linux namespaces and cgroups. It exists because "install these dependencies on the
server" is not reproducible; the image makes the build the unit of deployment, so what passed CI is
byte-for-byte what runs in production. For AI services the image pins the CUDA and torch stack, which
is where most environment drift lives.

## Why it exists
A Python service depends on far more than its `requirements.txt`: the interpreter version, system
libraries such as glibc and OpenSSL, compiled wheels built against a specific CUDA version, locale
settings, environment variables. Installing all of that by hand or by script onto a VM drifts: two
servers provisioned three months apart differ in ways nobody recorded, "works on my machine" is a
literal description of the problem, and rolling back means uninstalling. Virtual machines fix
isolation, but a VM image is gigabytes with its own kernel, boots in tens of seconds to minutes,
and running one per service wastes memory and makes dev/prod parity painful, so people share VMs
and the drift returns.

The insight behind containers is that a process does not need its own kernel to be isolated. The Linux
kernel already has namespaces (what a process can *see*: its own process tree, network interfaces,
mount table) and cgroups (what it can *use*: CPU, memory, I/O). Add a filesystem built from a
declarative recipe, make it immutable and addressable by content hash, and add a registry to move it
between machines, and the artifact itself becomes the deployment unit: no install step on the target,
no drift, rollback is pointing at the previous image.

For AI work this is the difference between a day of "which torch wheel matches which CUDA" and an
image that runs identically on a laptop GPU, a CI runner and a Kubernetes GPU node. The naive
alternative, a virtualenv plus a setup script, pins Python packages but nothing beneath them, and the
things beneath them (CUDA libraries, system codecs, build toolchains) are exactly what breaks.

## How it works
### Images: layers and content addressing
A Dockerfile is a sequence of instructions. Each `RUN`, `COPY` and `ADD` produces a layer: a tarball
of the filesystem changes relative to the previous layer, identified by the hash of its contents. An
image is an ordered list of layer digests plus a config (entrypoint, environment, exposed ports,
user). Layers are immutable and shared: twenty services built on the same `python:3.12-slim` base hold
one copy of those layers on disk and in the registry, and a push uploads only layers the registry
lacks. At run time a union filesystem (overlay2 on most hosts) stacks the layers read-only and adds a
thin writable layer per container. Writes to that layer disappear with the container, which is *why*
durable state goes in volumes and not in the container filesystem.

The build cache follows from this. Each instruction's result is cached, keyed on the instruction and
its inputs (for `COPY`, the content of the copied files). The first instruction whose inputs changed
invalidates every later layer, so ordering is a performance decision: copy the dependency manifest,
install, then copy source, because source changes every commit and dependencies change every few
weeks. Get it backwards and every commit reinstalls torch.

### Containers: namespaces and cgroups
`docker run` starts a process with its own PID namespace (it sees itself as PID 1), network namespace
(own interfaces and port space), mount namespace (its root is the image), UTS and IPC namespaces, and
optionally a user namespace. A cgroup caps CPU and memory: exceeding `--memory` triggers the kernel's
OOM killer for that container, which exits with code 137 and no application traceback. By default a
container also drops most Linux capabilities and runs under a seccomp profile that filters syscalls.
It remains a normal process: `ps` on the host shows it, a kernel vulnerability escapes it, and it
starts in milliseconds. Isolation is therefore good enough for your own services and not enough for
running untrusted code, which is why agent sandboxes use stronger boundaries such as gVisor or
microVMs.

### PID 1 and signals
The container's main process is PID 1 in its namespace, and the kernel treats PID 1 specially: default
signal dispositions do not apply, so a process without an explicit SIGTERM handler ignores
`docker stop` and is SIGKILLed when the grace period (10 seconds by default) expires. Two consequences:
use the exec form `CMD ["uvicorn", ...]` rather than the shell form `CMD uvicorn ...`, because the
shell form wraps the command in `/bin/sh -c`, and unless that shell happens to `exec` the command
(behaviour varies by shell and command), the shell is PID 1 and does not forward the signal;
and if the app spawns children, run under `--init` or `tini` so zombies are reaped. This plumbing is
what makes graceful draining of an in-flight streaming response possible later in Kubernetes.

### Tags, digests and registries
A tag such as `:1.4.2` or `:latest` is a mutable pointer; the digest `sha256:…` is the immutable
identity. Deploy by digest or by an immutable tag like the git SHA, because `:latest` on two nodes can
mean two different images.

### Multi-stage builds
A build stage with compilers, headers and dev tools produces artifacts; the final stage copies only
those artifacts into a slim runtime base. The toolchain never ships, the image shrinks, and the attack
surface with it.

### GPUs
Containers see no GPU by default. The NVIDIA Container Toolkit injects the device nodes and the host
driver's user-space libraries when you run with `--gpus all`. The split is: the image carries the CUDA
runtime and torch, the host carries the kernel driver, and the host driver must be new enough for the
CUDA version in the image. ML images are large (several gigabytes is typical, as an order of
magnitude), so base-image choice and layer ordering directly set pull time, which becomes cold-start
time when autoscaling.

### Compose
`docker compose` runs several containers (API, Redis, Postgres, a vector database) on one network with
named volumes from a single file: the local development stack that mirrors production topology.

## Example
A multi-stage Dockerfile for a FastAPI inference service using `uv`. Dependencies are installed from
the lockfile in their own layer before the source is copied, the runtime stage contains only the
virtualenv and code, the process runs as a non-root user, and the exec-form `CMD` makes uvicorn PID 1
so it receives SIGTERM. Pin the `uv` image to a specific version in a real repository.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
# Dependencies first: this layer is rebuilt only when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health')" || exit 1
# Exec form: uvicorn is PID 1 and receives SIGTERM from `docker stop`.
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run it with an immutable tag and a memory cap; a `.dockerignore` listing `.git`, `.venv`,
`data/` and model weights keeps the build context small and the `COPY` cache stable:

```bash
docker build -t llm-api:$(git rev-parse --short HEAD) .
docker run --rm -p 8000:8000 --memory=2g -e LLM_SECRET_ID=prod/llm llm-api:$(git rev-parse --short HEAD)
docker history llm-api:$(git rev-parse --short HEAD)   # where did the bytes go?
```

## Trade-offs
- Images make deploys reproducible and rollback a pointer change, but a multi-gigabyte CUDA image takes
  minutes to pull onto a fresh node, so autoscaling latency becomes an image-size problem; the fix flips
  toward runtime bases, pre-pulled nodes and weights stored outside the image.
- Sharing the host kernel gives near-native performance and millisecond startup compared with a VM,
  but weaker isolation; for code you did not write, such as agent-generated scripts, the balance flips
  to gVisor or microVM sandboxes.
- Layer caching makes rebuilds fast when instructions are ordered well, but the cache only knows the
  inputs the Dockerfile shows it; an `apt-get update` cached from last month or a `pip install`
  without a lockfile yields different images on different days.
- One container holding the API and the model is simple to run, but splitting the model server from
  the API adds a network hop in exchange for scaling and restarting the two independently.

## Pitfalls
- Shell-form `CMD` or an application without a SIGTERM handler; the symptom is `docker stop` taking
  exactly ten seconds and in-flight requests dying, and later, rolling updates dropping streams.
- Running as root; the symptom is every security review flagging it, and any code-execution bug
  becoming root inside the container. Add a `USER`.
- No `.dockerignore`; the symptom is builds that send gigabytes of context (`.git`, `.venv`,
  datasets, weights) and miss the cache on every build because a `COPY` input changed.
- Copying source before installing dependencies; the symptom is every one-line change reinstalling
  torch for several minutes.
- A `:latest` base image or unpinned dependencies; the symptom is a rebuild of the "same" Dockerfile
  producing a different image that fails, usually on the day of an incident.
- Writing state inside the container; the symptom is data vanishing on restart, plus slow writes
  through the overlay filesystem.
- CUDA runtime newer than the host driver supports; the symptom is a "driver version is insufficient
  for CUDA runtime version" error on one node pool and not another.
- A memory limit below real peak usage; the symptom is silent restarts with exit code 137 and no
  Python traceback, typically during model load or a large batch.

## When to use
Containerize anything you deploy more than once or onto more than one machine: inference APIs,
workers, eval jobs, fine-tuning runs. Skip it for a notebook on your laptop, and note that serverless
platforms that accept source or a zip build the image for you. Measure image size, cached and uncached
build time, pull time, and time from container start to ready, and watch each of them when a deploy
feels slow. Day to day you write Dockerfiles, debug with `docker exec`, `docker logs` and
`docker history`, and run the local stack with Compose. In interviews, be ready to explain layers
versus containers, namespaces versus cgroups, why PID 1 matters, and why instruction order changes
build time.

## Role lens
- **Engineer:** Writes the multi-stage Dockerfile, pins the base image and lockfile, handles signals
  for graceful shutdown, keeps the CUDA and torch versions consistent, and keeps the image small.
- **Architect:** Decides what lives in the image versus mounted at runtime (weights, config), sets the
  base-image and registry-scanning policy, and chooses the isolation level for untrusted execution.
- **FDE:** Ships the product as images into the customer's registry or air-gapped environment, and
  works around their driver versions, proxies and approved-base-image restrictions.

## Check yourself
**Q:** Your inference image is about 9 GB and each new node takes six minutes to become ready during
autoscaling. Walk through what you would investigate and change.
**A:** Start with `docker history` to see where the bytes are; it is usually CUDA toolkit layers, torch
wheels and model weights copied into the image. In order of payoff: move the weights out of the image
onto object storage or a persistent volume with a node-local cache, so the image is only code and
runtime; switch to a CUDA runtime base instead of a devel base and use a multi-stage build so compilers
never ship; drop unused dependencies; and order layers so the heavy, stable ones (base plus
dependencies) are shared across versions, so a node that pulled last week's image already has most of
this week's. Then attack the platform side: pre-pull images with a DaemonSet, keep a warm node pool,
and use a registry in the same region. Finally separate pull time from model-load time in the
measurements, because the second needs a startup probe and a weight cache rather than a smaller image.

## Quiz
### Q: Why should a Dockerfile copy the dependency manifest and install before copying the application source?
- [ ] Because the source files need the installed packages present at `COPY` time
- [x] Because the first changed layer invalidates every later cached layer, so putting rarely-changing dependency installation first lets most rebuilds skip it
- [ ] Because Docker requires all `COPY` instructions to appear at the end of a build
- [ ] Because installing dependencies after the source makes the final image larger
> The cache is keyed on each instruction and its inputs, in order. Source changes on every commit,
> dependencies rarely, so installing first means a code change rebuilds only the cheap final layers.
> Swapping these two steps leaves the final image essentially the same size; only rebuild time changes.

### Q: A container ignores `docker stop` and is killed after ten seconds. What is the most likely cause?
- [ ] The container's memory limit is set too low
- [ ] The image was built without a `HEALTHCHECK`
- [x] The main process is PID 1 and does not handle SIGTERM, for example because `CMD` used shell form so `/bin/sh` received the signal
- [ ] The container is running as root
> PID 1 has no default signal handlers, so SIGTERM is dropped unless the process installs one, and the
> shell form typically leaves a shell as PID 1 that does not forward it. Memory limits produce OOM kills, not stop
> timeouts, and health checks do not affect signal delivery.

### Q: What does a container share with its host that a virtual machine does not?
- [ ] The root filesystem
- [ ] The environment variables of the host shell
- [x] The kernel
- [ ] The network namespace
> A container is a process isolated by namespaces and cgroups on the host's kernel, which is why it
> starts in milliseconds and why a kernel exploit escapes it. Its root filesystem comes from the image
> and it gets its own network namespace by default.
