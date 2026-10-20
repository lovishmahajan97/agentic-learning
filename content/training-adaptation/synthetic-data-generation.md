---
id: synthetic-data-generation
module: training-adaptation
name: Synthetic data generation
kind: practice
difficulty: core
est_minutes: 16
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
Synthetic data generation uses a model, a program or a simulator to manufacture training or evaluation
examples instead of collecting them from people. It exists because the examples you need, rare failure
cases, verified reasoning traces, formats the public web lacks, are expensive or impossible to gather at
scale. Its value is decided by verification: generated examples are only as good as the filter that
chooses which survive, and training repeatedly on unfiltered model output collapses the distribution.

## Why it exists
Real labelled data has three problems that no budget fully fixes. It is scarce where you need it most:
the long tail of odd inputs that break a product appears once a month in logs and never in a scraped
corpus. It is private: a customer's internal forms, ticket formats and tool outputs exist nowhere public,
and often cannot leave their environment. And it rarely contains what you want the model to *do*: humans
do not write out step-by-step reasoning, preference comparisons between two drafts, or tool-call
sequences, so the behaviours modern post-training needs have no natural corpus.

Generation is cheap; a model can produce a hundred thousand examples overnight. The naive version,
"prompt the big model to write a dataset", fails in three predictable ways. The outputs collapse onto the
generator's favourite phrasings, names and structures, so the fine-tuned model learns a narrow template
and fails on real inputs. The generator's mistakes become the student's ground truth. And nothing stops
the generated set from containing your evaluation questions, which makes your scores meaningless.

Every effective synthetic-data recipe is built around the scarce resource, which is not generation but
*verification*. Self-Instruct bootstrapped instruction data from a few hundred seed tasks and filtered for
novelty; STaR kept only reasoning traces that reached the correct answer; code pipelines keep only
programs that pass tests; reasoning-model distillates keep only traces a checker accepts. Where
verification is strong, synthetic data lets a model exceed its own data; where it is absent, synthetic data
is a slow copy of the generator with the tails cut off.

## How it works
### The loop
Seed → specify → generate → verify → deduplicate and balance → train → evaluate on *real* held-out data →
inspect failures → adjust the specification. The last three steps are the ones teams skip, and they are
the reason the loop converges rather than drifts.

### Sources of diversity
Mode collapse is the default behaviour of a sampled generator, so diversity has to be engineered in:

- **Seeds and in-context examples.** Self-Instruct sampled a handful of tasks from a seed pool (human
  written at first, then including accepted generations) for every prompt and rejected candidates whose
  ROUGE-L overlap with existing tasks was too high, so each round pushed outward.
- **Evolution.** Evol-Instruct rewrites existing instructions along explicit axes (add a constraint,
  deepen, concretise, require more reasoning, or mutate to a different topic) so the difficulty
  distribution grows rather than regressing to easy prompts.
- **Personas.** Conditioning the generator on who is asking (a billion-persona pool was built for exactly
  this) spreads vocabulary, intent and style far more than temperature does.
- **Grounding in real artefacts.** Generate questions *from* real documents, records or code rather
  than from nothing. The inputs then follow the real distribution, and in many cases the label comes for
  free.
- **Sampling.** Multiple samples per prompt at moderate temperature, then deduplicate by n-gram overlap
  and by embedding similarity, because duplicates in synthetic sets are far denser than in scraped ones.

### Verification, strongest to weakest
1. **Execution.** Code against unit tests, SQL against a database, maths against a known answer or a
   symbolic solver. Exact, cheap, and the reason code and maths are where synthetic data has done best.
2. **Construction.** Decide the label first, then generate the input: sample a structured record and ask
   the model to write the email that contains it; sample a tool-call sequence and generate the user
   request it answers. The label is correct by construction. Add a round-trip check (an independent pass
   must recover the label from the input) to catch generations that quietly dropped a fact.
3. **Consistency.** Majority vote over several samples (self-consistency), or agreement between two
   different generators, filters out one-off errors; STaR's trick of regenerating a rationale with the
   answer as a hint ("rationalisation") recovers training signal from failures.
4. **LLM-as-judge.** A rubric-driven judge scales to anything, but carries biases (length, position,
   self-preference) that must be calibrated against human labels before you trust its filter rate.
5. **Humans.** Domain experts review a stratified sample; they are the gold standard and the bottleneck,
   so spend them on calibrating the other verifiers, not on everything.

### Where it is used
Instruction tuning (prompt–response pairs), preference data for DPO and RLHF (AI feedback over pairs of
candidates, with a judge or a rubric), pretraining-scale corpora (synthetic textbooks and rephrased web
text that proved unusually token-efficient for small models), evaluation and red-team sets (with the
caveat that a model must not be judged on data its own generator produced), and privacy-preserving
stand-ins for customer data, where a programmatic generator with synthetic identities lets work proceed
outside a restricted environment. Distilling a large model's behaviour into a small one is the same
machinery with a specific aim; see the knowledge distillation lesson.

### Model collapse and how to avoid it
Training a model on its own (or its predecessor's) unfiltered output, generation after generation,
shrinks the tails first and then the whole distribution: rare phrasings vanish, variance falls, errors
compound, and each generation is a blurrier copy. The defences follow from the mechanism. Keep real data
in the mix, accumulating rather than replacing it. Verify, so errors do not compound. Track diversity
directly (distinct n-gram ratios, embedding dispersion, length and topic distributions) across dataset
versions. Prefer a different or stronger generator to the model being trained. And decontaminate the
synthetic set against every eval, because a generator that has seen the benchmark will reproduce it.

### Provenance and permission
Record the generator model and version, the prompt template, the seed, every verifier's verdict and the
sampling settings for each example, because you will need to trace a bad behaviour back to the batch that
taught it. Check the generator's terms: some API providers prohibit using outputs to train models that
compete with theirs, and some open-weight licences attach conditions to derived models.

## Example
Construction-first generation for an extraction task, with a round-trip check. The record is sampled by a
program, so the label is correct before any model is involved; the model only writes the realistic email.
`llm` is a stub for whichever provider you use.

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

## Trade-offs
- Cheap, fast and controllable coverage of rare cases, against a distribution that drifts from real usage;
  once you have real traffic, curated real data should anchor the set and synthetic data fill the gaps,
  not the reverse.
- The student is bounded by the generator on the same task, unless verification adds information from
  outside the generator (test results, ground truth), in which case self-improvement loops become
  possible; without such a verifier, expect a slightly worse copy.
- Executable verifiers are exact and cheap but exist only for some domains; LLM judges cover everything
  with known biases; humans are accurate and slow, so the practical design calibrates the fast verifiers
  against a human-labelled sample.
- Diversity techniques (personas, evolution) widen coverage but produce some unrealistic examples, so they
  trade a realism check for the breadth they buy.
- Synthetic stand-ins unblock work on private data, but a generator fine-tuned on that private data can
  leak it, while a purely programmatic generator is safe and less realistic.

## Pitfalls
- No verification step: the student learns the generator's errors as truth, so the symptom is a fine-tuned
  model that is confidently wrong in exactly the same places as the generator.
- Low diversity: the same four names, the same opening sentence, the same structure; the symptom is a low
  distinct-n-gram ratio in the dataset and a model that handles the template and fails real inputs.
- Evaluating on data from the same generator and prompt as the training set: the symptom is an eval score
  that looks excellent and a production rollout that does not.
- Replacing real data entirely across successive versions: the symptom is shrinking vocabulary and
  variance from one dataset version to the next, which is collapse in progress.
- Inheriting the generator's style: overly polite, markdown-heavy, verbose; the symptom is a customer
  saying the outputs do not sound like their house style even though accuracy is fine.
- No per-example provenance: when a bad behaviour appears, nobody can say which batch, prompt or generator
  version taught it, so the fix becomes "regenerate everything".
- Ignoring the generator's terms of service or licence until launch review: the symptom is a legal block
  on a model that already works.

## When to use
Reach for synthetic data when real labelled data is scarce, private or missing the rare cases, when you
need behaviours nobody writes down (reasoning traces, preference pairs, tool-call sequences), when you are
bootstrapping before there is traffic, and above all when you have a verifier that can tell good examples
from bad. Lean on real curated data when it is abundant, and use synthetic data to fill specific gaps
found by error analysis. Measure verification pass rate per batch, diversity metrics across versions,
the eval delta on *real* held-out data, and agreement between your fast verifiers and a human-labelled
sample. In day-to-day work this is a generation pipeline with provenance on every row; in interviews,
expect to be asked how you would prevent collapse and why you would generate the label before the input.

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
human-labelled examples strictly as evaluation, never training. Trust comes from three measurements: the
round-trip pass rate per form type, SME review of a stratified sample of kept and rejected items, and the
eval score on the thirty real examples, with any gap between synthetic and real scores treated as the
pipeline's error bar, not noise.

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
