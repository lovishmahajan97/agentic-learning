---
id: ci-cd
module: backend-fundamentals
name: CI/CD
kind: practice
difficulty: core
est_minutes: 15
related:
  - backend-fundamentals/docker
  - backend-fundamentals/kubernetes
  - backend-fundamentals/secrets-management
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/evaluation-gates-for-production-releases
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/rollback
  - evals/regression-tests
sources:
  - Humble, Farley, 2010 — Continuous Delivery
  - Forsgren, Humble, Kim, 2018 — Accelerate
  - Official docs: GitHub Actions
---

## TL;DR
Continuous integration means every change lands on the shared mainline often and is automatically built
and tested there; continuous delivery means the resulting artifact is always deployable through an
automated, repeatable path; continuous deployment means it ships without a human click. The practice
exists because integration pain and the cost of a defect both grow with the size and age of the batch.
For AI systems the same pipeline versions prompts and models and gates releases on evals.

## Why it exists
Integrate once a sprint and the merge is a week of conflicts nobody remembers the context for. Deploy
once a month and the diff is five hundred commits, so when production breaks there is no way to tell
which one did it, and the rollback is a project of its own. Fear of that makes teams deploy less often,
which makes batches bigger, which makes deploys scarier: a loop that only ends when a change is small
enough to understand and automatic enough to repeat. The research summarised in *Accelerate* found that
teams deploying more frequently also had lower change-failure rates, not higher; small batches are the
safe option, not the reckless one.

The naive alternatives fail predictably. Nightly builds give feedback in hours and attribute a failure
to "everyone who committed yesterday". Release branches with a manual QA week work but drift from
mainline and turn every hotfix into a cherry-pick exercise. Deploying from a laptop means the artifact
depends on which laptop, which Python, and whose credentials, and leaves production secrets on
developer machines.

AI systems add a specific reason. A prompt edit or a model-version bump changes behaviour as much as a
code change does, but it is tempting to make it in a vendor dashboard with no diff, no test and no
rollback. Putting prompts, model configuration and evaluation suites into the same pipeline as code is
how those changes get history, review and a gate.

## How it works
### Continuous integration
Work on trunk or on branches that live hours, not weeks. A pull request triggers the pipeline: check
out, restore the dependency cache keyed by the lockfile hash, lint and type-check, run unit tests, and
in parallel run integration tests against real dependencies started as containers. Protected branches
require these checks to pass before merge. The pipeline must be fast enough that people wait for it, on
the order of ten minutes (illustrative), which is achieved by splitting fast and slow suites, sharding
tests across runners and caching aggressively. Flaky tests are quarantined and fixed, not retried until
green, *because* a retry loop hides real intermittent failures.

### Build once, promote the same artifact
After tests pass on mainline, build the container image once, tag it with the git SHA, push it to the
registry and record the immutable digest. Every later environment deploys that digest. Rebuilding per
environment reintroduces "it worked in staging" because a dependency can resolve differently an hour
later. Configuration and secrets are injected at deploy time, never baked in, so one artifact serves
every environment. Generate an SBOM and scan the image for known vulnerabilities at this stage; sign it
if your supply-chain requirements call for it.

### Continuous delivery: the path to production
Deploy the digest to staging; run smoke tests and contract tests against it; for AI services run the
evaluation gate on a sampled, versioned case set with thresholds rather than exact matches, since model
output is nondeterministic. Promote to production using a strategy that limits blast radius: rolling
(the Kubernetes default), blue-green (two full stacks and a switch), or canary (a small share of traffic
compared on error rate, latency and quality metrics, with automatic abort). Rollback is redeploying the
previous digest, which is fast precisely because the artifact already exists. Database migrations must
be backward compatible (expand, migrate, contract) so old and new versions coexist during a roll.
Feature flags separate deploying code from releasing behaviour.

### Continuous deployment versus delivery
Delivery means every mainline build *could* go to production through the automated path, with a human
approval where policy requires one. Deployment removes the approval. The second needs strong
observability and automatic rollback; the first is the right default for customer-visible AI behaviour
changes, where a human reviews the eval diff before promotion.

### Push versus pull deployment
A push pipeline runs `kubectl`, Helm or a cloud CLI with credentials obtained through OIDC federation.
A pull (GitOps) model has the pipeline update a manifest repository and a reconciler in the cluster
(Argo CD, Flux) apply it, *because* then no pipeline holds cluster credentials, git is the audit log,
and drift is corrected automatically. Push is simpler for a few services; pull pays off with many
clusters or strict audit requirements.

### Securing the pipeline
Ephemeral runners, least-privilege `permissions`, OIDC federation instead of long-lived cloud keys
stored as secrets, third-party actions pinned to a commit SHA, no secrets exposed to workflows triggered
by pull requests from forks, and protected environments with required reviewers for production.

### What changes for AI
Prompts, model ids, decoding parameters and eval cases are versioned files. The deterministic subset of
the eval suite runs on every pull request; the full suite runs on mainline or nightly because it costs
real money and minutes. Provider keys come from the secret store with spend caps. Model weights and
datasets are cached between runs. A model or prompt change is promoted through shadow or canary traffic
measured on quality, not only on errors.

## Example
A GitHub Actions workflow: tests on every pull request, a single image build on mainline tagged with the
commit and recorded by digest, and a production deploy of that exact digest using OIDC to assume a
cloud role, with the job failing if the rollout does not complete.

```yaml
name: ci-cd
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
  packages: write
  id-token: write                      # OIDC: the job gets a short-lived cloud credential, no stored key

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }     # cache keyed on the lockfile hash
      - run: pip install -r requirements-dev.txt
      - run: ruff check . && mypy src
      - run: pytest -q -m "not llm"                      # fast suite; paid-model evals run on main
  build:
    needs: test
    if: github.ref == 'refs/heads/main'                  # PRs only test; mainline produces the artifact
    runs-on: ubuntu-latest
    outputs:
      digest: ${{ steps.push.outputs.digest }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: push
        uses: docker/build-push-action@v6
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: production                              # required reviewers and env-scoped secrets
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gha-deploy
          aws-region: eu-west-1
      - run: aws eks update-kubeconfig --name prod
      - run: |
          kubectl set image deployment/embed-api \
            api="ghcr.io/${{ github.repository }}@${{ needs.build.outputs.digest }}"
          kubectl rollout status deployment/embed-api --timeout=5m   # non-zero exit fails the deploy
```

The `environment: production` gate is where continuous delivery and continuous deployment differ:
remove its required reviewers and every green mainline build ships itself. Rollback is the same
`kubectl set image` with the previous digest. Pin the action versions to the current releases when you
copy this.

## Trade-offs
- Small, frequent deploys cut risk per change but demand investment in test quality, automation and
  feature flags; it flips to a formal release cadence when you ship a regulated or on-premises artifact,
  where delivery means always deployable rather than always deployed.
- A fast pipeline and a thorough one pull against each other; split by trigger so pull requests get the
  fast subset, mainline the full suite, and nightly the expensive evals.
- Continuous deployment removes the human bottleneck but requires observability good enough to abort
  automatically; a manual approval on the eval diff is the better default for user-visible AI changes.
- GitOps removes cluster credentials from CI and gives a git audit trail, but adds a reconciler and a
  manifest repository to run; push deployment is simpler below a handful of services.
- Dependency and layer caches make builds fast but stale caches cause phantom failures; key them on
  the lockfile hash and bust them deliberately.

## Pitfalls
- Rebuilding the image per environment; the symptom is staging green and production different. Build
  once and promote by digest.
- Long-lived cloud keys stored as CI secrets; the symptom is one leaked runner log compromising
  production. Use OIDC federation and least-privilege permissions.
- Auto-retrying flaky tests; the symptom is real intermittent failures that reach production unnoticed.
  Quarantine and fix.
- Third-party actions referenced by a mutable tag or `@main`; the symptom is a supply-chain compromise
  that runs with your repository's permissions. Pin to a commit SHA.
- Running the full paid-model eval suite on every push; the symptom is forty-minute pull-request checks
  and a surprising provider bill. Sample on pull requests, run fully on mainline.
- A non-backward-compatible migration in a rolling deploy; the symptom is old pods crashing
  mid-rollout. Expand, migrate, then contract.
- A deploy step that does not wait for `rollout status`; the symptom is a green pipeline and a failed
  rollout nobody noticed.
- Prompts edited in a vendor dashboard outside the pipeline; the symptom is behaviour that changed with
  no diff and nothing to roll back to.

## When to use
Everything deployed goes through a pipeline; the decisions are how deep the gates go and where humans
sit. Track the four measures from the DORA research: deployment frequency, lead time for changes,
change-failure rate and time to restore service; they expose whether the pipeline is helping. Day to
day you maintain workflow files, keep the pipeline under the time people will wait, add an eval gate
when prompts or models change, and make rollback a one-command operation. In interviews, be ready to
explain build-once-promote-everywhere, how rollback works when migrations are involved, how a canary
decides to abort, and what makes CI for AI different: nondeterministic tests, eval cost, and treating
prompts as versioned artifacts.

## Role lens
- **Engineer:** Writes and maintains the workflow files, keeps the fast suite fast, tags images by
  commit, and wires the eval gate so a prompt change fails the build when quality drops.
- **Architect:** Sets the promotion path, the rollout strategy per service, the delivery-versus-deployment
  policy for AI behaviour changes, and the push-versus-GitOps decision across clusters.
- **FDE:** Adapts the pipeline to the customer's CI system, registry and change-management process,
  and proves to their reviewers that what runs in their cluster is the digest that passed the gates.

## Check yourself
**Q:** Pull-request checks take thirty-five minutes because they run the full 500-case LLM evaluation
suite against the provider on every push, and production deploys rebuild the image from `main`. Redesign
the pipeline and justify each change.
**A:** Split the suite by trigger: pull requests run lint, types, unit tests and a deterministic
sampled subset of evals with cached responses where possible, finishing in minutes; mainline runs the
full suite once per merge, nightly runs the expensive variants, and results are recorded against the
commit. Build the image once on mainline after tests pass, tag it with the SHA, record the digest, and
have staging and production deploy that digest, so what was tested is what runs. Put an eval gate with
thresholds between staging and production, with a human approval on the eval diff for prompt and model
changes. Obtain cloud credentials through OIDC rather than stored keys, pin actions by SHA, and make the
deploy step wait on rollout status so failure is visible. Rollback becomes redeploying the previous
digest, which the pipeline already has.

## Quiz
### Q: Why should the container image be built once and promoted by digest rather than rebuilt for each environment?
- [ ] Building is slow and the registry charges per build
- [x] Rebuilding can resolve dependencies differently later, so the production image would not be the one that passed tests
- [ ] Digests are shorter than tags and easier to type in deploy scripts
- [ ] Kubernetes refuses to pull an image that was built more than once
> The artifact that passed the gates is the only thing proven to work. A rebuild is a new artifact with
> potentially different base layers or packages, and the digest is how you guarantee identity.

### Q: What distinguishes continuous delivery from continuous deployment?
- [ ] Delivery uses containers while deployment uses virtual machines
- [ ] Delivery applies to libraries and deployment to services
- [x] Delivery keeps every mainline build deployable through an automated path, possibly with a human approval; deployment removes the approval and ships automatically
- [ ] Delivery runs tests before merge while deployment runs them after
> Both automate the path to production. The difference is only whether a person clicks; for
> user-visible AI behaviour changes a review of the eval diff before promotion is usually worth the
> click.

### Q: What does OIDC federation in a CI pipeline replace, and why is it safer?
- [ ] It replaces the git credentials used to check out the repository
- [x] It replaces long-lived cloud access keys stored as CI secrets with short-lived tokens issued per job and trusted only for a specific repository and branch
- [ ] It replaces the container registry login with anonymous pulls
- [ ] It replaces unit tests with signed attestations
> A stored key can be leaked from a log or a fork and lives until rotated. A per-job OIDC token
> expires in minutes and the cloud role's trust policy limits which repository and branch may assume
> it.
