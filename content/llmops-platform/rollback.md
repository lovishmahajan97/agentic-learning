---
id: rollback
module: llmops-platform
name: Rollback
kind: practice
difficulty: core
est_minutes: 13
status: fact-checked
related:
  - llmops-platform/canary-shadow-deployments
  - llmops-platform/model-registry
  - llmops-platform/prompt-registry
  - llmops-platform/model-lifecycle-and-deprecation-policy
  - deployment/rollout-criteria-and-rollback-triggers
  - data-architecture/embedding-versioning-re-indexing-strategy
  - deployment/runbooks
  - security-governance-compliance/ai-incident-response
sources:
  - Beyer et al., 2016 — Site Reliability Engineering
  - Humble & Farley, 2010 — Continuous Delivery
---

## TL;DR
Rollback is returning production to a previously validated version of the whole AI bundle (model,
prompt, parameters, tools, index, guardrails) in minutes, by repointing an alias rather than rebuilding
anything. It matters more for AI systems than for ordinary services because "fix forward" means
shipping a new, untested prompt under incident pressure, and because several components (re-embedded
indexes, replaced fine-tunes, retired vendor models) are only reversible if you planned for it.

## Why it exists
For a stateless web service, rollback is a solved problem: redeploy the previous container image. AI
systems break that model in three ways.

First, the deployable unit is a bundle of artefacts with different lifecycles and different stores. The
prompt lives in a registry, the model behind a provider API or a GPU fleet, the retrieval index in a
vector database, the tool schemas in code, the guardrail policy in a config service. Rolling back one
of them without the others yields a combination that was never evaluated: yesterday's prompt against
today's model is a new version, not the old one.

Second, some changes destroy their own undo path. Re-embedding a corpus in place with a new embedding
model overwrites the vectors the old prompt was tuned against, and the old query-embedding model
cannot be used against the new vectors at all, because the two models' vector spaces are not
comparable. Overwriting a fine-tuned checkpoint or adapter without retaining the previous one leaves
nothing to return to. A vendor retiring the previous
snapshot removes the old model entirely.

Third, the failures that justify a rollback are usually quality regressions, visible only through
lagging signals (thumbs-down rate, escalations, judge scores), not through a crash. By the time a human
notices, the bad version has been serving for hours. The naive alternative, "just tweak the prompt to
fix it", compounds the problem: every edit under pressure is another unevaluated version, and the
original cause is now buried under three patches. Rollback exists so that the first move in an incident
is a known-good state, after which diagnosis can happen calmly.

## How it works
### Make versions immutable and deployment a pointer
Every release is a manifest that pins each component: model id with snapshot date, prompt version hash,
decoding parameters, tool schema version, index id, guardrail policy version, and the feature flags
that were on. Manifests are immutable and stored in the registry. The environment, say `prod`, is an
alias pointing at one manifest. Deploying is moving the alias forward; rolling back is moving it to the
previous manifest. This is O(1) *because* nothing is rebuilt, re-embedded or re-trained; only a pointer
changes, and the data plane picks up the new pointer on its next config refresh.

The rule that follows: you may only roll back to a manifest that has previously served production (or
passed the same gates). Hand-assembling "old prompt + new model" during an incident is a forward change
in disguise.

### Keep the previous version reachable
A pointer is only useful if its target still exists and is warm.
- Self-hosted models: keep the previous weights loaded or at least on local disk; loading tens of
  gigabytes from object storage can take minutes (illustrative), which is the whole rollback budget.
- Provider models: pin snapshots, and track the vendor's deprecation calendar so the "previous" model is
  not one that was retired last month.
- Indexes: build the new index alongside the old (blue/green), switch the alias, keep the old index
  until the new one has baked. Never re-embed in place.
- Fine-tunes: keep the previous adapter or checkpoint and the training data that produced it.

### Decide triggers before the release
Rollback should be a decision made in advance and executed by a rule, not debated in the incident
channel. Guard metrics with thresholds and windows (error rate, p95 latency, schema-failure rate,
refusal rate, cost per request, tool-failure rate) trigger automatically; quality signals (judge score
on a sample, thumbs-down rate, escalation rate) usually trigger a human decision because they are
noisier and slower. Each trigger carries a minimum sample size, *because* a threshold evaluated on
twenty requests produces flapping.

### Handle what a pointer flip does not restore
Rollback restores behaviour, not data. Things to check after the flip:
- Caches keyed without the version: a semantic or response cache filled by the bad version will keep
  serving its answers. Key caches by manifest id, or purge on rollback.
- Conversations in flight: a session whose earlier turns and compaction summaries were produced by the
  bad version continues under the old one; usually acceptable, but worth knowing when reading feedback.
- Agent memory and side effects: notes the bad version wrote to long-term memory, tickets it filed,
  emails it sent. Rollback does not undo them; the audit log identifies them for cleanup.
- Downstream consumers: if another team cached embeddings or outputs from the bad version, tell them.

### Rehearse it
A rollback that has never been executed is a hypothesis. Exercise it in CI (deploy v2, roll back to
v1, run a smoke eval) and in periodic game days, and time it against the MTTR target in the runbook.

## Example
A registry with immutable manifests, an environment alias, and a guard loop that rolls back when a
metric breaches its threshold with enough samples. Promotion records history so rollback has a
validated target.

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Manifest:
    id: str; model: str; prompt: str; params: dict; tools: str; index: str; guardrails: str

@dataclass
class Registry:
    manifests: dict = field(default_factory=dict)
    alias: dict = field(default_factory=dict)      # env -> manifest id
    history: dict = field(default_factory=dict)    # env -> [manifest ids that served]

    def promote(self, env: str, mid: str):
        assert mid in self.manifests, "unknown manifest"
        self.history.setdefault(env, []).append(mid)
        self.alias[env] = mid                      # O(1): a pointer move, nothing rebuilt
        cache.invalidate(namespace=env)            # cached answers belonged to the old version

    def rollback(self, env: str) -> str:
        served = self.history[env]
        assert len(served) >= 2, "nothing validated to roll back to"
        served.pop()                               # drop the bad one
        self.alias[env] = served[-1]
        cache.invalidate(namespace=env)
        return served[-1]

GUARDS = {"schema_fail_rate": (0.02, 200), "p95_ms": (6000, 200), "refusal_rate": (0.10, 200)}

def check_guards(reg: Registry, env: str, window: dict) -> str | None:
    for metric, (limit, min_n) in GUARDS.items():
        if window["n"] >= min_n and window[metric] > limit:
            target = reg.rollback(env)
            alerts.page(f"{env}: {metric}={window[metric]:.3f} > {limit}; rolled back to {target}")
            return target
    return None
```

## Trade-offs
- Keeping the previous model warm makes rollback instant but costs GPU memory or a second serving
  pool; for infrequent releases a slower cold rollback may be the cheaper choice.
- Automatic rollback shortens time-to-recover at night but, if thresholds lack minimum samples or
  metrics are noisy, it rolls back healthy releases and trains people to ignore it.
- Bundle-level rollback is safe because the target was validated, but it is coarse: it reverts an
  unrelated good change that shipped in the same manifest, which argues for small manifests.
- Blue/green indexes make retrieval changes reversible at the cost of double storage during the bake
  window; for very large corpora that cost can dominate and force a partial-rebuild strategy.
- Immutable manifests and aliases impose registry discipline on every team; the overhead is real but
  it is what makes "which version served this request" answerable at all.

## Pitfalls
- Re-embedding the corpus in place; the symptom is a retrieval regression after a model change with no
  way back except a multi-hour rebuild.
- Caches not keyed by version; the symptom is "we rolled back but users still see the bad answers"
  for as long as the cache TTL.
- Relying on the vendor to keep serving the previous model; the symptom is a rollback target that
  returns a model-not-found error during the incident.
- Rolling back only the prompt because "the model did not change", when the model alias silently
  updated underneath; the symptom is a rollback that does not restore the old behaviour.
- Never rehearsing; the symptom is a rollback runbook that fails on a permissions error at the exact
  moment it is needed.
- Treating rollback as the whole fix; the symptom is memory entries, tickets or emails created by the
  bad version surfacing days later because nobody reconciled the audit log.

## When to use
Rollback is the first move whenever a release correlates with a guard breach or a credible quality
regression, and diagnosis comes second; the cost of being wrong about a rollback is small if the
previous version was validated, while the cost of debugging live is paid by users. Prefer fixing
forward only when the previous version is itself broken (a vendor retired it, an index was overwritten)
or when the fault is clearly outside the release. Measure time-to-rollback in rehearsals, keep it in
minutes, and make the trigger list part of every release plan. In day-to-day work this looks like
manifests in a registry, aliases in a control plane and a rollback button that engineers trust. In
interviews, the strong answer explains why an AI rollback is harder than a container rollback and what
has to be true beforehand for a pointer flip to actually restore behaviour.

## Role lens
- **Engineer:** Builds releases as immutable manifests, keys caches by manifest id, keeps the previous
  weights or index reachable, and wires guard metrics to the rollback action.
- **Architect:** Defines the deployable unit, the retention policy for previous versions (how many, how
  warm), and which triggers are automatic versus human, balancing recovery time against cost.
- **FDE:** Agrees rollback triggers and the rollback runbook with the customer before go-live, and
  verifies in the customer's environment that the previous version is actually still deployable.

## Check yourself
**Q:** A release changed the prompt and switched the embedding model, re-indexing the corpus in place.
Two days later answer quality drops. Walk through what rollback can and cannot do here and what you
would change for next time.
**A:** The prompt can be rolled back instantly if it is versioned in a registry and the environment
alias can be repointed. The index cannot: re-embedding in place destroyed the old vectors, and the
query side cannot go back to the old embedding model either, because old-model query vectors are
meaningless against new-model document vectors. So a prompt-only rollback runs the old prompt against
retrieval from the new model, a combination never evaluated and possibly worse. The honest options are to rebuild the old index from source (hours),
or to keep the new index and fix the prompt forward with a proper canary. For next time: treat the
bundle as one manifest, build the new index blue/green beside the old and switch an alias, keep the old
index through a bake window, key any response cache by manifest id, and define the quality trigger
and minimum sample before release so the decision happens on day one rather than day two.

## Quiz
### Q: Why is "roll back the prompt but keep the new model" risky even when the model change looked harmless?
- [ ] Prompt registries cannot store more than one version per model
- [x] It produces a combination that was never evaluated, so it is a forward change rather than a return to a known-good state
- [ ] Models cache the prompt, so the old prompt will not take effect
- [ ] It doubles inference cost during the transition
> A rollback is only safe because its target previously passed gates and served traffic. Mixing
> components from different manifests creates a new, unvalidated version under incident pressure.

### Q: After rolling back, users keep receiving the bad version's answers for an hour. What is the most likely cause?
- [ ] The rollback pointer takes an hour to propagate through DNS
- [ ] The old model needs an hour to warm its KV cache
- [x] A response or semantic cache was keyed without the version id, so it still serves answers produced by the bad version
- [ ] The guard metrics window is one hour long
> Rollback restores the code path, not the data it produced. A cache filled by the bad version keeps
> serving until its TTL unless caches are keyed by manifest or purged on rollback.

### Q: Which practice most directly ensures that a retrieval-index change can be rolled back?
- [ ] Storing the embedding model name in the prompt
- [ ] Lowering the index's HNSW parameters so rebuilds are faster
- [x] Building the new index beside the old one and switching an alias, keeping the old index during a bake window
- [ ] Re-embedding only documents that changed since the last release
> Blue/green indexing preserves the old vectors, so rollback is an alias switch. In-place re-embedding
> overwrites them and turns rollback into a multi-hour rebuild.
