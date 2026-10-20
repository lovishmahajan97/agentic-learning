---
id: prompt-versioning
module: prompting
name: Prompt versioning
kind: practice
difficulty: core
est_minutes: 12
status: fact-checked
related:
  - prompting/prompt-templates
  - prompting/prompt-caching
  - llmops-platform/prompt-registry
  - llmops-platform/ci-cd-for-prompts-and-models
  - llmops-platform/rollback
  - evals/regression-tests
  - evals/golden-dataset
  - observability/tracing
sources:
  - Sculley et al., 2015 — Hidden Technical Debt in Machine Learning Systems
  - Official docs: Langfuse
  - Official docs: LangSmith
---

## TL;DR
A prompt is behaviour-defining code that happens to be prose, so it needs what code already has:
immutable versions, an identifier recorded on every call, review, an eval before promotion and a
one-step rollback. Without that, a quality regression cannot be tied to a change, a user complaint
cannot be reproduced, and "the prompt in production" is whatever someone last pasted from a playground.

## Why it exists
The behaviour of an LLM feature is the product of model × prompt × parameters × tools × retrieval. Of
those, the prompt changes most often and is handled with the least discipline: it is a string literal
somewhere in the codebase, edited by a product manager in a vendor console, hot-patched on a Friday to
fix one complaint. Three failures follow directly.

First, irreproducibility. A user reports a wrong answer on Tuesday; the prompt was edited on Wednesday;
nobody can say which wording produced the complaint, so nobody can test the fix. Second, silent
regression. A sentence added "to handle one edge case" shifts behaviour on a few percent of unrelated
traffic, and because no eval ran before the change, the shift is discovered by customers. Third, coupled
rollout. If prompts live only in code, a bad prompt needs a code rollback; if they live only in a
database, the code may expect a template variable the new prompt dropped, and the failure appears at
runtime rather than at review.

The naive answer, "it is in git, so it is versioned", is half right. Git versions the file. It does
not record which commit produced which response, does not capture the variables rendered at runtime,
and hides that the model id and the tool descriptions changed in the same pull request. Sculley et al.
called this configuration debt: in mature ML systems the configuration is large, rarely reviewed
with the rigour applied to code, and a common source of costly mistakes. Prompt versioning is the practice of turning the *effective prompt* into an
identifiable, testable, revertible artifact.

## How it works
### What the versioned unit is
The thing that determines behaviour is not the system prompt alone. It is the bundle: template text
(system prompt, user-turn scaffolding, few-shot examples), tool names, descriptions and schemas, the
output schema, the model id, sampling and output parameters, and the rendering code that fills
variables. Version the bundle, *because* a change to any part changes behaviour, and a bundle that
records only the template will report "no change" after someone edits a tool description.

### Two identities: hash and label
A content hash (SHA-256 of a canonical serialization of the bundle) is exact, automatic and changes
when any byte changes; it is the identity you trust. A human label (`v14`, or a semver-style
`2.1.0` where a major bump means "behaviour changed, re-evaluate") is the identity you talk about.
Use both: the hash goes in every trace, the label goes in the changelog and the alias file.

### Immutable versions, mutable aliases
Versions never change once created. Deployment is a pointer: an alias such as `production` or
`canary` resolves to a version, and release means moving the pointer. This mirrors container tags and
exists *because* it decouples prompt rollout from code deploys and makes rollback a one-line change
with no rebuild. The cost is that code and prompt now evolve separately, so each version declares its
variable contract (which placeholders it expects) and the loader checks that contract at startup rather
than discovering a missing variable in a 3 a.m. request.

### Where versions live
Three workable layouts. In the repository as files (`prompts/<name>/v14.md` with a metadata header):
reviewed in pull requests, promoted by CI, tied to a commit; best when engineers own prompts. In a
registry (Langfuse, LangSmith, PromptLayer or a table you own): fetched by alias at runtime, editable
through a UI by non-engineers, hot-swappable without a deploy; best when domain experts iterate daily.
Or both: files are the source of truth and CI pushes them to the registry, which gives review *and*
runtime decoupling.

### Record the version on every call
This is the step that makes everything else pay off. Each trace of an LLM call carries the prompt
name, version label, content hash, model id and the rendered variables (or their hash when they are
sensitive). With that, "show every response from v13 next to v14 on the same inputs" and "replay the
exact prompt behind this complaint" become queries instead of archaeology.

### Promotion through evals
A new version is a candidate until it has run against the golden dataset and the regression suite,
been compared case by case against the current production version, and survived a canary slice of
live traffic. The eval exists *because* prompt changes have non-local effects that reading the diff
cannot predict; the canary exists because the golden set never covers the real distribution.

### Two interactions worth knowing
A prompt release changes bytes near the front of the request, so every request on the new version
misses the prompt cache until the new prefix has been written once: expect a short cost and
time-to-first-token blip, larger at high concurrency where many requests miss before the first write
lands. A gradual alias move keeps most traffic on the still-warm old prefix while the new one warms.
And a model upgrade is a prompt version too, because prompts are tuned to a model; pin the model in
the bundle and re-evaluate when it changes.

## Example
A minimal file-based registry: immutable version files, an alias file for promotion, a content hash,
a variable-contract check at load time, and the trace line that records which version answered.
Swapping the directory for a registry client changes `load`, nothing else.

```python
import hashlib, json, pathlib, re
import yaml                                  # pip install pyyaml

ROOT = pathlib.Path("prompts")               # prompts/support_triage/{aliases.yaml, v13.md, v14.md}

def load(name: str, alias: str = "production") -> dict:
    aliases = yaml.safe_load((ROOT / name / "aliases.yaml").read_text())   # {"production": "v13", "canary": "v14"}
    version = aliases[alias]
    header, _, template = (ROOT / name / f"{version}.md").read_text().partition("\n---\n")
    meta = yaml.safe_load(header)            # model, max_tokens, variables: [product, tier]
    found = set(re.findall(r"{{(\w+)}}", template))
    assert found == set(meta["variables"]), f"variable contract drift: {found ^ set(meta['variables'])}"
    canon = json.dumps({"meta": meta, "template": template}, sort_keys=True).encode()
    return {"name": name, "version": version, "hash": hashlib.sha256(canon).hexdigest()[:12],
            "meta": meta, "template": template}

def render(p: dict, **vars) -> str:
    missing = set(p["meta"]["variables"]) - vars.keys()
    if missing:
        raise ValueError(f"{p['name']}@{p['version']} needs {missing}")
    return re.sub(r"{{(\w+)}}", lambda m: str(vars[m.group(1)]), p["template"])

def triage(client, ticket: str, tracer) -> str:
    p = load("support_triage")               # alias resolved per call: rollback = edit aliases.yaml
    system = render(p, product="Acme Billing", tier="enterprise")
    r = client.messages.create(model=p["meta"]["model"], max_tokens=p["meta"]["max_tokens"],
                               system=system, messages=[{"role": "user", "content": ticket}])
    tracer.log(prompt=p["name"], version=p["version"], hash=p["hash"],   # the line that makes replay possible
               model=p["meta"]["model"], input_tokens=r.usage.input_tokens)
    return r.content[0].text
```

## Trade-offs
- Prompts as files in git give pull-request review and atomic code-plus-prompt changes, but couple
  rollout to deploys and lock out non-engineers; it flips to a registry once domain experts edit
  prompts weekly or a fix must ship without a release.
- A registry allows hot swaps and UI editing but invites drift between what the code expects and what
  the prompt provides; a declared variable contract checked at load time is the price of that freedom.
- Content hashes are exact but meaningless to humans, while labels communicate intent but depend on
  discipline; using both costs nothing and avoids arguing about which one is "real".
- Resolving the alias at request time makes rollback instant but puts the registry in the request
  path; cache the resolution with a short TTL and fall back to the last known version on failure.
- Running the full eval on every version is slow; it is mandatory for a major bump and overkill for a
  typo fix, which is exactly the distinction the version label exists to express.

## Pitfalls
- Editing in the vendor console and pasting into production; the symptom is a live prompt that matches
  no commit and a regression nobody can date.
- Changing the model and the prompt in the same version; the symptom is an eval drop that cannot be
  attributed to either, so both get reverted.
- Not recording the version on each call; the symptom is a user complaint you cannot reproduce because
  you do not know which prompt that user received.
- Versioning the template but not tool descriptions, few-shot examples or the output schema; the
  symptom is "the prompt did not change" while behaviour did after someone reworded a tool.
- Promoting on the aggregate score alone; the symptom is a mean that rose one point while one critical
  category fell from 95% to 70%, visible only in per-case diffs.
- Releasing to 100% of traffic at once; the symptom is a regression that reaches every user before any
  metric can flag it, plus a brief cost and latency spike as all traffic misses the prompt cache together.

## When to use
From the first prompt that reaches a user. The minimum that pays for itself in a week is: prompt in a
file with a metadata header, a content hash logged on every call, and the golden set run in CI before
merge. Add aliases when you need rollback without a deploy, a registry when non-engineers edit, and
canary routing when traffic is large enough that a 5% slice is informative. Measure three things: the
fraction of calls whose trace carries a prompt version (should be 100%), time from "bad release
noticed" to "rolled back", and the eval delta between consecutive versions. In interviews, "how do you
ship a prompt change safely" expects exactly this sequence: version, evaluate, canary, promote,
roll back, with the trace link that makes each step auditable.

## Role lens
- **Engineer:** Keeps prompts as versioned files or registry entries, logs the version and hash on
  every call, and wires the golden-set eval into CI so an unevaluated version cannot be promoted.
- **Architect:** Defines the versioned unit (template plus model, tools and schemas), chooses where
  the registry lives, and sets the promotion policy from candidate to canary to production.
- **FDE:** Maintains per-customer prompt variants as versions of a shared base, so a fix to the base
  can be re-evaluated and rolled out across customers instead of hand-merged into each one.

## Check yourself
**Q:** A product manager edits the support bot's system prompt in the vendor console on Friday to fix
one complaint. On Monday, escalations to human agents are up 20%. Walk through what should have
happened, and how you would set things up so this cannot recur.
**A:** The edit should have produced a new candidate version with its own hash, not a mutation of
production. That candidate should have run against the golden set and the regression suite, with a
case-by-case comparison against the current version, which would likely have shown the new sentence
changing behaviour on unrelated tickets. It should then have gone to a canary alias for a few percent
of traffic while escalation rate was watched, and only then been promoted. Recovery on Monday is a
pointer move back to the previous version, which is possible only if versions are immutable and
aliased. To make it stick: prompts live in files or a registry with aliases, the console is read-only
for production, every call logs prompt name, version and hash so Monday's escalations can be joined
to the version that caused them, and CI blocks promotion of any version without an eval run.

## Quiz
### Q: Which single piece of data makes prompt versioning pay off most once it is in place?
- [ ] A human-readable changelog entry for each version
- [x] The prompt name, version and content hash recorded on every call's trace
- [ ] A semantic version number that follows semver strictly
- [ ] A diff of the template stored alongside each version
> Changelogs and diffs describe what changed; only the per-call record ties a specific response, and
> therefore a complaint or a metric, to the version that produced it, which is what makes replay and
> comparison possible.

### Q: Why keep aliases such as `production` separate from immutable version identifiers?
- [ ] So that versions can be edited in place without changing their name
- [ ] Because registries cannot store more than one version per prompt
- [x] So that release and rollback are pointer moves that need no rebuild, and a version's eval results stay valid
- [ ] To hide the version number from the model
> If `production` were itself a mutable version, every edit would silently invalidate whatever eval
> had been run on it; separating the pointer from the content keeps versions testable and rollback
> instant.

### Q: A team versions the system prompt template but not the tool descriptions. What is the consequence?
- [ ] None, because tool descriptions do not influence model behaviour
- [ ] Tool calls will fail schema validation
- [x] Behaviour can change with no recorded version change, because tool descriptions are part of the effective prompt
- [ ] The prompt cache will stop working
> The model selects and uses tools by reading their descriptions, so rewording one changes behaviour
> exactly like rewording the system prompt; the versioned unit must include everything that is
> rendered into the request.
