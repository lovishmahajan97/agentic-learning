---
id: instruction-tuning
module: training-adaptation
name: Instruction tuning
kind: concept
difficulty: core
est_minutes: 14
status: fact-checked
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
Instruction tuning is SFT with a particular data design: many different tasks, each written as a
natural-language instruction with its answer, so the model learns the general skill "read an
instruction, do what it says" rather than any one task. The payoff is zero-shot generalisation to
instructions it never saw. Task diversity, not examples per task, is what drives it, which is why
building the mixture is most of the work.

## Why it exists
A base model can do many tasks if you show it a few examples in the prompt, because few-shot patterns
are common in its training data. But users do not write few-shot prompts. They write "summarise this
in two lines" or "translate to French, formal register", and a base model given that instruction may
echo it, answer something adjacent, or continue the document. The capability exists; the *interface*
does not.

The first attempt at fixing this was fine-tuning on one task at a time: a summarisation model, a
translation model. Each worked on its task and not at all on the next one, and the number of tasks
users invent is unbounded. The question that produced instruction tuning was: if we fine-tune on
*dozens* of tasks, each phrased as an instruction, does the model learn to follow instructions for
tasks it has never seen? FLAN and T0 showed that it does, and that the effect grows with the number of
distinct tasks and the size of the model. The model is not learning summarisation or translation; it
is learning the mapping from instruction text to behaviour, which transfers.

The naive alternative, a single huge dataset for one task phrased one way, fails because the model
overfits to that phrasing and that task, and because there is nothing for it to generalise across.
Instruction tuning works precisely because the data is wide and shallow.

## How it works
### The data unit
Each example is an instruction, an optional input, and an output: "Classify the sentiment of the
following review. Review: ... Answer: positive". Classic academic datasets are converted into this
form by writing *templates*: a sentiment dataset becomes instruction-tuning data through several
phrasings ("Is this review positive or negative?", "What is the sentiment of this text?"), and often
through inversion ("Write a negative review about a laptop"). Multiple templates per task matter
because the model must not key on the exact wording; it has to learn the intent.

### Why diversity beats depth
Experiments across the FLAN line found that adding more *tasks* improves held-out performance more
than adding more examples of the same tasks, and that gains saturate quickly per task. The mechanism
is that each task teaches the model one more point in "instruction space", and generalisation is
interpolation between those points. As a rule of thumb from those ablations, a mixture of hundreds of tasks
capped at a few thousand examples each generalises better than a handful of tasks with a hundred
thousand each. The same logic says that mixtures should be *balanced*:
sample tasks roughly in proportion to their size but cap the large ones, so a giant dataset does not
dominate the gradient.

### Where the instructions come from
- **Converted benchmarks.** Public NLP datasets re-templated; cheap, broad, but academic in style.
- **Human-written.** Annotators write realistic requests and answers; expensive, the most useful per
  example, and the only way to get genuine user-style prompts.
- **Model-generated (Self-Instruct).** Start from a seed set of hand-written tasks, prompt a strong
  model to invent new instructions and answer them, filter for quality and diversity, repeat. This is
  how most open instruction datasets scaled: a few hundred seeds become tens of thousands of examples.
  The risk is that the student inherits the teacher's errors and style, and that generated instructions
  cluster around what the teacher finds easy.

### Reasoning and format data
Including chain-of-thought examples in the mixture preserves and strengthens multi-step reasoning;
instruction mixtures without any reasoning data were found to *hurt* reasoning benchmarks, because the
model learns to answer tersely. The same holds for anything you want to survive the tuning: code,
long-form answers, refusals, tool calls. If it is not in the mixture, the model drifts away from it.

### How it relates to SFT and chat tuning
Mechanically, instruction tuning *is* supervised fine-tuning: next-token loss on the output tokens
with the instruction masked. The difference is intent and data: SFT as a term covers any
demonstration-based fine-tuning, including narrow single-task adaptation, while instruction tuning
specifically targets broad instruction following. Modern chat models are instruction-tuned with
multi-turn conversational data through a chat template; the single-turn instruction/input/output
format is the ancestor of that.

### Scale dependence
The original FLAN work found the benefit of instruction tuning appeared only above a certain model
size, with small models actually getting worse on held-out tasks. The usual reading is that small
models lack the spare capacity to learn both the tasks and the meta-skill, so they overfit the seen
tasks. Later work with better mixtures narrowed this gap, but the lesson stands: instruction tuning
surfaces latent ability, it does not create it.

## Example
A miniature instruction mixture built from two task families with several templates each, balanced
with a per-task cap. This is the shape of what FLAN-style pipelines do at a scale of hundreds of tasks.

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

Holding out entire tasks is the evaluation that matters: if the model handles a task family it never
saw in training, the instruction-following skill transferred; if it only handles seen tasks, you have a
multi-task model, not an instruction-following one.

## Trade-offs
- Broad, shallow mixtures generalise to unseen instructions, while narrow deep ones excel at the seen
  task; when you only ever need one task, the narrow fine-tune wins on that task.
- Model-generated instructions scale cheaply and widen coverage, but import the teacher's errors and
  style, so the saving reverses if the teacher is weaker than your target or your domain is unusual.
- Templating public benchmarks gives thousands of tasks for free, yet academic phrasings differ from
  real user requests, so a slice of genuine human-written prompts is worth more per example.
- Including reasoning and long-form data keeps those abilities but lengthens average outputs and
  training cost; omit them only when the deployment never needs them.

## Pitfalls
- Using one template per task; the symptom is a model that follows training phrasings exactly and
  fails when a user words the same request differently.
- Letting one huge dataset dominate the mixture; the symptom is a model that is excellent at that task
  and has quietly regressed on everything else, visible only if you evaluate per task family.
- Evaluating on held-out rows of seen tasks and calling it generalisation; the number looks great and
  says nothing about unseen instructions, which is what users send.
- Training on instruction data with no chain-of-thought or long answers; the symptom is terse replies
  and a drop on multi-step reasoning benchmarks after tuning.
- Treating "instruction tuned" as "aligned"; an instruction-tuned model follows harmful instructions as
  readily as benign ones until preference training and safety data add a refusal policy.

## When to use
Instruction tuning is the right tool when you need a model that follows a wide range of requests it has
not seen verbatim: a general assistant, an internal copilot that fields open-ended tasks, or a base for
later preference training. It is overkill when the product has one fixed task, where a narrow SFT on
that task is simpler and better. In practice most teams do not build the mixture from scratch; they
start from an already instruction-tuned open model and add their own tasks to a general mixture to
keep breadth. Measure with held-out *task families* and a general benchmark suite. In interviews, be
ready to explain why diversity beats depth, how Self-Instruct bootstraps data, and how instruction
tuning differs from both plain SFT and preference optimisation.

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
