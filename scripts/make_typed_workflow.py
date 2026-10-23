#!/usr/bin/env python3
"""Generate scripts/lms-typed.workflow.js: the SPEC v5 pass (three lesson types) over every lesson.

Each lesson gets lesson_type (concept | lab | decision) and its type section (Misconceptions, Lab or
Decision case); everything else stays as the full reference. Progress lives on disk (status: typed /
typed-verified), so regenerate after any stop:
  python3 scripts/make_typed_workflow.py --status
  python3 scripts/make_typed_workflow.py --groups 3
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import compare_lesson  # noqa: E402

TAX = json.loads((ROOT / "taxonomy.json").read_text())
CHUNKS = json.loads((ROOT / "chunks.json").read_text())

DECISION_MODULES = {"system-design", "reliability", "agent-runtime-architecture", "data-architecture",
                    "infra-capacity-economics", "llmops-platform", "security-governance-compliance", "strategy",
                    "scoping", "enterprise-integration", "customer-data-evaluation", "deployment",
                    "delivery-ownership", "adoption", "practice-use-cases", "portfolio-projects", "cumulative-projects"}
LAB_MODULES = {"python-for-ai", "engineering-fundamentals", "backend-fundamentals", "fastapi-for-ai", "node-ts-for-ai",
               "agent-frameworks", "mcp", "mini-project-ideas", "observability", "agent-reliability"}


def suggest(module, path):
    kind = ""
    if path.exists():
        m = re.search(r"^kind:\s*(\S+)", path.read_text(encoding="utf-8")[:2000], re.M)
        kind = m.group(1) if m else ""
    if kind == "decision" or module in DECISION_MODULES:
        return "decision"
    if kind == "tool" or module in LAB_MODULES:
        return "lab"
    return "concept"


def state(path):
    txt = path.read_text(encoding="utf-8")[:2000]
    m = re.search(r"^status:\s*(\S+)\s*$", txt, re.M)
    st = m.group(1).lower() if m else ""
    if not st.startswith("typed"):
        return "todo"
    build.V5_FORCE = True
    try:
        _, errs, _ = build.parse_topic(path)
    finally:
        build.V5_FORCE = False
    if errs:
        return "todo"
    old = compare_lesson.ref_text("pre-typed", str(path.relative_to(ROOT)))
    if old is not None and compare_lesson.compare(old, path.read_text(encoding="utf-8")):
        return "todo"
    return "verified" if st == "typed-verified" else "done"


def scan():
    chunks = []
    for i, ch in enumerate(CHUNKS):
        slugs = [t["id"] for t in ch["topics"]]
        paths = [ROOT / "content" / ch["module"] / f"{s}.md" for s in slugs]
        st = [state(p) for p in paths]
        chunks.append({"i": i, "module": ch["module"], "index": ch["index"], "slugs": slugs,
                       "suggest": [suggest(ch["module"], p) for p in paths],
                       "rewritten": all(x in ("done", "verified") for x in st), "verified": all(x == "verified" for x in st),
                       "n_done": sum(x in ("done", "verified") for x in st), "n_verified": sum(x == "verified" for x in st)})
    modules = [{"id": m["id"], "title": m["title"], "tier": m["tier"], "n": len(m["topics"]), "guide_done": True} for m in TAX["modules"]]
    return modules, chunks


SCRIPT = r'''export const meta = {
  name: 'lms-typed',
  description: 'Give every lesson a type (concept, lab or decision) and its type section (Misconceptions, Lab, Decision case) with no fact loss, then verify each by running labs and recomputing decision cases',
  phases: [
    { title: 'Write', detail: 'one agent per chunk adds the type section' },
    { title: 'Verify', detail: 'independent reviewer: run lab starter and tests, check decision cases' },
  ],
}

const DATA = __DATA__
const ROOT = (args && args.root) || DATA.root
const G = (args && args.groups) || 1
const GI = (args && args.group) || 0
const byModule = {}
for (const c of DATA.chunks) (byModule[c.module] ||= []).push(c)
const TODO = DATA.modules.filter(m => (byModule[m.id] || []).some(c => !c.verified))
const cost = m => (byModule[m.id] || []).reduce((a, c) => a + (c.rewritten ? 0 : 2) + (c.verified ? 0 : 1), 0)
const groups = Array.from({ length: G }, () => []), load = Array(G).fill(0)
for (const m of [...TODO].sort((a, b) => cost(b) - cost(a))) { let g = 0; for (let k = 1; k < G; k++) if (load[k] < load[g]) g = k; groups[g].push(m); load[g] += cost(m) }
const MODULES = (args && args.only) ? TODO.filter(m => args.only.includes(m.id)) : groups[GI]
const filesOf = c => c.slugs.map(s => `content/${c.module}/${s}.md`)
const listing = c => c.slugs.map((s, k) => `  content/${c.module}/${s}.md   (suggested type: ${c.suggest[k]})`).join('\n')

const WRITE = { type: 'object', properties: { files: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, type: { type: 'string' } }, required: ['file', 'type'] } },
  uncertain: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, claim: { type: 'string' } }, required: ['file', 'claim'] } },
  notes: { type: 'string' } }, required: ['files', 'uncertain', 'notes'] }
const VERIFY = { type: 'object', properties: {
  corrections: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' } }, required: ['file', 'issue', 'fix'] } },
  labs: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, starter_runs: { type: 'boolean' }, tests_fail_on_starter: { type: 'boolean' }, tests_pass_on_solution: { type: 'boolean' } }, required: ['file', 'starter_runs', 'tests_fail_on_starter', 'tests_pass_on_solution'] } },
  types: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, type: { type: 'string' } }, required: ['file', 'type'] } } },
  required: ['corrections', 'labs', 'types'] }

const COMMON = `Project root: ${ROOT} (your working directory); all paths are relative to it. Never run git commands that change anything; read-only "git show pre-typed:<path>" shows a lesson before this pass. Create and edit files with the Write and Edit tools, never shell heredocs. Run code only in a fresh directory under /tmp (a temporary venv is fine; for TypeScript a temp npm project) and delete it afterwards; never leave files in the project.`

function writePrompt(c, m) {
  return `You are adding a lesson-type section to lessons of an LMS for a learner preparing to build AI agents. The learner asked for three lesson types instead of one structure: concept lessons (definitions, fundamentals, mental models), engineering labs (tools, implementation, debugging) and architecture and decision lessons (system design, reliability, security, delivery). ${COMMON}

FIRST read content/SPEC.md: the v5 section at the top in full (type choice and the exact skeleton for each type), then skim v4, v3 and the mermaid rules. The concept exemplar is content/model-internals/kv-cache.md (see its "## Misconceptions").

YOUR FILES (module "${m.title}", id ${m.id}, chunk ${c.index}); other agents own other files:
${listing(c)}
Skip a file only if it already has "status: typed" or "status: typed-verified" and both checks pass.

For each file:
1. Read the whole lesson. Choose lesson_type: take the suggestion unless the topic clearly belongs to another type (a pure concept inside a lab module, a design decision inside a concept module); note any override in your notes.
2. Add "lesson_type: ..." to front matter and set "status: typed". Keep every other field.
3. Add exactly one type section in the place SPEC v5 says: "## Misconceptions" (concept), "## Lab" (lab) or "## Decision case" (decision), following its skeleton exactly. Build on the lesson's own content and the agent-builder framing; do not repeat whole sections, and do not delete or shorten anything else.
4. Lab: the starter code must run, the tests must fail on the starter for the right reason, and a correct solution (write it privately in /tmp, then delete it) must pass them and print the expected output. Use only stable APIs; note the version you ran against. If the topic needs a GPU, paid API or external service, design the lab around a local fake or a recorded response so it still runs.
5. Decision: realistic scenario with explicit constraints, 2-4 options in a table, a justified decision tied to the constraints and what would change it, accepted trade-offs, and a 4-6 item observable rubric. Recompute every number.
6. Concept: misconceptions a smart engineer actually holds, each corrected precisely. Make sure the first example under "## Examples" is the single best worked example (reorder the ### examples if needed, without losing any).
7. Run both checks and fix until both print ✓:
     python3 scripts/build.py --check --v5 content/${c.module}/<slug>.md
     python3 scripts/compare_lesson.py content/${c.module}/<slug>.md
Return each file with its chosen type, any claim you are not certain of, and notes (what you ran).`
}

function verifyPrompt(c) {
  return `You are an independent, skeptical reviewer of new lesson-type sections in an LMS for future agent builders. ${COMMON} Read the v5 section at the top of content/SPEC.md first.

Files (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If a file lacks "status: typed", add its type section yourself per SPEC v5 first.

For each file:
- Type: is lesson_type right for this topic? Change it (and the section) if clearly wrong.
- Lab: copy the starter and tests to a temp dir under /tmp and RUN them. The starter must run; the tests must fail on it for the right reason; write your own solution and confirm the tests pass and the output matches Expected output. Fix the lab until all three hold. Check failure scenarios actually produce the stated symptoms, and that the submission can be evaluated. No solution in the lesson.
- Decision: the scenario's constraints are explicit and the numbers recompute; the options are genuinely competitive (not a straw man); the decision follows from the constraints; trade-offs are honest; the rubric is observable. No invented prices or SLAs stated as fact.
- Concept: the misconceptions are real and the corrections exact; the first example is the best worked example.
- Nothing else changed or lost: compare_lesson must pass.
When a file is right, set "status: typed-verified". Both checks must pass:
  python3 scripts/build.py --check --v5 <file>
  python3 scripts/compare_lesson.py <file>
Return every substantive correction, the lab results per lab file, and the final type per file.`
}

const results = await pipeline(MODULES,
  m => pipeline(byModule[m.id] || [],
    c => c.rewritten
      ? Promise.resolve({ files: [], uncertain: [], notes: 'already typed' })
      : agent(writePrompt(c, m), { label: `type:${c.module}#${c.index}`, phase: 'Write', schema: WRITE }),
    (w, c) => c.verified
      ? Promise.resolve({ write: w, verify: { corrections: [], labs: [], types: [], skipped: true } })
      : agent(verifyPrompt(c), { label: `verify:${c.module}#${c.index}`, phase: 'Verify', schema: VERIFY, effort: 'high' }).then(v => ({ write: w, verify: v })),
  ),
  (chunkResults, m) => { log(`✓ ${m.id}`); return { module: m.id, chunks: chunkResults } }
)

const all = results.filter(Boolean).flatMap(r => (r.chunks || []).filter(Boolean))
const corrections = all.flatMap(ch => (ch.verify && ch.verify.corrections) || [])
const labs = all.flatMap(ch => (ch.verify && ch.verify.labs) || [])
const types = all.flatMap(ch => (ch.verify && ch.verify.types) || [])
const count = k => types.filter(x => (x.type || '').toLowerCase().startsWith(k)).length
return {
  group: GI + 1, of: G, modules: MODULES.map(m => m.id),
  corrections_count: corrections.length,
  types: { concept: count('concept'), lab: count('lab'), decision: count('decision') },
  labs_all_good: labs.filter(l => l.starter_runs && l.tests_fail_on_starter && l.tests_pass_on_solution).length,
  labs_with_issues: labs.filter(l => !(l.starter_runs && l.tests_fail_on_starter && l.tests_pass_on_solution)).map(l => l.file),
  corrections_sample: corrections.slice(0, 20),
}
'''


def main(argv):
    groups = int(argv[argv.index("--groups") + 1]) if "--groups" in argv else 1
    modules, chunks = scan()
    n = sum(len(c["slugs"]) for c in chunks)
    print(f"typed lessons {sum(c['n_done'] for c in chunks)}/{n} | verified {sum(c['n_verified'] for c in chunks)}/{n}")
    rw = sum(not c["rewritten"] for c in chunks); vf = sum(not c["verified"] for c in chunks)
    print(f"agents still needed: write {rw}, verify {vf} = {rw + vf}")
    sug = [x for c in chunks for x in c["suggest"]]
    print("suggested types:", {k: sug.count(k) for k in ("concept", "lab", "decision")})
    if "--status" in argv:
        return
    data = {"root": str(ROOT), "modules": modules, "chunks": chunks}
    out = ROOT / "scripts" / "lms-typed.workflow.js"
    out.write_text(SCRIPT.replace("__DATA__", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}; launch {groups} group(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
