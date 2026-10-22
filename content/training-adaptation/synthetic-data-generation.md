---
id: synthetic-data-generation
module: training-adaptation
name: Synthetic data generation
kind: practice
difficulty: core
est_minutes: 16
status: agentic
agent_relevance: core
related:
  - training-adaptation/data-curation
  - training-adaptation/knowledge-distillation
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/dpo
  - evals/golden-dataset
  - evals/llm-as-a-judge
  - customer-data-evaluation/golden-dataset-creation-with-client-smes
  - data-architecture/model-and-dataset-provenance
sources:
  - Wang et al., 2022 — Self-Instruct: Aligning Language Models with Self-Generated Instructions
  - Zelikman et al., 2022 — STaR: Bootstrapping Reasoning With Reasoning
  - Xu et al., 2023 — WizardLM: Empowering Large Language Models to Follow Complex Instructions
  - Shumailov et al., 2024 — AI models collapse when trained on recursively generated data
---

## TL;DR
Agent builders use a model, program or simulator to manufacture tool-call training data, agent tasks and
simulated users for evals. Its value is decided by verification: examples are only as good as the filter
choosing survivors, and unfiltered recursion collapses.

## For agent builders
- **Why it matters for agents.** Before launch you have no agent traffic, and after launch the failures
  you care about are rare. Synthetic tasks, tool-call examples and simulated users are how most teams
  get an agent eval suite at all, and how they teach a small model a tool-call format.
- **Where it shows up in your stack.** A generation script calling your provider, tool schemas as the
  source of labels, a sandbox or mock tool backend that executes calls, an LLM judge or end-state
  checker, and the eval harness that replays the tasks against every agent version.
- **What you control.** The specification (tools, task types, personas, difficulty), the verifier
  (executed end state beats judge), the mix with real traces, dedup and diversity gates, and which sets
  stay walled off as evals.
- **What you can skip.** Pretraining-scale synthetic corpora and the model-collapse theory beyond its
  defences. Know construction-first generation, execution verification, judge calibration and
  decontamination.

## Why it exists
- **Real data is scarce where you need it most.** The long tail of odd inputs that break a product
  appears once a month in logs and never in a scraped corpus.
- **Real data is private.** A customer's internal forms, ticket formats and tool outputs exist nowhere
  public, and often cannot leave their environment.
- **Real data lacks the behaviours you want.** Humans do not write out step-by-step reasoning, preference
  comparisons between two drafts, or tool-call sequences, so modern post-training has no natural corpus.
- **Naive generation fails three ways.** Generation is cheap (a hundred thousand examples overnight), but
  "prompt the big model to write a dataset":
  - collapses onto the generator's favourite phrasings, names and structures, so the model learns a
    narrow template and fails on real inputs;
  - turns the generator's mistakes into the student's ground truth;
  - can contain your evaluation questions, making scores meaningless.
- **The scarce resource is verification, not generation.** Self-Instruct bootstrapped from 175
  human-written seed tasks and filtered for novelty; STaR kept only traces reaching the correct answer;
  code pipelines keep programs that pass tests; reasoning distillates keep traces a checker accepts. With
  strong verification a model can exceed its own data; without it, you get a slow copy of the generator
  with the tails cut off.

## How it works
### The loop
```
seed → specify → generate → verify → deduplicate and balance → train
     → evaluate on REAL held-out data → inspect failures → adjust the specification
```
The last three steps are the ones teams skip, and they are why the loop converges rather than drifts.

### Sources of diversity
Mode collapse is the default behaviour of a sampled generator, so diversity must be engineered in:
- **Seeds and in-context examples.** Self-Instruct sampled a handful of tasks from a seed pool (human
  written at first, then including accepted generations) per prompt, and rejected candidates whose ROUGE-L
  overlap with existing tasks was too high, so each round pushed outward.
- **Evolution.** Evol-Instruct rewrites instructions along explicit axes (add a constraint, deepen,
  concretise, require more reasoning, or mutate to a new topic), so difficulty grows instead of regressing
  to easy prompts.
- **Personas.** Conditioning on who is asking (a billion-persona pool was built for exactly this) spreads
  vocabulary, intent and style far more than temperature does.
- **Grounding in real artefacts.** Generate questions *from* real documents, records or code. Inputs then
  follow the real distribution, and often the label comes for free.
- **Sampling.** Several samples per prompt at moderate temperature, then dedup by n-gram overlap and
  embedding similarity, because duplicates are far denser in synthetic sets than in scraped ones.

### Verification, strongest to weakest
| # | Verifier | How | Strength | Weakness |
|---|---|---|---|---|
| 1 | Execution | code vs unit tests, SQL vs a database, maths vs a known answer or symbolic solver | exact, cheap | only some domains |
| 2 | Construction | decide the label first, generate the input | correct by construction | needs a structured label |
| 3 | Consistency | majority vote (self-consistency) or two generators agreeing | filters one-off errors | shared errors survive |
| 4 | LLM-as-judge | rubric-driven judge | scales to anything | length, position, self-preference bias |
| 5 | Humans | domain experts on a stratified sample | gold standard | the bottleneck |

- **Execution** is why code and maths are where synthetic data has done best.
- **Construction:** sample a structured record and have the model write the email containing it; sample
  a tool-call sequence and generate the request it answers. Add a round-trip check (an independent pass
  must recover the label) to catch generations that quietly dropped a fact.
- **Consistency:** STaR's "rationalisation" regenerates a rationale with the answer as a hint, recovering
  training signal from failures.
- **LLM judges** must be calibrated against human labels before you trust their filter rate.
- **Humans:** spend them on calibrating the other verifiers, not on everything.

### Where it is used
- **Instruction tuning:** prompt–response pairs.
- **Preference data for DPO and RLHF:** AI feedback over pairs of candidates, with a judge or a rubric.
- **Pretraining-scale corpora:** synthetic textbooks and rephrased web text proved unusually
  token-efficient for small models.
- **Evaluation and red-team sets:** with the caveat that a model must not be judged on data its own
  generator produced.
- **Privacy-preserving stand-ins:** a programmatic generator with synthetic identities lets work proceed
  outside a restricted environment.
- **Distillation:** the same machinery with a specific aim; see the knowledge distillation lesson.

### Synthetic data for agents
Three shapes cover most agent work:
1. **Tool-call examples by construction.** Sample a valid call (tool name plus arguments) from the
   schema, have a model write the user request that needs it, then check the agent recovers the same
   call. The label is right before any model touches it.
2. **Synthetic tasks with an executable end state.** Seed a sandbox (a test database, a repository, a
   mock CRM), state a goal, and verify by inspecting the end state after the agent runs: the row
   exists, the tests pass, the refund was not issued twice.
3. **Simulated users.** A second model plays a user with a persona, a goal and hidden facts it reveals
   only when asked, so multi-turn agents get tested on clarifying questions and recovery, not just
   one-shot requests.

- **Verification order still holds:** an executed end state beats a judge reading the transcript.
- **Walls still hold:** tasks used for training never appear in the eval suite, and the simulator's model
  should differ from the agent's.

### Model collapse and how to avoid it
**Mechanism:** training on its own (or a predecessor's) unfiltered output, generation after generation,
shrinks the tails first, then the whole distribution. Rare phrasings vanish, variance falls, errors
compound, and each generation is a blurrier copy. The defences follow from the mechanism:
- **Keep real data in the mix**, accumulating rather than replacing it.
- **Verify**, so errors do not compound.
- **Track diversity directly** across dataset versions: distinct n-gram ratios, embedding dispersion,
  length and topic distributions.
- **Prefer a different or stronger generator** to the model being trained.
- **Decontaminate against every eval**, because a generator that has seen the benchmark will reproduce it.

### Provenance and permission
- **Per example, record** the generator model and version, prompt template, seed, every verifier's
  verdict and the sampling settings, because you will need to trace a bad behaviour to the batch that
  taught it.
- **Check the generator's terms.** Some API providers prohibit using outputs to train competing models,
  and some open-weight licences attach conditions to derived models.

## Diagram
The generate-verify loop, as used for agent tool-call data and eval tasks: verification (ideally an
executed end state) decides what survives, real held-out agent traffic decides whether it worked, and
failures flow back into the specification rather than into more generation.

```mermaid
flowchart TD
    S["Seeds, personas, real artefacts"] --> SP["Specify: task, schema, diversity axes"]
    SP --> G["Generate several samples per prompt"]
    G --> V["Verify: execution, construction, consistency, judge, humans"]
    V -->|"fails"| RJ[("Rejects, read by hand")]
    V -->|"passes"| DD["Deduplicate and balance, check distinct-n"]
    DD --> MX["Mix with accumulated real data"]
    MX --> T["Train"]
    T --> E["Evaluate on REAL held-out data"]
    E --> F["Inspect failures"]
    F -->|"adjust the specification"| SP
```

## Examples
### Construction-first generation with a round-trip check
An extraction task. A program samples the record, so the label is correct before any model is involved;
the model only writes the realistic email. `llm` is a stub for whichever provider you use.

```python
import json, random

def llm(prompt: str, temperature: float = 0.8) -> str:
    """Call your model provider and return text. Use temperature ~0.8 to write, 0 to check."""
    raise NotImplementedError

VENDORS = ["Acme Tooling", "Northwind Traders", "Blue Fern Studio", "Kappa Logistics"]
PERSONAS = ["terse accounts-payable clerk", "chatty small-business owner", "automated billing system"]

def sample_record(rng):                          # the LABEL comes first, so it is correct by construction
    return {"vendor": rng.choice(VENDORS), "invoice_no": f"INV-{rng.randint(1000, 99999)}",
            "total": round(rng.uniform(20, 5000), 2), "currency": rng.choice(["USD", "EUR"]),
            "due_date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"}

def make_example(rng):
    rec = sample_record(rng)
    email = llm(f"You are a {rng.choice(PERSONAS)}. Write a realistic email of 60-150 words that mentions "
                f"exactly these facts in your own words and order, plus one unrelated detail: "
                f"{json.dumps(rec)}. Do not output JSON.")
    # round-trip verification: an independent extraction pass must recover the record exactly
    back = llm("Extract vendor, invoice_no, total (number), currency and due_date (YYYY-MM-DD) as JSON "
               f"from this email:\n{email}", temperature=0)
    try:
        ok = json.loads(back) == rec
    except json.JSONDecodeError:
        ok = False
    return {"input": email, "output": rec, "verified": ok,
            "provenance": {"generator": "<model id>", "prompt": "invoice-v3", "persona_pool": "v1"}}

rng = random.Random(7)
examples = [make_example(rng) for _ in range(1000)]
kept = [e for e in examples if e["verified"]]
print(f"kept {len(kept)}/{len(examples)}")
# Next: near-deduplicate the emails, balance vendors and currencies, hold out a slice for evaluation,
# and have a human read 50 kept and 50 rejected examples before trusting the pass rate.
```

### Construction-first tool-call data for an agent
The call is sampled from the tool schema first, so the label is correct; the model only writes the
request. The round-trip check asks an independent, stronger checker model (not the model you will train)
to produce the call and keeps exact matches.

`llm` is the same provider stub as above; `checker_call` is a tool-call request to that checker.

```python
import json, random

TOOLS = {"search_orders": {"customer_email": ["ana@example.com", "li@example.com"],
                           "status": ["open", "shipped", "refunded"]},
         "create_refund": {"order_id": ["A-1001", "B-2002"], "reason": ["damaged", "late", "wrong_item"]}}
PERSONAS = ["impatient customer on mobile", "polite non-native speaker", "support agent forwarding a case"]

def sample_call(rng):
    name = rng.choice(list(TOOLS))
    return {"name": name, "arguments": {k: rng.choice(v) for k, v in TOOLS[name].items()}}

def make_tool_example(rng, checker_call):
    call = sample_call(rng)                                     # label first: valid by construction
    request = llm(f"As a {rng.choice(PERSONAS)}, write one message that requires exactly this action "
                  f"and states every argument value, without naming the tool: {json.dumps(call)}")
    predicted = checker_call(request, tools=list(TOOLS))        # round trip: did the request keep every fact?
    return {"input": request, "label": call, "verified": predicted == call,
            "provenance": {"generator": "<model id>", "prompt": "toolcall-v1"}}

# Keep verified rows for training; read rejects, because some are hard-but-valid requests the checker
# missed, which belong in the eval suite rather than the bin.
```

### Worked scenario: bootstrapping an agent eval suite before launch
A refunds agent has 4 tools and no traffic yet. Illustrative funnel for synthetic tasks in a sandbox
seeded with test orders:

| Stage | Count | Notes |
|---|---|---|
| Task specs generated (persona × goal × edge case) | 2,000 | goals include partial refunds and duplicate requests |
| After dedup by embedding similarity | 1,300 | many near-identical "refund my late order" tasks |
| End-state checker written and passing on a reference run | 900 | tasks whose expected end state could be stated exactly |
| SME review of a 100-task sample | 87 of 100 accepted | rejects were unrealistic policies |
| Final suite, walled off from training | ~780 | about 87% of 900 |

The suite now gates every prompt, model and tool change; once real traffic exists, real failures are
added and synthetic tasks are retired where real ones cover the same case.

### Execution verifier for generated code
Pure Python, runnable. Keeps only candidates whose unit tests pass in a separate process with a timeout.
`subprocess` isolation is not a security sandbox; run untrusted generations in a locked-down container.

```python
import subprocess, sys, tempfile, os

def passes_tests(candidate_code: str, tests: str, timeout_s: float = 5.0) -> bool:
    """Run generated code plus its unit tests in a separate process; keep only clean exits."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(candidate_code + "\n\n" + tests)
        path = f.name
    try:
        r = subprocess.run([sys.executable, "-I", path], capture_output=True, timeout=timeout_s)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False                                   # infinite loops count as failures
    finally:
        os.unlink(path)

tests = "assert slugify('Hello World') == 'hello-world'\nassert slugify('  A  b ') == 'a-b'"
good = "def slugify(s):\n    return '-'.join(s.lower().split())"
bad = "def slugify(s):\n    return s.lower().replace(' ', '-')"
print(passes_tests(good, tests), passes_tests(bad, tests))   # True False
```

### A diversity gate: distinct-n across a batch
Pure Python. A batch that collapsed onto one template scores far lower than a varied one; track this
number per batch and per dataset version.

```python
import re

def distinct_n(texts, n=2):
    """Unique n-grams / total n-grams across a batch: falls as a generator collapses onto templates."""
    grams = [tuple(t[i:i + n]) for t in (re.findall(r"\w+", x.lower()) for x in texts)
             for i in range(len(t) - n + 1)]
    return len(set(grams)) / max(1, len(grams))

collapsed = ["Dear team, I hope this email finds you well. Invoice INV-1001 is due soon."] * 3 + \
            ["Dear team, I hope this email finds you well. Invoice INV-2002 is due soon."]
varied = ["hey - acme here, 1001 still unpaid?? due friday",
          "Per our records, invoice INV-2002 (EUR 310.00) remains outstanding.",
          "Reminder: Kappa Logistics bill #3003 falls due on 2026-04-12.",
          "Hi Sam, quick one: can you confirm you got invoice 4004 for the shoot?"]
print(f"collapsed distinct-2 = {distinct_n(collapsed):.2f}")   # 0.29
print(f"varied    distinct-2 = {distinct_n(varied):.2f}")      # 1.00
```

## Production patterns
- **Sandboxed agent tasks with end-state checks.** Build eval and training tasks against a seeded sandbox
  or mock tool backend, and verify by inspecting its state after the run rather than by judging the
  transcript. Monitor per-task pass rate across agent versions and flag tasks that every version fails
  (often a broken task, not a weak agent).
- **Construction-first for structured tasks.** For extraction, classification and tool calls, sample
  the label programmatically and generate only the surface text, with a round-trip extraction at
  temperature 0. Monitor round-trip pass rate per type, and read rejects to catch a checker that discards
  hard-but-valid examples.
- **Per-row provenance in the dataset schema.** Every row carries generator id and version, prompt
  template version, seed, sampling settings and verifier verdicts, so a bad behaviour traces to one batch
  and you regenerate that batch, not everything.
- **Batch gates on verification and diversity.** Reject a batch whose pass rate or distinct-n falls
  outside the range of previous accepted batches; also track length, topic and per-tool distributions
  per version.
- **Judge calibration before scale.** Before an LLM judge filters a full run, score a few hundred
  human-labelled items and measure agreement; recalibrate whenever the judge model or rubric changes.
- **Real data as the anchor, walled-off evals.** Accumulate real curated data across versions and add
  synthetic data only for gaps found by error analysis. Keep evaluation sets from any generator prompt,
  and decontaminate synthetic sets against them.

## Trade-offs
- **Coverage vs drift.** Cheap, fast, controllable coverage of rare cases, against a distribution that
  drifts from real usage. Once real traffic exists, curated real data should anchor the set and synthetic
  data fill gaps, not the reverse.
- **Bounded by the generator.** The student is bounded by the generator on the same task, unless
  verification adds outside information (test results, ground truth); then self-improvement loops become
  possible. Without such a verifier, expect a slightly worse copy.
- **Verifier choice.** Executable verifiers are exact and cheap but exist only for some domains; LLM judges
  cover everything with known biases; humans are accurate and slow. Calibrate the fast verifiers against a
  human-labelled sample.
- **Breadth vs realism.** Personas and evolution widen coverage but produce some unrealistic examples, so
  you trade a realism check for the breadth.
- **Privacy vs realism.** Synthetic stand-ins unblock work on private data, but a generator fine-tuned on
  that data can leak it; a purely programmatic generator is safe and less realistic.

## Pitfalls
- **No verification step.** The student learns the generator's errors as truth. Symptom: confidently
  wrong in exactly the generator's places.
- **Letting a model-based verifier define "correct" without auditing rejects.** A round-trip or judge
  filter keeps what the checker already handles, discarding hard-but-valid examples and drifting toward
  easy cases. Symptom: high pass rate, little gain on the hardest real inputs. Read the rejects and rescue
  valid ones with a stronger checker or a human.
- **Low diversity.** The same four names, opening sentence and structure. Symptom: a low distinct-n-gram
  ratio and a model that handles the template but fails real inputs.
- **Evaluating on data from the same generator and prompt as training.** Symptom: an excellent eval score
  and a production rollout that does not match.
- **Replacing real data across versions.** Symptom: vocabulary and variance shrink version to version,
  which is collapse in progress.
- **Inheriting the generator's style.** Overly polite, markdown-heavy, verbose. Symptom: the customer says
  outputs do not sound like their house style despite fine accuracy.
- **No per-example provenance.** Nobody can say which batch, prompt or generator version taught a bad
  behaviour, so the fix becomes "regenerate everything".
- **Ignoring terms of service or licence until launch review.** Symptom: a legal block on a model that
  already works.
- **Simulated users that are too cooperative.** The simulator volunteers every fact up front and never
  changes its mind. Symptom: high eval pass rate, while real users trigger clarifying-question failures.
  Fix: give the simulator hidden facts, a persona and permission to be vague or wrong.
- **Judging agent tasks from the transcript alone.** A judge accepts a confident "refund issued" message.
  Symptom: passing evals while the sandbox shows no refund or two. Fix: check the end state.

## When to use
- **Use when:** real labelled data is scarce, private or missing rare cases; you need behaviours nobody
  writes down (reasoning traces, preference pairs, tool-call sequences); you are bootstrapping before
  traffic; and above all when you have a verifier that tells good examples from bad.
- **Lean on real curated data when:** it is abundant; use synthetic data to fill gaps found by error
  analysis.
- **Measure:** verification pass rate per batch, diversity across versions, the eval delta on *real*
  held-out data, and agreement between fast verifiers and a human-labelled sample.
- **Agents before launch:** synthetic tasks in a sandbox and simulated users are often the only way to
  get an eval suite; retire them as real failures arrive.
- **Day to day:** a generation pipeline with provenance on every row.
- **Interviews:** how you would prevent collapse, and why you would generate the label before the input.

## Role lens
- **Engineer:** Builds the generate–verify–deduplicate pipeline with per-row provenance, calibrates the
  judge against human labels, and gates every batch on verification pass rate and diversity metrics.
- **Architect:** Decides which generators are permitted (licence and terms), how synthetic and real data
  are mixed and versioned, and which evaluation sets are walled off from any generator.
- **FDE:** Bootstraps a customer's model from thirty labelled examples by construction-first generation
  over their real document types, with SMEs reviewing samples and verifying the round-trip check.

## Check yourself
**Q:** A customer wants a model that extracts fields from forty internal form types, but can supply only
thirty labelled examples. How would you build a training set, and how would you know it is trustworthy?
**A:** I would not ask the model to invent forms. I would take the forty real templates and the field
schema, write a program that samples valid records for each type, and use a model to render each record
into realistic filled forms in several personas and styles, varying layout noise. The label is the sampled
record, so it is correct by construction; a separate extraction pass must recover it exactly, and only
round-trip successes survive. I would near-deduplicate, balance across form types, and keep the thirty
human-labelled examples strictly as evaluation, never training or few-shot prompts. Thirty examples
cannot cover forty types, though, so I would ask the SMEs to label a handful more for each uncovered type,
or report those types as unmeasured rather than averaging them away. Trust comes from three measurements: the
round-trip pass rate per form type, SME review of a stratified sample of kept and rejected items, and the
eval score on the thirty real examples, with any gap between synthetic and real scores treated as the
pipeline's error bar, not noise.

## Interview questions
### Temperature alone does not give a diverse synthetic set. What do you do instead?
- **Seeds with a novelty filter:** Self-Instruct sampled tasks from a seed pool and rejected candidates
  whose ROUGE-L overlap with existing tasks was too high, so each round pushed outward.
- **Evolution and personas:** Evol-Instruct rewrites prompts along explicit axes (add a constraint,
  deepen, mutate topic); conditioning on who is asking spreads vocabulary, intent and style.
- **Ground in real artefacts:** generating questions from real documents, records or code keeps inputs on
  the real distribution, then dedup by n-gram and embedding similarity.

### Your model scores very well on a synthetic eval but disappoints in production. How do you debug it?
- **Check eval provenance:** if the eval came from the same generator and prompt as training, the score
  measures template-matching; rebuild it from real held-out data.
- **Check diversity:** a low distinct-n ratio or repeated names and openings means the model learned a
  narrow template that real inputs do not follow.
- **Check style and verifier drift:** inherited generator style, or a filter that kept only easy cases,
  show up as fine accuracy on easy inputs and misses on the hardest real ones.

### Can a student trained on synthetic data beat its generator? When would you not use synthetic data?
- **Only with outside information:** test results or ground truth from a verifier let self-improvement
  loops exceed the generator; without one, expect a slightly worse copy.
- **Avoid leaning on it** when real curated data is abundant: use it to anchor the set and generate only
  for gaps found by error analysis.
- **Also avoid** when the generator's terms or licence forbid training on its outputs.

### You want an LLM judge to filter 500,000 generated examples. How do you decide whether to trust it?
- **Calibrate first:** score a few hundred human-labelled items and measure agreement before the judge
  filters the full run.
- **Know its biases:** length, position and self-preference bias can skew what survives, so inspect
  rejects as well as keeps.
- **Recalibrate on change:** whenever the judge model or rubric changes, rerun the agreement check, and
  spend human effort calibrating verifiers rather than labelling everything.

### You are launching a refunds agent with no traffic. How do you build its eval suite?
- **Synthetic tasks in a sandbox:** seed test orders, generate task specs across personas, goals and
  edge cases (partial refunds, duplicates), and write an end-state check per task.
- **Simulated users:** a different model plays the customer with hidden facts, so multi-turn behaviour
  and clarifying questions get tested.
- **Trust it:** dedup by embedding similarity, have SMEs review a sample, wall the suite off from any
  training data, and replace synthetic tasks with real failures once traffic arrives.

### How would you generate training data to teach a small model your tool-call format?
- **Construction-first:** sample valid calls from the tool schemas, have a strong model write the user
  request that needs each one, with personas for diversity.
- **Verify:** an independent checker must recover the exact call from the request, and rejects are read
  by hand, because some are hard-but-valid cases that belong in the eval suite.
- **Gate batches:** distinct-n, per-tool balance and round-trip pass rate per tool, then evaluate the
  trained model on real held-out requests, not synthetic ones. For generated code, the same idea is
  execution: unit tests in a separate process with a timeout, and `subprocess` is not a security
  sandbox.

## Quiz
### Q: What most determines the quality of a synthetic training set?
- [ ] The size of the generator model
- [ ] The sampling temperature
- [x] The strength of the verification step that decides which generated examples survive
- [ ] The number of examples generated
> Generation is cheap and errors are the norm; a verifier that catches wrong or duplicated examples is what
> separates a dataset that improves a model from one that teaches the generator's mistakes.

### Q: Why generate the label first and the input second in a construction-first pipeline?
- [ ] Because inputs are longer and more expensive to generate
- [x] Because the label is then correct by construction, and a round-trip check can verify the input faithfully contains it
- [ ] Because models cannot produce structured output reliably
- [ ] Because it avoids needing any deduplication
> Sampling the structured answer programmatically removes the generator from the label entirely; the model
> only writes the surface text, and an independent extraction pass confirms nothing was dropped or changed.

### Q: Which practice best protects against model collapse across successive training rounds?
- [ ] Raising the sampling temperature each round
- [ ] Using a larger generator each round
- [x] Keeping real data in the mix, verifying outputs, and tracking diversity across dataset versions
- [ ] Training for fewer epochs
> Collapse comes from recursively copying a narrowing distribution with compounding errors. Anchoring on
> real data, filtering errors and measuring diversity attack each part of that mechanism directly.

## Use cases & projects
### Use cases
- **Pre-launch agent eval suites.** With no real traffic yet, teams generate tasks against a seeded
  sandbox and verify each by the end state it should produce.
- **Teaching a small model a tool-call format.** Construction-first generation samples valid calls from
  the tool schemas and asks a model only for the user request that leads to them.
- **Simulated users for multi-turn agents.** A persona-driven model plays the customer so the agent is
  tested on clarifications, corrections and off-topic turns.
- **Covering rare failure cases.** Error analysis on real traces names a gap, and synthetic examples fill
  it while real data stays the anchor.

### Mini project
**Generate and verify a synthetic agent eval set (2-3 hours).**
- **Goal:** build 50 verified tasks for a small tool-using agent and use them to compare two agent
  versions.
- **Steps:** write a mock backend with 3 tools and seeded state; have a model propose tasks with the
  expected end state; run each task once with a reference agent and keep only those whose end state
  matches; deduplicate and record generator, prompt version and seed per row; replay the set against two
  prompt versions of your agent.
- **Done when:** you have the task file with provenance, the share of generated tasks that survived
  verification, and a per-version pass-rate table.
