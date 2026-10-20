---
id: docker-ci-cd-deployment
module: node-ts-for-ai
name: Docker / CI/CD deployment
kind: practice
difficulty: core
est_minutes: 16
status: reviewed
related:
  - backend-fundamentals/docker
  - backend-fundamentals/ci-cd
  - backend-fundamentals/kubernetes
  - backend-fundamentals/secrets-management
  - node-ts-for-ai/graceful-shutdown
  - node-ts-for-ai/vitest-jest-and-integration-testing
  - deployment/health-checks
  - llmops-platform/canary-shadow-deployments
sources:
  - Official docs: Docker — Dockerfile reference and multi-stage builds
  - Official docs: Node.js — Docker and signal handling
  - Official docs: GitHub Actions
---

## TL;DR
The general Docker and CI/CD lessons live in backend-fundamentals; this one covers what a Node/TypeScript
AI service needs from them. Build the image in stages so the runtime layer holds compiled JavaScript
and production dependencies only, run Node as PID 1 in exec form so `SIGTERM` can drain in-flight
model streams, inject provider keys at runtime rather than baking them in, and gate the pipeline on
typecheck, deterministic tests and a commit-tagged build, with real-model evals beside the gate, not
inside it.

## Why it exists
A TypeScript service does not run as written. It has to be compiled (or at least type-stripped),
its dependency tree is large and mostly only needed at build time, and `node_modules` can contain
native addons compiled for one libc. Shipping the source checkout and running `npm install` on the
server reproduces none of this reliably: the lockfile may be ignored, `devDependencies` land in
production, the compile step depends on whatever `tsc` version the host has, and the image weighs
hundreds of megabytes more than it needs to. Docker exists so the artifact that passed the tests is
the artifact that runs; the Node-specific work is arranging the build so that is actually true.

AI services add three pressures ordinary web services lack. First, requests are long: a streamed
completion holds a connection for seconds to minutes, so a deploy that kills the process on `SIGTERM`
without draining cuts users off mid-answer and wastes tokens already billed. Second, the process holds
the most sensitive secret in the company, the provider API key, and a key baked into an image layer is
visible to anyone who can pull it. Third, "the code" now includes prompts, tool schemas and model
identifiers whose change can regress quality without any test turning red; the pipeline has to version
them with the build and make the quality check a visible, budgeted step.

The naive pipeline, "build on push, deploy on green", is not wrong; it is incomplete. Without
reproducible installs it is not building what you tested; without a drain step it is not deploying
safely; without an eval beside the gate it is deploying prompt changes blind.

## How it works
### The image: stages because the runtime needs almost nothing
A multi-stage Dockerfile separates *installing*, *building* and *running*. The deps stage copies only
`package.json` and the lockfile and runs `npm ci` (`pnpm install --frozen-lockfile`, `yarn install
--immutable`): `ci` refuses to proceed if the lockfile and manifest disagree, which is exactly the
guarantee a build wants, and copying the manifests before the source means this expensive layer is
cached until a dependency changes rather than on every commit. The build stage adds the source and
runs `tsc` (or a bundler such as esbuild), then prunes dev dependencies. The runtime stage starts from
a fresh base image and copies in only `dist/`, the pruned `node_modules` and `package.json`. The result
is smaller, has a smaller attack surface, and cannot accidentally depend on a dev tool.

Choose the base deliberately. `node:<major>-slim` is Debian-based with glibc and is the safe default;
`node:<major>-alpine` is smaller but uses musl, which breaks some native addons and some prebuilt
binaries (tokenizer and ONNX packages are common victims). Pin the major version to an LTS line and
set `NODE_ENV=production`, which some libraries read to disable debug paths. A `.dockerignore` listing
`node_modules`, `.env`, `dist` and `.git` keeps the build context small and keeps local secrets out of
the image. Run as the `node` user the official image provides rather than root.

### Signals: exec form, PID 1 and draining
`CMD ["node", "dist/server.js"]` (exec form) makes Node PID 1. The shell form `CMD node dist/server.js`
runs `/bin/sh -c "node dist/server.js"`, so the shell is PID 1 and Node its child, and a shell does not
forward `SIGTERM` to its children; the orchestrator waits out its grace period and then `SIGKILL`s the
process mid-stream. (Some shells exec a lone `-c` command directly, which would accidentally make
Node PID 1; do not rely on it, since it breaks the moment someone adds `&&`.) Being PID 1 has two
consequences of its own. The kernel applies no default action to signals sent to PID 1, so Node only
reacts to `SIGTERM` if your code installs a handler; without one the signal is ignored and you again
wait for `SIGKILL`. And Node does not reap zombie children; if the service spawns subprocesses (an MCP
server over stdio, a converter), run with `docker run --init`, `tini` or `dumb-init` as the entrypoint. The drain itself, stop accepting, finish or
abort in-flight streams, close the pool, is the graceful-shutdown lesson; the deployment's job is to
deliver the signal and to set the grace period longer than the longest completion you allow.

### Memory and the container boundary
Node's default V8 heap limit is a heuristic derived from the memory it detects. Older releases read
the host's total memory and ignored the container's cgroup limit; newer ones read the cgroup limit,
but how well depends on the Node version and cgroup v1 versus v2. Either way the heap limit covers only
the JavaScript heap: `Buffer`s, native addons, thread stacks and the code cache live outside it, so the
process's resident memory can cross the container limit while V8 still believes it has room, and the
kernel's OOM killer ends it. Set `--max-old-space-size` explicitly, somewhat below the container limit
(the example leaves about a quarter of a 1 GiB limit for non-heap memory), and confirm with
`v8.getHeapStatistics()` at startup; streaming large contexts and holding many concurrent responses
are where Node services discover this.

### Health, readiness and config
Expose a liveness endpoint that only proves the event loop is responsive and a readiness endpoint that
checks dependencies (database, cache, and whether the process is draining). Keep the two apart
*because* a liveness probe that checks the database turns a database blip into a restart of every
replica at once. On shutdown, readiness flips to false the moment `SIGTERM` arrives, which is the
signal a plain load balancer uses to stop routing. Kubernetes adds a race: a terminating pod is
removed from the Service's endpoints, but that removal reaches kube-proxy and ingress controllers
asynchronously, so for a few seconds after `SIGTERM` new connections can still arrive. If the
process closes its listener immediately, those requests fail. The usual fix is a short `preStop`
sleep (or a delay in the handler) before `server.close()`, and the grace period must cover that
delay plus the longest stream, since `preStop` time counts against it. Configuration comes from the environment, parsed once at startup with a schema so a
missing `ANTHROPIC_API_KEY` or a non-numeric `PORT` fails before the first request. Secrets are
mounted or injected by the platform at runtime (Kubernetes Secrets, a secret manager, CI-provided
environment), never passed as `ARG`/`ENV` at build time: build args are recorded in image history.

### The pipeline
A pull request runs, in parallel where possible: `tsc --noEmit`, lint, unit tests, and integration
tests against service containers started by CI. `main` additionally builds the image once with
BuildKit, tags it with the git SHA (and a semver tag at release), pushes it to a registry, and deploys
that digest, so staging and production run byte-identical images and a rollback is a tag swap. Note
the gap this leaves: the PR tests ran against the source on the CI runner, not against the image.
A file missing from a `COPY`, a native addon built for the wrong libc, or a `dist/` path typo only
appears in the image, so a smoke test against the deployed digest in staging (start, hit readiness,
make one request through a stubbed or cheap model) is what makes "the artifact we tested" true. Prompt
files, tool schemas and the model identifier are part of the repository and therefore of the image;
changing them is a deploy like any other. Beside the gate, an eval job runs the golden set against
the real model with a budget and publishes its score; a drop blocks promotion to production without
making every pull request pay for model calls. Rollouts of prompt or model changes are safest as a
canary: route a slice of traffic, compare quality and cost metrics, then widen.

## Example
A multi-stage Dockerfile for a TypeScript service, then a trimmed GitHub Actions workflow that gates on
typecheck and tests against a real Postgres, builds once, and pushes an image tagged with the commit.
Secrets reach the runtime from the platform, not from either file.

```dockerfile
# syntax=docker/dockerfile:1
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci       # cached until the lockfile changes

FROM deps AS build
COPY tsconfig.json ./
COPY src ./src
RUN npm run build && npm prune --omit=dev             # dist/ plus production deps only

FROM node:22-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY package.json ./
USER node
EXPOSE 3000
# exec form so SIGTERM reaches node; heap set for a 1 GiB container memory limit
CMD ["node", "--max-old-space-size=768", "dist/server.js"]
```

```yaml
name: ci
on: { pull_request: {}, push: { branches: [main] } }
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: test }
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: npm }
      - run: npm ci
      - run: npm run typecheck && npm run lint && npm test
        env: { DATABASE_URL: postgres://postgres:test@localhost:5432/postgres }
  image:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
```

## Trade-offs
- Multi-stage builds shrink the runtime image and its attack surface but add build complexity; for a
  throwaway prototype a single-stage image is fine until it ships to a shared environment.
- Alpine images are smaller than slim ones, yet musl breaks some native tokenizer and ML addons; the
  saving flips to a loss the first time a prebuilt binary fails to load.
- Bundling with esbuild into one file speeds cold start and removes `node_modules` from the image, but
  it hides dependency versions from scanners and breaks packages that rely on `__dirname` or dynamic
  requires.
- Keeping evals out of the PR gate keeps merges fast and cheap, but only if the scheduled eval blocks
  promotion; an eval that merely reports is a dashboard nobody reads.
- A long termination grace period lets streams finish cleanly, while a very long one slows every
  rollout and masks handlers that never stop; cap it at the maximum completion time you actually allow.
- Pinning the base image by digest is reproducible but freezes security patches; a pinned major tag
  plus a scheduled rebuild is the usual compromise.

## Pitfalls
- Shell-form `CMD` or an entrypoint script that does not `exec`; the symptom is pods that always take
  the full grace period to stop and users whose streams cut off at every deploy.
- Copying the whole source before `npm ci`; the symptom is a dependency install on every commit and a
  ten-minute build for a one-line change.
- Passing the provider key as a build `ARG` or `ENV`; the symptom is the key readable in the image
  history (`docker history`) and in every registry that image was pushed to.
- Leaving the heap limit to the default heuristic; the symptom is OOM kills by the kernel under load
  (exit code 137) with no JavaScript heap error in the logs.
- Running `npm install` instead of `npm ci`; the symptom is a dependency version in production that no
  developer machine or CI run ever tested.
- A readiness probe that stays green while draining, or a listener closed the instant `SIGTERM`
  arrives with no `preStop` delay; the symptom is a burst of 502s or connection resets at every
  rollout even though the drain code looks correct.
- Deploying prompt or model-id changes with no eval in the pipeline; the symptom is a quality
  regression reported by users days after a deploy that "only touched a text file".

## When to use
Every Node AI service that runs anywhere but a laptop needs this; the question is how much. A single
multi-stage Dockerfile, `npm ci`, exec-form `CMD`, runtime secrets and a PR pipeline with typecheck and
tests are the floor. Add integration tests with service containers once a database or queue is in the
path, an explicit heap limit and readiness-aware drain once you stream, and a budgeted eval job as soon
as prompts change regularly. Measure image size, build time for a one-line change (with warm caches it
should cost little more than the compile step, since the install layer is reused), deploy duration, and the count of requests failed during a
rollout, which should be zero. Interviewers ask why `SIGTERM` is not reaching the process, what
`npm ci` guarantees, and where the API key should live; concrete answers to those three separate a
senior engineer from someone who has only run `docker build`.

## Role lens
- **Engineer:** Writes the Dockerfile and workflow, keeps the layer order cache-friendly, wires the
  readiness flip into shutdown and sets heap and grace-period values from measured completion times.
- **Architect:** Owns the promotion policy: which checks gate a merge, which gate production, how evals
  and canaries fit the rollout, and how secrets and model credentials are isolated per environment.
- **FDE:** Adapts the pipeline to the customer's registry, secret manager and cluster, and proves to
  their platform team that the image is non-root, lockfile-pinned and free of embedded keys.

## Check yourself
**Q:** After each deploy, a streaming chat service shows a spike of truncated responses and the old
pods linger for exactly thirty seconds before dying. What is happening, and what changes fix it?
**A:** Thirty seconds is Kubernetes' default `terminationGracePeriodSeconds`, and lingering for all of
it means the process never acted on `SIGTERM`. Three causes are common: the `CMD` is in shell form, so
`sh` is PID 1 and the signal never reaches Node; Node is PID 1 but installs no `SIGTERM` handler, and
the kernel ignores default-action signals for PID 1; or the handler exists but nothing stops the HTTP
server and in-flight streams. In each case the orchestrator finally sends `SIGKILL` mid-response. Fix it by switching to exec-form
`CMD` (or `exec` in the entrypoint script), handling `SIGTERM` by flipping readiness to false, waiting a few seconds
(a `preStop` sleep) for endpoint removal to propagate, then calling `server.close()`, aborting or finishing active streams with an `AbortController`, and closing pools;
then set the grace period above the longest allowed completion. Verify with a deploy under synthetic
streaming load and confirm zero truncated responses.

## Quiz
### Q: Why copy `package.json` and the lockfile and run `npm ci` before copying the source tree?
- [ ] Because `npm ci` fails if source files are present in the build context
- [x] So the dependency layer is cached and only rebuilt when the lockfile changes, not on every commit
- [ ] To keep `devDependencies` out of the final image
- [ ] Because TypeScript must be compiled before dependencies can resolve
> Docker caches layers by their inputs. If the source is copied first, every commit invalidates the
> install layer. Copying manifests first means the slow install reruns only when dependencies change.
> Pruning dev dependencies is a separate step and unrelated to layer order.

### Q: A container's `CMD` is written as `CMD node dist/server.js`. What is the practical consequence on `kubectl rollout`?
- [ ] Node starts faster because the shell preloads the module cache
- [ ] Environment variables are not expanded, so the process reads no configuration
- [x] `/bin/sh` becomes PID 1 and does not forward `SIGTERM`, so the process is killed after the grace period instead of draining
- [ ] The container cannot expose ports because the shell owns the network namespace
> Shell form wraps the command in `sh -c`. The shell receives the signal and does not pass it on, Node
> never sees it, and the orchestrator escalates to `SIGKILL`. Exec form (`CMD ["node", ...]`) makes Node
> PID 1 so the graceful-shutdown handler you install runs. Environment expansion and ports are unaffected.

### Q: Where should the provider API key enter a deployed Node AI service?
- [ ] As a Docker build argument so the image is self-contained
- [ ] Committed in an `.env` file that the Dockerfile copies into the image
- [x] Injected at runtime by the platform (secret manager or orchestrator secret) into the process environment
- [ ] Hard-coded in the compiled bundle, since `dist/` is not human-readable
> Anything present at build time lands in an image layer and is readable from image history or any
> registry copy. Runtime injection keeps the secret out of the artifact, lets it rotate without a
> rebuild, and allows different keys per environment.
