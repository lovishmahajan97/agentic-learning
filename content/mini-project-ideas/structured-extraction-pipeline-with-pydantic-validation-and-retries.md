---
id: structured-extraction-pipeline-with-pydantic-validation-and-retries
module: mini-project-ideas
name: structured extraction pipeline with Pydantic validation and retries
kind: practice
difficulty: intro
est_minutes: 20
status: typed
lesson_type: lab
agent_relevance: core
related:
  - python-for-ai/pydantic
  - python-for-ai/instructor
  - prompting/structured-outputs-json-mode
  - serving-inference/structured-constrained-generation
  - agents/typed-tool-contracts
  - evals/golden-dataset
  - serving-inference/retry-strategies
sources:
  - Pydantic documentation (models, validators, JSON schema)
  - Instructor documentation
  - OpenAI API documentation (structured outputs and JSON mode)
---

## TL;DR
Build a pipeline that turns messy documents (invoices, receipts or job posts) into validated Pydantic objects,
feeds validation errors back to the model for bounded retries, and reports field-level accuracy on a labelled set.

## For agent builders
- **Skills it exercises.** Pydantic v2 models, field and model validators, JSON Schema generation, provider
  structured-output modes, error-feedback retries, and field-level evaluation.
- **Earlier lessons it uses.** Pydantic and Instructor for the libraries, structured outputs and constrained
  generation for the provider side, typed tool contracts for why the same models guard tool arguments.
- **Why it matters for agents.** Every tool call is a structured extraction: the model fills arguments that your code
  must trust. The validate-then-retry loop here is the same loop that guards tool inputs.
- **What you can skip.** OCR and PDF layout parsing. Start from plain text; add parsing as a stretch goal.

## Lab
### Task
Build the core of the extraction pipeline against a recorded model: two business-rule validators, a
bounded retry loop that sends the exact validation error back, a dead-letter queue for documents that use
up the budget, and per-field accuracy against labels. Time box: 45-60 min.
- **Prerequisites:** Pydantic; Structured outputs and JSON mode; Typed tool contracts; Retry strategies.
- **Setup:** Python 3.10+, `pip install pydantic pytest` (checked with pydantic 2.14.1, pytest 9.1,
  Python 3.12). Save the starter as `lab.py` and the tests as `test_lab.py`.
- **No API key:** `ScriptedLLM` replays recorded replies and keeps every message list it received, so the
  tests can check what feedback the model would have seen. Swap in a real `llm` callable afterwards.

### Starter code
```python
"""Lab: validate, retry with error feedback, dead-letter and score field accuracy. Run: python lab.py"""
import json
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

class LineItem(BaseModel):
    description: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)

class Invoice(BaseModel):
    vendor: str
    invoice_number: str
    issue_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[LineItem] = Field(min_length=1)
    total: Decimal
    # YOUR CODE HERE: two business rules as validators
    # - vendor: strip whitespace; a blank vendor raises ValueError("vendor must not be blank")
    # - total must equal sum(quantity * unit_price) within 0.01, else
    #   ValueError(f"total {total} != sum of items {expected}")

class ScriptedLLM:
    """A recorded model: returns its replies in order and keeps every message list it was sent."""
    def __init__(self, replies: list[str]):
        self.replies, self.seen = list(replies), []
    def __call__(self, messages: list[dict]) -> str:
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)

DEAD_LETTERS: list[dict] = []

def extract(text: str, llm, max_attempts: int = 3) -> tuple[Invoice | None, int]:
    """Return (invoice or None, attempts used). On failure append
    {"text": text, "attempts": [raw replies], "error": str(last error)} to DEAD_LETTERS."""
    messages = [{"role": "system", "content": "Extract the invoice as JSON matching this schema: "
                 + json.dumps(Invoice.model_json_schema())},
                {"role": "user", "content": text}]
    # YOUR CODE HERE: up to max_attempts calls; validate with Invoice.model_validate_json; on
    # ValidationError append the reply as an assistant message and a user message containing
    # "Validation failed:\n{error}\nReturn corrected JSON only."
    raise NotImplementedError

def field_accuracy(preds: list[Invoice | None], labels: list[dict], fields: list[str]) -> dict:
    # YOUR CODE HERE: for each field, the share of documents whose prediction equals the label
    # (compare str(getattr(pred, field)) with str(label[field])); a None prediction is wrong
    raise NotImplementedError

def doc(vendor="Acme", total="20.00", number="INV-1"):
    return json.dumps({"vendor": vendor, "invoice_number": number, "issue_date": "2025-03-01",
                       "currency": "EUR", "items": [{"description": "pens", "quantity": 4,
                       "unit_price": "5.00"}], "total": total})

if __name__ == "__main__":
    runs = [ScriptedLLM([doc(vendor="Acme GmbH")]),
            ScriptedLLM([doc(total="25.00", number="INV-2"), doc(number="INV-2")]),
            ScriptedLLM([doc(vendor="  "), doc(vendor=""), doc(vendor=" ")])]
    labels = [{"vendor": "Acme", "total": "20.00", "invoice_number": n} for n in ("INV-1", "INV-2", "INV-3")]
    results = [extract(f"invoice text {i}", llm) for i, llm in enumerate(runs)]
    preds = [inv for inv, _ in results]
    print("parsed:", sum(p is not None for p in preds), "of", len(preds),
          "| attempts:", [n for _, n in results])
    print("feedback sent:", "total 25.00 != sum of items 20.00" in runs[1].seen[1][-1]["content"])
    print("dead letters:", len(DEAD_LETTERS), "| last error has rule:",
          "vendor must not be blank" in DEAD_LETTERS[0]["error"])
    acc = field_accuracy(preds, labels, ["vendor", "total", "invoice_number"])
    print({f: round(v, 2) for f, v in acc.items()})
```

### Expected output
`python lab.py` with a correct solution prints exactly the lines below. The parse rate is 2 of 3, yet
`vendor` is right on only 1 of 3 documents: "Acme GmbH" is valid JSON and passes every rule, and only the
labels catch it.

```
parsed: 2 of 3 | attempts: [1, 2, 3]
feedback sent: True
dead letters: 1 | last error has rule: True
{'vendor': 0.33, 'total': 0.67, 'invoice_number': 0.67}
```

### Tests
Run `pytest -q test_lab.py`. On the starter all 5 fail: the rules test with `AssertionError` (the vendor is
not stripped and nothing is rejected), the rest with `NotImplementedError`. A correct solution passes all 5.

```python
import json
import pytest
from decimal import Decimal
from pydantic import ValidationError
import lab
from lab import Invoice, ScriptedLLM, doc, extract, field_accuracy

@pytest.fixture(autouse=True)
def clean():
    lab.DEAD_LETTERS.clear()

def test_business_rules_reject_bad_documents():
    assert Invoice.model_validate_json(doc(vendor="  Acme ")).vendor == "Acme"
    with pytest.raises(ValidationError, match="vendor must not be blank"):
        Invoice.model_validate_json(doc(vendor="   "))
    with pytest.raises(ValidationError, match=r"total 21.00 != sum of items 20.00"):
        Invoice.model_validate_json(doc(total="21.00"))
    assert Invoice.model_validate_json(doc(total="20.005")).total == Decimal("20.005")   # within 0.01

def test_first_valid_reply_uses_one_attempt():
    inv, n = extract("t", ScriptedLLM([doc()]))
    assert n == 1 and inv.total == Decimal("20.00")

def test_retry_sends_the_exact_error_back():
    llm = ScriptedLLM([doc(total="25.00"), "not json at all", doc()])
    inv, n = extract("t", llm)
    assert inv is not None and n == 3
    second = llm.seen[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == doc(total="25.00")
    assert "total 25.00 != sum of items 20.00" in second[3]["content"]
    assert "Return corrected JSON only." in second[3]["content"]

def test_budget_spent_goes_to_dead_letters():
    llm = ScriptedLLM([doc(vendor=""), doc(vendor=" "), doc(vendor="\t"), doc()])
    inv, n = extract("broken doc", llm, max_attempts=3)
    assert inv is None and n == 3 and len(llm.replies) == 1            # 4th reply never requested
    (dl,) = lab.DEAD_LETTERS
    assert dl["text"] == "broken doc" and len(dl["attempts"]) == 3
    assert "vendor must not be blank" in dl["error"]

def test_field_accuracy_counts_missing_predictions_as_wrong():
    a = Invoice.model_validate_json(doc())
    b = Invoice.model_validate_json(doc(number="INV-9"))
    labels = [{"vendor": "Acme", "invoice_number": "INV-1"}] * 3
    acc = field_accuracy([a, b, None], labels, ["vendor", "invoice_number"])
    assert acc == {"vendor": pytest.approx(2 / 3), "invoice_number": pytest.approx(1 / 3)}
```

### Failure scenarios
- **Retry without feedback** (call `llm(messages)` again unchanged). The retry test fails on the role
  sequence; with a real model the same wrong total comes back. Log attempts per document and compare the
  mean before and after adding feedback.
- **Compare `total` with `==`.** The `20.005` case is rejected, and real invoices with rounding differences
  burn every retry. Print `expected - self.total` in the error so the size of the gap is visible.
- **Dead-letter only the error string.** The dead-letter test fails on `attempts`, and in production you
  cannot tell a bad document from a bad prompt. Keep every raw reply, then read three dead letters by hand.
- **Divide accuracy by parsed documents only.** In the demo `vendor` reads 0.5 instead of 0.33, the accuracy
  test fails, and failures inflate the score. Use the labelled set size as the denominator, and report parse rate separately.

### Submission
- **`lab.py`** with the validators, `extract` and `field_accuracy` done; the recorded replies unchanged.
- **A green test run** (5 passed) and the output of `python lab.py`.
- **One real run:** wrap a provider SDK as `llm` on 10 of your documents and paste parse rate, mean
  attempts and the per-field table.
- **A short note:** one field the validators cannot protect, and how you would catch it.
- **Evaluated on:** tests passing, exact error text in the retry message, bounded attempts and per-field
  numbers over the whole labelled set.

## Why it exists
- **"Valid JSON" is not "correct data".** Schema-constrained output guarantees shape, not that the total equals the
  line items or the date is real. Business rules need validators.
- **Retries without feedback waste money.** Re-asking the same prompt often returns the same mistake; sending the
  exact validation error usually fixes it in one extra call.
- **Unbounded retries hide bad inputs.** A cap (for example 3 attempts) plus a dead-letter file turns silent loops
  into a review queue with numbers.
- **Field accuracy is the honest metric.** "92% of documents parsed" can hide that `total` is wrong on 1 in 5;
  per-field accuracy on 30-50 labelled documents shows where to work.

## How it works
### Stack and data
- **Python 3.11+, Pydantic v2,** one provider SDK, optionally Instructor to compare against your own loop.
- **Data:** 30-50 documents of one type, from public sample invoices or synthetic ones you generate, each with a
  hand-written expected JSON. Include 5 that are deliberately broken (missing totals, two currencies).

### The loop
1. **Prompt** with the model's JSON Schema (`Invoice.model_json_schema()`) and the document text.
2. **Parse and validate** with `Invoice.model_validate_json(raw)`; field constraints and validators run here.
3. **On `ValidationError`,** append the model's reply and the error text, ask for corrected JSON, and try again.
4. **Stop** on success or after `max_attempts`; failures go to a dead-letter file with every attempt.

### Milestones
1. **Schema.** Models with types, constraints and two business validators. Acceptance: unit tests reject 6 bad examples.
2. **Single-shot extraction.** One call per document. Acceptance: a run over the set writes results and a parse rate.
3. **Retry with feedback.** The loop above. Acceptance: parse rate and average attempts reported before and after.
4. **Labelled eval.** Compare each field with the expected JSON. Acceptance: a per-field accuracy table.
5. **Dead letters and cost.** Log attempts, tokens and failures. Acceptance: cost per document and failure list in README.
6. **Compare approaches.** Your loop vs Instructor vs the provider's strict structured-output mode. Acceptance: one table.

## Diagram
Validation sits between the model and your data; errors loop back with their exact messages until the attempt
budget runs out.

```mermaid
flowchart TD
    D["Document text"] --> P["Prompt with JSON Schema"]
    P --> M["Model"]
    M --> V["Pydantic validate"]
    V -->|"valid"| O["Typed Invoice object"]
    V -->|"ValidationError"| F["Append error, ask for fix"]
    F -->|"attempt under 3"| M
    F -->|"budget spent"| DL[("Dead-letter file")]
    O --> E["Field-level eval vs labels"]
```

## Examples
### Starter code: models, validators and the retry loop
The model call is injected, so the loop is unit-testable with a fake.

```python
import json
from datetime import date
from decimal import Decimal
from typing import Callable
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

class LineItem(BaseModel):
    description: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)

class Invoice(BaseModel):
    vendor: str
    invoice_number: str
    issue_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[LineItem] = Field(min_length=1)
    total: Decimal

    @field_validator("vendor")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("vendor must not be blank")
        return v.strip()

    @model_validator(mode="after")
    def total_matches_items(self):
        expected = sum(i.quantity * i.unit_price for i in self.items)
        if abs(expected - self.total) > Decimal("0.01"):
            raise ValueError(f"total {self.total} != sum of items {expected}")
        return self

def extract(text: str, llm: Callable[[list[dict]], str], max_attempts: int = 3) -> Invoice:
    schema = json.dumps(Invoice.model_json_schema())      # real JSON, not a Python dict repr
    messages = [{"role": "system", "content": f"Extract the invoice as JSON matching this schema: {schema}"},
                {"role": "user", "content": text}]
    last_error = None
    for attempt in range(1, max_attempts + 1):
        raw = llm(messages)
        try:
            return Invoice.model_validate_json(raw)
        except ValidationError as e:
            last_error = e
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": f"Validation failed:\n{e}\nReturn corrected JSON only."}]
    raise RuntimeError(f"extraction failed after {max_attempts} attempts: {last_error}")
```

A real `llm` can wrap the OpenAI SDK: `client.chat.completions.create(model=..., messages=messages,
response_format={"type": "json_object"})` and return `choices[0].message.content`.

### The same extraction with Instructor
Instructor wraps a client so `response_model` and `max_retries` run a similar validate-and-retry loop for you.

```python
import instructor
from openai import OpenAI

client = instructor.from_openai(OpenAI())
invoice = client.chat.completions.create(
    model="your-model-id",            # a current model ID; check the docs
    response_model=Invoice,
    max_retries=3,
    messages=[{"role": "user", "content": document_text}],
)
```

Check the current Instructor docs for the client constructor for your provider; it has changed across versions.

### Milestone plan with time estimates
| Milestone | Time | Done when |
|---|---|---|
| 1. Schema and validators | 2 h | 6 bad examples rejected in unit tests |
| 2. Single-shot run | 1-2 h | parse rate printed for the whole set |
| 3. Retry with feedback | 2 h | parse rate and mean attempts before vs after |
| 4. Field-level eval | 2-3 h | accuracy per field, worst field named |
| 5. Dead letters and cost | 2 h | cost per document and failure file in README |
| 6. Compare approaches | 2-3 h | own loop vs Instructor vs strict mode table |

## Production patterns
- **Constrain shape at the provider, rules in Pydantic.** Use the provider's strict structured-output or JSON mode so
  syntax errors vanish, and keep cross-field rules (totals, date order) in validators, because no decoder enforces them.
- **Bounded retries with the real error.** Send the `ValidationError` text back, cap at 2-3 attempts, and record the
  attempt count per document. A rising mean attempt count is an early signal of prompt or input drift.
- **Dead-letter queue, not silent drops.** Write failed documents with all attempts to a file or table for review,
  and report the dead-letter rate in every run.
- **Field-level eval in CI.** Keep the labelled set in the repo and fail the build when any key field's accuracy
  drops, because a prompt change can fix `vendor` while breaking `total`.
- **Normalise before comparing.** Compare decimals as numbers, dates as dates and strings case-folded, so the eval
  measures extraction errors rather than formatting differences.

## Trade-offs
- **Own loop vs Instructor.** Your loop is 20 lines you fully understand; Instructor adds provider adapters, streaming
  partials and hooks, at the cost of another dependency whose API moves.
- **Strict schema mode vs prompt-only JSON.** Strict mode removes parse failures but supports a subset of JSON Schema
  on some providers; prompt-only works everywhere but needs more retries.
- **More retries vs cost and latency.** Each retry costs a full call; after 2-3 attempts the input is usually the
  problem, so send it to review instead.
- **Rich validators vs extraction recall.** Strict rules reject bad data but also reject odd-but-valid documents;
  measure how many real documents each rule rejects.

## Pitfalls
- **Retrying without the error message.** The model repeats the same mistake and attempts hit the cap. Include the
  exact validation error and the previous reply.
- **Floats for money.** `0.1 + 0.2` is not exactly 0.3 in binary floats, so exact totals checks fail at random and
  a loose tolerance can hide real cent errors. Use `Decimal`.
- **Measuring parse rate only.** Valid but wrong values pass unnoticed. Score each field against labels.
- **Labels written from model output.** Copying the model's answer into the expected file bakes its errors into the
  eval. Label from the source document.
- **Schema too permissive.** Everything `Optional[str]` always validates and teaches you nothing. Use real types and constraints.

## When to use
- **Prerequisites.** Python typing, the Pydantic lesson, and one provider API key.
- **Suits.** A first agent project for engineers coming from backend work, and anyone building document workflows.
- **Avoid when.** Documents follow a fixed template; a regex or template parser is cheaper and exact.
- **Measure.** Parse rate, mean attempts, per-field accuracy, dead-letter rate and cost per document.

## Role lens
- **Engineer:** Writes typed models and validators, the bounded retry loop and a field-level eval, and can show
  which field fails most and why.
- **Architect:** Standardises the pattern for every model-produced structure, tool arguments included: provider
  constraints for shape, validators for rules, budgets for retries.
- **FDE:** Builds the labelled set with the customer's real documents in the first week and reports field accuracy
  they can compare with manual entry.

## Check yourself
**Q:** Your extraction pipeline reports a 98% parse rate, but finance says one in six invoice totals is wrong. What is
missing, and what do you add?
**A:** Parse rate only measures that output matched the schema, not that values are correct. Add a labelled set and
per-field accuracy so `total` is measured directly. Add a model validator that checks the total against the sum of line
items (using `Decimal`), so mismatches trigger a retry with the error text instead of passing. Send documents that still
fail after the retry budget to a dead-letter queue, and track total accuracy and dead-letter rate in CI.

## Interview questions
### Why validate with Pydantic if the provider already supports structured outputs?
- **Structured outputs guarantee shape,** not business rules: a total that disagrees with line items or a date in the
  future still conforms to the schema.
- **Pydantic validators enforce those rules** in code you test, and their error messages make good retry feedback.

### How did you design the retry loop and its budget?
- **Each retry includes the previous reply and the exact validation error,** which usually fixes it in one more call.
- **I capped attempts at 3,** because further retries rarely helped in my eval, and sent remaining failures to a dead-letter file.

### How do you evaluate an extraction system?
- **A labelled set of real documents** with expected values, compared field by field after normalising numbers, dates and strings.
- **Report per-field accuracy, parse rate, mean attempts and cost,** and gate releases on the fields that matter most.

### When would you choose Instructor over your own loop?
- **When you need several providers,** streaming partial objects or hooks for logging, since it packages those around the same pattern.
- **Keep your own loop** when the dependency's API churn costs more than 20 lines of code you control.

### How does this relate to tool calling in agents?
- **Tool arguments are structured extraction:** the model fills a schema your code must trust before executing.
- **The same models and validators guard tools,** returning validation errors as tool results so the agent corrects itself.

## Quiz
### Q: A provider's strict JSON Schema mode is enabled. Which error can still reach your code?
- [ ] Invalid JSON syntax
- [ ] A missing required field
- [x] A total that does not equal the sum of the line items
- [ ] A string where an integer is required
> Schema-constrained decoding enforces shape and types, but cross-field business rules are invisible to it. A Pydantic
> model validator catches the mismatch.

### Q: What makes a retry after a validation failure most likely to succeed?
- [ ] Raising the temperature
- [ ] Sending the same prompt again
- [x] Including the previous reply and the exact validation error in the next request
- [ ] Removing the schema from the prompt
> The model needs to know what was wrong. The error text points at the field and rule, so the corrected reply usually
> passes on the next attempt.

### Q: Why use `Decimal` rather than `float` for invoice amounts?
- [ ] `Decimal` is faster
- [x] Floating-point rounding makes exact money comparisons fail unpredictably
- [ ] JSON cannot store floats
- [ ] Pydantic rejects floats
> Binary floats cannot represent many decimal amounts exactly, so totals that should match differ by tiny amounts and
> trigger false validation failures.

## Use cases & projects
### Use cases
- **Accounts-payable intake.** Extract invoices from email bodies and attachments, with totals checks and a review
  queue for dead letters.
- **Typed tool arguments.** Reuse the same validate-and-retry wrapper around every tool call in the CLI assistant.
- **Resume or job-post parsing.** Extract skills, years and locations into a searchable table, with per-field accuracy.
- **PDF and scanned input.** Add a document parser and measure how much accuracy drops versus clean text.
- **Small-model extraction.** Run the same eval with a small local model and report where it matches a large one.

### Mini project
**Warm-up: one model, ten documents (1-2 hours).**
- **Goal:** see validation errors drive successful retries on real model output.
- **Steps:** define a three-field model (for example `vendor`, `date`, `total`) with one validator; write 10 short
  documents and their expected values; run single-shot, then with the retry loop; count parse failures and wrong fields.
- **Done when:** you have a two-row table (single-shot vs retry) of parse rate and field accuracy, and one logged example
  of an error message that the model fixed on its second attempt.
