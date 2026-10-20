#!/usr/bin/env python3
"""Generate scripts/lms-continue.workflow.js from the current state of content/.

Scans content/ and marks, per chunk of chunks.json, whether its lessons are written,
fact-checked and pedagogy-reviewed (front-matter `status:` line), and per module whether a valid
guide (_module.md) exists. The emitted workflow skips every stage that is already done, so after an
interruption you just regenerate and relaunch: only the remaining work is scheduled.

Usage:
  python3 scripts/make_workflow.py                 # write scripts/lms-continue.workflow.js
  python3 scripts/make_workflow.py --groups 3      # balance the remaining work over 3 parallel runs
  python3 scripts/make_workflow.py --status        # print what remains, write nothing
  python3 scripts/make_workflow.py --root /abs/path --out other.js

Then, in Claude Code (one call per group, all at once):
  Workflow({scriptPath: "scripts/lms-continue.workflow.js", args: {"group": 0, "groups": 3}})
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402  (validator; reads taxonomy.json)

TAX = json.loads((ROOT / "taxonomy.json").read_text())
CHUNKS = json.loads((ROOT / "chunks.json").read_text())


def status_of(path: Path):
    """'missing' | 'written' | 'fact-checked' | 'reviewed' from the lesson's front matter."""
    if not path.exists():
        return "missing"
    head = path.read_text(encoding="utf-8")[:2000]
    m = re.search(r"^status:\s*(.+)$", head, re.M)
    if not m:
        return "written"
    v = m.group(1).strip().strip("'\"").lower()
    if v not in ("fact-checked", "reviewed"):
        return "written"
    # A stamped file that no longer validates (e.g. an agent was killed mid-edit) must be reviewed again;
    # the fact-checker repairs it.
    _, errs, _ = build.parse_topic(path)
    return "written" if errs else v


def scan():
    modules = []
    for m in TAX["modules"]:
        guide = ROOT / "content" / m["id"] / "_module.md"
        guide_done = False
        if guide.exists():
            _, errs = build.parse_module(guide)
            guide_done = not errs
        modules.append({"id": m["id"], "title": m["title"], "tier": m["tier"], "n": len(m["topics"]),
                        "guide_done": guide_done})
    chunks = []
    for i, ch in enumerate(CHUNKS):
        slugs = [t["id"] for t in ch["topics"]]
        st = [status_of(ROOT / "content" / ch["module"] / f"{s}.md") for s in slugs]
        chunks.append({"i": i, "module": ch["module"], "index": ch["index"], "slugs": slugs,
                       "complete": all(s != "missing" for s in st),
                       "fact_done": all(s in ("fact-checked", "reviewed") for s in st),
                       "teach_done": all(s == "reviewed" for s in st)})
    return modules, chunks


def summarize(modules, chunks):
    by = {}
    for c in chunks:
        by.setdefault(c["module"], []).append(c)
    rows, tot = [], {"write": 0, "fact": 0, "teach": 0, "guide": 0}
    for m in modules:
        cs = by.get(m["id"], [])
        w = sum(0 if c["complete"] else 1 for c in cs)
        f = sum(0 if c["fact_done"] else 1 for c in cs)
        t = sum(0 if c["teach_done"] else 1 for c in cs)
        g = 0 if m["guide_done"] else 1
        tot["write"] += w; tot["fact"] += f; tot["teach"] += t; tot["guide"] += g
        if w + f + t + g:
            rows.append(f"  {m['id']:34s} tier {m['tier']}  chunks to write={w:2d} fact-check={f:2d} teach-check={t:2d} guide={'todo' if g else 'done'}")
    n_files = sum(1 for m in TAX["modules"] for t in m["topics"] if (ROOT / "content" / m["id"] / f"{t['id']}.md").exists())
    n_fact = sum(1 for m in TAX["modules"] for t in m["topics"] if status_of(ROOT / "content" / m["id"] / f"{t['id']}.md") in ("fact-checked", "reviewed"))
    n_rev = sum(1 for m in TAX["modules"] for t in m["topics"] if status_of(ROOT / "content" / m["id"] / f"{t['id']}.md") == "reviewed")
    n_guides = sum(1 for m in modules if m["guide_done"])
    total = sum(len(m["topics"]) for m in TAX["modules"])
    head = (f"lessons written {n_files}/{total} | fact-checked {n_fact}/{total} | reviewed {n_rev}/{total} | "
            f"guides {n_guides}/{len(modules)}\n"
            f"agents still needed: write {tot['write']}, fact-check {tot['fact']}, teach-check {tot['teach']}, "
            f"guides {tot['guide']} = {sum(tot.values())} total")
    return head + ("\nremaining by module:\n" + "\n".join(rows) if rows else "\nnothing remains: everything is written, reviewed and guided.")


TEMPLATE = r'''export const meta = {
  name: 'lms-lessons-continue',
  description: 'Write the remaining lessons, fact-check and pedagogy-review every chunk in place, then write a guide per module (one module group per run)',
  phases: [
    { title: 'Write', detail: 'incomplete chunks of ≤8 topics, one agent each, validator-checked' },
    { title: 'Fact-check', detail: 'skeptical technical review of every chunk, edits in place' },
    { title: 'Teach-check', detail: 'reasoning and pedagogy review of every chunk, edits in place' },
    { title: 'Module guides', detail: 'overview, mental model, order and capstone per module' },
  ],
}

// Generated by scripts/make_workflow.py from the state of content/ — regenerate after any interruption.
const DATA = __DATA__
const ROOT = (args && args.root) || DATA.root

const byModule = {}
for (const c of DATA.chunks) (byModule[c.module] ||= []).push(c)
const cost = m => (byModule[m.id] || []).reduce((a, c) => a + (c.complete ? 0 : 1) + (c.fact_done ? 0 : 1) + (c.teach_done ? 0 : 1), m.guide_done ? 0 : 1)
const TODO = DATA.modules.filter(m => cost(m) > 0)
// Deterministic greedy split of the remaining modules into `groups` balanced groups; this run handles group `group`.
const G = (args && args.groups) || 1
const GI = (args && args.group) || 0
const groups = Array.from({ length: G }, () => []), load = Array(G).fill(0)
for (const m of TODO) { let g = 0; for (let k = 1; k < G; k++) if (load[k] < load[g]) g = k; groups[g].push(m); load[g] += cost(m) }
const MODULES = (args && args.only) ? TODO.filter(m => args.only.includes(m.id)) : groups[GI]
log(`group ${GI + 1}/${G}: ${MODULES.map(m => m.id).join(', ') || '(nothing left)'} (${MODULES.reduce((a, m) => a + cost(m), 0)} agents)`)

const WRITE = { type: 'object', properties: {
  files: { type: 'array', items: { type: 'string' } },
  uncertain: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, claim: { type: 'string' } }, required: ['file', 'claim'] } },
  notes: { type: 'string' } }, required: ['files', 'uncertain', 'notes'] }
const FACT = { type: 'object', properties: {
  corrections: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, issue: { type: 'string' }, fix: { type: 'string' } }, required: ['file', 'issue', 'fix'] } },
  files_ok: { type: 'array', items: { type: 'string' } } }, required: ['corrections', 'files_ok'] }
const TEACH = { type: 'object', properties: {
  reviews: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, changes: { type: 'string' }, depth_score: { type: 'integer' } }, required: ['file', 'changes', 'depth_score'] } } }, required: ['reviews'] }
const GUIDE = { type: 'object', properties: { file: { type: 'string' }, notes: { type: 'string' } }, required: ['file', 'notes'] }

const filesOf = c => c.slugs.map(s => `content/${c.module}/${s}.md`)

function writePrompt(c, m) {
  return `You are writing lessons for an interactive LMS that teaches the "AI Roles Skills Taxonomy" (AI Engineer / AI Architect / Forward Deployed Engineer). Project root: ${ROOT} (your working directory) — every path below is relative to it. Do not run git.

FIRST read, in full:
1. content/SPEC.md — the exact file format and the quality bar. A validator enforces it.
2. content/model-internals/kv-cache.md — the exemplar lesson. Match its depth, concreteness and reasoning-first voice.
Then print your assignment with the exact taxonomy names and hints:
   python3 -c "import json;print(json.dumps(json.load(open('chunks.json'))[${c.i}],indent=1))"
and this module's full topic key list (for 'related' links inside the module):
   python3 -c "import json;m=[m for m in json.load(open('taxonomy.json'))['modules'] if m['id']=='${c.module}'][0];print([t['key'] for t in m['topics']])"
'related' entries must be '<module>/<slug>' keys that exist in taxonomy.json (any module); grep taxonomy.json for candidates in other modules (it is large: grep for keys, never print it whole).

YOUR ASSIGNMENT — module "${m.title}" (id ${c.module}, Tier ${m.tier}), chunk ${c.index}. Write exactly these files, one per topic:
${filesOf(c).map(f => '  ' + f).join('\n')}
If one of them already exists and passes "python3 scripts/build.py --check <file>", leave it untouched. Do not create or edit any other lesson file: other agents own them in parallel.

For each topic:
- Write the complete lesson, every section in SPEC.md, reasoning first: the forcing problem, why the naive alternative fails, the mechanism step by step with the "because" behind each design choice, a concrete example (correct code, or a worked numeric scenario), trade-offs with the condition under which each flips, pitfalls with the symptom that reveals each, decision guidance, the three role lenses, an interview-style check question with an expert answer, and 3 quiz questions with plausible distractors and explanations.
- Cover every parenthesised item in the taxonomy name. Respect kind hints (kind: tool) and caveats (state the caveat first in the TL;DR). Orientation topics (notes in the chunk) should orient and point to the deep module rather than duplicate it.
- No invented numbers, benchmarks, paper titles or version-specific claims you are not sure of; label order-of-magnitude figures as illustrative.
- Use the Write tool to create files (shell heredocs mangle backticks and dollar signs).
- After each file run: python3 scripts/build.py --check content/${c.module}/<slug>.md and fix every reported error until it prints ✓.

Finish by running the check on all your files together and confirm every one prints ✓. Return the list of files written, plus any claim you were not certain of (file + claim) so the fact-checker can look at it.`
}

function factPrompt(c) {
  return `You are a skeptical senior AI/ML engineer fact-checking lessons before they ship in an LMS. Project root: ${ROOT} (your working directory) — all paths are relative to it. Do not run git. Read content/SPEC.md once so you know the required format.

Files to check (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If any file is missing or fails "python3 scripts/build.py --check <file>", write or repair it yourself following SPEC.md and the exemplar content/model-internals/kv-cache.md, then continue. Do not touch any other lesson file.

For each file, read it fully and hunt for errors on the assumption that some exist:
- wrong or hand-wavy formulas, complexity claims, memory / latency / cost arithmetic;
- wrong API usage, or code that would not run or does not demonstrate what the prose claims (trace the code line by line);
- misattributed or invented papers, tools or versions; vendor-specific or time-sensitive claims stated as universal truths;
- quiz questions whose marked answer is wrong, or where a second option is also defensible; explanations that do not actually justify the answer;
- "Check yourself" answers an expert would dispute.
Check every number. If you cannot be confident a figure is right, rewrite it as an order-of-magnitude illustration or remove it.
Fix problems directly in the file. Keep the format, keep or increase depth, never cut below the spec minimums, and do not restyle prose that is correct.
When you have finished with a file (corrected or verified clean), add the line "status: fact-checked" to its front matter directly after the est_minutes line (replace any existing status line). Then run python3 scripts/build.py --check <file> until ✓.
Return every substantive correction (file, what was wrong, what you changed) and the list of files you verified clean.`
}

function teachPrompt(c) {
  return `You are a demanding teacher reviewing LMS lessons for whether a reader could actually learn and reason about each topic, not merely recognise the term. Project root: ${ROOT} (your working directory) — all paths are relative to it. Do not run git. Read content/SPEC.md and the exemplar content/model-internals/kv-cache.md first; they define the bar.

Files (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If a file is missing or fails "python3 scripts/build.py --check <file>", write or repair it per SPEC.md first. Do not touch any other lesson file.

For each file, judge and improve in place:
- "Why it exists": does it argue from the forcing problem and show why the naive alternative fails? A restated definition is a failure; rewrite it.
- "How it works": is the mechanism walked step by step, with the reason behind each design choice ("because ...")? Are the formula, data flow or algorithm present where one exists? Add what is missing.
- "Example": concrete (real numbers, shapes, requests) and illuminating the mechanism rather than merely calling an API? Make it so.
- Trade-offs: does each state the condition under which the trade flips? Pitfalls: does each name the symptom that reveals it?
- "When to use": real decision guidance a practitioner would use, including what to measure?
- "Check yourself": does it require reasoning, and does the answer model expert thinking?
- Coverage: is any parenthesised item of the taxonomy name, or an obvious sub-concept, missing?
- Consistency: does the lesson contradict standard understanding or the exemplar's framing of neighbouring topics?
Expand where thin; tighten where padded. Do not introduce numbers, benchmarks or citations you are not sure of.
When you have finished with a file, set its front-matter status line to "status: reviewed" (replace the existing status line, or add one directly after est_minutes). Keep the validator passing (python3 scripts/build.py --check <file>) after every edit.
Return, per file, what you changed and a depth score 1–5 for the lesson after your edits (5 = a strong senior engineer would learn something and find nothing to object to).`
}

function guidePrompt(m) {
  return `Write the module guide content/${m.id}/_module.md for the module "${m.title}" (id ${m.id}, Tier ${m.tier}) of an interactive LMS teaching the AI Roles Skills Taxonomy. Project root: ${ROOT} (your working directory) — all paths are relative to it. Do not run git.
Read the "Module file" section of content/SPEC.md for the exact format, then read every lesson in content/${m.id}/ (at minimum each file's TL;DR, "Why it exists" and "When to use" sections) so the guide reflects what the lessons actually teach. Get the authoritative slug list with:
   python3 -c "import json;m=[m for m in json.load(open('taxonomy.json'))['modules'] if m['id']=='${m.id}'][0];print([t['id'] for t in m['topics']])"
Requirements:
- Overview (150+ words): what the module covers, how its topics connect, what a learner can do afterwards, and which role(s) it matters most for.
- Why it matters: Engineer / Architect / FDE bullets, each specific to this module.
- Mental model (100+ words): the one organising picture a learner should hold.
- Suggested order: every slug exactly once, numbered, each with a one-line reason, prerequisites first.
- Capstone: a realistic project a learner could build or deliver in a weekend to a week that exercises most of the module; 3+ concrete deliverables; 3+ observable rubric items.
Use the Write tool to create the file. Validate with python3 scripts/build.py --check content/${m.id}/_module.md until ✓. Return the file path and notes.`
}

let done = 0
const results = await pipeline(MODULES,
  m => pipeline(byModule[m.id] || [],
    c => c.complete
      ? Promise.resolve({ files: filesOf(c), uncertain: [], notes: 'already written before this run' })
      : agent(writePrompt(c, m), { label: `write:${c.module}#${c.index}`, phase: 'Write', schema: WRITE }),
    (w, c) => c.fact_done
      ? Promise.resolve({ write: w, fact: { corrections: [], files_ok: filesOf(c), skipped: true } })
      : agent(factPrompt(c), { label: `fact:${c.module}#${c.index}`, phase: 'Fact-check', schema: FACT, effort: 'high' }).then(f => ({ write: w, fact: f })),
    (r, c) => c.teach_done
      ? Promise.resolve({ ...(r || {}), teach: { reviews: [], skipped: true }, chunk: c.i })
      : agent(teachPrompt(c), { label: `teach:${c.module}#${c.index}`, phase: 'Teach-check', schema: TEACH })
        .then(t => { log(`✓ ${c.module}#${c.index} written and reviewed`); return { ...(r || {}), teach: t, chunk: c.i } }),
  ),
  (chunkResults, m) => m.guide_done
    ? Promise.resolve({ module: m.id, chunks: chunkResults, guide: { file: `content/${m.id}/_module.md`, notes: 'already written', skipped: true } })
    : agent(guidePrompt(m), { label: `guide:${m.id}`, phase: 'Module guides', schema: GUIDE })
      .then(g => { done++; log(`✓ ${m.id} complete (${done}/${MODULES.length} modules in this group)`); return { module: m.id, chunks: chunkResults, guide: g } })
)

const out = results.filter(Boolean)
const chunksAll = out.flatMap(r => (r.chunks || []).filter(Boolean))
const corrections = chunksAll.flatMap(ch => (ch.fact && ch.fact.corrections) || [])
const reviews = chunksAll.flatMap(ch => (ch.teach && ch.teach.reviews) || [])
const uncertain = chunksAll.flatMap(ch => (ch.write && ch.write.uncertain) || [])
const lowDepth = reviews.filter(r => r.depth_score <= 3).map(r => ({ file: r.file, depth_score: r.depth_score, changes: String(r.changes).slice(0, 200) }))
return {
  group: GI + 1, of: G,
  modules: MODULES.map(m => m.id),
  modules_done: out.length,
  chunks_expected: MODULES.reduce((a, m) => a + (byModule[m.id] || []).length, 0),
  chunks_done: chunksAll.length,
  guides: out.map(r => r.guide && r.guide.file).filter(Boolean),
  corrections_count: corrections.length,
  uncertain_claims_count: uncertain.length,
  low_depth: lowDepth,
  avg_depth: reviews.length ? Math.round(reviews.reduce((a, r) => a + r.depth_score, 0) / reviews.length * 100) / 100 : null,
  corrections_sample: corrections.slice(0, 25).map(c => ({ file: c.file, issue: String(c.issue).slice(0, 160) })),
}
'''


def main(argv):
    root, out, groups, status_only = str(ROOT), ROOT / "scripts" / "lms-continue.workflow.js", 1, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root": root = argv[i + 1]; i += 2
        elif a == "--out": out = Path(argv[i + 1]); i += 2
        elif a == "--groups": groups = int(argv[i + 1]); i += 2
        elif a == "--status": status_only = True; i += 1
        else: print(__doc__); return 2
    modules, chunks = scan()
    print(summarize(modules, chunks))
    if status_only:
        return 0
    data = {"root": root, "modules": modules, "chunks": chunks}
    js = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    out.write_text(js, encoding="utf-8")
    remaining = sum(1 for m in modules if any(
        not c["complete"] or not c["fact_done"] or not c["teach_done"] for c in chunks if c["module"] == m["id"]) or not m["guide_done"])
    print(f"\nwrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} (root {root}); {remaining} modules still have work.")
    print("Launch in Claude Code, one call per group, all in the same message:")
    for g in range(groups):
        print(f'  Workflow({{scriptPath: "{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}", args: {{"group": {g}, "groups": {groups}}}}})')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
