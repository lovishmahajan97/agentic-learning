# Lesson authoring spec (v3: agent-builder framing on the crisp format)

**Who this is for.** The learner is preparing to BUILD AGENTS: LLM apps that plan, call tools, retrieve,
remember, run in production and get evaluated. Every lesson is written from that seat. General theory
appears only as much as an agent builder needs it, framed by what it changes in their agent.
- Example of the stance: "gradient descent" is not taught as calculus; it is taught as what you need to
  read a fine-tuning loss curve, pick a learning rate for LoRA on agent traces, and spot a run that
  diverged. The math stays (keep every fact) but compressed into a "Background" subsection.

**v3 adds to the crisp format (v2 below):**
- front matter `agent_relevance: core | useful | background` (core = you use it directly when building
  agents; useful = you meet it when fine-tuning, serving or operating agents; background = context you
  should recognise) and `status: agentic` once both checks pass (`agentic-verified` after review);
- a `## For agent builders` section directly after the TL;DR: 3-5 bold-led bullets, e.g. **Why it matters
  for agents**, **Where it shows up in your stack** (frameworks, APIs, settings), **What you control**,
  **What you can skip**;
- the TL;DR, How it works, Diagram, Examples, Production patterns and Interview questions all framed in
  agent terms: at least one example is an agent scenario (tool-calling loop, RAG step, memory, eval,
  serving agents); production patterns are things agent teams do; at least 2 interview questions are
  about building or operating agents;
- unchanged: Role lens, Check yourself and Quiz (copied exactly);
- no-loss rule vs tag `pre-agentic`: front matter (except status and agent_relevance), Role lens, Check
  yourself and Quiz unchanged; every number and inline code span must still appear. Generic code blocks
  MAY be replaced by agent-focused code. Check with `python3 scripts/build.py --check --v3 <file>` and
  `python3 scripts/compare_lesson.py <file>`.

The exemplar is `content/model-internals/kv-cache.md` (compare with `git show pre-agentic:<path>`).

---

# Base format (v2: crisp format)

One Markdown file per topic at `content/<module>/<slug>.md` (`<module>` and `<slug>` from `taxonomy.json`).
Check every file you write with both tools and fix until both print ✓:

    python3 scripts/build.py --check --v2 content/<module>/<slug>.md      # format and readability
    python3 scripts/compare_lesson.py content/<module>/<slug>.md          # nothing lost vs tag pre-crisp

The exemplar that sets the bar is `content/model-internals/kv-cache.md`. Read it before writing.

## Audience and voice

A working software engineer moving into AI engineering, architecture or forward-deployed work. They
read on a laptop between tasks and must be able to **scan** a lesson: bold lead-ins, short bullets,
numbered steps, small tables, real code. Every point still carries its reason ("X, because Y"). Write
like a senior colleague at a whiteboard: direct, concrete, numbers over adjectives, no filler.

## Readability rules (enforced)

- No prose paragraph over **60 words**; no bullet over **80 words**. Split, or turn into a list or table.
- Lead each bullet with a **bold phrase** that states the point; the rest of the bullet explains it.
- Prefer tables for comparisons and numbers, numbered lists for sequences, bullets for sets.
- Code is excluded from the word limits.

## File format

```
---
(front matter unchanged from the existing lesson, except: status: crisp)
---

## TL;DR
1-2 sentences, 10-45 words: what it is and why it matters.

## Why it exists
3-5 bullets: the forcing problem, why the naive fix fails, what this buys (with numbers when possible).

## How it works
The mechanism as ### subsections, numbered steps, bullets and tables. Keep formulas and algorithms
in fenced blocks. Every design choice keeps its "because".

## Diagram
One or two sentences saying what to look at, then ONE ```mermaid block (flowchart, sequenceDiagram or
stateDiagram-v2) showing the mechanism, data flow, request lifecycle or decision path. See the
mermaid rules below.

## Examples
At least 2 examples, each under its own `### title`, at least one with a fenced code block:
- a runnable code example (Python unless the topic is Node/TS or SQL; under ~40 lines);
- a worked scenario with real numbers, ideally as a short table (a calculation, an incident, a
  before/after), or a second code example showing a production use.

## Production patterns
3-6 bullets, each starting with a **bold pattern name**, then: when to use it, how (a concrete
setting, config flag, library call or snippet), and what to monitor. Patterns must be things teams
actually do in production; name real tools only when you are sure the setting exists.

## Trade-offs
3+ bullets: **Gain vs cost.** What you gain, what you pay, and the condition under which it flips.

## Pitfalls
3+ bullets: **The mistake.** The symptom that reveals it, and the fix.

## When to use
3+ bullets, e.g. **Use when**, **Avoid when**, **Measure**, **Interviews**.

## Role lens            (unchanged, copy exactly)
## Check yourself       (unchanged, copy exactly)

## Interview questions
4-6 questions a real interviewer would ask about this topic, each as a `### question` heading followed
by a model answer of 2-4 bold-led bullets (25+ words). Mix levels: one definition-plus-why, one design
or debugging scenario, one trade-off or "when would you not", one production or numbers question. Do not
repeat the Check yourself question.

## Quiz                 (unchanged, copy exactly)
```

## Mermaid rules (diagrams must render)

- Start with `flowchart TD`, `flowchart LR`, `sequenceDiagram` or `stateDiagram-v2`.
- Node ids are plain letters/digits (`A`, `KV1`); put every label in double quotes: `A["KV cache (per layer)"]`.
  Never put a double quote inside a label; use words instead.
- Edge labels also quoted: `A -->|"append k, v"| B`. Dotted: `A -.->|"label"| B`. Database shape: `C[("Store")]`.
- Never use `;`, `classDef`, `style`, `click`, HTML tags (except `<br/>`), or `#` entities.
- sequenceDiagram: `participant U as User`, messages like `U->>S: POST /chat`, notes like
  `Note over S: validate`. Keep message text free of `;` and `#`.
- Keep it to the core idea: at most ~12 nodes or ~10 messages, max 40 lines.
- Prefer `flowchart TD` (top-down) for anything over 5 nodes; it fits a reading column. Use `LR`
  only for short chains. Keep node labels under ~6 words; put detail in the caption, not the boxes.

## Rewriting an existing lesson (the no-loss rule)

You are restructuring, not summarising. `compare_lesson.py` fails the rewrite if anything is lost:
- front matter must be identical except `status` (set it to `crisp` when both checks pass);
- `## Role lens`, `## Check yourself` and `## Quiz` must stay byte-for-byte the same in content;
- every original fenced code block must still appear (you may add new ones);
- every number and every inline `code` span in the original prose must still appear somewhere.
Move facts into the new structure rather than deleting them. You may add facts, examples and patterns.

## Hard rules

- Cover the topic as named in the taxonomy, including every item in its parentheses.
- No invented numbers, benchmarks, APIs, config flags or citations. Label rough figures as
  illustrative orders of magnitude.
- Caveated topics (distributed training, feature stores) keep the caveat first in the TL;DR.
- Create and edit files with the Write/Edit tools, not shell heredocs.
- Keep code blocks free of `## ` at line start (it would be read as a section heading).

## Module file: `content/<module>/_module.md`

Crisp guides set `status: crisp` in front matter and follow the same readability rules (no paragraph
over 60 words, no bullet over 80) in Overview, Mental model and the Capstone brief. Validate with
`python3 scripts/build.py --check --v2 content/<module>/_module.md`.

```
---
id: <module id>
title: <module title>
---

## Overview
150+ words: what this module is about, how its topics connect, what you can do after it.

## Why it matters
- **Engineer:** 12+ words
- **Architect:** 12+ words
- **FDE:** 12+ words

## Mental model
100+ words: the one picture that organises the module.

## Suggested order
1. <slug> — one-line reason
2. <slug> — ...
(every topic slug of the module exactly once, in a sensible learning order)

## Capstone
### Brief
120+ words: a realistic project that exercises the module end to end.
### Deliverables
- 3+ bullets
### Rubric
- 3+ bullets: what "done well" looks like, observable and specific
```
