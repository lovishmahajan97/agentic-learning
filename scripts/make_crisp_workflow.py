#!/usr/bin/env python3
"""Generate scripts/lms-crisp.workflow.js: rewrite every lesson into the crisp format (SPEC v2),
verify each rewrite independently, then make each module guide crisp.

Progress lives on disk, so after an interruption you just regenerate and relaunch:
  lesson front matter `status: crisp`           rewritten, passes both checks
  lesson front matter `status: crisp-verified`  also passed the independent verify pass
  _module.md front matter `status: crisp`       guide done

Usage:
  python3 scripts/make_crisp_workflow.py --status
  python3 scripts/make_crisp_workflow.py --groups 3
Then launch one Workflow per group, all in the same message:
  Workflow({scriptPath: "scripts/lms-crisp.workflow.js", args: {"group": 0, "groups": 3}})
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import compare_lesson  # noqa: E402

TAX = json.loads((ROOT / "taxonomy.json").read_text())
CHUNKS = json.loads((ROOT / "chunks.json").read_text())


def fm_status(path):
    m = re.search(r"^status:\s*(\S+)\s*$", path.read_text(encoding="utf-8")[:2000], re.M)
    return m.group(1).lower() if m else ""


def lesson_state(path):
    """'todo' | 'crisp' | 'verified', counting a stamp only if both checks still pass."""
    st = fm_status(path)
    if not st.startswith("crisp"):
        return "todo"
    build.V2_FORCE = True
    try:
        _, errs, _ = build.parse_topic(path)
    finally:
        build.V2_FORCE = False
    if errs:
        return "todo"
    rel = str(path.relative_to(ROOT))
    old = compare_lesson.ref_text("pre-crisp", rel)
    if old is not None and compare_lesson.compare(old, path.read_text(encoding="utf-8")):
        return "todo"
    return "verified" if st == "crisp-verified" else "crisp"


def guide_done(mid):
    p = ROOT / "content" / mid / "_module.md"
    if not p.exists() or not fm_status(p).startswith("crisp"):
        return False
    build.V2_FORCE = True
    try:
        _, errs = build.parse_module(p)
    finally:
        build.V2_FORCE = False
    return not errs


def scan():
    chunks = []
    for i, ch in enumerate(CHUNKS):
        slugs = [t["id"] for t in ch["topics"]]
        st = [lesson_state(ROOT / "content" / ch["module"] / f"{s}.md") for s in slugs]
        chunks.append({"i": i, "module": ch["module"], "index": ch["index"], "slugs": slugs,
                       "rewritten": all(x in ("crisp", "verified") for x in st),
                       "verified": all(x == "verified" for x in st),
                       "n_crisp": sum(x != "todo" for x in st), "n_verified": sum(x == "verified" for x in st)})
    modules = [{"id": m["id"], "title": m["title"], "tier": m["tier"], "n": len(m["topics"]),
                "guide_done": guide_done(m["id"])} for m in TAX["modules"]]
    return modules, chunks


def status(modules, chunks):
    n = sum(len(c["slugs"]) for c in chunks)
    print(f"lessons crisp {sum(c['n_crisp'] for c in chunks)}/{n} | verified {sum(c['n_verified'] for c in chunks)}/{n}"
          f" | guides {sum(m['guide_done'] for m in modules)}/{len(modules)}")
    rw = sum(not c["rewritten"] for c in chunks); vf = sum(not c["verified"] for c in chunks)
    gd = sum(not m["guide_done"] for m in modules)
    print(f"agents still needed: rewrite {rw}, verify {vf}, guides {gd} = {rw + vf + gd}")


SCRIPT = r'''export const meta = {
  name: 'lms-crisp',
  description: 'Rewrite every lesson into the crisp format (bullets, 2+ examples, production patterns) with no fact loss, verify each rewrite, then make each module guide crisp',
  phases: [
    { title: 'Rewrite', detail: 'one agent per chunk of up to 8 lessons; both checkers must pass' },
    { title: 'Verify', detail: 'independent reviewer per chunk: accuracy, readability, nothing lost' },
    { title: 'Guides', detail: 'one agent per module guide' },
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

const REWRITE = { type: 'object', properties: {
  files: { type: 'array', items: { type: 'string' } },
  uncertain: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, claim: { type: 'string' } }, required: ['file', 'claim'] } },
  notes: { type: 'string' } }, required: ['files', 'uncertain', 'notes'] }
const VERIFY = { type: 'object', properties: {
  corrections: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' } }, required: ['file', 'issue', 'fix'] } },
  readability: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, score: { type: 'integer' } }, required: ['file', 'score'] } } },
  required: ['corrections', 'readability'] }
const GUIDE = { type: 'object', properties: { file: { type: 'string' }, notes: { type: 'string' } }, required: ['file', 'notes'] }

const COMMON = `Project root: ${ROOT} (your working directory); all paths are relative to it. Never run git commands that change anything (no add, commit, checkout, stash, reset); read-only "git show pre-crisp:<path>" is allowed and is how you see a lesson's original text. Create and edit files with the Write and Edit tools, never shell heredocs. If you run any code, do it in a fresh directory under /tmp and delete it afterwards; never leave files in the project.`

function rewritePrompt(c, m) {
  return `You are turning paragraph-heavy lessons of an LMS (the "AI Roles Skills Taxonomy" course for AI Engineers, AI Architects and Forward Deployed Engineers) into a crisp, scannable format. ${COMMON}

FIRST read, in full:
1. content/SPEC.md — the crisp format (v2), the readability rules and the no-loss rule.
2. The exemplar BEFORE and AFTER, to see exactly the transformation wanted:
   git show pre-crisp:content/model-internals/kv-cache.md      (before)
   content/model-internals/kv-cache.md                          (after)
   Note what changed: walls of text became bold-led bullets, numbered steps and tables; every number, code block and caveat survived; a second example (calculator + worked incident table) and a Production patterns section were added; Role lens, Check yourself and Quiz were copied unchanged.

YOUR FILES (module "${m.title}", id ${m.id}, chunk ${c.index}). Rewrite exactly these, in place; other agents own other files:
${filesOf(c).map(f => '  ' + f).join('\n')}
Skip a file only if it already has "status: crisp" or "status: crisp-verified" AND both checks below pass.

For each file:
1. Read the whole current lesson.
2. Rewrite it to SPEC v2 with the Write tool: TL;DR of 10-45 words; Why it exists as 3-5 bold-led bullets; How it works as ### subsections, numbered steps, bullets and tables (no paragraph over 60 words, no bullet over 80); Examples with at least 2 "### titled" examples, at least one with code, ideally one runnable code example plus one worked numeric scenario or production snippet; a NEW "## Production patterns" section with 3-6 bullets, each "**Pattern name** — when, how (concrete setting, flag, call or snippet), what to monitor"; Trade-offs, Pitfalls and When to use as bold-led bullets.
3. Keep EVERY fact: every number, every inline code span, every original code block (verbatim), every caveat and every "because". Copy front matter unchanged except set "status: crisp". Copy "## Role lens", "## Check yourself" and "## Quiz" exactly as they are.
4. New examples and patterns must be correct and real: no invented APIs, config flags, numbers or citations. If unsure a flag exists, describe the setting in words instead. Check new code by reading it line by line; run small pure-Python snippets in /tmp if useful.
5. Run both checks and fix until both print ✓:
     python3 scripts/build.py --check --v2 content/${c.module}/<slug>.md
     python3 scripts/compare_lesson.py content/${c.module}/<slug>.md
Finish by running both checks on all your files together. Return the files, any claim you added but are not certain of, and notes.`
}

function verifyPrompt(c) {
  return `You are an independent, skeptical reviewer of lessons that were just rewritten from long prose into a crisp format. A learner will study from these. ${COMMON} Read content/SPEC.md first.

Files (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If a file is not yet crisp (no "status: crisp"), rewrite it yourself per SPEC v2 first.

For each file compare the current version with the original ("git show pre-crisp:<path>") and fix in place:
- Lost meaning: a caveat, condition, "because", or qualifier dropped in the compression; a claim made stronger or vaguer than the original. Restore it.
- Accuracy of everything NEW: added code examples (trace them; would they run, and do they show what the text claims?), worked numbers (recompute them), production patterns (do the named settings, flags and APIs exist and do what is claimed?). Fix or remove anything wrong or unverifiable.
- Readability: can a learner scan it? Bold lead-ins state the point, bullets are short, tables are used for comparisons and numbers, no section is a wall of text, and nothing is choppy to the point of losing the argument.
- Production patterns must be genuinely useful practice for this topic, not generic advice.
When a file is right, set its front matter to "status: crisp-verified". Both checks must pass after your edits:
  python3 scripts/build.py --check --v2 <file>
  python3 scripts/compare_lesson.py <file>
Return every substantive correction (file, issue, fix) and a readability score 1-5 per file after your edits.`
}

function guidePrompt(m) {
  return `Make the module guide content/${m.id}/_module.md (module "${m.title}", Tier ${m.tier}) crisp and scannable. ${COMMON}
Read the "Module file" section of content/SPEC.md. Then rewrite the guide in place with the Write tool:
- Overview: short paragraphs (max 60 words each) plus bullets: what the module covers, how topics connect, what you can do afterwards, which roles need it most. Keep at least 150 words in total.
- Mental model: bullets, a small table, or a short numbered flow; no paragraph over 60 words.
- Capstone brief: split into short paragraphs and bullets (keep at least 120 words); keep Deliverables and Rubric.
- Keep "Why it matters" and "Suggested order" content as they are (every slug exactly once, with its reason).
- Keep every fact; restructure rather than delete. Set front matter "status: crisp" (add the line after id/title).
Validate with python3 scripts/build.py --check --v2 content/${m.id}/_module.md until ✓. Return the file path and notes.`
}

const results = await pipeline(MODULES,
  m => pipeline(byModule[m.id] || [],
    c => c.rewritten
      ? Promise.resolve({ files: filesOf(c), uncertain: [], notes: 'already crisp' })
      : agent(rewritePrompt(c, m), { label: `rewrite:${c.module}#${c.index}`, phase: 'Rewrite', schema: REWRITE }),
    (w, c) => c.verified
      ? Promise.resolve({ rewrite: w, verify: { corrections: [], readability: [], skipped: true } })
      : agent(verifyPrompt(c), { label: `verify:${c.module}#${c.index}`, phase: 'Verify', schema: VERIFY, effort: 'high' })
          .then(v => ({ rewrite: w, verify: v })),
  ),
  (chunkResults, m) => (m.guide_done ? Promise.resolve({ file: `content/${m.id}/_module.md`, notes: 'already crisp' })
      : agent(guidePrompt(m), { label: `guide:${m.id}`, phase: 'Guides', schema: GUIDE }))
    .then(g => { log(`✓ ${m.id}`); return { module: m.id, chunks: chunkResults, guide: g } })
)

const out = results.filter(Boolean)
const chunksAll = out.flatMap(r => (r.chunks || []).filter(Boolean))
const corrections = chunksAll.flatMap(ch => (ch.verify && ch.verify.corrections) || [])
const scores = chunksAll.flatMap(ch => (ch.verify && ch.verify.readability) || [])
return {
  group: GI + 1, of: G, modules: MODULES.map(m => m.id), modules_done: out.length,
  corrections_count: corrections.length,
  uncertain_claims: chunksAll.flatMap(ch => (ch.rewrite && ch.rewrite.uncertain) || []).length,
  avg_readability: scores.length ? Math.round(scores.reduce((a, s) => a + s.score, 0) / scores.length * 100) / 100 : null,
  low_readability: scores.filter(s => s.score <= 3),
  corrections_sample: corrections.slice(0, 25),
}
'''


def main(argv):
    groups = 1
    if "--groups" in argv:
        groups = int(argv[argv.index("--groups") + 1])
    modules, chunks = scan()
    status(modules, chunks)
    if "--status" in argv:
        return
    data = {"root": str(ROOT), "modules": modules, "chunks": chunks}
    out = ROOT / "scripts" / "lms-crisp.workflow.js"
    out.write_text(SCRIPT.replace("__DATA__", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}; launch {groups} group(s):")
    for g in range(groups):
        print(f'  Workflow({{scriptPath: "scripts/lms-crisp.workflow.js", args: {{"group": {g}, "groups": {groups}}}}})')


if __name__ == "__main__":
    main(sys.argv[1:])
