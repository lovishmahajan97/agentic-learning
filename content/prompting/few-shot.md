---
id: few-shot
module: prompting
name: Few-shot prompting
kind: pattern
difficulty: core
est_minutes: 14
related:
  - prompting/zero-shot
  - prompting/prompt-engineering
  - prompting/prompt-caching
  - prompting/prompt-templates
  - prompting/chain-of-thought
  - training-adaptation/fine-tune-vs-rag-vs-prompt-decision
  - rag-retrieval/embedding-models
  - evals/golden-dataset
sources:
  - Brown et al., 2020 — Language Models are Few-Shot Learners
  - Min et al., 2022 — Rethinking the Role of Demonstrations: What Makes In-Context Learning Work?
  - Zhao et al., 2021 — Calibrate Before Use: Improving Few-Shot Performance of Language Models
  - Liu et al., 2021 — What Makes Good In-Context Examples for GPT-3?
---

## TL;DR
Few-shot prompting puts input → output demonstrations in the prompt. The model performs in-context
learning: conditioning on the examples shifts its output distribution toward their format, label space
and style without any weight update. It is how you specify what is easier to show than to say. The price
is tokens on every call and a set of biases the examples bring with them: order, label frequency and
copied surface detail.

## Why it exists
Instructions say what to do; they are poor at conveying how a good answer looks. Try writing rules for the
exact layout of a changelog entry, the tone of a refund email, or where the boundary falls between "bug"
and "feature request" for one particular product. Each rule you add covers one case and introduces a
possible contradiction with another, and the model follows a demonstrated pattern more reliably than a
described one, because pattern continuation is what it was trained on from the first token.

The observation that made this a technique was that sufficiently large pretrained models could learn a
task from a handful of demonstrations in the prompt alone, with no gradient steps. This was striking
because the model had never been trained to learn from examples; the ability emerged from next-token
prediction over text that itself contains many lists, tables and repeated structures.

Two naive alternatives bracket few-shot. Writing more rules fails because rules cannot enumerate a shape.
Fine-tuning fails on loop time: hours to days per iteration versus seconds, and it needs hundreds of
examples before it beats a handful in context. Few-shot is the middle: show the shape, measure, adjust the
examples, and only bake them into weights once volume makes the per-call token cost the larger bill.

## How it works
### What demonstrations actually teach
In-context learning is best understood as task inference: the demonstrations make "do the same thing to
the next input" the overwhelmingly most probable continuation. Experiments that swap demonstration labels
for random ones found, for many models and tasks, that much of the gain survives; what matters most is
that the examples show the input distribution, the label space and the output format. Larger and more
recent models do appear to use the labels more, and the picture varies by task, so treat it as a tendency
rather than a law. The practical reading is the same either way: examples are a format and boundary
specification first and a source of knowledge a distant second. They do not teach the model facts it
lacks.

### How many, and which
Returns diminish quickly; a few to around ten examples is a typical working range (illustrative, not a
rule), and cost grows linearly with each one. What matters more than count is coverage: every label
represented, and the examples drawn from the boundary cases where the zero-shot prompt was inconsistent,
not from the easy middle. An example of an obviously-billing ticket teaches nothing the label definition
did not.

### Static versus dynamic selection
Static examples are hand-picked and fixed, so they form a stable prefix that caches. Dynamic selection
embeds the incoming input, retrieves the k most similar labelled examples from a store and renders those;
it raises accuracy on heterogeneous inputs because each request sees demonstrations that look like it.
The cost is a retrieval step on the request path, an example store to maintain, and a prompt whose
content differs per request, which forfeits prefix caching unless the examples are placed after the
cached block.

### The biases examples introduce
Measured on classification tasks: majority-label bias (the model favours whichever label appears most
among the demonstrations), recency bias (the last example's label is over-predicted) and common-token
bias (labels that are frequent words get a head start). Order alone can swing accuracy substantially on
the same set of examples. Mitigations: balance labels, choose an order by eval rather than by accident,
restate the instruction after the examples, and for classification, calibrate by measuring the model's
output on a content-free input and correcting the decision for the skew it reveals.

### Where the examples live in a chat API
Two placements work. Inside the system prompt as delimited `<example>` blocks, which is simple and clearly
marks them as demonstrations. Or as alternating user/assistant message pairs before the real user turn,
which matches the structure the model was fine-tuned on and tends to anchor format most strongly, at the
risk that the model treats them as real conversation history. Either way, label them as examples and keep
the instructions distinct from them.

### Examples beat instructions when they conflict
If an instruction says "respond in one sentence" and the examples show three, the model will produce
three. The demonstrated pattern is the stronger signal, so every example must comply with every rule.

## Example
Dynamic few-shot selection: a small labelled pool is embedded once, the four nearest neighbours of each
incoming ticket are rendered as user/assistant pairs ahead of the real request, and the model copies
their format. In production the pool would be a few hundred curated boundary cases, balanced by label,
and the embedding lookup would be a vector index rather than a dot product over a list.

```python
import anthropic, json
import numpy as np
from sentence_transformers import SentenceTransformer

client, MODEL = anthropic.Anthropic(), "claude-opus-5-5"
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# Labelled pool: in practice a few hundred boundary cases curated from real traffic, balanced by label.
POOL = [("Charged twice for March", "billing"), ("Refund never arrived", "billing"),
        ("Export button does nothing on Safari", "bug"), ("Dashboard shows yesterday's numbers", "bug"),
        ("Please add dark mode", "feature_request"), ("Could reports be scheduled weekly?", "feature_request"),
        ("Thanks, all working now", "other"), ("What is your office address?", "other")]
POOL_VECS = embedder.encode([t for t, _ in POOL], normalize_embeddings=True)

SYSTEM = ("Classify the ticket into one of: billing, bug, feature_request, other. "
          "The examples show where the boundaries fall; copy their format exactly: JSON {\"label\": ...} only.")

def classify(ticket, k=4):
    q = embedder.encode([ticket], normalize_embeddings=True)[0]
    nearest = np.argsort(-(POOL_VECS @ q))[:k]                     # cosine similarity, top-k
    messages = []
    for i in nearest:                                             # demonstrations as user/assistant pairs
        text, label = POOL[i]
        messages += [{"role": "user", "content": f"<ticket>{text}</ticket>"},
                     {"role": "assistant", "content": json.dumps({"label": label})}]
    messages.append({"role": "user", "content": f"<ticket>{ticket}</ticket>"})
    r = client.messages.create(model=MODEL, max_tokens=64, system=SYSTEM, messages=messages)
    return json.loads(next(b.text for b in r.content if b.type == "text"))["label"]

print(classify("My invoice total is wrong and the PDF download is broken"))
# Inspect the neighbours' labels before trusting a result: if all k share one label,
# majority-label bias is in play and k or the pool balance needs adjusting.
```

## Trade-offs
- Specification by demonstration against tokens on every call; the balance flips toward fine-tuning once
  volume is high and the behaviour is stable, because fine-tuning is effectively few-shot with the examples
  moved into the weights.
- Dynamic selection raises accuracy on diverse inputs against a retrieval step, an example store and a
  per-request prompt that caching cannot reuse; on homogeneous inputs a static, cached set is cheaper and
  nearly as good.
- Examples anchor format strongly against the model copying their content (names, numbers, phrasing); for
  creative or diverse outputs that anchoring is a defect, and zero-shot or lighter guidance is better.
- More examples stabilise the boundary until context cost and position effects take over; past a handful
  the marginal example costs more than it adds, and long example lists push the real input toward the
  middle of the context where it is used least reliably.

## Pitfalls
- Unbalanced labels among the examples; the symptom is systematic over-prediction of the majority label,
  visible as a skewed confusion matrix.
- Examples that violate the instruction they accompany; the symptom is the model following the examples
  while the rule appears to be ignored.
- Choosing only clear-cut examples; the symptom is boundary cases failing exactly as they did zero-shot.
- Entities or numbers from the examples appearing in outputs for unrelated inputs; the symptom is
  hallucinations that look suspiciously like your demonstrations.
- Evaluating on the same items used as demonstrations; the symptom is an eval score that collapses on
  production traffic.
- Rotating or re-ordering examples per request without need; the symptom is a cache hit rate near zero and
  bills that do not drop after caching is enabled.

## When to use
Reach for few-shot after a precise zero-shot prompt is still inconsistent on cases humans label
consistently, when the output format is intricate, when the tone or style is hard to describe, or when
labels are domain-specific and their boundaries are better shown than defined. Do not use it when outputs
should be diverse, when every token matters at very high volume (fine-tune instead), or as a substitute
for missing knowledge (retrieve instead). Measure the delta against the zero-shot baseline on a held-out
set, the per-label confusion matrix for bias, tokens per request and, if examples are dynamic, retrieval
latency. In interviews, be ready to explain what in-context learning actually conditions on and to name
the three biases and their fixes.

## Role lens
- **Engineer:** Curates a balanced example set from real boundary cases, renders it as cached message
  pairs or a retrieval-selected block, and watches the confusion matrix for label bias.
- **Architect:** Decides when per-call example tokens cost more than fine-tuning, and whether an example
  store and its retrieval step belong on the request path at all.
- **FDE:** Runs labelling sessions with the customer's experts to harvest the boundary cases that become
  demonstrations, then holds out a separate set to prove the gain on their data.

## Check yourself
**Q:** A classifier uses six fixed examples, five labelled "bug" and one "billing", in that order. In
production it over-predicts "bug", including for clear billing tickets. Explain the cause and how you would
fix and verify it.
**A:** Two documented biases are stacked. Majority-label bias: five of six demonstrations carry the same
label, so "do the same thing" favours it before the input is even read. Recency bias: if a "bug" example is
last, its label gets a further boost. The fix is to balance the set, at least one strong example per label
and more for the labels with fuzzy boundaries, choose the order by eval rather than leaving it to chance,
and restate the label definitions after the examples. For a classification task I would also calibrate:
send a content-free input, observe the label distribution it produces, and correct the decision for that
skew. Verification is a per-label confusion matrix on a held-out set containing none of the
demonstrations, compared against the zero-shot baseline, plus a check that the examples are rendered in a
stable prefix so caching still works.

## Quiz
### Q: What do few-shot demonstrations mainly teach the model?
- [ ] New facts about the domain that the model did not have
- [x] The output format, the label space and what inputs look like, so the correct continuation is to do the same thing to the next input
- [ ] A better tokenisation of the input
- [ ] The weights of a small classifier head trained during the request
> No weights change. In-context learning is task inference from the pattern in the prompt. Examples
> specify shape and boundaries; they do not supply missing knowledge, which needs retrieval or fine-tuning.

### Q: What is the main cost of selecting examples dynamically by embedding similarity?
- [ ] The model can no longer see the instruction
- [ ] It requires fine-tuning the embedding model on every request
- [x] A retrieval step on the request path and per-request prompt content that forfeits prefix caching unless the examples come after the cached block
- [ ] It forces all the examples to share one label
> Dynamic selection improves relevance on heterogeneous inputs, but each request now renders different
> demonstrations, so the cached static prefix must end before them and the retrieval latency is paid on
> every call.

### Q: An instruction says "answer in one sentence" but the three examples each answer in a paragraph. What will the model most likely do?
- [ ] Follow the instruction, because instructions outrank examples
- [x] Answer in a paragraph, because the demonstrated pattern is the stronger signal
- [ ] Refuse because the prompt is contradictory
- [ ] Alternate between the two formats
> Pattern continuation is what the model does natively; a demonstrated format dominates a described one.
> Every example must comply with every rule, or the rule is effectively overwritten.
