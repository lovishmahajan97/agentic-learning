---
id: technical-documentation
module: delivery-ownership
name: Technical documentation
kind: practice
difficulty: core
est_minutes: 14
status: reviewed
related:
  - system-design/adrs
  - deployment/runbooks
  - fastapi-for-ai/openapi-docs
  - adoption/handoff-to-customer-teams
  - delivery-ownership/operational-ownership-and-support-boundaries
  - deployment/security-questionnaires
  - llmops-platform/prompt-registry
sources:
  - Official docs: Diátaxis documentation framework
  - Nygard, 2011 — Documenting Architecture Decisions
  - Mitchell et al., 2019 — Model Cards for Model Reporting
---

## TL;DR
Technical documentation is how a system keeps working when its builders are not in the room. It works
when each document has one reader and one job (learn, do a task, look something up, understand why),
lives next to the code it describes, has an owner, and is checked for staleness like code is checked for
bugs. AI systems need extra pages: model and prompt versions, evaluation results, data flows and known
failure modes, because those are what operators and reviewers ask about first.

## Why it exists
Delivered systems outlive the attention of the people who built them. The forward-deployed engineer
rotates to the next customer, the original architect changes teams, and the customer's operations team
inherits a pipeline at 2 a.m. with an error message they have never seen. Every question that is not
answered in writing becomes a page to someone who has moved on, or a guess. Security reviewers,
auditors and new team members ask the same questions again and again; without documents, senior people
answer them in meetings forever.

The naive approaches fail. "The code is the documentation" answers what the system does but never why it
does it that way, what was rejected, or what to do when it breaks. One giant wiki page that mixes a
tutorial, a configuration reference and design rationale serves nobody: the newcomer drowns in
reference tables and the on-call engineer cannot find the one command they need. And documentation
written once at the end of a project decays immediately, because the system keeps changing and nothing
forces the docs to follow. The cause is structural, not laziness: the author pays the cost of updating a
document and a different person, later, pays the cost of it being wrong, so without a mechanism that
puts the update at the moment of change, drift is the default. A stale doc is worse than none: it is
trusted, and it is wrong.

AI systems sharpen this. Behaviour depends on artefacts that are not obviously code: the model version,
the prompt, the retrieval index, the evaluation set. When output changes, the first question is which of
these changed, and only documentation and registries can answer it.

## How it works
### One reader, one job per document
The Diátaxis framework separates documentation into four types *because* readers arrive in four
different modes, and mixing them makes each worse:
1. **Tutorials**: learning by doing, for a newcomer. A guaranteed-to-work path from nothing to a first
   result. No options, no edge cases.
2. **How-to guides**: a competent reader with a goal. "Rotate the provider API key", "add a new tenant".
   Steps, preconditions, how to verify success.
3. **Reference**: lookup. Configuration keys, API endpoints, environment variables, error codes.
   Generated from the source of truth wherever possible (OpenAPI, config schemas) *because* hand-written
   reference drifts first.
4. **Explanation**: understanding. Architecture, design rationale, trade-offs and constraints.

### The delivery document set
For a system handed to a customer or another team, the minimum set usually maps onto those types:
- **Architecture overview** (explanation): components, data flows, trust boundaries, external
  dependencies. Security review starts here, so draw the data flow, not just the boxes.
- **Architecture decision records** (explanation): one short record per significant decision with
  context, options and consequences, *because* six months later "why not just use X?" is the question.
- **Runbooks** (how-to): one per alert, in a fixed order: symptom and how to confirm it, user impact,
  the first safe action (usually rollback or failover, *because* it stabilises before anyone
  understands the cause), diagnosis steps with expected output, and a "stop and escalate if" line
  naming who to call.
- **API and configuration reference**, generated where possible.
- **AI system card** (explanation plus reference): model and version, prompt versions, retrieval sources,
  evaluation sets and latest results, intended use, out-of-scope uses and known failure modes. This
  borrows the model-card idea and extends it to the whole system.
- **Operations and support guide**: who owns what, escalation paths, SLAs, how to roll back.

### Docs as code
Keep documents in the repository, change them in the same pull request as the code, and review them with
it. *Because* review is where staleness is cheapest to catch, the pull-request template should ask
"which docs does this change invalidate?". Add CI checks: broken links, executable code samples,
generated reference rebuilt on each build, and freshness rules mapping docs to the code they cover. Give
every document an owner and a review date; an unowned document is a stale document waiting to happen.

### Versioned with what is deployed
Documentation must describe the version the reader is running. If a customer runs release 2.3 and the
docs describe 2.5, the runbook's commands may not exist yet. Tag docs with the release that built them,
publish per-version reference, and put the deployed model, prompt and index versions in the system card
by pulling them from the registry or deployment manifest rather than typing them, *because* typed
version strings are the first thing to go stale.

### Testing documentation
A document is only verified when someone other than its author uses it to do the job. Have a new team
member follow the tutorial from a clean machine and record each place they stalled; run a tabletop
incident where responders may use only the runbooks; ask the customer's operator to perform a rollback
from the operations guide before handoff. Each stall is a bug in the doc. This is the documentation
equivalent of a failover drill, and for the same reason: an unexercised path should be presumed broken.

### Writing so it gets used
Lead with the answer, then the detail. Make commands copy-pasteable and state what success looks like.
Write for the reader's state: an on-call engineer at 2 a.m. needs the first safe action, not the history.

## Example
A CI check that fails the build when code changes but the document that describes it does not. Each doc
declares which paths it covers in a `covers:` line; the check compares last-commit times.

```python
"""Fail CI when documented code changed after its doc was last updated."""
import pathlib, re, subprocess, sys

def last_commit_ts(path: str) -> int:
    out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out or 0)                     # 0 if the path has no history

stale = []
for doc in pathlib.Path("docs").rglob("*.md"):
    m = re.search(r"^covers:\s*(.+)$", doc.read_text(), re.M)
    if not m:                                # e.g. covers: src/extract/ prompts/invoice.yaml
        continue
    doc_ts = last_commit_ts(str(doc))
    for target in m.group(1).split():
        if last_commit_ts(target) > doc_ts:
            stale.append(f"{doc}: '{target}' changed after this doc was last updated")

print("\n".join(stale) or "all covered docs are fresh")
sys.exit(1 if stale else 0)
```

On a customer deployment, `docs/runbooks/extraction-backlog.md` might cover `src/extract/` and
`prompts/invoice.yaml`. When someone edits the invoice prompt, CI fails until the runbook is touched,
which forces a reviewer to ask whether its steps and expected outputs still hold. The check is a nudge,
not proof: a trivial edit silences it, so pair it with the pull-request question about invalidated docs.
It also needs real history: many CI systems check out a shallow clone of depth one by default, where every
path reports the same last commit and the check silently passes, so fetch full history (or at least the
merge base) in the job that runs it.

## Trade-offs
- Docs as code keeps documentation current with changes, but non-engineers find repositories hard to
  edit; it flips toward a wiki for customer-facing guides owned by support or product staff.
- Generated reference never drifts from the source, but reads poorly and lacks intent; it flips to
  hand-written sections where the reader needs context a schema cannot express.
- A full document set eases handoff and audits, but every page is a maintenance liability; prefer fewer,
  owned documents when the system is still changing weekly.
- Detailed runbooks shorten recovery for known failures, but encourage blind command-following; they need
  a "stop and escalate if" line for situations outside what the author anticipated.

## Pitfalls
- Mixing tutorial, reference and rationale in one page; the symptom is readers asking questions in chat
  that the document technically answers somewhere in its fourth screen.
- Writing documentation once at handoff; the symptom is the customer's team discovering, during their first
  incident, that the runbook names a config key that was renamed months ago.
- Documenting what the code does but not why; the symptom is a new owner "simplifying" a retry or a
  prompt instruction that existed to work around a specific provider failure.
- Omitting model and prompt versions from system documentation; the symptom is a behaviour change nobody
  can attribute because no one knows what was running last month.
- Only ever reviewing docs by reading them; the symptom is the first real use revealing that a runbook
  assumes access, tools or tribal context the customer's operator does not have.
- Leaving documents without owners; the symptom is a wiki where every page has a "this may be out of date"
  banner and nobody trusts any of it.

## When to use
Write documentation whenever someone other than the current author must operate, review, extend or
approve the system, which for delivered work is always. Start early with the architecture overview and
decision records, because security reviews and design debates need them; write runbooks with the alerts
they serve; write the handoff and system card before the people who know the answers leave. Do not write
long documents for throwaway prototypes. Measure usefulness by the questions that stop arriving and by
incident timelines: how long did responders spend finding what to do? In interviews, describing how you
kept documentation true, not merely that you wrote it, signals real ownership experience.

## Role lens
- **Engineer:** Updates docs in the same pull request as the change, keeps reference generated from
  schemas, and writes runbooks that start with the first safe action.
- **Architect:** Owns the architecture overview and decision records, and sets the documentation standard
  every delivered system must meet before it is considered production-ready.
- **FDE:** Writes the handoff set the customer's operators will actually use, and the data-flow and system
  card pages their security and compliance reviewers ask for first.

## Check yourself
**Q:** You are handing an AI document-extraction system to a customer's operations team in three weeks. What
documentation do you produce, in what order, and how do you keep it accurate after you leave?
**A:** First the architecture overview with the data flow and trust boundaries, because their security
review gates go-live. Then runbooks for each alert, written from real incidents in the pilot, then the
operations guide with ownership, escalation and rollback. Next an AI system card listing model and
prompt versions, evaluation results, intended use and known failure modes, plus generated configuration
reference. Decision records for the major choices go in alongside. To keep it true, it lives in their
repository, each doc has a named owner on their side, the pull-request template asks which docs a change
invalidates, and CI checks links and freshness. I would run one tabletop incident using only the docs.

## Quiz
### Q: Why separate how-to guides from tutorials instead of writing one combined guide?
- [ ] Tutorials must be generated automatically while how-to guides are written by hand
- [x] A newcomer needs a single guaranteed path to learn, while a competent reader needs steps for a specific goal
- [ ] How-to guides are only for internal staff and tutorials only for customers
- [ ] Combined guides cannot include code samples
> Readers arrive in different modes. A tutorial removes choices to build confidence; a how-to assumes
> competence and targets a goal. Mixing them gives the learner too many options and the expert too much hand-holding.

### Q: Which artefact best answers "why did we choose this retrieval design over fine-tuning?" a year later?
- [ ] The API reference generated from the OpenAPI schema
- [ ] The runbook for the retrieval latency alert
- [x] An architecture decision record capturing context, options considered and consequences
- [ ] The commit message of the first retrieval commit
> Decision records exist to preserve rationale and rejected alternatives. Reference and runbooks describe
> what and how, and commit messages are scattered and rarely capture the options weighed.

### Q: What is the strongest reason to keep documentation in the same repository as the code?
- [ ] Repositories render Markdown better than wikis do
- [ ] It removes the need for document owners
- [x] Docs can be changed and reviewed in the same pull request that changes the behaviour they describe
- [ ] It makes the documentation publicly visible
> Staleness is cheapest to catch at the moment of change. Co-locating docs lets review and CI tie them to
> the code change; ownership is still needed, and visibility depends on repository settings.
