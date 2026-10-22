#!/usr/bin/env python3
"""Generate scripts/lms-mastery.workflow.js: the SPEC v4 (mastery) pass.

Scope (from the learner's improvement plan):
  B. upgrade high-value modules in place: exercise, mastery test, failure diagnosis, prerequisites,
     executed code, no growth in length;
  C. write the new fundamentals modules;
  E. write the five cumulative projects (with milestones placed after the modules that unlock them).

Progress lives on disk (front matter status: mastery / mastery-verified), so regenerate after any stop:
  python3 scripts/make_mastery_workflow.py --status
  python3 scripts/make_mastery_workflow.py --groups 3
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import compare_lesson  # noqa: E402

UPGRADE = ["agent-reliability", "rag-retrieval", "evals", "agents", "safety-security", "deployment", "slm-for-agents"]
NEW = ["engineering-fundamentals", "math-for-ai", "cumulative-projects"]
TAX = json.loads((ROOT / "taxonomy.json").read_text())
CHUNKS = json.loads((ROOT / "chunks.json").read_text())


def fm_status(path):
    m = re.search(r"^status:\s*(\S+)\s*$", path.read_text(encoding="utf-8")[:2000], re.M)
    return m.group(1).lower() if m else ""


def lesson_state(path):
    if not path.exists():
        return "missing"
    st = fm_status(path)
    if not st.startswith("mastery"):
        return "todo"
    build.V2_FORCE = build.V3_FORCE = build.V4_FORCE = True
    try:
        _, errs, _ = build.parse_topic(path)
    finally:
        build.V2_FORCE = build.V3_FORCE = build.V4_FORCE = False
    if errs:
        return "todo"
    old = compare_lesson.ref_text("pre-mastery", str(path.relative_to(ROOT)))
    if old is not None and compare_lesson.compare(old, path.read_text(encoding="utf-8")):
        return "todo"
    return "verified" if st == "mastery-verified" else "done"


def guide_done(mid):
    p = ROOT / "content" / mid / "_module.md"
    if not p.exists():
        return False
    _, errs = build.parse_module(p)
    return not errs


def scan():
    scope = set(UPGRADE + NEW)
    chunks = []
    for i, ch in enumerate(CHUNKS):
        if ch["module"] not in scope:
            continue
        slugs = [t["id"] for t in ch["topics"]]
        st = [lesson_state(ROOT / "content" / ch["module"] / f"{s}.md") for s in slugs]
        chunks.append({"i": i, "module": ch["module"], "index": ch["index"], "slugs": slugs,
                       "missing": any(x == "missing" for x in st),
                       "rewritten": all(x in ("done", "verified") for x in st),
                       "verified": all(x == "verified" for x in st),
                       "n_done": sum(x in ("done", "verified") for x in st), "n_verified": sum(x == "verified" for x in st)})
    modules = [{"id": m["id"], "title": m["title"], "tier": m["tier"], "n": len(m["topics"]),
                "guide_done": guide_done(m["id"]) if m["id"] in NEW else True}
               for m in TAX["modules"] if m["id"] in scope]
    return modules, chunks


def status(modules, chunks):
    n = sum(len(c["slugs"]) for c in chunks)
    print(f"mastery lessons {sum(c['n_done'] for c in chunks)}/{n} | verified {sum(c['n_verified'] for c in chunks)}/{n}"
          f" | new guides {sum(m['guide_done'] for m in modules if m['id'] in NEW)}/{len(NEW)}")
    rw = sum(not c["rewritten"] for c in chunks); vf = sum(not c["verified"] for c in chunks)
    gd = sum(not m["guide_done"] for m in modules)
    print(f"agents still needed: upgrade/write {rw}, verify {vf}, guides {gd} = {rw + vf + gd}")


SCRIPT = r'''export const meta = {
  name: 'lms-mastery',
  description: 'Mastery pass: upgrade high-value lessons (exercise, mastery test, failure diagnosis, executed code, no growth), write fundamentals and cumulative projects, verify each',
  phases: [
    { title: 'Write', detail: 'upgrade or write each chunk to SPEC v4' },
    { title: 'Verify', detail: 'independent reviewer: run the code, test the exercise and mastery test, check claims' },
    { title: 'Guides', detail: 'guides for the new modules' },
  ],
}

const DATA = __DATA__
const ROOT = (args && args.root) || DATA.root
const G = (args && args.groups) || 1
const GI = (args && args.group) || 0
const byModule = {}
for (const c of DATA.chunks) (byModule[c.module] ||= []).push(c)
const TODO = DATA.modules.filter(m => !m.guide_done || (byModule[m.id] || []).some(c => !c.verified))
const cost = m => (byModule[m.id] || []).reduce((a, c) => a + (c.rewritten ? 0 : 2) + (c.verified ? 0 : 1), 0) + (m.guide_done ? 0 : 1)
const groups = Array.from({ length: G }, () => []), load = Array(G).fill(0)
for (const m of [...TODO].sort((a, b) => cost(b) - cost(a))) { let g = 0; for (let k = 1; k < G; k++) if (load[k] < load[g]) g = k; groups[g].push(m); load[g] += cost(m) }
const MODULES = (args && args.only) ? TODO.filter(m => args.only.includes(m.id)) : groups[GI]
const filesOf = c => c.slugs.map(s => `content/${c.module}/${s}.md`)

const WRITE = { type: 'object', properties: { files: { type: 'array', items: { type: 'string' } },
  uncertain: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, claim: { type: 'string' } }, required: ['file', 'claim'] } },
  notes: { type: 'string' } }, required: ['files', 'uncertain', 'notes'] }
const VERIFY = { type: 'object', properties: {
  corrections: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' } }, required: ['file', 'issue', 'fix'] } },
  code: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, tested: { type: 'string' }, how: { type: 'string' } }, required: ['file', 'tested', 'how'] } },
  readability: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, score: { type: 'integer' } }, required: ['file', 'score'] } } },
  required: ['corrections', 'code', 'readability'] }
const GUIDE = { type: 'object', properties: { file: { type: 'string' }, notes: { type: 'string' } }, required: ['file', 'notes'] }

const COMMON = `Project root: ${ROOT} (your working directory); all paths are relative to it. Never run git commands that change anything; read-only "git show pre-mastery:<path>" shows a lesson's version before this pass. Create and edit files with the Write and Edit tools, never shell heredocs. Run code only in a fresh directory under /tmp (a temporary venv is fine) and delete it afterwards; never leave files in the project.`

const CHECKS = (dir) => `Run both checks and fix until both print ✓:
     python3 scripts/build.py --check --v4 content/${dir}/<slug>.md
     python3 scripts/compare_lesson.py content/${dir}/<slug>.md`

const BRIEF = {
  'engineering-fundamentals': 'Fundamentals an AI engineer must actually master: HTTP, a debugging method, testing, git and code review, reading errors and logs, the command line. Teach from first principles with AI-system examples (an LLM API call over HTTP, debugging a failing agent tool, testing a retriever, reviewing AI-generated code). Exercises must be hands-on in a terminal.',
  'math-for-ai': 'Just enough math to reason about models and evaluations, always tied to a decision an AI engineer makes: probability (sampling, temperature, calibration), statistics for evaluation (is a 2-point eval gain real? sample sizes, confidence intervals, paired comparisons), linear algebra (embeddings, dot products, cosine), calculus (gradients and learning rates), information theory (cross-entropy loss, perplexity, KL in RLHF and distillation), numerical precision (fp16/bf16 overflow, quantization). Use small numeric examples computed in Python (numpy optional) and check every number.',
  'cumulative-projects': 'Each lesson is one CUMULATIVE PROJECT that ties several modules together, built over the course and finished at the end. Project 1 reliable RAG application (ingestion, chunking, retrieval, citations, evaluation of retrieval quality and answer correctness, simple UI). Project 2 tool-using agent (limited tools, input validation, permissions, timeouts, retries, execution limits, logging, human approval for risky actions). Project 3 production AI service (auth, persistent storage, observability, automated tests, error handling, cost tracking, deployment). Project 4 SLM feasibility study (narrow real task, baseline, compare models, evaluate quality and latency, estimate operating cost, document whether an SLM is viable). Project 5 end-to-end business solution (real operational problem, success metrics, design, implement, test failure scenarios, deploy, evaluate business outcome). Map the lesson sections as: How it works = milestones with acceptance criteria and the lessons each needs; Examples = a starter skeleton plus an evaluation table; Exercise = the first milestone done independently; Mastery test = a variation that changes a constraint. Put front matter milestones as entries "<module-id>: <milestone title>" naming the module after which each milestone becomes possible (use real module ids from taxonomy.json, 4-7 milestones). Set mastery: evaluate.',
}

function upgradePrompt(c, m) {
  return `You are upgrading lessons of an LMS for a learner preparing to build AI agents, following their own improvement plan: replace passive reading with active learning, teach failure analysis, and do NOT make lessons longer. ${COMMON}

FIRST read content/SPEC.md (v4 at the top, then v3, v2, readability and mermaid rules). Then study the v4 exemplar: compare "git show pre-mastery:content/model-internals/kv-cache.md" with the current content/model-internals/kv-cache.md. Note: front matter gained mastery, code_tested and prerequisites; pitfalls became failure modes with a Diagnose step; an Exercise with no solution and acceptance checks; a Mastery test with a new problem and a strong-answer rubric; and repeated material was cut so the lesson did not grow.

YOUR FILES (module "${m.title}", id ${m.id}, chunk ${c.index}); other agents own other files:
${filesOf(c).map(f => '  ' + f).join('\n')}
Skip a file only if it already has "status: mastery" or "status: mastery-verified" and both checks pass.

For each file:
1. Set front matter "status: mastery", add "mastery:" (the level this lesson should reach: understand, implement or evaluate; engineering lessons in this module are usually implement or evaluate), "prerequisites:" (0-5 real prerequisite lesson keys from taxonomy.json, only genuine gaps), and "code_tested:".
2. Rewrite Pitfalls as failure modes: each bullet gives symptom, cause, how to Diagnose it (what to log, measure or inspect) and the fix.
3. Add "## Exercise" after When to use and "## Mastery test" after the Quiz, per SPEC v4.
4. Make the strongest example runnable: execute every self-contained code example in a temp venv under /tmp (install only what it needs). Fix code that fails. Set code_tested to yes if all self-contained examples ran, partial if some need GPUs, paid APIs or services you could not run, no otherwise. Where a tool's API is version-sensitive, note the version you checked against.
5. Keep total prose within 5% of the original: cut repetition and padding, never facts, numbers, caveats or the reasoning behind a point. Keep Role lens, Check yourself and Quiz unchanged.
6. ${CHECKS(c.module)}
Return the files, any claim you are not certain of, and notes (include what code you ran).`
}

function writeNewPrompt(c, m) {
  return `You are writing NEW lessons for an LMS whose learner is preparing to build AI agents and wants to master fundamentals, not skim them. Module "${m.title}" (id ${m.id}). ${COMMON}
MODULE BRIEF: ${BRIEF[m.id] || ''}

FIRST read content/SPEC.md fully (v4 at the top, then v3 and v2 with the readability and mermaid rules) and the exemplar content/model-internals/kv-cache.md. Print your assignment:
   python3 -c "import json;print(json.dumps(json.load(open('chunks.json'))[${c.i}],indent=1))"
For related and prerequisites keys grep taxonomy.json (never print it whole).

Write exactly these files (create them; other agents own other files):
${filesOf(c).map(f => '  ' + f).join('\n')}

Each file follows SPEC v4 completely: front matter (id, module, name, kind, difficulty, est_minutes, agent_relevance, status: mastery, mastery, code_tested, prerequisites, related, sources, and milestones for project lessons), then every section in order: TL;DR, For agent builders, Why it exists, How it works, Diagram, Examples, Production patterns, Trade-offs, Pitfalls (failure modes with Diagnose), When to use, Exercise, Role lens, Check yourself, Interview questions, Quiz (exactly 3 questions), Mastery test, Use cases & projects. Be concise: aim for 2,000-3,000 words of prose. One strong runnable example beats several weak ones; execute it in a temp venv under /tmp and set code_tested accordingly.
${CHECKS(c.module)}
Return the files, any claim you are not certain of, and notes.`
}

function verifyPrompt(c) {
  return `You are an independent, skeptical reviewer for an LMS lesson pass whose goal is real competence: can the learner explain, implement and evaluate this topic without help? ${COMMON} Read content/SPEC.md (v4 at the top).

Files (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If a file lacks "status: mastery", bring it to SPEC v4 yourself first.

For each file (compare with "git show pre-mastery:<path>" when it exists):
- RUN the code: execute every self-contained example in a temp venv under /tmp; fix what fails; make code_tested honest.
- Exercise: is it solvable from the lesson plus prerequisites, free of a worked solution, and are the acceptance checks something the learner can actually run?
- Mastery test: is it genuinely new (not the Check yourself or an interview question restated), and does the strong-answer rubric match what an expert would say? Recompute any numbers.
- Failure modes: are the diagnosis steps concrete and correct (real metrics, logs, commands)?
- Prerequisites: real gaps only, valid keys; mastery level honest.
- Nothing lost and no growth: compare_lesson must pass; restore any dropped caveat or reasoning.
- Accuracy of all new claims; mark version-sensitive details with versions.
- For project lessons: milestones point at the right modules and the acceptance criteria are measurable.
When a file is right, set "status: mastery-verified". Both checks must pass:
  python3 scripts/build.py --check --v4 <file>
  python3 scripts/compare_lesson.py <file>
Return every substantive correction, what code you ran per file (tested: yes, partial or no, and how), and a readability score 1-5 per file.`
}

function guidePrompt(m) {
  return `Write the module guide content/${m.id}/_module.md for the new module "${m.title}" (id ${m.id}). ${COMMON}
Read the "Module file" section of content/SPEC.md and the TL;DR, For agent builders and Exercise sections of every lesson in content/${m.id}/. Overview (150+ words, short paragraphs and bullets: what this module lets you do, how it connects to the rest of the course); Why it matters (Engineer / Architect / FDE bullets); Mental model (bullets plus ONE mermaid flowchart following the SPEC mermaid rules); Suggested order (every slug exactly once with its reason); Capstone (the module's major project: brief of 120+ words in short paragraphs, 3+ deliverables, 3+ measurable rubric items). Front matter: id, title, status: agentic.
Validate with python3 scripts/build.py --check --v2 content/${m.id}/_module.md until ✓. Return the file path and notes.`
}

const results = await pipeline(MODULES,
  m => pipeline(byModule[m.id] || [],
    c => c.rewritten
      ? Promise.resolve({ files: filesOf(c), uncertain: [], notes: 'already done' })
      : agent(c.missing ? writeNewPrompt(c, m) : upgradePrompt(c, m), { label: `${c.missing ? 'write' : 'upgrade'}:${c.module}#${c.index}`, phase: 'Write', schema: WRITE }),
    (w, c) => c.verified
      ? Promise.resolve({ write: w, verify: { corrections: [], code: [], readability: [], skipped: true } })
      : agent(verifyPrompt(c), { label: `verify:${c.module}#${c.index}`, phase: 'Verify', schema: VERIFY, effort: 'high' }).then(v => ({ write: w, verify: v })),
  ),
  (chunkResults, m) => (m.guide_done ? Promise.resolve({ file: `content/${m.id}/_module.md`, notes: 'exists' })
      : agent(guidePrompt(m), { label: `guide:${m.id}`, phase: 'Guides', schema: GUIDE }))
    .then(g => { log(`✓ ${m.id}`); return { module: m.id, chunks: chunkResults, guide: g } })
)

const out = results.filter(Boolean)
const all = out.flatMap(r => (r.chunks || []).filter(Boolean))
const corrections = all.flatMap(ch => (ch.verify && ch.verify.corrections) || [])
const code = all.flatMap(ch => (ch.verify && ch.verify.code) || [])
const scores = all.flatMap(ch => (ch.verify && ch.verify.readability) || [])
return {
  group: GI + 1, of: G, modules: MODULES.map(m => m.id), modules_done: out.length,
  corrections_count: corrections.length,
  code_tested: { yes: code.filter(x => /^yes/i.test(x.tested)).length, partial: code.filter(x => /^partial/i.test(x.tested)).length, no: code.filter(x => /^no/i.test(x.tested)).length },
  avg_readability: scores.length ? Math.round(scores.reduce((a, s) => a + s.score, 0) / scores.length * 100) / 100 : null,
  low_readability: scores.filter(s => s.score <= 3),
  corrections_sample: corrections.slice(0, 20),
}
'''


def main(argv):
    groups = int(argv[argv.index("--groups") + 1]) if "--groups" in argv else 1
    modules, chunks = scan()
    status(modules, chunks)
    if "--status" in argv:
        return
    data = {"root": str(ROOT), "modules": modules, "chunks": chunks}
    out = ROOT / "scripts" / "lms-mastery.workflow.js"
    out.write_text(SCRIPT.replace("__DATA__", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}; launch {groups} group(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
