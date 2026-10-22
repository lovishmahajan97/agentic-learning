# Continuing the lesson build (handoff)

## Status 2026-10-22: agent-builder edition complete

- 613 lessons in 32 modules, 6 stages (`curriculum.json`); new modules: Agent frameworks & tooling (18)
  and Model Context Protocol in depth (11).
- Every lesson is in SPEC v3 (agent-builder framing on the crisp format): For agent builders box,
  agent_relevance tag ({'useful': 276, 'core': 316, 'background': 21}), diagram, 2+ examples, production patterns, interview questions,
  use cases and a mini project. Every lesson is `agentic-verified` by an independent reviewer.
- 32/32 module guides rewritten for agent builders; each Capstone is the module's major project.
- Checks: `python3 scripts/build.py` ok=613/613; `python3 scripts/compare_lesson.py --all` 0 lost data
  vs tag `pre-agentic`; 645/645 mermaid diagrams parse (613 lessons + 32 guides).
- Design: vibrant light theme (stage hues, signature gradient), dark mode on the toggle.
- Baselines: tag `pre-crisp` (long-form originals) and tag `pre-agentic` (crisp edition) keep every
  earlier version recoverable.

To add or change lessons later: edit the Markdown, run `python3 scripts/build.py --check --v3 <file>` and
`python3 scripts/compare_lesson.py <file>`, then `python3 scripts/build.py`. For bulk work, regenerate with
`python3 scripts/make_agentic_workflow.py --groups 3` (it schedules only unfinished work).

---


## Snapshot 2026-10-21: crisp rewrite stopped on request

**What this phase does.** Every lesson is rewritten from long prose into a scannable format (SPEC v2 in
`content/SPEC.md`): bullets, steps and tables, a mermaid **Diagram**, 2+ **Examples**, a new
**Production patterns** section, and 4-6 **Interview questions**. Facts are never dropped:
`scripts/compare_lesson.py` compares each rewrite with the tag `pre-crisp` (the last commit before the
rewrite) and fails on any lost number, code block, inline code span, quiz, check-yourself answer or role
lens. The app was also rearranged into one track of 6 stages (Python first) defined in `curriculum.json`;
the role-path dropdown is gone (role data is kept in `taxonomy.json`).

**State at the stop point**

| Item | Done |
|---|---|
| Lessons in crisp format | 583 / 584 (`multimodal/document-understanding` still in the old format) |
| Lessons independently verified (`status: crisp-verified`) | 90 / 584 |
| Module guides made crisp (with a mermaid map) | 0 / 30 |
| `python3 scripts/build.py` | ok=584/584, modules invalid=0 |
| `python3 scripts/compare_lesson.py --all` | 583 crisp lessons, 0 lost data |
| Diagrams parsed with mermaid 10.9.1 | 583 / 583 parse |
| Prose paragraphs | 2,201 → 636; median 81 → 28 words |

Remaining work: rewrite 1 chunk, verify 72 chunks, 30 guides (103 agents).

**Resume**

    python3 scripts/make_crisp_workflow.py --status      # what is left (reads the files, no session state)
    python3 scripts/make_crisp_workflow.py --groups 3    # regenerates scripts/lms-crisp.workflow.js

Then launch `scripts/lms-crisp.workflow.js` three times in one message with args
`{"group": 0, "groups": 3}`, `{"group": 1, "groups": 3}`, `{"group": 2, "groups": 3}`. When they finish:
`python3 scripts/build.py`, `python3 scripts/compare_lesson.py --all`, parse every diagram in the browser
(see the mermaid rules in SPEC.md), commit, push, and republish the artifact in place
(https://claude.ai/artifact/F7P3j3rqGSKnXKCCb2vH1b; page `site/page.html` plus `app.js`, `styles.css`,
`data.js` and every `data/<module>.js`). The published artifact still shows the previous version.

**Gotchas from this phase**
- Workflow prompts are JavaScript template strings: never put backticks in prompt text.
- Do not move the `pre-crisp` tag; it is the no-loss baseline.
- Agents left over-long TL;DRs (46-50 words) in 6 lessons; they were trimmed by hand.

---


> **Status 2026-10-20 (session 3, local): the build is complete.** 584/584 lessons written, fact-checked
> and pedagogy-reviewed; 30/30 module guides; `python3 scripts/build.py` reports `ok=584/584`,
> `modules missing=0 invalid=0`. The cloud branch was merged into `main` and deleted, so work on `main`.
> The rest of this file is kept as a record of how the build was resumed.

This file records where the 584-lesson generation stands, why it was interrupted, and exactly how to
resume it from a **local** Claude Code / Cowork session on your own computer. Read it top to bottom
before launching anything.

## What happened

1. **Session 1 (local, Mac, 2026-10-20).** Built the app (`site/`), the taxonomy (`taxonomy.json`),
   the authoring spec (`content/SPEC.md`), the exemplar (`content/model-internals/kv-cache.md`) and the
   validator (`scripts/build.py`). Launched three `lms-lessons` workflows (the old
   `scripts/lms-lessons.workflow.js`, now superseded) to write, fact-check and pedagogy-review every
   lesson. **They were stopped by the plan's session usage limit** ("Session limit reached") after
   232 lessons had been written. The fact-check, teach-check and module-guide phases never ran, and
   three lessons failed validation (TL;DR over 90 words).
2. **Session 2 (cloud, branch `claude/wizardly-wozniak-5lduqj`).** Fixed the three TL;DRs, generated a
   resumable workflow from the on-disk state, launched it as 8 parallel runs (the cloud box has 4 CPUs,
   so each run gets only 2 concurrent agents), and auto-committed `content/` to this branch every
   15 minutes. **Stopped on request at 2026-10-20 14:17 UTC** with everything on disk validated, committed and
   pushed; no cloud agents are running any more, so local work can start immediately. **The branch is
   the source of truth, not any session.**

## The failed / unfinished task, in one sentence

For every one of the 584 topics in `taxonomy.json`: a lesson written to `content/SPEC.md`, then
fact-checked, then pedagogy-reviewed, plus a `_module.md` guide for each of the 30 modules; then a
clean `python3 scripts/build.py`, a commit, and a republish of the private "AI Roles Academy" artifact.

## How progress is tracked on disk (no session state needed)

| Evidence on disk                                            | Meaning                              |
|-------------------------------------------------------------|--------------------------------------|
| `content/<module>/<slug>.md` exists                         | lesson written                       |
| its front matter has `status: fact-checked`                 | passed the skeptical fact-check pass |
| its front matter has `status: reviewed`                     | also passed the pedagogy review pass |
| `content/<module>/_module.md` exists and validates          | module guide done                    |

Live numbers and the per-module remaining work:

    python3 scripts/make_workflow.py --status
    python3 scripts/build.py --report        # validator view: ok / missing / invalid

Snapshot when this file was last updated:

```
# 2026-10-20 14:17 UTC
topics ok=400/584 missing=184 invalid=0 | modules missing=30 invalid=0
lessons written 400/584 | fact-checked 177/584 | reviewed 0/584 | guides 0/30
agents still needed: write 33, fact-check 59, teach-check 85, guides 30 = 207 total
remaining by module:
  ml-fundamentals                    tier 1  chunks to write= 0 fact-check= 0 teach-check= 3 guide=todo
  model-internals                    tier 1  chunks to write= 0 fact-check= 0 teach-check= 3 guide=todo
  training-adaptation                tier 1  chunks to write= 0 fact-check= 1 teach-check= 4 guide=todo
  prompting                          tier 1  chunks to write= 0 fact-check= 2 teach-check= 2 guide=todo
  rag-retrieval                      tier 1  chunks to write= 2 fact-check= 5 teach-check= 5 guide=todo
  agents                             tier 1  chunks to write= 0 fact-check= 3 teach-check= 3 guide=todo
  agent-reliability                  tier 1  chunks to write= 0 fact-check= 2 teach-check= 2 guide=todo
  evals                              tier 1  chunks to write= 0 fact-check= 4 teach-check= 4 guide=todo
  serving-inference                  tier 1  chunks to write= 0 fact-check= 0 teach-check= 4 guide=todo
  observability                      tier 1  chunks to write= 0 fact-check= 0 teach-check= 2 guide=todo
  safety-security                    tier 1  chunks to write= 0 fact-check= 0 teach-check= 2 guide=todo
  multimodal                         tier 1  chunks to write= 0 fact-check= 0 teach-check= 1 guide=todo
  backend-fundamentals               tier 1  chunks to write= 0 fact-check= 3 teach-check= 3 guide=todo
  python-for-ai                      tier 1  chunks to write= 6 fact-check= 7 teach-check= 7 guide=todo
  fastapi-for-ai                     tier 1  chunks to write= 5 fact-check= 5 teach-check= 5 guide=todo
  node-ts-for-ai                     tier 1  chunks to write= 0 fact-check= 3 teach-check= 3 guide=todo
  system-design                      tier 2  chunks to write= 0 fact-check= 0 teach-check= 5 guide=todo
  reliability                        tier 2  chunks to write= 0 fact-check= 0 teach-check= 2 guide=todo
  agent-runtime-architecture         tier 2  chunks to write= 0 fact-check= 0 teach-check= 1 guide=todo
  data-architecture                  tier 2  chunks to write= 0 fact-check= 2 teach-check= 2 guide=todo
  infra-capacity-economics           tier 2  chunks to write= 4 fact-check= 4 teach-check= 4 guide=todo
  llmops-platform                    tier 2  chunks to write= 0 fact-check= 2 teach-check= 2 guide=todo
  security-governance-compliance     tier 2  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
  strategy                           tier 2  chunks to write= 1 fact-check= 1 teach-check= 1 guide=todo
  scoping                            tier 3  chunks to write= 3 fact-check= 3 teach-check= 3 guide=todo
  enterprise-integration             tier 3  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
  customer-data-evaluation           tier 3  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
  deployment                         tier 3  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
  delivery-ownership                 tier 3  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
  adoption                           tier 3  chunks to write= 2 fact-check= 2 teach-check= 2 guide=todo
```

## Resume on your computer, step by step

0. **Make sure no other session is still writing.** Check the branch on GitHub: if commits titled
   `wip: lesson content progress` are still arriving every ~15 minutes, the cloud session is alive;
   wait for it to stop (or stop it) before running locally, otherwise two sets of agents edit the same
   files. Quick check (anything older than ~30 minutes means it has stopped):

       git fetch origin && git log -1 --format='%cr  %s' origin/claude/wizardly-wozniak-5lduqj
1. Get the latest content:

       git fetch origin
       git checkout claude/wizardly-wozniak-5lduqj
       git pull origin claude/wizardly-wozniak-5lduqj

2. Regenerate the workflow from what is on disk. Pick the number of parallel runs you want
   (each run gets `min(16, CPUs - 2)` concurrent agents; on an 8-core Mac, 2–3 runs is plenty):

       python3 scripts/make_workflow.py --groups 3

   It prints what remains and writes `scripts/lms-continue.workflow.js` with **only the remaining
   stages** (already-written chunks skip the writer, fact-checked chunks skip the fact-checker, and so
   on). The script embeds this checkout's absolute path as the project root.
3. Launch it from Claude Code — paste the prompt in the next section. Watch progress with
   `/workflows`. Each run ends by returning counts, low-depth lessons and a sample of corrections.
4. If a run dies or the session is cut off again: `git add content && git commit`, then go back to
   step 2. Regenerating is always safe; nothing already on disk is redone.
5. When every run has finished, validate and build:

       python3 scripts/build.py
       # expect: topics ok=584/584 missing=0 invalid=0 | modules missing=0 invalid=0

   Anything still invalid is listed in `build_report.json`; fix it by hand or with
   `python3 scripts/build.py --check content/<module>/<slug>.md`, then rebuild.
6. Commit everything (`content/`, `build_report.json`, `site/data.js`, `site/data/*.js`,
   `site/index.html`) and push.
7. Preview locally with `node scripts/serve.js 8765 site` and open http://localhost:8765.
8. Republish the private artifact **in place** (same URL): the page is `site/page.html`; supporting
   files are `app.js`, `styles.css`, `data.js` and every `data/<module>.js`, all published at those
   same relative paths. Artifact: https://claude.ai/artifact/F7P3j3rqGSKnXKCCb2vH1b

## Prompt to paste into the local Claude Code session

> Continue the AI Roles Academy lesson build in this repo. Read `CONTINUE.md` first and follow it.
> Pull branch `claude/wizardly-wozniak-5lduqj`, run `python3 scripts/make_workflow.py --groups 3`,
> then run the workflow at `scripts/lms-continue.workflow.js` three times in the same message with
> args `{"group": 0, "groups": 3}`, `{"group": 1, "groups": 3}` and `{"group": 2, "groups": 3}`.
> While they run, commit and push `content/` every 15 minutes. When all three finish, run
> `python3 scripts/build.py`, fix anything it reports until it prints `ok=584/584` and
> `modules missing=0 invalid=0`, commit and push, then republish the AI Roles Academy artifact in
> place as described in CONTINUE.md.

## Gotchas learned so far

- The validator caps the TL;DR at 90 words and floors every other section; the only failures seen
  so far were over-long TL;DRs. `--check` tells you exactly what to fix.
- Agents must create files with the Write tool, not shell heredocs (backticks and `$` get mangled).
- `chunks.json` is referenced by index from the writer prompt (`chunks.json[i]`): do not reorder it.
- Workflow scripts cannot read the filesystem or call `Date.now()`; that is why completeness is baked
  into the generated script and why you regenerate after an interruption instead of editing it.
- Never run two sessions (cloud + local) over the same files at the same time.
- Each 8-topic writer costs roughly 40–50k tokens; a full pass over everything is in the low millions,
  which is what exhausted the session limit. Regenerating only schedules what is left.
