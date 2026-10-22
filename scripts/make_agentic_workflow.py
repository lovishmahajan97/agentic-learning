#!/usr/bin/env python3
"""Generate scripts/lms-agentic.workflow.js (agent-builder reframe, SPEC v3). Based on make_crisp_workflow.py: rewrite every lesson into the crisp format (SPEC v2),
verify each rewrite independently, then make each module guide crisp.

Progress lives on disk, so after an interruption you just regenerate and relaunch:
  lesson front matter `status: crisp`           rewritten, passes both checks
  lesson front matter `status: crisp-verified`  also passed the independent verify pass
  _module.md front matter `status: crisp`       guide done

Usage:
  python3 scripts/make_crisp_workflow.py --status
  python3 scripts/make_crisp_workflow.py --groups 3
Then launch one Workflow per group, all in the same message:
  Workflow({scriptPath: "scripts/lms-agentic.workflow.js", args: {"group": 0, "groups": 3}})
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
    if not path.exists():
        return "missing"
    st = fm_status(path)
    if not st.startswith("agentic"):
        return "todo"
    build.V2_FORCE = build.V3_FORCE = True
    try:
        _, errs, _ = build.parse_topic(path)
    finally:
        build.V2_FORCE = build.V3_FORCE = False
    if errs:
        return "todo"
    rel = str(path.relative_to(ROOT))
    old = compare_lesson.ref_text("pre-agentic", rel)
    if old is not None and compare_lesson.compare(old, path.read_text(encoding="utf-8")):
        return "todo"
    return "verified" if st == "agentic-verified" else "crisp"


def guide_done(mid):
    p = ROOT / "content" / mid / "_module.md"
    if not p.exists() or not fm_status(p).startswith("agentic"):
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
                       "missing": any(x == "missing" for x in st),
                       "verified": all(x == "verified" for x in st),
                       "n_crisp": sum(x in ("crisp", "verified") for x in st), "n_verified": sum(x == "verified" for x in st)})
    modules = [{"id": m["id"], "title": m["title"], "tier": m["tier"], "n": len(m["topics"]),
                "guide_done": guide_done(m["id"])} for m in TAX["modules"]]
    return modules, chunks


def status(modules, chunks):
    n = sum(len(c["slugs"]) for c in chunks)
    print(f"lessons agentic {sum(c['n_crisp'] for c in chunks)}/{n} | verified {sum(c['n_verified'] for c in chunks)}/{n}"
          f" | guides {sum(m['guide_done'] for m in modules)}/{len(modules)}")
    rw = sum(not c["rewritten"] for c in chunks); vf = sum(not c["verified"] for c in chunks)
    gd = sum(not m["guide_done"] for m in modules)
    print(f"agents still needed: rewrite {rw}, verify {vf}, guides {gd} = {rw + vf + gd}")


SCRIPT = r'''export const meta = {
  name: 'lms-agentic',
  description: 'Reframe every lesson for agent builders (For agent builders box, relevance tag, agent examples, patterns and interview questions) with no fact loss, verify each, then reframe each module guide',
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
  return `You are reframing lessons of an LMS for a learner who is preparing to BUILD AI AGENTS (tool-calling loops, RAG, memory, evals, serving and operating agents). The lessons are already in a crisp, scannable format; your job is to rewrite each one from the agent builder's seat. ${COMMON}

FIRST read, in full:
1. content/SPEC.md: the v3 section at the top (agent-builder framing), then the v2 base format, readability rules and mermaid rules.
2. The exemplar BEFORE and AFTER:
   git show pre-agentic:content/model-internals/kv-cache.md      (before)
   content/model-internals/kv-cache.md                            (after)
   Note: an agent-framed TL;DR, a "## For agent builders" box (why it matters for agents, where it shows up in the stack, what you control, what you can skip), an agent worked scenario with real numbers, agent-specific production patterns, an agent interview question, and agent_relevance in front matter. Role lens, Check yourself and Quiz are unchanged.

YOUR FILES (module "${m.title}", id ${m.id}, chunk ${c.index}). Rewrite exactly these, in place; other agents own other files:
${filesOf(c).map(f => '  ' + f).join('\n')}
Skip a file only if it already has "status: agentic" or "status: agentic-verified" AND both checks below pass.
If a file is already agentic and the v3 check fails only because "## Use cases & projects" is missing, ADD that closing section (after the Quiz) without reworking the rest.

For each file:
1. Read the whole lesson. Decide agent_relevance: core (used directly when building agents), useful (met when fine-tuning, serving, evaluating or operating agents), or background (context to recognise).
2. Rewrite it per SPEC v3 with the Write tool: add "agent_relevance: ..." to front matter and set "status: agentic"; agent-framed TL;DR (10-45 words); "## For agent builders" right after the TL;DR (3-5 bold-led bullets, 50+ words); How it works, Diagram, Examples, Production patterns, Trade-offs, Pitfalls, When to use and Interview questions all reframed around building and running agents; and a closing "## Use cases & projects" section AFTER the Quiz with "### Use cases" (3-5 bold-led bullets) and "### Mini project" (1-3 hours, agent-focused: **Goal**, **Steps**, **Done when**). At least one example must be an agent scenario (code or a worked numeric table); at least 2 interview questions must be about building or operating agents. For background topics, compress general theory into a short "### Background" subsection of How it works and spend the space on what the agent builder does with it.
3. Keep every number and inline code span somewhere in the lesson, and keep caveats. Generic code may be replaced by agent-focused code. Copy "## Role lens", "## Check yourself" and "## Quiz" exactly. Keep all crisp-format rules (no paragraph over 60 words, bullet over 80, bold lead-ins, tables) and the mermaid rules.
4. No invented APIs, flags, numbers or citations; when unsure a setting exists, describe it in words.
5. Run both checks and fix until both print ✓:
     python3 scripts/build.py --check --v3 content/${c.module}/<slug>.md
     python3 scripts/compare_lesson.py content/${c.module}/<slug>.md
Finish by running both checks on all your files together. Return the files, any claim you added but are not certain of, and notes.`
}

function writeNewPrompt(c, m) {
  return `You are writing NEW lessons for an LMS whose learner is preparing to BUILD AI AGENTS. These are hands-on tool lessons in the module "${m.title}" (id ${m.id}). ${COMMON}

FIRST read, in full: content/SPEC.md (v3 at the top, then the v2 base format, readability and mermaid rules) and the exemplar content/model-internals/kv-cache.md. Then print your assignment with exact names and hints:
   python3 -c "import json;print(json.dumps(json.load(open('chunks.json'))[${c.i}],indent=1))"
For 'related' keys, grep taxonomy.json (any module; never print it whole). Related lessons that already cover parts of these tools include agents/frameworks, observability/langsmith, observability/langfuse, evals/tooling, agents/model-context-protocol, python-for-ai/litellm, python-for-ai/instructor, node-ts-for-ai/vercel-ai-sdk, system-design/workflow-orchestration, agents/agent-memory, agents/sandboxing, and for MCP: agents/model-context-protocol, agents/mcp-authentication-authorization-transport-security, python-for-ai/mcp-python-sdk, node-ts-for-ai/mcp-typescript-sdk; link them and do not repeat their content at length. For MCP lessons, follow the current MCP specification's concepts (hosts, clients, servers, tools, resources, prompts, sampling, roots, elicitation, stdio and Streamable HTTP transports, OAuth-based authorization for remote servers) and say which spec version a detail comes from when unsure.

Write exactly these files (create them; other agents own other files):
${filesOf(c).map(f => '  ' + f).join('\n')}

Each file follows SPEC v3 fully, with front matter: id, module, name, kind (tool for a tool, decision for a choice), difficulty, est_minutes, agent_relevance, status: agentic, related (2-8 keys), sources (official docs names only, no URLs). Content, from the agent builder's seat:
- What the tool is for, its core abstractions and mental model, and when to pick it over the alternatives (named, with honest trade-offs).
- A runnable, minimal agent example in code (Python, or TypeScript for Mastra) using the tool's stable core API, plus a second example (a production setup, a worked scenario, or a comparison table).
- Production patterns (deployment, persistence, tracing, evals, cost, failure handling) that teams actually use with this tool.
- 4-6 interview questions a hiring team building agents would ask about it.
- A "## Quiz" with exactly 3 questions in the SPEC format, a "## Check yourself" Q/A, and a "## Role lens" with Engineer, Architect and FDE bullets.
- A closing "## Use cases & projects" section after the Quiz: "### Use cases" (3-5 bold-led bullets) and "### Mini project" (1-3 hours: **Goal**, **Steps**, **Done when**).
These libraries change quickly. Use only APIs you are confident exist in their stable releases; prefer the core, long-lived abstractions; say "check the current docs" for anything version-sensitive; never invent class names, parameters or flags.

Validate each file and fix until both print ✓:
   python3 scripts/build.py --check --v3 content/${c.module}/<slug>.md
   python3 scripts/compare_lesson.py content/${c.module}/<slug>.md
Return the files, any claim you are not certain of, and notes.`
}

function verifyPrompt(c) {
  return `You are an independent, skeptical reviewer of LMS lessons that were just reframed for learners preparing to build AI agents. ${COMMON} Read content/SPEC.md first (v3 at the top, then v2 and the mermaid rules).

Files (module ${c.module}, chunk ${c.index}):
${filesOf(c).map(f => '  ' + f).join('\n')}
If a file does not have "status: agentic" yet, reframe it yourself per SPEC v3 first. For brand-new tool lessons (no "git show pre-agentic" version exists), check every API, class and parameter name against what you are confident exists in the tool's stable release, and fix or soften anything doubtful.

For each file compare the current version with "git show pre-agentic:<path>" and fix in place:
- Agent framing is real: the "For agent builders" box says something specific and actionable for this topic, the agent example is realistic, the patterns are what agent teams actually do, and agent_relevance is honest (do not mark theory "core").
- Nothing important lost: caveats, conditions and the reasoning behind each point survive.
- Accuracy of everything new: trace code, recompute numbers, confirm named settings and APIs exist. Fix or remove anything wrong or unverifiable.
- Readability and spacing: scannable, bold lead-ins, short bullets, tables for numbers; the diagram follows the SPEC mermaid rules exactly.
- Interview questions: realistic, correct, at least 2 about building or operating agents, not repeating Check yourself.
- Use cases & projects: real use cases; the mini project is doable in 1-3 hours, agent-focused, with a clear "Done when".
When a file is right, set its front matter to "status: agentic-verified". Both checks must pass:
  python3 scripts/build.py --check --v3 <file>
  python3 scripts/compare_lesson.py <file>
Return every substantive correction (file, issue, fix) and a readability score 1-5 per file after your edits.`
}

function guidePrompt(m) {
  return `Write or rewrite (create it if it does not exist) the module guide content/${m.id}/_module.md (module "${m.title}", Tier ${m.tier}) for a learner preparing to build AI agents. ${COMMON}
Read the "Module file" section and the v3 section of content/SPEC.md, and skim the TL;DR and "For agent builders" sections of the lessons in content/${m.id}/. Then rewrite the guide in place with the Write tool:
- Overview: short paragraphs (max 60 words each) plus bullets: what this module gives an agent builder, which lessons are core for agents and which are background, what you can build afterwards. At least 150 words in total.
- Why it matters: Engineer / Architect / FDE bullets, each framed around building or running agents.
- Mental model: bullets or a small table plus ONE mermaid flowchart (in a fenced mermaid code block, following the SPEC "Mermaid rules": quoted labels, no semicolons, no classDef or style, prefer flowchart TD) showing where this module's ideas sit in an agent system.
- Suggested order: every slug exactly once with its reason; put agent-core lessons first where prerequisites allow.
- Capstone: this is the module's MAJOR PROJECT (multi-day): a working agent or agent component that combines most of the module's lessons. Brief of at least 120 words in short paragraphs and bullets that starts with the use case it serves, 3+ concrete deliverables, 3+ observable rubric items.
- Keep every fact; restructure rather than delete. Set front matter "status: agentic" (add the line after id/title).
Validate with python3 scripts/build.py --check --v2 content/${m.id}/_module.md until ✓. Return the file path and notes.`
}

const results = await pipeline(MODULES,
  m => pipeline(byModule[m.id] || [],
    c => c.rewritten
      ? Promise.resolve({ files: filesOf(c), uncertain: [], notes: 'already crisp' })
      : agent(c.missing ? writeNewPrompt(c, m) : rewritePrompt(c, m), { label: `${c.missing ? 'write' : 'rewrite'}:${c.module}#${c.index}`, phase: 'Rewrite', schema: REWRITE }),
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
    out = ROOT / "scripts" / "lms-agentic.workflow.js"
    out.write_text(SCRIPT.replace("__DATA__", json.dumps(data, separators=(",", ":"))), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}; launch {groups} group(s):")
    for g in range(groups):
        print(f'  Workflow({{scriptPath: "scripts/lms-agentic.workflow.js", args: {{"group": {g}, "groups": {groups}}}}})')


if __name__ == "__main__":
    main(sys.argv[1:])
