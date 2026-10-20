---
id: docker
module: backend-fundamentals
name: Docker
kind: tool
difficulty: core
est_minutes: 14
related:
  - backend-fundamentals/kubernetes
  - backend-fundamentals/ci-cd
  - backend-fundamentals/secrets-management
  - node-ts-for-ai/docker-ci-cd-deployment
  - python-for-ai/lockfiles-and-dependency-pinning
  - agents/sandboxing
  - serving-inference/model-serving
  - infra-capacity-economics/gpu-infrastructure
sources:
  - Official docs: Docker
  - Official docs: Open Container Initiative (image and runtime specifications)
  - Official docs: NVIDIA Container Toolkit
---

## TL;DR
Docker packages an application and its exact dependencies into an immutable, layered image and runs it
as an ordinary Linux process fenced off by kernel namespaces and cgroups. It exists because "works on my
machine" is a dependency problem, and the fix is to ship the relevant parts of the machine (Python
version, CUDA libraries, system packages) together with the code. A container is not a virtual machine:
it shares the host kernel, which is why it starts in milliseconds and why its isolation is weaker.

## Why it exists
An AI service depends on a matrix: a Python version, a `torch` build compiled against a particular CUDA
and cuDNN, system libraries for PDF parsing or audio, and a few hundred transitive packages. Installing
that matrix by hand on a laptop, a CI runner and three production hosts yields four slightly different
environments, and the differences surface as the bug that only happens in production. Configuration
management tools (Ansible, Chef) converge a host toward a description, but the host still drifts between
runs, and a package that was available yesterday may resolve differently today.

Virtual machines solve isolation and reproducibility, but each carries a full operating system to boot,
patch and store: minutes to start, gigabytes per instance, and a hypervisor tax on every workload. That
is too heavy to use as the unit of deployment for a service you redeploy ten times a day.

Docker (2013) made two long-standing kernel features, namespaces and cgroups, usable by combining them
with three things that did not exist together before: a build recipe (the Dockerfile), a layered,
content-addressed image format, and a registry to push and pull images. The result is an artifact that
CI builds once, tests, and every environment runs byte-for-byte unchanged. That artifact is what
Kubernetes schedules and what a rollback points back to.

## How it works
### Images: content-addressed layers
Each Dockerfile instruction that changes the filesystem produces a layer: a tarball of the diff,
identified by the digest of its content. An image is a manifest listing those layers plus a config
(entrypoint, environment, user, exposed ports). At run time a union filesystem (overlay2 on Linux)
stacks the read-only layers and adds a thin writable layer on top, copy-on-write. Because layers are
content-addressed, identical layers are stored once on disk and in the registry, and the build cache can
reuse a layer whenever its inputs (the instruction, the files it copies and the parent layer) are
unchanged. The practical consequence: order the Dockerfile from least to most frequently changing, base
image, system packages, lockfile and dependencies, then source code, so that a code edit rebuilds only
the last layer. With PyTorch images running to several gigabytes (illustrative), this ordering is the
difference between a ten-second and a ten-minute rebuild.

### Containers: a process with a restricted view
`docker run` asks the daemon, through containerd and runc (an OCI runtime), to start a process inside
fresh namespaces: `pid` (it sees only its own processes), `net` (its own interfaces and ports), `mnt`
(its root filesystem is the image), `uts`, `ipc` and optionally `user` (root inside maps to an
unprivileged uid outside). cgroups cap CPU and memory: with `--memory=2g` the kernel OOM-kills the
process at the limit rather than the host. Linux capabilities are dropped and a default seccomp profile
filters syscalls. It is still the host kernel, so a kernel vulnerability escapes the container; that is
why hostile code (user-submitted scripts, agent-generated code) wants a microVM or gVisor as an extra
boundary. GPUs work by bind-mounting the device nodes and driver libraries into the container (the NVIDIA
Container Toolkit, `--gpus all`), because the driver lives in the host kernel and only the CUDA runtime
ships in the image.

### PID 1 and signals
The entrypoint becomes PID 1 inside its pid namespace. PID 1 gets no default signal handlers and must
reap zombie children, so `docker stop` (SIGTERM, then SIGKILL after a grace period) is ignored by a
server that does not handle SIGTERM, and a shell-form `CMD python app.py` leaves `sh` as PID 1 and the
server never sees the signal at all. Use the exec form (`ENTRYPOINT ["uvicorn", …]`) and `--init` or
`tini` when the process spawns subprocesses.

### Networking and state
Each container has its own network namespace; ports are published with `-p host:container`, and
containers on a user-defined bridge network resolve each other by name, which is what Compose sets up
for a local API, database and vector store. The writable layer dies with the container, so anything
durable (a database, a Hugging Face model cache) lives in a volume or bind mount. Model weights are
either mounted from a volume or object store, or deliberately baked into the image for deterministic
cold starts at the cost of image size.

### Build hygiene
Multi-stage builds install and compile in a builder stage and copy only the results into a slim runtime
stage, so build tools and caches never ship. `.dockerignore` keeps `.git`, datasets and `.env` out of
the build context. Run as a non-root `USER`. Pin the base image to a specific tag or digest; `latest`
is mutable. `--mount=type=secret` provides a credential to one build step without writing it to a
layer; `--mount=type=cache` keeps the pip cache between builds. Tag images with the git SHA and let the
deployment reference the digest.

### Compared with the alternatives
VMs and microVMs (Firecracker) have their own kernel: stronger isolation, slower start; the right tool
for untrusted multi-tenant code. Podman offers the same CLI daemonless and rootless. containerd and
CRI-O are the runtimes Kubernetes drives directly; Docker is the developer-facing toolchain around the
same OCI images. Nix and conda make dependencies reproducible without process isolation.

## Example
A multi-stage Dockerfile for a FastAPI inference service. Dependencies are installed before the source
is copied so the expensive layer is cached, the runtime stage contains no compiler or pip cache, the
process runs as a non-root user, and the exec-form entrypoint receives SIGTERM directly.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .                      # lockfile first: this layer survives code edits
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /install /usr/local      # installed packages only; no build tools, no cache
COPY --chown=app:app src/ ./src
ENV PYTHONUNBUFFERED=1 HF_HOME=/home/app/.cache/huggingface
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
ENTRYPOINT ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build, run with limits, and inspect:

```bash
docker build -t embed-api:$(git rev-parse --short HEAD) .
docker run --rm -p 8000:8000 --memory=4g --cpus=2 \
  -v hf-cache:/home/app/.cache/huggingface embed-api:abc1234     # model cache survives restarts
docker history embed-api:abc1234      # confirm no secret and no multi-GB layer you did not intend
docker stats                          # memory against the cgroup limit, not the host
```

Editing a file under `src/` and rebuilding reuses every layer up to `COPY --chown=app:app src/`, so
the rebuild is seconds; changing `requirements.txt` invalidates the install layer and everything after
it.

## Trade-offs
- Reproducible artifacts and millisecond start-up, paid for in image size, registry storage and weaker
  isolation than a VM; it flips to microVMs or gVisor when the code inside is untrusted.
- Layer caching makes builds fast but makes it easy to ship stale base layers with unpatched libraries;
  rebuild from the base on a schedule, not only when code changes.
- Baking model weights into the image gives deterministic deploys and fast cold starts, but produces
  multi-gigabyte images and slow pushes; mounting weights from a volume or object store reverses both.
- Running as non-root with a read-only filesystem hardens the container but breaks tools that write to
  `/root/.cache`; set `HOME` and cache variables to a writable path instead of giving up.
- On macOS and Windows, Docker Desktop runs a Linux VM, so bind-mount I/O is slow and GPU passthrough is
  unavailable; CUDA work needs a Linux host.

## Pitfalls
- Copying the whole repository before installing dependencies; the symptom is a ten-minute rebuild of
  `torch` after a one-line code change.
- Shell-form `CMD`; the symptom is `docker stop` taking the full grace period and Kubernetes rollouts
  killing requests mid-flight because SIGTERM never reached the server.
- Deploying `latest`; the symptom is a "nothing changed" deploy that behaves differently. Pin a tag or
  digest and tag builds with the commit.
- Secrets in `ENV`, `ARG` or a copied `.env`; the symptom is `docker history` or an image scan
  revealing them. Use build secrets and runtime injection.
- Ignoring the memory limit; the symptom is exit code 137 with no traceback, because the Python process
  saw the host's RAM and the cgroup killed it.
- Running as root by default; the symptom is a failed security review and a larger breakout surface.
  Add a `USER`.
- A huge build context (datasets, `.git`); the symptom is "sending build context" taking minutes. Add a
  `.dockerignore`.
- A CUDA runtime in the image newer than the host driver supports; the symptom is "CUDA driver version
  is insufficient" at start-up on a host that worked last month.

## When to use
Containerise anything you deploy more than once or on more than one machine, every CI job that must be
reproducible, local development of CUDA-dependent stacks, and the execution sandbox for agent-generated
code (with additional hardening, or a microVM, because the kernel is shared). Do not bother for a
one-off script on a laptop, and never treat the container boundary alone as the security boundary for
hostile code. Measure image size, build time with a warm cache, and cold-start time including model
load. In interviews, expect to explain image versus container, why layer order matters, namespaces
versus cgroups, why a container is not a VM, and what PID 1 has to do with graceful shutdown.

## Role lens
- **Engineer:** Writes multi-stage Dockerfiles for inference and worker services, orders layers for
  cache hits, runs as non-root, and debugs exit-137 and signal-handling problems.
- **Architect:** Sets the base-image policy, where model weights live relative to the image, the
  registry and tagging scheme, and when a workload needs a microVM rather than a container.
- **FDE:** Hands the customer a single image that runs identically in their cluster, air-gapped
  registry or VPC, and troubleshoots driver and CUDA mismatches on their GPU hosts.

## Check yourself
**Q:** A team's inference image takes twelve minutes to build on every commit, weighs 9 GB, runs as
root, and Kubernetes rollouts drop requests. Diagnose each symptom and name the fix.
**A:** Twelve-minute builds mean the dependency install layer is invalidated on every change, almost
always because `COPY . .` precedes `pip install`; copy the lockfile first, install, then copy source,
and add a pip cache mount. Nine gigabytes usually combines a full base image with build tools, a pip
cache and model weights baked in; use a slim base, a multi-stage build that copies only the installed
packages, a `.dockerignore`, and mount weights from a volume or object store unless cold-start
determinism justifies baking them. Root is a one-line `USER` fix plus writable cache paths. Dropped
requests on rollout point to a shell-form `CMD` or a server that does not handle SIGTERM: switch to the
exec form, confirm uvicorn is PID 1, and make the readiness probe fail before shutdown begins.

## Quiz
### Q: Why does changing one line of application code sometimes rebuild the entire image?
- [ ] Docker always rebuilds every layer when any file in the context changes
- [x] The source was copied before the dependency install, so the cache is invalidated from that layer onward
- [ ] Multi-stage builds cannot use the layer cache
- [ ] The base image tag resolved to a new digest
> The cache reuses a layer only if its instruction, inputs and parent are unchanged. A `COPY` of the
> whole tree early in the file makes every later layer, including the expensive install, a cache miss.

### Q: What makes a container start in milliseconds while a VM takes much longer?
- [ ] Containers use a compressed image format that is faster to read
- [ ] Containers run only interpreted languages, which need no boot
- [x] A container is a process on the host kernel inside namespaces and cgroups, so there is no guest kernel or operating system to boot
- [ ] Containers are pre-started by the daemon and kept in a pool
> No kernel boots and no init system runs; the daemon creates namespaces, mounts the image and
> executes the entrypoint. The same fact explains why isolation is weaker than a VM's.

### Q: A container exits with code 137 under load and prints no traceback. What happened?
- [ ] The application raised an unhandled exception that Docker suppressed
- [x] The cgroup memory limit was exceeded and the kernel's OOM killer sent SIGKILL
- [ ] The health check failed three times and Docker stopped the container
- [ ] The process received SIGTERM from `docker stop` and shut down cleanly
> 137 is 128 + 9, the signal number of SIGKILL, which the OOM killer uses when a cgroup passes its
> memory limit. A health-check failure only marks the container unhealthy, and SIGTERM would be 143.
