# Lesson authoring spec

One Markdown file per topic at `content/<module>/<slug>.md`. The `<module>` and `<slug>` come from
`taxonomy.json` (`modules[].id` and `modules[].topics[].id`). Validate every file you write with:

    python3 scripts/build.py --check content/<module>/<slug>.md

and fix until it prints ✓. The exemplar that sets the quality bar is `content/model-internals/kv-cache.md`.

## Audience and voice

The reader is a working software engineer moving into AI engineering, architecture or forward-deployed
work. They want to *understand*, not memorize: every lesson must explain the reasoning — what problem
forced this idea into existence, why the obvious alternative fails, what the mechanism actually does,
and how to decide in practice. Write like a senior colleague explaining at a whiteboard: direct, concrete,
no filler, no marketing tone. Use second person sparingly. Prefer numbers, formulas and small examples
to adjectives. Where a claim is contested or model/vendor-dependent, say so.

## File format (strict)

```
---
id: <slug>                      # must equal the file name
module: <module id>             # must equal the folder name
name: <display name>            # the taxonomy name, lightly tidied is fine
kind: concept | tool | metric | pattern | practice | decision
difficulty: intro | core | advanced
est_minutes: <integer reading time, 6–25>
related:
  - <module>/<slug>             # 2–8 keys that exist in taxonomy.json
sources:                        # optional, 0–4; names only, NO URLs
  - Author(s), year — Title     # or "Official docs: <project>"
---

## TL;DR
15–90 words. The whole idea in two or three sentences, including the *why*.

## Why it exists
120+ words. The problem that made this necessary, what breaks without it, and why the naive
alternative is not enough. This is the reasoning section — earn the idea before explaining it.

## How it works
250+ words for concepts (150+ for tools). The mechanism step by step. Include the math,
data-flow or algorithm where one exists, and the reasoning behind each design choice
("it does X *because* Y"). Sub-headings with `###` are allowed inside this section.

## Example
A fenced code block (Python unless the topic is Node/TS or SQL) **or** a worked numeric /
scenario walkthrough of 120+ words. Must be concrete: real numbers, real shapes, a real request.
Code must be correct and runnable in spirit; keep it under ~40 lines.

## Trade-offs
- 3+ bullets, each 8+ words: what you gain vs. what you pay, with the condition under which it flips.

## Pitfalls
- 3+ bullets, each 8+ words: the mistakes people actually make, and the symptom that reveals each.

## When to use
60+ words of decision guidance: when to reach for it, when not to, what to measure, how it
shows up in day-to-day work and in interviews.

## Role lens
- **Engineer:** 12+ words — how an AI Engineer meets this in code.
- **Architect:** 12+ words — the decision or boundary the Architect owns here.
- **FDE:** 12+ words — how it shows up inside a customer's environment.

## Check yourself
**Q:** One interview-style question that requires reasoning, not recall.
**A:** A 60+ word model answer a strong candidate would give.

## Quiz
### Q: question text
- [ ] option
- [x] correct option (exactly one)
- [ ] option
- [ ] option
> 15+ word explanation of why the right answer is right and the tempting one is wrong.

(exactly 3 questions; distractors must be plausible, not silly)
```

## Hard rules

- Cover the topic as named in the taxonomy, including every item in its parentheses
  (e.g. "optimizers (Adam, AdamW)" must explain both and the difference).
- No invented numbers, benchmarks or citations. If you give a figure, make it one you are sure of
  or label it as an illustrative order of magnitude.
- `kind: tool` lessons still need reasoning: what problem the tool solves, what it does under the
  hood, how it compares to the alternatives named in the taxonomy, and its sharp edges.
- Caveated topics (distributed training, feature stores) must open the TL;DR by stating the caveat.
- Do not pad. Hitting the word minimums with fluff is a failure; exceed them only with substance.
- Keep code blocks free of `## ` at line start (it would be read as a section heading).

## Module file: `content/<module>/_module.md`

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
