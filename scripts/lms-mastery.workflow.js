export const meta = {
  name: 'lms-mastery',
  description: 'Mastery pass: upgrade high-value lessons (exercise, mastery test, failure diagnosis, executed code, no growth), write fundamentals and cumulative projects, verify each',
  phases: [
    { title: 'Write', detail: 'upgrade or write each chunk to SPEC v4' },
    { title: 'Verify', detail: 'independent reviewer: run the code, test the exercise and mastery test, check claims' },
    { title: 'Guides', detail: 'guides for the new modules' },
  ],
}

const DATA = {"root":"/Users/lovish/Desktop/agentic-learning","modules":[{"id":"rag-retrieval","title":"RAG & retrieval","tier":1,"n":33,"guide_done":true},{"id":"agents","title":"Agents","tier":1,"n":24,"guide_done":true},{"id":"agent-reliability","title":"Agent reliability & runtime","tier":1,"n":16,"guide_done":true},{"id":"evals","title":"Evals","tier":1,"n":25,"guide_done":true},{"id":"safety-security","title":"Safety & security","tier":1,"n":14,"guide_done":true},{"id":"deployment","title":"Deployment","tier":3,"n":13,"guide_done":true},{"id":"slm-for-agents","title":"Small language models (SLMs) for agents","tier":1,"n":10,"guide_done":true},{"id":"engineering-fundamentals","title":"Engineering fundamentals","tier":1,"n":6,"guide_done":false},{"id":"math-for-ai","title":"Math and statistics for AI","tier":1,"n":6,"guide_done":false},{"id":"cumulative-projects","title":"Cumulative projects","tier":1,"n":5,"guide_done":false}],"chunks":[{"i":12,"module":"rag-retrieval","index":0,"slugs":["chunking-strategies","chunk-overlap","retrieval-granularity","embedding-models","embedding-dimension-and-normalization","embedding-pipelines","vector-databases"],"missing":false,"rewritten":false,"verified":false,"n_done":3,"n_verified":0},{"i":13,"module":"rag-retrieval","index":1,"slugs":["similarity-search","ann-indexes","dense-vs-sparse-retrieval","bm25-internals","inverted-indexes","hybrid-search","reranking"],"missing":false,"rewritten":true,"verified":false,"n_done":7,"n_verified":0},{"i":14,"module":"rag-retrieval","index":2,"slugs":["recall-vs-precision-trade-off","query-rewriting","query-decomposition","multi-hop-retrieval","hyde","parent-document-retrieval","contextual-retrieval"],"missing":false,"rewritten":true,"verified":false,"n_done":7,"n_verified":0},{"i":15,"module":"rag-retrieval","index":3,"slugs":["metadata-filtering","acl-aware-retrieval","lost-in-the-middle","citations-grounding","citation-correctness","graphrag","knowledge-graphs"],"missing":false,"rewritten":false,"verified":false,"n_done":4,"n_verified":0},{"i":16,"module":"rag-retrieval","index":4,"slugs":["freshness-document-deletion-index-consistency","retrieval-failure-diagnosis","long-context-vs-rag","agentic-rag","document-parsing"],"missing":false,"rewritten":true,"verified":false,"n_done":5,"n_verified":0},{"i":17,"module":"agents","index":0,"slugs":["agent-loop","tool-calling","tool-schemas-and-argument-validation","typed-tool-contracts","tool-selection-accuracy","tool-result-interpretation","parallel-tool-calls","model-context-protocol"],"missing":false,"rewritten":false,"verified":false,"n_done":5,"n_verified":0},{"i":18,"module":"agents","index":1,"slugs":["mcp-authentication-authorization-transport-security","a2a-protocol","planning","task-decomposition","reflection-self-critique","multi-agent-patterns","agent-memory","memory-retrieval-update-deletion-policies"],"missing":false,"rewritten":false,"verified":false,"n_done":2,"n_verified":0},{"i":19,"module":"agents","index":2,"slugs":["context-compaction-summarization","state-machines","human-in-the-loop","approval-gates-for-consequential-actions","sandboxing","computer-use","workflows-vs-agents","frameworks"],"missing":false,"rewritten":false,"verified":false,"n_done":6,"n_verified":0},{"i":20,"module":"agent-reliability","index":0,"slugs":["stopping-conditions-termination","max-step-budgets","loop-detection","durable-execution-and-checkpointing","state-persistence-and-conversation-recovery","state-and-checkpoint-stores","conversation-execution-identifiers","idempotent-tool-execution"],"missing":false,"rewritten":false,"verified":false,"n_done":5,"n_verified":0},{"i":21,"module":"agent-reliability","index":1,"slugs":["tool-permissions-and-least-privilege","tool-adapters-service-interfaces","structured-event-streams","audit-logs-execution-traces","deterministic-replay-and-reproducibility","agent-trajectory-evaluation","task-completion-rate","tool-call-success-rate"],"missing":false,"rewritten":true,"verified":false,"n_done":8,"n_verified":0},{"i":22,"module":"evals","index":0,"slugs":["model-evaluation","golden-dataset","eval-harness","task-specific-acceptance-criteria","benchmarking","dataset-contamination","llm-as-a-judge"],"missing":false,"rewritten":false,"verified":false,"n_done":5,"n_verified":0},{"i":23,"module":"evals","index":1,"slugs":["judge-calibration-and-bias","rubrics","pairwise-comparison","inter-annotator-agreement","pass-k","retrieval-evaluation","faithfulness-groundedness"],"missing":false,"rewritten":true,"verified":false,"n_done":7,"n_verified":0},{"i":24,"module":"evals","index":2,"slugs":["hallucination-evaluation","regression-tests","metamorphic-testing","property-based-testing","adversarial-evaluation","offline-vs-online-evaluation","a-b-testing"],"missing":false,"rewritten":false,"verified":false,"n_done":4,"n_verified":0},{"i":25,"module":"evals","index":3,"slugs":["error-analysis","red-teaming","eval-driven-development","tooling"],"missing":false,"rewritten":false,"verified":false,"n_done":3,"n_verified":0},{"i":32,"module":"safety-security","index":0,"slugs":["input-output-guardrails","output-validation","pii-redaction","content-moderation","prompt-injection","rag-poisoning","excessive-agency"],"missing":false,"rewritten":false,"verified":false,"n_done":3,"n_verified":0},{"i":33,"module":"safety-security","index":1,"slugs":["tool-privilege-escalation","cross-tenant-data-leakage","data-leakage","supply-chain-security-for-models-and-datasets","owasp-top-10-for-llms","llama-guard","nemo-guardrails"],"missing":false,"rewritten":false,"verified":false,"n_done":3,"n_verified":0},{"i":79,"module":"deployment","index":0,"slugs":["customer-vpc-on-prem-air-gapped","private-endpoints","vpn","terraform","security-questionnaires","security-and-architecture-reviews","compliance-constraints"],"missing":false,"rewritten":false,"verified":false,"n_done":2,"n_verified":0},{"i":80,"module":"deployment","index":1,"slugs":["deployment-readiness-reviews","uat","rollout-criteria-and-rollback-triggers","runbooks","health-checks","hypercare"],"missing":false,"rewritten":true,"verified":false,"n_done":6,"n_verified":0},{"i":94,"module":"slm-for-agents","index":0,"slugs":["when-an-slm-is-enough","choosing-an-slm","running-slms-locally","quantized-slms","tool-calling-and-structured-output-with-slms"],"missing":false,"rewritten":false,"verified":false,"n_done":4,"n_verified":0},{"i":95,"module":"slm-for-agents","index":1,"slugs":["fine-tuning-an-slm-for-an-agent-task","distilling-a-large-model-agent-into-an-slm","slm-llm-cascades-and-routing","on-device-and-edge-agents-with-slms","evaluating-slms-on-your-agent-tasks"],"missing":false,"rewritten":false,"verified":false,"n_done":3,"n_verified":0},{"i":97,"module":"engineering-fundamentals","index":0,"slugs":["http-from-first-principles","debugging-method","testing-fundamentals","git-and-code-review-workflow","reading-errors-logs-and-stack-traces","command-line-and-environment-basics"],"missing":true,"rewritten":false,"verified":false,"n_done":4,"n_verified":0},{"i":98,"module":"math-for-ai","index":0,"slugs":["probability-for-ai-engineers","statistics-for-evaluation","linear-algebra-intuition","calculus-intuition-for-gradients","information-theory-basics","numerical-precision"],"missing":false,"rewritten":true,"verified":false,"n_done":6,"n_verified":0},{"i":99,"module":"cumulative-projects","index":0,"slugs":["project-1-reliable-rag-application","project-2-tool-using-agent-with-guardrails","project-3-production-ai-service","project-4-slm-feasibility-study","project-5-end-to-end-business-solution"],"missing":false,"rewritten":true,"verified":false,"n_done":5,"n_verified":0}]}
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
