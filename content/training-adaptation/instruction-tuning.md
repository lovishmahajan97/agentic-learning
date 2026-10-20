---
id: instruction-tuning
module: training-adaptation
name: Instruction tuning
kind: concept
difficulty: core
est_minutes: 14
status: crisp
related:
  - training-adaptation/supervised-fine-tuning
  - training-adaptation/post-training
  - training-adaptation/synthetic-data-generation
  - training-adaptation/data-curation
  - prompting/zero-shot
  - prompting/few-shot
  - prompting/prompt-templates
  - prompting/chain-of-thought
sources:
  - Wei et al., 2021 — Finetuned Language Models Are Zero-Shot Learners
  - Sanh et al., 2021 — Multitask Prompted Training Enables Zero-Shot Task Generalization
  - Wang et al., 2022 — Self-Instruct: Aligning Language Models with Self-Generated Instructions
  - Chung et al., 2022 — Scaling Instruction-Finetuned Language Models
---

## TL;DR
Instruction tuning is SFT on many different tasks, each phrased as a natural-language instruction, so
the model learns "read an instruction, do what it says" and generalises zero-shot. Task diversity, not
examples per task, drives it.

## Why it exists
- **The capability exists; the interface does not.** A base model does many tasks given few-shot
  examples, because such patterns are common in its training data. But users write "summarise this in two
  lines" or "translate to French, formal register", and a base model may echo that, answer something
  adjacent, or continue the document.
- **One task at a time does not scale.** A summarisation model and a translation model each worked on its
  task and not at all on the next, and the number of tasks users invent is unbounded.
- **Dozens of instruction-phrased tasks transfer.** FLAN and T0 showed that fine-tuning on many tasks
  teaches instruction following on unseen ones, and the effect grows with distinct tasks and model size.
  The model learns the mapping from instruction text to behaviour, which transfers.
- **One huge single-task dataset fails** because the model overfits to that phrasing and task, and there
  is nothing to generalise across. Instruction tuning works precisely because the data is wide and
  shallow.

## How it works
### The data unit
- **Instruction, optional input, output:** "Classify the sentiment of the following review. Review: ...
  Answer: positive".
- **Templates convert academic datasets.** A sentiment set becomes instruction data through several
  phrasings ("Is this review positive or negative?", "What is the sentiment of this text?") and often
  inversion ("Write a negative review about a laptop").
- **Multiple templates per task matter** because the model must not key on exact wording; it has to learn
  the intent.

### Why diversity beats depth
- **The finding (FLAN line):** more *tasks* improve held-out performance more than more examples of the
  same tasks, and per-task gains saturate quickly.
- **The mechanism:** each task is one more point in "instruction space"; generalisation is interpolation
  between those points.
- **The rule of thumb:** past a modest number of examples per task, a new task is worth more than more
  rows of an old one.
- **Balance the mixture.** Sample tasks roughly in proportion to size but cap the large ones
  (examples-proportional mixing with a cap, as the FLAN work did), so a giant dataset does not dominate
  the gradient.

### Mixing prompt formats, not just tasks
| Format | Prompt contains |
|---|---|
| Zero-shot | instruction only |
| Few-shot | instruction plus worked examples |
| Chain-of-thought | an answer with reasoning steps |

Later FLAN work found that training on a mix of these improved both zero-shot and few-shot use versus a
single format, for the same reason as templates: learn the intent, not one surface layout.

### Where the instructions come from
| Source | How | Strength | Risk |
|---|---|---|---|
| **Converted benchmarks** | re-template public NLP datasets | cheap, broad | academic in style |
| **Human-written** | annotators write realistic requests and answers | most useful per example; the only way to get genuine user-style prompts | expensive |
| **Model-generated (Self-Instruct)** | seed tasks → strong model invents and answers new instructions → filter for quality and diversity → repeat | how most open instruction datasets scaled: a few hundred seeds become tens of thousands of examples | student inherits teacher's errors and style; instructions cluster around what the teacher finds easy |

### Reasoning and format data
- **Keep chain-of-thought in the mixture.** It preserves and strengthens multi-step reasoning. Mixtures
  without any reasoning data were found to *hurt* reasoning benchmarks, because the model learns to answer
  tersely.
- **Whatever is absent drifts away:** code, long-form answers, refusals, tool calls. If it is not in the
  mixture, the model drifts from it.

### How it relates to SFT and chat tuning
- **Mechanically it *is* SFT:** next-token loss on output tokens, instruction masked.
- **The difference is intent and data.** SFT covers any demonstration-based fine-tuning, including narrow
  single-task adaptation; instruction tuning targets broad instruction following.
- **Modern chat models** are instruction-tuned with multi-turn conversational data through a chat
  template; the single-turn instruction/input/output format is its ancestor.

### Scale dependence
- **The original FLAN finding:** the benefit appeared only above a certain model size; small models got
  *worse* on held-out tasks.
- **The usual reading:** small models lack spare capacity for both the tasks and the meta-skill, so they
  overfit the seen tasks.
- **Later work with better mixtures narrowed the gap**, but the lesson stands: instruction tuning surfaces
  latent ability, it does not create it.

## Diagram
Follow how many narrow sources become one wide, balanced mixture, and note that evaluation holds out
whole task families rather than rows.

```mermaid
flowchart TD
    BM["Public benchmarks"] -->|"several templates and inversions per task"| P["Instruction pool"]
    HW["Human-written real requests"] --> P
    SD["Seed tasks"] --> SI["Self-Instruct: teacher invents and answers"]
    SI -->|"filter for quality and diversity"| P
    P --> MX["Mix formats: zero-shot, few-shot, chain-of-thought"]
    MX --> CAP["Examples-proportional mixing with a per-task cap"]
    CAP --> SFT["SFT loss: instruction masked, output supervised"]
    SFT --> IT["Instruction-tuned model"]
    HO["Held-out task families and real user requests"] --> EV{"Works on unseen tasks?"}
    IT --> EV
    EV -->|"no: add tasks and phrasings"| P
    EV -->|"yes"| NX["Preference training and safety data"]
```

## Examples
### A miniature FLAN-style mixture
Two task families, several templates each, balanced with a per-task cap. FLAN-style pipelines do this at
a scale of hundreds of tasks.

```python
import random
random.seed(0)

sentiment = [("The battery lasts two days, love it.", "positive"),
             ("Screen cracked in a week.", "negative")]
summar = [("Q3 revenue rose 12% on cloud demand while hardware fell 3%.",
           "Cloud growth drove a 12% Q3 revenue rise despite weaker hardware.")]

templates = {
    "sentiment": ["Classify the sentiment of this review as positive or negative.\nReview: {x}",
                  "Is the following text positive or negative?\n{x}",
                  "What sentiment does this review express?\n{x}"],
    "summarize": ["Summarise in one sentence:\n{x}",
                  "Give a one-line summary of the text below.\n{x}"],
}
raw = {"sentiment": sentiment, "summarize": summar}

def build(cap=1000):
    out = []
    for task, rows in raw.items():
        rows = rows[:cap]                         # cap large tasks so no task dominates
        for x, y in rows:
            for t in random.sample(templates[task], k=min(2, len(templates[task]))):
                out.append({"instruction": t.format(x=x), "output": y, "task": task})
    random.shuffle(out)
    return out

data = build()
for ex in data[:3]:
    print(ex["task"], "|", ex["instruction"].split("\n")[0], "->", ex["output"])
# Each example is then rendered through the chat template and trained with the SFT loss
# (instruction masked, output supervised). Hold out whole tasks, not rows, to measure generalisation.
```

- **Hold out entire tasks for evaluation.** If the model handles a task family it never saw, the skill
  transferred; if it only handles seen tasks, you have a multi-task model, not an instruction-following
  one.

### Worked numbers: why the cap matters
Suppose the real mixture has three tasks with 500,000, 20,000 and 2,000 examples.

| Mixing rule | Task A (500,000) | Task B (20,000) | Task C (2,000) | Effect |
|---|---|---|---|---|
| Proportional to size | ~96% | ~4% | ~0.4% | effectively single-task |
| Cap each at 10,000 | ~45% | ~45% | ~9% | small task becomes visible |

The large task loses examples it did not need. The cap is a knob you tune by watching per-task held-out
scores, not a constant. The same arithmetic as a function:

```python
def mixing_rates(sizes, cap=None):
    """Examples-proportional mixing, optionally capping each task's size first."""
    eff = {t: min(n, cap) if cap else n for t, n in sizes.items()}
    total = sum(eff.values())
    return {t: round(100 * n / total, 1) for t, n in eff.items()}

sizes = {"qa": 500_000, "extraction": 20_000, "rewrite": 2_000}
print(mixing_rates(sizes))              # {'qa': 95.8, 'extraction': 3.8, 'rewrite': 0.4}
print(mixing_rates(sizes, cap=10_000))  # {'qa': 45.5, 'extraction': 45.5, 'rewrite': 9.1}
```

## Production patterns
- **Extend an instruct checkpoint, do not start from scratch.** Most teams take an already
  instruction-tuned open model and add their tasks to a general instruction mixture to keep breadth.
  Monitor a general benchmark suite before and after; a drop means the general share is too small.
- **Hold out task families, not rows.** Tag every example with a task id and reserve whole families (and a
  sample of real user requests) for evaluation. Monitor the gap between seen-task and unseen-task scores;
  a large gap means a multi-task model, not an instruction follower.
- **Write several templates per task, including inversions.** Generate at least two or three phrasings
  per task and evaluate on paraphrases never used in training. Monitor score variance across phrasings
  of the same request; high variance means the model keys on wording.
- **Filter generated instructions for diversity.** When bootstrapping with Self-Instruct, drop a new
  instruction that is too similar to existing ones (the paper used a ROUGE-L overlap threshold of 0.7),
  and have humans review a sample. Monitor the task-type distribution so it does not collapse toward
  what the teacher finds easy.
- **Keep reasoning, long-form and refusal data in every mix.** Reserve a fixed slice for chain-of-thought,
  code, long answers and refusals. Monitor multi-step reasoning benchmarks, mean output length and refusal
  rate, which fall first when these slices are dropped.

## Trade-offs
- **Broad and shallow vs narrow and deep.** Broad mixtures generalise to unseen instructions; narrow ones
  excel at the seen task. When you only ever need one task, the narrow fine-tune wins on that task.
- **Model-generated scale vs teacher limits.** Generated instructions scale cheaply and widen coverage but
  import the teacher's errors and style; the saving reverses if the teacher is weaker than your target or
  your domain is unusual.
- **Templated benchmarks vs real requests.** Templating public benchmarks gives thousands of tasks for
  free, but academic phrasings differ from real user requests, so a slice of genuine human-written
  prompts is worth more per example.
- **Reasoning and long-form data vs cost.** Including them keeps those abilities but lengthens average
  outputs and training cost; omit them only when the deployment never needs them.

## Pitfalls
- **One template per task.** Symptom: the model follows training phrasings exactly and fails when a user
  words the same request differently.
- **Only zero-shot prompts.** Symptom: confusion, or copying the examples' format, when a user pastes a
  few demonstrations into the prompt.
- **One huge dataset dominating the mixture.** Symptom: excellent at that task, quietly regressed on
  everything else, visible only if you evaluate per task family.
- **Held-out rows of seen tasks called generalisation.** The number looks great and says nothing about
  unseen instructions, which is what users send.
- **No chain-of-thought or long answers in the data.** Symptom: terse replies and a drop on multi-step
  reasoning benchmarks after tuning.
- **Treating "instruction tuned" as "aligned".** An instruction-tuned model follows harmful instructions
  as readily as benign ones until preference training and safety data add a refusal policy.

## When to use
- **Use when** you need a model that follows a wide range of requests it has not seen verbatim: a general
  assistant, an internal copilot fielding open-ended tasks, or a base for later preference training.
- **Avoid when** the product has one fixed task; a narrow SFT on that task is simpler and better.
- **In practice** start from an already instruction-tuned open model and add your tasks to a general
  mixture to keep breadth, rather than building the mixture from scratch.
- **Measure** with held-out *task families* and a general benchmark suite.
- **Interviews:** explain why diversity beats depth, how Self-Instruct bootstraps data, and how
  instruction tuning differs from plain SFT and from preference optimisation.

## Role lens
- **Engineer:** Builds the template and mixing pipeline, caps task sizes, holds out task families, and
  verifies the chat template renders every example the way serving will.
- **Architect:** Chooses between adopting an instruction-tuned open model and extending it with domain
  tasks versus narrow tuning, based on how open-ended the product's requests really are.
- **FDE:** Collects the real phrasings a customer's users send and converts them into instruction
  data, because academic templates rarely match how a procurement analyst actually asks.

## Check yourself
**Q:** Your instruction-tuned model scores well on held-out examples from all 40 training tasks, but
users complain it misunderstands simple requests. What is the likely gap in your evaluation and data,
and what would you change?
**A:** Held-out rows from seen tasks only measure multi-task performance; they never test whether the
model generalises to new instructions, which is what users send. The likely causes are too few
distinct tasks and too few phrasings per task, so the model keys on training wording. I would first
re-evaluate on entirely held-out task families and on a sample of real user requests to confirm the
gap, then widen the mixture: add tasks, write several templates per task including inverted forms,
and add a slice of genuine user-style instructions, possibly bootstrapped with Self-Instruct and
filtered by humans. I would also check that the mixture is balanced, since one large task can crowd out
the generalisation signal, and confirm the model is large enough to benefit, because small models
tend to overfit seen tasks.

## Interview questions
### What is instruction tuning, and how is it different from plain SFT?
- **Mechanically it is SFT:** next-token loss on the output, with the instruction masked.
- **The difference is the data and the goal.** It trains on many tasks phrased as natural-language
  instructions, so the model learns the mapping from instruction to behaviour and follows unseen ones
  zero-shot.
- **Plain SFT** also covers narrow single-task adaptation, which does not generalise to new requests.

### You have one 500,000-example task and two small ones. How do you build the mixture?
- **Proportional mixing** gives the big task about 96% of samples, so the result is effectively
  single-task.
- **Cap each task** (for example at 10,000) before proportional mixing; the small tasks then become
  visible at about 45% and 9%.
- **Tune the cap** by watching per-task held-out scores, because it is a knob, not a constant.

### When would you not instruction-tune, and do a narrow fine-tune instead?
- **When the product has one fixed task**, because a narrow SFT on that task is simpler and wins on it.
- **When an existing instruct model already passes your evals** with prompting, since owning a tuned
  model is a maintenance cost.
- **Instruction tuning pays** when requests are open-ended and users will word them in ways you cannot
  enumerate.

### Your tuned model handles training phrasings but fails on paraphrases. What went wrong?
- **Too few templates per task**, so the model keyed on wording instead of intent.
- **Fix the data:** write several phrasings per task including inversions, and add genuine user-style
  requests.
- **Fix the eval:** test on paraphrases and task families never seen in training, and track score
  variance across phrasings of the same request.

### What are the risks of scaling instruction data with Self-Instruct, and how do you control them?
- **The student inherits the teacher's errors and style**, and generated tasks cluster around what the
  teacher finds easy.
- **Controls:** filter new instructions that are too similar to existing ones (the paper used a ROUGE-L
  threshold of 0.7), have humans review a sample, and keep human-written seeds.
- **Monitor** the task-type distribution across bootstrap rounds so diversity does not collapse.

## Quiz
### Q: Why does adding more distinct tasks help instruction tuning more than adding more examples of existing tasks?
- [ ] Larger datasets cause overfitting regardless of task count
- [x] Each new task adds a point in instruction space, and generalisation comes from covering that space, not from depth on one task
- [ ] More tasks reduce the learning rate needed
- [ ] Examples of the same task are usually duplicates
> The target skill is "map an instruction to a behaviour" in general. Depth on one task refines that
> task; breadth across tasks teaches the mapping itself, which is what transfers to unseen instructions.

### Q: What is the main risk of building an instruction dataset with Self-Instruct from a strong teacher model?
- [ ] The resulting data cannot be used with a chat template
- [ ] It produces too few examples to be useful
- [x] The student inherits the teacher's mistakes and style, and generated tasks cluster around what the teacher finds easy
- [ ] The loss must be computed on instruction tokens as well
> Bootstrapping scales data cheaply, but every error and bias in the teacher's answers becomes a training
> target, and diversity narrows toward the teacher's comfort zone unless filtering and human-written
> seeds counteract it.

### Q: After instruction tuning, a model answers multi-step maths questions worse than the base model did with few-shot prompts. What most likely happened?
- [ ] The learning rate was too low to change behaviour
- [x] The mixture contained no chain-of-thought or long-form data, so the model learned to answer tersely
- [ ] Instruction tuning always reduces reasoning ability
- [ ] The chat template removed the ability to use few-shot prompts
> Whatever is absent from the mixture drifts away. Without reasoning traces in the data the model learns
> that outputs are short answers, which suppresses the step-by-step behaviour the base model would show
> when prompted.
