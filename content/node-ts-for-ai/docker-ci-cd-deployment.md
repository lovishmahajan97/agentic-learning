---
id: docker-ci-cd-deployment
module: node-ts-for-ai
name: Docker / CI/CD deployment
kind: practice
difficulty: core
est_minutes: 16
status: crisp
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
Ship a Node AI service as a multi-stage image of compiled JavaScript and production deps, run Node as PID
1 so `SIGTERM` drains streams, inject keys at runtime, and gate on typecheck and tests, with evals beside
the gate, not inside it.

## Why it exists
- **TypeScript does not run as written.** It must be compiled (or type-stripped), its dependency tree is
  large and mostly build-time only, and `node_modules` can hold native addons compiled for one libc.
  Running `npm install` on the server may ignore the lockfile, land `devDependencies` in production, use
  whatever `tsc` the host has, and add hundreds of megabytes.
- **Docker makes the tested artifact the running artifact.** The Node-specific work is arranging the
  build so that is actually true. The general Docker and CI/CD lessons live in backend-fundamentals.
- **AI requests are long.** A streamed completion holds a connection for seconds to minutes, so a deploy
  that kills the process on `SIGTERM` without draining cuts users off mid-answer and wastes tokens already
  billed.
- **The process holds the company's most sensitive secret.** A provider API key baked into an image layer
  is visible to anyone who can pull the image.
- **"The code" includes prompts, tool schemas and model identifiers.** Changing them can regress quality
  with no test turning red. "Build on push, deploy on green" is incomplete: without reproducible installs it
  does not build what you tested, without a drain it does not deploy safely, and without an eval it ships
  prompt changes blind.

## How it works
### The image: stages because the runtime needs almost nothing
1. **Deps stage (the cached layer):** copy only `package.json` and the lockfile, then run `npm ci` (`pnpm install --frozen-lockfile`,
   `yarn install --immutable`).
   - `ci` refuses to proceed if lockfile and manifest disagree, exactly the guarantee a build wants.
   - Copying manifests before source caches this expensive layer until a dependency changes, not on every
     commit.
2. **Build stage.** Add the source, run `tsc` (or a bundler such as esbuild), then prune dev dependencies.
3. **Runtime stage.** Start from a fresh base image and copy only `dist/`, the pruned `node_modules` and
   `package.json`. Smaller, smaller attack surface, and it cannot accidentally depend on a dev tool.

| Choice | Guidance | Because |
|---|---|---|
| `node:<major>-slim` | Safe default | Debian-based, glibc |
| `node:<major>-alpine` | Only after testing native deps | Smaller, but musl breaks some native addons and prebuilt binaries (tokenizer and ONNX packages are common victims) |
| Version | Pin the major to an LTS line | Predictable runtime |
| `NODE_ENV=production` | Set it | Some libraries read it to disable debug paths |
| `.dockerignore` | List `node_modules`, `.env`, `dist`, `.git` | Small build context, local secrets stay out |
| User | The `node` user the official image provides | Not root |

### Signals: exec form, PID 1 and draining
- **Exec form makes Node PID 1.** `CMD ["node", "dist/server.js"]` runs Node directly.
- **Shell form hides Node behind a shell.** `CMD node dist/server.js` runs `/bin/sh -c "node dist/server.js"`;
  the shell is PID 1, it does not forward `SIGTERM`, and the orchestrator waits out the grace period then
  `SIGKILL`s mid-stream.
- **Do not rely on shell luck.** Some shells exec a lone `-c` command directly, accidentally making Node
  PID 1; that breaks the moment someone adds `&&`.
- **PID 1 gets no default signal action.** The kernel applies none, so Node reacts to `SIGTERM` only if
  your code installs a handler; otherwise the signal is ignored and you wait for `SIGKILL` again.
- **PID 1 must reap zombies.** Node does not. If the service spawns subprocesses (an MCP server over stdio,
  a converter), run with `docker run --init`, `tini` or `dumb-init` as the entrypoint.
- **Division of labour.** The drain (stop accepting, finish or abort in-flight streams, close the pool) is
  the graceful-shutdown lesson. The deployment delivers the signal and sets the grace period longer than
  the longest completion you allow.

### Memory and the container boundary
- **The default V8 heap limit is a heuristic** from detected memory. Older releases read host memory and
  ignored the cgroup limit; newer ones read the cgroup limit, with quality depending on the Node version
  and cgroup v1 versus v2.
- **The heap limit covers only the JavaScript heap.** `Buffer`s, native addons, thread stacks and the code
  cache live outside it, so resident memory can cross the container limit while V8 thinks it has room, and
  the kernel's OOM killer ends the process.
- **Set `--max-old-space-size` explicitly**, somewhat below the container limit. The example leaves about a
  quarter of a 1 GiB limit for non-heap memory. Confirm with `v8.getHeapStatistics()` at startup.
- **Where services discover this:** streaming large contexts and holding many concurrent responses.

### Health, readiness and config
- **Liveness proves only that the event loop responds; readiness checks dependencies** (database, cache,
  draining). Keep them apart *because* a liveness probe that checks the database turns a database blip
  into a restart of every replica at once.
- **Readiness flips to false the moment `SIGTERM` arrives**, the signal a plain load balancer uses to stop
  routing.
- **Kubernetes adds an endpoint race.** A terminating pod is removed from the Service's endpoints, but the
  removal reaches kube-proxy and ingress controllers asynchronously, so new connections can arrive for a few
  seconds. Closing the listener immediately fails them.
- **Fix the race with a delay.** A short `preStop` sleep (or a delay in the handler) before `server.close()`.
  The grace period must cover that delay plus the longest stream, since `preStop` time counts against it.
- **Parse config once at startup with a schema**, so a missing `ANTHROPIC_API_KEY` or a non-numeric `PORT`
  fails before the first request.
- **Secrets arrive at runtime** from the platform (Kubernetes Secrets, a secret manager, CI-provided
  environment), never as `ARG`/`ENV` at build time, because build args are recorded in image history.

### The pipeline
1. **Pull request**, in parallel where possible: `tsc --noEmit`, lint, unit tests, and integration tests
   against service containers started by CI.
2. **`main`** additionally builds the image once with BuildKit, tags it with the git SHA (plus a semver tag
   at release), pushes it to a registry and deploys that digest. Staging and production run byte-identical
   images, and a rollback is a tag swap.
3. **Smoke test the image in staging.** PR tests ran against source on the runner, not the image. A file
   missing from a `COPY`, a native addon built for the wrong libc, or a `dist/` path typo only appears in the
   image. Start it, hit readiness, make one request through a stubbed or cheap model.
4. **Prompts, tool schemas and the model identifier ship in the image**, so changing them is a deploy like
   any other.
5. **Eval beside the gate.** A job runs the golden set against the real model with a budget and publishes a
   score; a drop blocks promotion to production without making every PR pay for model calls.
6. **Canary prompt or model changes.** Route a slice of traffic, compare quality and cost metrics, widen.

## Diagram
Pull requests stop at the deterministic gate; `main` builds one SHA-tagged image that is smoke-tested,
eval-checked and canaried before it reaches all traffic.

```mermaid
flowchart TD
    PR["Pull request"] --> G["Typecheck, lint, unit, integration"]
    G -->|"merge to main"| B["Build image once, tag git SHA"]
    B --> R[("Registry")]
    R --> S["Deploy digest to staging"]
    S --> SM["Smoke test: readiness plus one request"]
    SM --> EV{"Eval score holds?"}
    EV -->|"no"| X["Block promotion"]
    EV -->|"yes"| C["Canary slice in production"]
    C -->|"quality and cost OK"| P["Widen to all traffic"]
    C -.->|"regression"| RB["Rollback: tag swap"]
```

## Examples
### Multi-stage Dockerfile
Installs from the lockfile with a cached layer, compiles, prunes, and runs as `node` in exec form.
Secrets reach the runtime from the platform, not from this file.

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

### GitHub Actions: gate, build once, tag with the commit
Typecheck and tests run against a real Postgres service container; only `main` builds and pushes an image
tagged with the commit SHA.

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

### Worked scenario: sizing the grace period for streams
A chat service caps completions at 120 seconds by policy (an illustrative value; use your own maximum).

| Phase after `SIGTERM` | Time budget | Why |
|---|---|---|
| Readiness flips to false | immediate | load balancer stops routing |
| `preStop` sleep | 5 s | endpoint removal propagates to kube-proxy and ingress |
| `server.close()`, finish or abort streams | up to 120 s | longest allowed completion |
| Close pools, flush telemetry | ~5 s | clean exit |
| Margin | ~5 s | jitter |
| **`terminationGracePeriodSeconds`** | **135** | default of 30 would cut long streams |

- **Check:** old pods should exit as soon as their last stream ends, not at exactly 135 s; lingering for the
  full period means the handler never acted.
- **Cost:** every rollout can now take up to that long per pod batch, which is why the cap is the policy
  maximum, not "infinite".

## Production patterns
- **Deploy by digest, roll back by tag.** Tag every image with `${{ github.sha }}` and deploy that exact
  digest to staging and then production, so rollback re-points to the previous SHA without a rebuild.
  Monitor which digest each environment runs and alert on drift between them.
- **Smoke test the artifact, not the source.** After the staging deploy, a CI job starts the image, waits
  for readiness and sends one request through a stubbed or cheap model before promotion. Watch for
  failures caused by a missing `COPY` or wrong-libc addons that PR tests cannot see.
- **`preStop` sleep plus readiness flip.** In the pod spec, add a `lifecycle.preStop` exec hook running a
  short `sleep` (a few seconds), flip readiness on `SIGTERM`, and size `terminationGracePeriodSeconds` from
  the longest allowed completion. Monitor 502s and connection resets during each rollout; the target is zero.
- **Scheduled rebuild of a pinned major.** Keep `FROM node:22-slim` and add an `on: schedule` cron trigger
  to the workflow that rebuilds weekly, so base-image security patches land without unpinning. Give the
  rebuild its own tag (for example the SHA plus a date) rather than overwriting the SHA tag, so one tag
  keeps meaning one digest. Watch image scan findings and image size over time.
- **Audit images for leaked secrets.** Run `docker history --no-trunc <image>` (or a scanner in CI) on
  release images to confirm no key appears in build args or layers. Alert on any match and rotate the key
  immediately, since registry copies cannot be recalled.
- **Canary prompt and model changes.** Route a small slice of traffic to the new digest, compare quality
  score, cost per request and error rate against the baseline, then widen. Monitor the eval score as the
  promotion gate.

## Trade-offs
- **Multi-stage: smaller image vs build complexity.** Shrinks the runtime image and attack surface; for a
  throwaway prototype a single-stage image is fine until it ships to a shared environment.
- **Alpine vs slim: size vs compatibility.** Alpine is smaller, yet musl breaks some native tokenizer and ML
  addons; the saving flips to a loss the first time a prebuilt binary fails to load.
- **esbuild bundle: cold start vs visibility.** One file speeds cold start and removes `node_modules`, but
  hides dependency versions from scanners and breaks packages relying on `__dirname` or dynamic requires.
- **Evals outside the PR gate: speed vs enforcement.** Merges stay fast and cheap only if the scheduled eval
  blocks promotion; an eval that merely reports is a dashboard nobody reads.
- **Grace period: clean streams vs slow rollouts.** Long lets streams finish; very long slows every rollout
  and masks handlers that never stop. Cap it at the maximum completion time you actually allow.
- **Digest pin vs patch flow.** Pinning the base image by digest is reproducible but freezes security
  patches; a pinned major tag plus a scheduled rebuild is the usual compromise.

## Pitfalls
- **Shell-form `CMD` or an entrypoint script that does not `exec`.** Symptom: pods always take the full grace
  period to stop and users' streams cut off at every deploy. Fix: exec-form `CMD`, or `exec node …` as the
  script's last line, plus a `SIGTERM` handler.
- **Copying the whole source before `npm ci`.** Symptom: a dependency install on every commit and a
  ten-minute build for a one-line change. Fix: copy only the manifests, run `npm ci`, then copy the source.
- **Passing the provider key as a build `ARG` or `ENV`.** Symptom: the key is readable in image history
  (`docker history`) and in every registry the image was pushed to. Fix: inject it at runtime and rotate the
  leaked key.
- **Leaving the heap limit to the default heuristic.** Symptom: kernel OOM kills under load (exit code 137)
  with no JavaScript heap error in the logs. Fix: set `--max-old-space-size` below the container limit.
- **Running `npm install` instead of `npm ci`.** Symptom: a dependency version in production that no
  developer machine or CI run ever tested. Fix: `npm ci` (or the frozen-lockfile equivalent) everywhere.
- **Readiness green while draining, or the listener closed the instant `SIGTERM` arrives with no `preStop`
  delay.** Symptom: a burst of 502s or connection resets at every rollout even though the drain code looks
  correct. Fix: flip readiness on `SIGTERM` and sleep in `preStop` before `server.close()`.
- **Deploying prompt or model-id changes with no eval.** Symptom: users report a quality regression days
  after a deploy that "only touched a text file". Fix: a budgeted eval that blocks promotion, plus a canary.

## When to use
- **The floor, for any Node AI service beyond a laptop:** a multi-stage Dockerfile, `npm ci`, exec-form
  `CMD`, runtime secrets, and a PR pipeline with typecheck and tests.
- **Add as you grow:** integration tests with service containers once a database or queue is in the path;
  an explicit heap limit and readiness-aware drain once you stream; a budgeted eval job as soon as prompts
  change regularly.
- **Measure:** image size, build time for a one-line change (with warm caches, little more than the compile
  step, since the install layer is reused), deploy duration, and requests failed during a rollout, which
  should be zero.
- **Interviews:** why `SIGTERM` is not reaching the process, what `npm ci` guarantees, and where the API key
  should live; concrete answers separate a senior engineer from someone who has only run `docker build`.

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

## Interview questions
### What does `npm ci` guarantee that `npm install` does not, and why does it matter in a Dockerfile?
- **Definition:** `npm ci` installs exactly what the lockfile says and fails if lockfile and `package.json`
  disagree, instead of resolving and possibly rewriting versions.
- **Why it matters:** the image then contains the dependency tree CI tested, not a version no machine ever ran.
- **Layer order:** copying only the manifests before `npm ci` keeps that slow layer cached until dependencies change.

### A pod is killed with exit code 137 under load, but the logs show no JavaScript heap error. What do you check?
- **Diagnosis:** 137 means `SIGKILL`, here from the kernel OOM killer: resident memory crossed the container
  limit while V8 thought it had heap room.
- **Cause:** `Buffer`s, native addons, thread stacks and the code cache live outside the V8 heap limit.
- **Fix:** set `--max-old-space-size` below the limit (768 for 1 GiB leaves about a quarter), check
  `v8.getHeapStatistics()` at startup, and measure memory under concurrent streams.

### Why should the provider API key never be a Docker build argument?
- **Build args are recorded in image history**, so anyone who can pull the image, from any registry copy,
  can read the key with `docker history`.
- **Runtime injection** from a secret manager or orchestrator secret keeps it out of the artifact, allows
  rotation without a rebuild, and lets each environment use its own key.

### When would you choose an Alpine base image over slim, and when not?
- **Choose Alpine** when the dependency tree is pure JavaScript and image size matters.
- **Avoid it** with native tokenizer, ONNX or other prebuilt binaries, because musl breaks some of them.
- **Default:** `node:<major>-slim` (glibc), pinned to an LTS major and rebuilt on a schedule for patches.

### How would you size `terminationGracePeriodSeconds` for a streaming service?
- **Add the phases:** `preStop` delay for endpoint propagation, plus the longest allowed completion, plus
  pool close and a margin.
- **Example:** 5 s + 120 s + about 10 s gives about 135 s, versus the default of 30.
- **Verify:** deploy under synthetic streaming load; zero truncated responses and pods exiting before the
  deadline mean the drain works.

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
