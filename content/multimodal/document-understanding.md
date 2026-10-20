---
id: document-understanding
module: multimodal
name: Document understanding
kind: concept
difficulty: advanced
est_minutes: 16
status: fact-checked
related:
  - multimodal/ocr
  - multimodal/vision-language-models
  - multimodal/image-embeddings
  - rag-retrieval/document-parsing
  - data-architecture/document-processing-pipelines
  - enterprise-integration/client-document-parsing
  - prompting/structured-outputs-json-mode
  - evals/golden-dataset
sources:
  - Xu et al., 2020 — LayoutLM: Pre-training of Text and Layout for Document Image Understanding
  - Huang et al., 2022 — LayoutLMv3: Pre-training for Document AI with Unified Text and Image Masking
  - Kim et al., 2022 — OCR-free Document Understanding Transformer (Donut)
  - Mathew et al., 2021 — DocVQA: A Dataset for VQA on Document Images
---

## TL;DR
Document understanding is turning pages (invoices, contracts, forms, reports) into structured,
verifiable information: classified, with fields extracted, tables recovered and questions answered.
It exists because text alone is not the document; meaning lives in layout, position and tables, and
OCR-plus-regex pipelines break on every new template. The modern mechanism is a model that reads text,
position and pixels together, wrapped in a pipeline that validates and routes what it cannot verify.

## Why it exists
A purchase order is not a paragraph. "Total" means the number in the box to the right of the word, on
the last page, after the line items, not the first number that follows the word "total" in a text
dump. A contract's clause numbering, a form's checkboxes, a bank statement's columns: in each case the
information is encoded in *where* things are as much as in what they say. Flatten the page to a string
and that encoding is gone.

The first generation of document automation accepted this and fought it with templates: for each
vendor's invoice layout, hand-written coordinates or regexes that located each field. It works until
the vendor changes its layout, a new vendor appears, or a scan is slightly skewed, which in practice
means it is always breaking somewhere, and the maintenance cost scales with the number of templates. The
"zonal OCR" business existed because of this treadmill.

The second naive alternative arrived with LLMs: OCR the page and ask the model to extract fields from
the text. Better, but the LLM now reasons over a reading order that OCR guessed, over tables that have
become interleaved numbers, with no idea which value sat in which column. It also returns answers with
total confidence and no mechanism to say a field was absent. Document understanding as a discipline
exists to put layout back into the model's input and to put verification around the model's output.

## How it works
### Three model families
1. **OCR plus layout-aware encoder (LayoutLM family).** OCR provides words and bounding boxes. The model
   embeds each token with its text, its 2-D position (normalised box coordinates as extra embeddings)
   and, in later versions, image patch features of the page. Pretraining masks text and image regions
   so the model learns to associate words with positions and appearance. Fine-tuned heads then do token
   classification for key information extraction (label each word as invoice_number, total, none),
   page classification, or extractive question answering. It inherits OCR's errors but also its
   confidences and boxes, which is why it remains common in regulated extraction.
2. **OCR-free end-to-end (Donut, Pix2Struct).** A vision encoder reads the page image and a text decoder
   generates the output directly, trained to emit a serialised JSON of the fields. No OCR stage means no
   reading-order errors and one model to run, but also no per-word confidence and a fixed resolution
   that limits fine print.
3. **General vision-language models with structured prompts.** A VLM receives the page image (and often
   the OCR text as extra context) and a schema, and returns JSON. Zero-shot across templates, strong on
   irregular layouts, highest per-page cost and the strongest tendency to invent a value when the field
   is missing. Document parsers (cloud Document AI services, open-source layout toolkits) sit beside
   these as pre-processors that emit text, blocks, tables and reading order the VLM can consume.

### The pipeline around the model
The model is one stage. A production path looks like: ingest and classify the file type; render PDF
pages to images at a resolution that preserves small text (around 150–200 DPI is a common starting
point, tuned per corpus); detect whether a text layer exists so born-digital pages skip OCR; run OCR
where needed; run layout analysis to find blocks (title, paragraph, table, figure, header, footer) and
reading order; recover table structure (rows, columns, spanning cells) separately, because tables fail
differently from prose; split multi-document packets by page classification; extract against an explicit
schema; validate; route. Each stage exists because a failure there has a distinct signature, and
isolating stages is what makes the failure diagnosable.

### Schema-driven extraction and validation
Extraction is only useful when the output is typed and checkable. Define the schema with explicit
nullability so the model can say "absent" instead of guessing, request structured output, and validate
with code: types and formats (dates, currency), cross-field arithmetic (line item quantities × unit
prices summed against the subtotal, subtotal plus tax against the total), referential checks against
systems of record (a vendor ID that exists), and consistency across pages. Each failed check becomes a
reason to route the document to review. This is the layer that converts a probabilistic model into a
process with a known error budget, and the straight-through-processing rate (documents needing no human
touch) is the business metric that falls out of it.

### Evaluation
Field-level exact match or F1 for extraction, with per-field breakdown because "total" and "notes" do
not matter equally. For question answering on document images the standard metric is ANLS, a
normalised edit-distance score that tolerates minor OCR-style spelling differences. For tables, TEDS
measures structural similarity as a tree edit distance over cell structure. Build the evaluation set
from real customer documents with reviewer labels, stratified by template and scan quality, because
public benchmarks will not contain your vendors' layouts.

### Retrieval over documents
For "find the clause about termination in these 3,000 PDFs", the same choices recur at index time: chunk
OCR text with layout-aware boundaries so a table and its header stay together, or embed page images
directly with a document-screenshot embedder and let retrieval happen over pixels. The deep treatment
of chunking, parsing and ingestion lives in the retrieval and data-architecture modules; this lesson is
about getting trustworthy structure out of a page.

## Example
A minimal extraction path for one invoice page: detect whether the PDF already has text (skip OCR if
so), render the page, ask a VLM served behind an OpenAI-compatible API for a schema-shaped JSON with
explicit nulls, then validate with pydantic including the arithmetic check that catches most
hallucinated amounts. Review routing is the output, not just the fields.

```python
import base64, json, fitz                     # PyMuPDF
from pydantic import BaseModel, ValidationError
from openai import OpenAI

class Line(BaseModel):
    description: str; quantity: float; unit_price: float; amount: float
class Invoice(BaseModel):
    invoice_number: str | None; invoice_date: str | None; currency: str | None
    subtotal: float | None; tax: float | None; total: float | None; lines: list[Line]

SCHEMA = json.dumps(Invoice.model_json_schema())
client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")

def extract(pdf_path, page_no=0):
    page = fitz.open(pdf_path)[page_no]
    has_text_layer = len(page.get_text().strip()) > 50         # born-digital: text is already exact
    png = page.get_pixmap(dpi=200).tobytes("png")               # resolution preserves small print
    prompt = ("Extract this invoice page into JSON matching the schema. Use null for any field not "
              "visibly present; never guess. Dates as YYYY-MM-DD.\nSchema: " + SCHEMA)
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                                                   + base64.b64encode(png).decode()}}]
    if has_text_layer:                                          # give exact text as grounding
        content.insert(1, {"type": "text", "text": "Page text:\n" + page.get_text()})
    r = client.chat.completions.create(model="<served-vlm>", temperature=0,
                                       messages=[{"role": "user", "content": content}])
    try:
        inv = Invoice.model_validate_json(r.choices[0].message.content)
    except ValidationError as e:
        return None, ["schema: " + str(e)[:120]]
    issues = []
    if inv.lines and inv.subtotal is not None and abs(sum(l.amount for l in inv.lines) - inv.subtotal) > 0.01:
        issues.append("lines do not sum to subtotal")
    if None not in (inv.subtotal, inv.tax, inv.total) and abs(inv.subtotal + inv.tax - inv.total) > 0.01:
        issues.append("subtotal + tax != total")
    return inv, issues                                          # issues non-empty -> human review queue

inv, issues = extract("po_2024_0417.pdf")
print(inv.total if inv else None, issues)
```

## Trade-offs
- Layout-aware encoders on OCR output keep confidences and boxes for audit, but inherit OCR errors and
  need labelled fine-tuning per task; VLMs skip labelling and lose the confidence signal.
- OCR-free models remove reading-order failures and run as one stage, yet their fixed input resolution
  loses fine print, which flips the decision on dense statements and engineering drawings.
- Schema-driven extraction with validation raises straight-through rates and catches hallucinated
  amounts, but every rule is maintenance; keep rules to checks the business can state as invariants.
- Higher render resolution and tiling improve small-text accuracy at multiplied token cost per page;
  find the plateau on your corpus and render at that, not at maximum.

## Pitfalls
- Rendering pages at screen resolution; the symptom is correct headers and wrong digits, because small
  numerals fall below the encoder's effective resolution.
- Letting the model fill absent fields; the symptom is invoices with a plausible tax amount that does not
  appear anywhere on the page, found only when the ledger does not reconcile.
- Treating multi-page packets as one document; the symptom is a total extracted from the wrong invoice in
  a stapled scan, fixed by page classification and splitting before extraction.
- Flattening tables before extraction; the symptom is quantities and prices swapped between columns, or
  line items merged, especially on tables with wrapped descriptions.
- Measuring an average accuracy only; the symptom is a release that improved overall F1 while getting
  worse on the one field, the total, that drives the business outcome.
- Assuming the document is trustworthy input; the symptom is an extraction agent obeying instructions
  printed in a submitted PDF, the document-borne form of prompt injection.

## When to use
Use a document-understanding pipeline when the output must be structured and acted upon: accounts
payable, claims, KYC onboarding, contract review, forms processing, and any RAG system over scanned
material where tables carry the answers. For a handful of pages a human reads anyway, a plain VLM call
is enough; for regulated extraction at volume, the full pipeline with validation and review routing is
the product. Decide the model family by whether you need confidences and audit trails (layout-aware on
OCR) or zero-shot coverage across unknown templates (VLM), and often use both: VLM for breadth,
validation for trust. Measure per-field accuracy, straight-through rate, and reviewer time per
document. In interviews, be ready to explain why layout matters, what validation catches that models
cannot, and how you would evaluate on a customer's own documents.

## Role lens
- **Engineer:** Implements rendering, text-layer detection, schema-driven extraction and the validation
  rules, and emits review reasons rather than raw fields to the queue.
- **Architect:** Chooses the model family and parser per document class, defines the schema contract with
  downstream systems, and sets the straight-through-rate and cost-per-document targets.
- **FDE:** Assembles a labelled sample from the customer's real documents across templates and scan
  quality, measures per-field accuracy, and tunes thresholds against their review capacity.

## Check yourself
**Q:** A customer's invoice extraction reaches 96% field accuracy on a benchmark but finance rejects it
because wrong totals reach the ledger about twice a day. Design the changes that would make the system
acceptable without necessarily improving the model.
**A:** The gap is between accuracy and *trust*: a 4% error rate with no detection is unacceptable for a
money field, while a 4% error rate that is always caught is fine. Add verification independent of the
model: arithmetic invariants (line items sum to subtotal, subtotal plus tax equals total), format and
range checks, duplicate-invoice detection against the ledger, and vendor master-data lookups. Make the
schema nullable so absence is representable and prohibit guessing in the prompt. Route any failed
check to human review with the reason attached, and report straight-through rate and escaped-error rate
instead of average accuracy. Then stratify evaluation by field, with the total weighted as the
acceptance criterion. Finance accepts a system whose failures are visible and bounded; raising model
accuracy alone would still leave silent errors.

## Quiz
### Q: Why does the LayoutLM family add 2-D position embeddings to each OCR token?
- [ ] To compress the input sequence so long pages fit in the context
- [x] Because the meaning of a token on a document depends on where it sits relative to other tokens
- [ ] To replace the need for a text encoder
- [ ] To encode the scan's DPI and skew angle
> The number next to "Total" and the number inside a line item are distinguished by position, not by
> text. Position embeddings give the transformer that signal; DPI and skew are preprocessing concerns.

### Q: What is the main advantage an OCR-free model like Donut gives up compared with an OCR-plus-layout pipeline?
- [ ] The ability to read printed text at all
- [ ] Support for languages other than English
- [x] Per-word confidence scores and bounding boxes usable for audit and review routing
- [ ] Handling of multi-page documents
> Generating JSON directly from pixels removes the OCR stage and its reading-order errors, but the
> confidences and coordinates that the OCR stage produced go with it, which audit workflows rely on.

### Q: An extraction system returns a tax amount for invoices that show no tax line. Which change addresses the root cause rather than the symptom?
- [ ] Increase the render resolution
- [ ] Switch to a larger vision-language model
- [x] Make the field nullable in the schema, instruct the model never to guess, and validate subtotal + tax = total
- [ ] Lower the temperature to zero
> The model is filling a required slot with a plausible value. Representing absence explicitly and
> checking an invariant turns an invented value into a detectable failure; resolution and size do not.
