---
id: prompt-registry
module: llmops-platform
name: Prompt registry
kind: pattern
difficulty: core
est_minutes: 14
related:
  - prompting/prompt-versioning
  - prompting/prompt-templates
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/model-registry
  - llmops-platform/rollback
  - observability/langfuse
  - enterprise-integration/tenant-specific-configuration-and-feature-flags
  - llmops-platform/evaluation-gates-for-production-releases
sources:
  - Official docs: Langfuse
  - Official docs: MLflow
---

## TL;DR
A prompt registry is a versioned store of prompt templates and their model settings, with immutable
numbered versions, mutable environment labels such as `production` or `canary`, and a runtime fetch API
that caches and falls back safely. It exists because prompts are behaviour-defining configuration edited
by people who do not ship code, and a hard-coded string gives you neither a record of which prompt
produced which answer nor a rollback that does not require a deploy.

## Why it exists
Prompts start life as string constants. That is fine for one engineer and one prompt. It stops being
fine the first time a product manager wants to change the tone of the answer, a forward-deployed
engineer needs a customer-specific variant, or an incident review asks "which prompt produced this
response at 02:14 on Tuesday". With constants, every tweak is a code deploy, variants become nested
if-statements, and the trace holds the rendered text at best and nothing at worst.

The naive upgrade, a `prompts` table edited from an admin page, solves the deploy problem and creates a
worse one: a mutable row has no history, so a bad edit is indistinguishable from the previous good one,
there is no review step, no eval evidence attached to a change, and rollback means remembering what the
text used to be. Teams that go this route tend to discover the problem at 3 am.

The registry pattern separates the two things people conflate: the *content* of a prompt, which must be
immutable once created so that every past trace can be resolved forever, and the *pointer* that says
which content a given environment uses, which must be mutable so that deploy and rollback are a pointer
move. It is the same split a model registry makes between versions and aliases, applied to text.

## How it works
### Data model
A **prompt** is a named entity (`support_answer`). Each **version** is immutable and holds the template
body, usually a list of chat messages with roles rather than a single string, the declared variables and
their types, the model settings it was tuned against (model id, temperature, max tokens, output schema),
a content digest, the author, a change note and a created timestamp. Versions are append-only because
a trace that recorded `support_answer@v12` must resolve to the same text in a year.

A **label** is a mutable pointer from `(prompt, label)` to a version: `production`, `staging`, `canary`,
or tenant-scoped labels such as `tenant:acme`. Deploy is a label move; rollback is the same operation in
reverse; an A/B test is two labels resolved by a routing rule. Labels are mutable because the
alternative, deploying code to change which version runs, is exactly what the registry exists to avoid.

Attached to a version, not a label, are the **evidence** records: eval run fingerprints and scores on a
named golden-set version, and links to the review that approved it. Evidence belongs to content, not to
pointers, because the question "was this text tested" has one answer regardless of where it is deployed.

### Prompt and model are a pair
A prompt is tuned to a model. The same text that works on one model family may over-refuse, ignore the
schema or change tone on another, so a version stores model id and decoding parameters alongside the
text. Swapping the model under an unchanged prompt is a new bundle and must be re-evaluated; the registry
makes that visible by refusing to pretend the text alone is the artifact.

### Runtime fetch
The application fetches by name and label at request time through an SDK that keeps an in-memory copy
with a short TTL, refreshes in the background, and serves the stale copy if the registry is unreachable.
A bundled copy of the production versions ships with the application as a last-resort fallback. The
registry is therefore never on the hot path in the sense that its outage cannot take the product down,
which is the condition under which it is acceptable to let it control behaviour at all.

Every model call logs the prompt name, version and digest into the trace. Observability platforms that
include prompt management, Langfuse being the common open-source example, link a trace to the prompt
version automatically, which turns "find every answer produced by v12" into a filter.

### Change workflow
Create a draft version, run the eval suite against it (the registry or the CI pipeline triggers this),
attach the results, get a review, move the label. The pipeline's service account should be the only
principal allowed to move `production`, so that an edit in the UI can create a version but cannot
deploy it. A diff view between two versions, both text and settings, is the review artifact.

### Where to host it
Prompts in a git directory with a build step give review and history for free but couple prompt
deploys to code deploys unless the application loads them from an object store at runtime. Hosted
registries in observability or eval platforms (Langfuse, LangSmith, Braintrust; MLflow added a prompt
registry in its 3.x line) give labels, a UI for non-engineers and trace linkage. A home-grown table with
versions and labels is a few hundred lines and is often the right answer for a small platform team that
already owns a config service. What matters is the split between immutable versions and mutable labels,
not the vendor.

## Example
A minimal registry showing the three operations that matter: register an immutable version (identical
content maps to the existing version, so re-registering is idempotent), move a label, and fetch with a
TTL cache that serves stale on failure. A real deployment backs `store` with a database.

```python
import hashlib, json, time

class PromptRegistry:
    def __init__(self, store):                 # store: {"versions": {name: [...]}, "labels": {(name, label): {...}}}
        self.store, self._cache = store, {}

    def register(self, name, messages, model, params, author, note):
        body = json.dumps({"messages": messages, "model": model, "params": params}, sort_keys=True)
        digest = hashlib.sha256(body.encode()).hexdigest()[:12]
        versions = self.store["versions"].setdefault(name, [])
        for v in versions:
            if v["digest"] == digest:
                return v["version"]            # same content, same version: registering is idempotent
        v = {"version": len(versions) + 1, "digest": digest, "messages": messages, "model": model,
             "params": params, "author": author, "note": note, "created": time.time(), "evals": {}}
        versions.append(v)                     # append-only: a version is never edited or deleted
        return v["version"]

    def set_label(self, name, label, version, by):      # deploy and rollback are both this one call
        self.store["labels"][(name, label)] = {"version": version, "by": by, "at": time.time()}

    def get(self, name, label="production", ttl=60):
        now, hit = time.time(), self._cache.get((name, label))
        if hit and now - hit["at"] < ttl:
            return hit["v"]
        try:
            ver = self.store["labels"][(name, label)]["version"]
            v = self.store["versions"][name][ver - 1]
            self._cache[(name, label)] = {"v": v, "at": now}
            return v
        except Exception:
            if hit:
                return hit["v"]                # registry down: serve stale rather than fail the request
            raise

reg = PromptRegistry({"versions": {}, "labels": {}})
v1 = reg.register("support_answer",
                  [{"role": "system", "content": "Answer using only the provided context: {{context}}"},
                   {"role": "user", "content": "{{question}}"}],
                  model="vendor-model-2025-04-14",        # a dated snapshot id, never a floating alias
                  params={"temperature": 0.2, "max_tokens": 600}, author="ana", note="initial")
reg.set_label("support_answer", "production", v1, by="ci:release-42")
p = reg.get("support_answer")
# Log p["version"] and p["digest"] on every trace so any answer resolves to its exact prompt.
```

## Trade-offs
- Decoupling prompt deploys from code deploys lets non-engineers iterate in minutes, but it creates a
  second production change path that must be gated as strictly as code or it becomes the bypass.
- Immutable versions guarantee every trace resolves forever, at the cost of a growing store and a
  retention policy; for most teams prompts are kilobytes and this never matters, for multi-tenant
  platforms with thousands of variants it does.
- Runtime fetching keeps behaviour changeable without redeploy, but it adds a dependency; the TTL cache
  and bundled fallback remove the availability risk while introducing a propagation delay of one TTL.
- Storing model settings with the prompt makes the pair explicit and re-evaluation mandatory on model
  change; it also means a platform-wide model migration touches every prompt version rather than one
  config line.
- A hosted registry gives UI, labels and trace linkage out of the box; a git directory gives review and
  blame for free. The hosted option wins once people outside engineering own prompts.

## Pitfalls
- Editing a version in place "because it is just a typo"; the symptom is two traces with the same version
  id and different rendered text, which destroys every post-incident analysis that relies on the id.
- Resolving labels at deploy time and baking the text into the image; the symptom is a label move that
  changes nothing until the next deploy, and an engineer who concludes the registry is broken.
- Logging only the prompt name, not the version and digest; the symptom is an investigation that knows
  which prompt family failed and cannot tell whether it was before or after Tuesday's change.
- Treating the registry as prompt-only and leaving temperature and model id in application config; the
  symptom is a prompt that passed its eval behaving differently in production because the two were tuned
  apart.
- Allowing the `production` label to be moved from the UI by anyone with edit rights; the symptom is a
  behaviour change with no pipeline run and no evidence attached.

## When to use
Reach for a registry as soon as a second person edits prompts, a prompt needs per-tenant or per-locale
variants, or an audit or incident process needs to tie an answer to the exact text that produced it.
Before that point a prompts directory in the repository with versioned file names is enough. Measure the
things the registry is supposed to deliver: time from prompt edit to production, number of versions with
attached eval evidence, and whether every production trace resolves to a version. In interviews, the
expected depth is the immutable-version versus mutable-label split, why evidence attaches to versions,
how the runtime fetch avoids becoming a single point of failure, and why the prompt and its model
settings are versioned together.

## Role lens
- **Engineer:** Integrates the SDK with TTL caching and fallback, logs version and digest on every trace,
  and makes the eval pipeline run on new versions automatically.
- **Architect:** Decides where the registry lives, who may move which labels, how tenant variants are
  scoped, and that prompt plus model settings is the versioned unit.
- **FDE:** Uses tenant labels to ship customer-specific prompt variants without forking the product, and
  shows the customer's change-control team the version history and attached evidence.

## Check yourself
**Q:** Your registry stores only prompt text, and application config holds `temperature=0.7`. A platform
team migrates the whole fleet to a new model snapshot by changing one config line, and a legal-summary
prompt that passed its evals last month starts omitting mandatory disclaimers. Explain what the registry
design got wrong and what the version record should have contained.
**A:** The registry treated text as the artifact when the artifact is the pair of text and the model
settings it was evaluated against. Because the model id lived outside the version, the migration did not
create a new version, triggered no eval, and left every trace pointing at a version id that now means
something different in practice. The version record should hold the model snapshot id, temperature,
max tokens and output schema alongside the messages, with a digest over all of it; a model migration
then becomes a new version per prompt, each of which must pass the eval suite and be promoted by label.
The migration gets slower by exactly the amount of evidence it was skipping, and the disclaimer
regression would have been caught by the legal-summary golden set before the label moved.

## Quiz
### Q: Why must prompt versions be immutable while labels are mutable?
- [ ] Immutable versions compress better in storage
- [x] Traces must resolve to the exact text forever, while deploy and rollback need to be a pointer move
- [ ] Labels are immutable too; only drafts can change
- [ ] Mutable versions would break template rendering
> Immutability of content keeps past traces meaningful and evidence attached to the right text; mutability
> of the pointer is what lets a deploy or rollback happen without shipping code.

### Q: An application fetches prompts from the registry on every request with no cache. What is the main risk?
- [ ] Prompts will be rendered with stale variables
- [ ] Versions will be created on every request
- [x] A registry outage or slowdown takes the product down with it
- [ ] Labels cannot be resolved without a cache
> Runtime fetching is acceptable only when the registry cannot become a single point of failure, which is
> what the TTL cache, background refresh and bundled fallback provide.

### Q: Where should eval results for a prompt be attached, and why?
- [ ] To the label, because that is what production uses
- [x] To the version, because evidence describes the content regardless of where it is deployed
- [ ] To the trace, because that is where the answer appears
- [ ] To the application release, because prompts ship with code
> "Was this text tested" has a single answer per version. Attaching it to a label would lose the evidence
> every time the label moved and would let an untested version inherit a tested label's history.
