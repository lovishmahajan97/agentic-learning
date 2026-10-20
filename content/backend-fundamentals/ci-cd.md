---
id: ci-cd
module: backend-fundamentals
name: CI/CD
kind: practice
difficulty: core
est_minutes: 15
status: reviewed
related:
  - backend-fundamentals/docker
  - backend-fundamentals/kubernetes
  - backend-fundamentals/secrets-management
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - node-ts-for-ai/docker-ci-cd-deployment
sources:
  - Humble, Farley, 2010 — Continuous Delivery
  - Forsgren, Humble, Kim, 2018 — Accelerate
  - Official docs: GitHub Actions
---

## TL;DR
Continuous integration means every change is merged to the main line often and verified by an
automated build and test run on a clean machine; continuous delivery means every verified build
becomes one immutable artifact that can reach production through a repeatable, automated path, and
continuous deployment removes the final button. The pipeline replaces trust in laptops with evidence
and makes releases small and routine enough that failure is cheap to detect and reverse. For AI
systems it must gate prompts and models too, whose tests are evaluations.

## Why it exists
Without a pipeline, integration happens late: weeks of divergent branches meet at release time and the
merge is where the bugs live. Tests run on developer machines with whatever happens to be installed,
so "passes locally" carries no information about production. Deployment is a runbook followed by a
person, usually under time pressure, and the second execution differs from the first in some detail
nobody wrote down. Nobody can say which commit is in production, and rollback means "redeploy the old
one", which cannot be rebuilt because the dependency resolution has moved on.

Each of these is a feedback-delay problem. The cost of a defect grows with the distance between the
change that introduced it and the moment it is detected, because context is lost and other changes
pile on top. Continuous integration shrinks that distance by integrating at least daily against a
build that must stay green. Continuous delivery extends the idea through to production with a
deployment pipeline whose stages give fast, cheap feedback first and slow, expensive feedback later.
The DORA research reported in *Accelerate* found that teams deploying more often with shorter lead
times also had *lower* change failure rates: small, frequent changes are safer than large, rare ones,
which is the opposite of the intuition behind "test everything manually before the big release".

AI systems raise the stakes. A prompt edit is a behaviour change with no type checker; a model version
bump can silently regress a task the team never wrote a test for; a retrieval index rebuild changes
answers without touching code. Without a pipeline that treats these as changes to be verified,
quality drift ships unnoticed and is discovered by users.

## How it works
### The stages of a deployment pipeline
1. **Commit stage, minutes.** Check out on a clean runner, install from the lockfile, lint, type-check,
   run unit tests with the model client mocked or recorded, scan for secrets. It runs on every push,
   so it must be cheap and deterministic; it exists to fail fast on the majority of mistakes.
2. **Build.** Build the container image once, tag it with the git SHA, push it to a registry. The image
   digest is now the artifact. "Build once, promote many" is a rule *because* rebuilding per
   environment can resolve different dependencies or base layers, so production would run something
   that was never tested.
3. **Acceptance.** Start the real dependencies (Postgres, Redis) as service containers, run API tests
   against the built image, run contract tests against provider APIs, and run a small, fixed
   evaluation set against the prompts and model with pass thresholds.
4. **Supply chain.** Audit dependencies, scan the image, produce a software bill of materials, sign the
   image so later stages can verify they are deploying what this stage produced.
5. **Staging.** Deploy automatically, run smoke tests against a production-shaped environment.
6. **Production.** Promote the same digest, gated by an approval (delivery) or automatically
   (deployment), progressively: a canary takes a slice of traffic while error rate, latency and online
   quality metrics are compared against the baseline, then full rollout or automatic rollback.

The three common production strategies differ in what they cost and what they can detect. A **rolling**
update replaces replicas a few at a time: no extra capacity beyond the surge, but old and new versions
serve side by side and a bad version reaches every user before anyone compares metrics. **Blue-green**
stands up a full second environment and switches traffic at once: rollback is an instant switch back,
paid for with double capacity during the release, and it detects nothing that only shows under real
load until all users are on it. A **canary** sends a small share of traffic to the new version and
promotes only if its metrics hold: the only strategy that limits the blast radius of a bad release, but
it needs enough traffic for the comparison to be statistically meaningful and metrics that actually
reflect quality, which for LLM features means online evaluation signals, not just HTTP 5xx.

With GitOps, the pipeline's last step is a commit of the new digest to a configuration repository, and
a controller (Argo CD, Flux) reconciles the cluster to it. The config repo becomes the audit log of
what ran when, and rollback is `git revert`.

### Principles that make the stages work
- **Trunk-based development with feature flags.** Small pull requests into a main branch that is
  always deployable; flags separate *deploying* code from *releasing* behaviour.
- **Pipeline as code**, versioned next to the application, so a pipeline change is reviewed like any
  other change.
- **Reproducibility.** Lockfiles for dependencies, base images pinned by digest, third-party pipeline
  actions pinned to a commit SHA rather than a mutable tag.
- **Fast feedback.** Cache dependencies keyed on the lockfile hash, parallelise test shards, and move
  slow suites to a nightly or on-demand lane rather than letting the commit stage grow to thirty
  minutes, because a slow pipeline makes developers batch changes, which is exactly the large, risky
  merge the pipeline exists to prevent.
- **Secrets through identity.** The CI job presents an OIDC token the cloud exchanges for a short-lived
  role credential, instead of a stored long-lived key; secrets are masked in logs and never exposed to
  jobs triggered by untrusted pull requests.
- **Environment protection.** Production deploys require a reviewer, a branch rule, or both.
- **Backward-compatible changes across a deploy.** During any rolling or canary release, old and new
  versions run at once against the same database, queue and caches, and a rollback puts the old version
  back on data the new one wrote. Schema changes therefore follow expand-contract: add the new column or
  table first (old code ignores it), deploy code that writes both and reads the new one, backfill, and
  only in a later release drop the old shape. Message and API formats follow the same rule.

### What changes for AI systems
- **Non-determinism.** Model outputs vary between runs, so unit tests mock or record the client and
  assert on structure and control flow, while quality is measured in an evaluation stage with
  thresholds over many cases, never with exact-match asserts on generated text.
- **Cost and quota.** Real-model tests spend money and rate limit. Run a small smoke evaluation on
  every pull request, the full set on main or nightly, under a CI-only key with a spend limit.
- **Artifacts beyond code.** Model weights and indexes live in a registry or object store, referenced
  by version; a change to a prompt template, a model version or a retrieval index must trigger the
  evaluation gate exactly as a code change triggers tests.
- **GPU work.** Tests that need a GPU run on self-hosted runners or as cluster Jobs, never on the
  default runner.
- **Rollback scope.** The deployable unit pins code, prompt and model version together, so a rollback
  reverts all three.

### How you know it is working
The four DORA metrics: deployment frequency, lead time for changes, change failure rate, and time to
restore service. Add pipeline duration and flake rate; a pipeline nobody trusts is one nobody waits for.

## Example
A GitHub Actions workflow with two jobs. Every pull request runs lint, types and mocked unit tests,
plus a small smoke evaluation that only runs when the secret is available (pushes and same-repository
pull requests). On `main`, the image is built once, pushed with the commit SHA as its tag, and the
exact digest is rolled out to the cluster using short-lived credentials obtained through OIDC rather
than a stored cloud key. The `production` environment carries required-reviewer protection.

```yaml
name: ci
on:
  pull_request:
  push: {branches: [main]}
permissions: {contents: read}                # least privilege by default; jobs widen it
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4             # pin to a commit SHA in a real repository
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --frozen
      - run: uv run ruff check . && uv run mypy src
      - run: uv run pytest -q tests/unit      # model client mocked: deterministic, free, fast
      - run: uv run pytest -q -m smoke tests/eval   # ~20 golden cases against a cheap model
        if: github.event_name == 'push' || github.event.pull_request.head.repo.full_name == github.repository
        env: {LLM_API_KEY: "${{ secrets.CI_LLM_KEY }}"}
  build-and-deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: production                  # protection rule: required reviewers
    permissions: {contents: read, packages: write, id-token: write}   # id-token: OIDC to the cloud
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with: {registry: ghcr.io, username: "${{ github.actor }}", password: "${{ secrets.GITHUB_TOKEN }}"}
      - id: build
        uses: docker/build-push-action@v6
        with: {push: true, tags: "ghcr.io/${{ github.repository }}:${{ github.sha }}"}   # repo name must be lowercase
      - uses: aws-actions/configure-aws-credentials@v4
        with: {role-to-assume: "arn:aws:iam::123456789012:role/ci-deploy", aws-region: us-east-1}
      - run: |                                 # promote the exact digest that was built and tested
          aws eks update-kubeconfig --name prod
          kubectl set image deployment/llm-api api=ghcr.io/${{ github.repository }}@${{ steps.build.outputs.digest }}
          kubectl rollout status deployment/llm-api --timeout=10m
```

## Trade-offs
- Running evaluations on every pull request catches prompt regressions early, but costs money, time and
  attention when the eval is noisy; the balance flips to a small smoke set per pull request plus a full
  nightly run once the per-pull-request eval bill or wait time starts pushing people to batch prompt
  changes or skip the gate.
- Continuous deployment maximises flow by removing the human gate, but for regulated customers or
  high-blast-radius changes continuous delivery with an approval and a canary is the right stopping
  point.
- Build-once-promote-many guarantees production runs what was tested, but forces every environment
  difference into runtime configuration rather than a rebuild, which demands discipline about config
  injection.
- GitOps gives an auditable, declarative rollback path, but adds a reconciliation delay and another
  controller to operate; direct `kubectl` from the pipeline is simpler for a single team and few
  services.
- Aggressive caching keeps pipelines fast, but a stale cache produces "green in CI, broken in prod";
  key caches on lockfile hashes and expire them on base-image changes.

## Pitfalls
- Tolerating flaky tests; the symptom is a "just re-run it" culture in which a real failure is merged
  because red no longer means anything. Quarantine flaky tests immediately and fix or delete them.
- Unit tests that call the real model and assert exact strings; the symptom is intermittent failures
  that start the day the provider updates the model.
- Rebuilding the image at deploy time; the symptom is production behaving differently from staging
  with the "same" commit because dependency resolution drifted between builds.
- Long-lived cloud keys stored as CI secrets; the symptom is a leaked runner log or compromised action
  yielding permanent credentials. Use OIDC federation.
- Third-party actions referenced by a mutable tag or branch; the symptom is a supply-chain compromise
  of that action silently running in your pipeline. Pin to a commit SHA.
- Only code is versioned and rolled back; the symptom is a bad prompt requiring a hotfix pull request
  under pressure instead of a one-click revert.
- `pull_request_target` workflows, which run with secrets in the base repository's context, that check
  out and execute the fork's code; the symptom is a malicious pull request exfiltrating secrets through
  a build script or log line.
- A commit stage that grows past half an hour; the symptom is rising pull-request size and lead time,
  because developers batch changes to avoid waiting.
- Shipping a destructive migration (rename or drop a column) in the same release as the code that needs
  it; the symptom is errors from the still-running old replicas during rollout, and a rollback that
  fails because the old code cannot read the new schema.

## When to use
Set up a pipeline on day one for any codebase with more than one contributor or more than one deploy;
a five-minute lint-and-test workflow is enough to start. Add stages as risk grows: image build and scan
once you deploy containers, an evaluation gate once prompt or model changes affect users, canaries once
traffic is large enough for a comparison to mean something. Use path filters so documentation changes
skip the heavy stages. Track the four DORA metrics plus pipeline duration, flake rate and eval-gate pass
rate. In interviews, explain build-once-promote-many, why small frequent deploys are safer, how you test
non-deterministic model code, and how a CI job authenticates to the cloud without a stored key.

## Role lens
- **Engineer:** Writes the workflow, mocks the model client in unit tests, maintains the smoke
  evaluation set, pins actions and base images, and reads failed job logs before anyone else does.
- **Architect:** Defines the promotion path, environments and gates (eval thresholds, approvals,
  canary criteria), the artifact and config repositories, and the rollback policy that covers prompt
  and model versions.
- **FDE:** Fits delivery into the customer's tooling (GitLab, Jenkins, Azure DevOps), their change
  windows and approval requirements, and builds the evaluation gate on their data.

## Check yourself
**Q:** A pull request changes the system prompt of your RAG assistant and bumps the model version.
Describe what the pipeline should do before this reaches users, and how you keep it affordable.
**A:** Treat it as code first: the commit stage runs lint, types and unit tests with the model mocked,
which costs nothing. Because files under the prompt and model paths changed, the evaluation gate
triggers: a fixed golden set of a few hundred questions with graded references runs against the new
configuration, scored by deterministic checks and an LLM judge, and compared with the baseline from
main using thresholds on groundedness, refusal rate and output format; the pull request fails if it
regresses beyond tolerance. Keep cost down by running a twenty-case smoke subset on every push and the
full set only when those paths change or a label is applied, under a CI-only key with a spend limit.
Then build the image once, deploy to staging, smoke-test, and canary a small share of production
traffic while watching the same metrics online, with the prompt and model version pinned in the same
artifact so a rollback reverts both.

## Quiz
### Q: Why build the image once and promote the same digest through environments rather than rebuilding per environment?
- [ ] Because container registries charge per build
- [x] Because a rebuild can resolve different dependencies or base layers, so production would run an artifact that was never tested; promoting one digest guarantees identity
- [ ] Because Kubernetes can only pull a given image once per cluster
- [ ] Because tests only execute on the first build of a commit
> The artifact that passed acceptance tests is the only thing known to work; any rebuild is a new,
> untested artifact. Registries do not limit pulls this way and test execution is a pipeline choice,
> not a constraint.

### Q: Unit tests for code that calls an LLM fail intermittently with slightly different wording in the output. What is the best fix?
- [ ] Raise the temperature so outputs become more consistent
- [ ] Automatically retry each failing test three times
- [x] Mock or record the model client in unit tests, and move quality checks to an evaluation stage with thresholds rather than exact-match asserts
- [ ] Pin the tests to one model version and assert the exact string
> Generated text is non-deterministic by nature, so exact matches will keep breaking. Unit tests should
> verify control flow against a stubbed client, while quality is measured statistically over many
> cases. Retries hide real failures and higher temperature increases variance.

### Q: Why is OIDC federation preferred over a stored cloud access key in a CI pipeline?
- [ ] OIDC tokens never expire, so the pipeline never needs to re-authenticate
- [x] The job presents a short-lived signed identity token that the cloud exchanges for temporary credentials, so there is no long-lived secret to leak or rotate
- [ ] Kubernetes requires OIDC for any `kubectl` access from a pipeline
- [ ] Stored keys cannot be restricted to a single repository
> The credential is short-lived, and the cloud's trust policy accepts the token only when its claims
> (repository, branch or environment) match, which removes the single most common CI leak. OIDC tokens are deliberately
> short-lived, and `kubectl` access works with many credential types.
