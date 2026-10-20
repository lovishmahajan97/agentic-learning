---
id: document-parsing
module: rag-retrieval
name: Document parsing (OCR, layout, tables)
kind: concept
difficulty: core
est_minutes: 17
related:
  - multimodal/ocr
  - multimodal/document-understanding
  - multimodal/vision-language-models
  - enterprise-integration/client-document-parsing
  - rag-retrieval/chunking-strategies
  - rag-retrieval/embedding-pipelines
  - rag-retrieval/retrieval-failure-diagnosis
sources:
  - Official docs: pdfplumber
  - Official docs: Tesseract OCR
---

## TL;DR
Parsing turns PDFs, scans and office files into clean, ordered text with structure before anything is
chunked or embedded. It needs OCR for pages that are images, layout analysis to recover reading order and
drop headers and footers, and table extraction that keeps each cell tied to its row and column headers.
It matters because every later stage only sees what the parser produced: retrieval cannot find text that
was garbled, reordered or flattened.

## Why it exists
A PDF is not a text file. It is a set of drawing instructions: place these glyphs at these coordinates,
draw this line. There is no notion of "paragraph", "column" or "table cell", and sometimes not even of
words: characters may be placed one by one, and spaces inferred from gaps. A scanned PDF is worse: each
page is a photograph with no text layer at all.

The naive approach, calling a library's `extract_text()` and chunking the result, fails in predictable
ways. Two-column papers come out with lines interleaved across columns. Every page's running header ("ACME
Corp Confidential, Page 7") is repeated hundreds of times and pollutes retrieval. Scanned pages yield
empty strings, so whole documents silently vanish from the index. Tables are the worst case: a cell like
"30 days" ends up as a token in a stream of numbers, detached from the row label "Refund window" and the
column header "Enterprise plan", so no query can match it meaningfully and the model cannot interpret it
even if it is retrieved.

These failures are invisible downstream. The embedding model happily embeds garbage, the vector store
indexes it, and the eventual symptom is a wrong answer that looks like a retrieval or model problem. In
practice parsing errors are among the most common root causes found in RAG failure analysis, and they are
cheapest to fix at the source.

## How it works
### 1. Triage each page
Decide per page, not per file, *because* mixed PDFs (a digital contract with scanned signature pages) are
common. If the page has a text layer with a plausible character count, extract it directly; it is exact
and cheap. If the text layer is empty, tiny or garbage (some scanners add a poor OCR layer), render the
page to an image and OCR it.

### 2. Extract with coordinates
For digital pages, extract words or characters with bounding boxes and font information, not just a string.
Coordinates are what make layout recovery possible; font size and weight are the main signals for headings.

### 3. OCR for image pages
OCR has two stages: *detection* finds text regions and lines, *recognition* converts each region to
characters, with a confidence score. Quality depends heavily on input: render at a sufficient resolution
(around 300 DPI is the commonly recommended setting for Tesseract), deskew, and set the correct language.
Keep the per-word confidence, *because* low-confidence regions are where numbers get corrupted ("0" vs
"O", "1" vs "l") and are worth flagging or routing to a stronger model.

### 4. Layout analysis
Group words into blocks and label them: title, heading, paragraph, list, table, figure, caption, header,
footer. Rule-based approaches cluster by x-coordinates to find columns and recursively cut the page along
whitespace (XY-cut); learned layout models detect regions directly from the page image and handle irregular
layouts better. Then order blocks for reading: column by column, top to bottom. Remove running headers and
footers by detecting text that repeats at the same position across pages. Preserve the heading hierarchy as
metadata *because* chunking and retrieval both benefit from knowing "this paragraph is under 4.2 Refunds".

### 5. Tables
Table extraction has two parts: detecting the table region, and recognising its structure (rows, columns,
merged and spanning cells, multi-row headers). Ruled tables can be recovered from drawn lines; borderless
tables need alignment heuristics or a learned structure model. Output the table as Markdown or HTML so
structure survives, and for retrieval also serialise each row with its headers ("Plan: Enterprise; Refund
window: 30 days"), *because* a row read in isolation must still be meaningful once chunked. Large numeric
tables are often better loaded into a database and queried with SQL than embedded at all.

### 6. Vision-language models as parsers
A VLM given a page image can transcribe it to Markdown, including tables and reading order, and often
copes with layouts that break rule-based pipelines. The costs: per-page inference cost and latency, and a
specific risk that the model *generates* plausible text rather than transcribing, silently normalising a
value or filling a smudged digit. Use it where layouts are complex, and validate numbers against the text
layer or OCR output where possible.

### Tools
Text-layer libraries (pdfplumber, PyMuPDF, pdfminer.six), OCR engines (Tesseract and cloud OCR), document
pipelines that bundle layout and tables (Unstructured, Docling), and managed services (AWS Textract, Azure
AI Document Intelligence, Google Document AI). They differ mainly in layout and table quality on *your*
documents, so compare them on a sample of your hardest pages.

## Example
A per-page parser: use the text layer when present, fall back to OCR, and serialise tables row by row with
their headers.

```python
import pdfplumber, pytesseract

def parse_pdf(path: str, min_chars: int = 50) -> list[dict]:
    out = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            source = "text-layer"
            if len(text.strip()) < min_chars:                 # scanned or empty layer
                img = page.to_image(resolution=300).original  # PIL image
                text = pytesseract.image_to_string(img)
                source = "ocr"
            out.append({"page": i, "type": "text", "source": source, "text": text})
            for table in page.extract_tables():
                header, *rows = table
                for row in rows:
                    cells = [f"{h}: {v}" for h, v in zip(header, row) if v]
                    out.append({"page": i, "type": "table_row", "source": source,
                                "text": "; ".join(cells)})
    return out

# A pricing table row becomes:
# {"page": 4, "type": "table_row", "text": "Plan: Enterprise; Refund window: 30 days; Seats: Unlimited"}
```

This is deliberately simple: it does not reorder multi-column pages, strip headers, or handle merged
header cells, and `extract_tables` only works on text-layer pages. Each of those is a step you add once a
sample of your real documents shows you need it.

## Trade-offs
- Text-layer extraction is exact, fast and cheap, but only for born-digital PDFs; on scans or broken layers
  it returns nothing, so per-page detection and OCR fallback are mandatory for mixed corpora.
- Rule-based layout (column clustering, XY-cut) is deterministic and cheap, yet fails on irregular layouts
  like brochures and forms, where learned layout models or VLMs pay for their cost.
- VLM page transcription handles hard layouts and tables well, but costs per page and can hallucinate or
  normalise values; the trade flips toward OCR plus rules when exact numbers matter and layouts are simple.
- Row-wise table serialisation makes cells retrievable on their own, at the cost of more chunks and
  repetition; for large analytical tables, loading them into SQL is cheaper and more accurate than embedding.

## Pitfalls
- Using one `extract_text()` call for everything; the symptom is scanned documents producing empty chunks,
  so whole contracts are missing from search with no error raised.
- Ignoring reading order; the symptom is retrieved chunks whose sentences jump between two columns and that
  match queries poorly despite containing the right words.
- Leaving running headers and footers in; the symptom is many unrelated chunks scoring high for the company
  name or "Confidential" and crowding out real results.
- Flattening tables to a stream of values; the symptom is the model citing the right chunk but reading the
  wrong cell, such as quoting the Basic plan's price for the Enterprise plan.
- Never evaluating the parser; problems surface only as downstream wrong answers, whereas a sample of pages
  compared against hand transcriptions would show them immediately.

## When to use
Treat parsing as the first stage to verify on any new corpus, before choosing chunk sizes or embedding
models. Sample pages from every document type (digital, scanned, multi-column, table-heavy, forms) and
inspect the output by eye, then measure character error rate on OCR pages and cell accuracy on tables.
Use plain text-layer extraction when documents are clean and born-digital; add OCR, layout models or VLMs
only where the sample shows failures. In interviews, "I would look at the parsed text first" is often the
answer that separates experienced RAG builders from tutorial-level ones.

## Role lens
- **Engineer:** Builds the per-page triage, OCR fallback, header stripping and table serialisation, and keeps
  page numbers and bounding boxes as metadata for citations.
- **Architect:** Chooses between in-house parsing, open-source pipelines and managed document services based
  on document mix, data residency, cost per page and the accuracy measured on representative samples.
- **FDE:** Collects the customer's ugliest real documents on day one (faxed forms, scanned annexes, nested
  tables) because they decide the parser and the timeline more than any model choice does.

## Check yourself
**Q:** A finance customer's RAG system answers questions from quarterly reports, and numeric answers are
wrong about a third of the time even when the cited page is correct. How do you diagnose and fix it?
**A:** The right page is retrieved, so the loss is in how the content was parsed or read. I would open the
parsed text for those pages: likely the tables were flattened so values lost their row and column headers,
or OCR corrupted digits on scanned pages. Fixes: detect tables and keep their structure as Markdown or
HTML, serialise rows with headers for retrieval, check OCR confidence and re-run low-confidence regions at
higher resolution or with a stronger engine, and possibly load financial tables into a database for SQL
lookups. I would build a small eval of numeric questions with known answers to confirm the fix, and avoid
VLM transcription for numbers unless outputs are cross-checked.

## Quiz
### Q: Why should OCR fallback be decided per page rather than per file?
- [ ] OCR engines can only process one page at a time
- [x] Many PDFs mix born-digital pages with scanned ones, so a file-level check would miss image-only pages
- [ ] Text-layer extraction is slower than OCR on large files
- [ ] Per-page OCR improves the embedding model's accuracy
> A contract may be digital except for scanned signature pages or annexes. A file-level check sees text and
> skips OCR, so those pages silently produce empty chunks and vanish from retrieval.

### Q: What is the main reason to serialise each table row together with its column headers?
- [ ] It reduces the number of chunks in the index
- [ ] It lets the embedding model skip numeric tokens
- [x] A row retrieved on its own is only interpretable if it carries the labels that give its values meaning
- [ ] It is required for Markdown rendering in the UI
> After chunking, a row like "30 | 10 | Unlimited" is meaningless without "Refund window", "Seats" and the
> plan name. Repeating headers makes the row both matchable by queries and readable by the model.

### Q: Which is the most specific risk of using a vision-language model to transcribe document pages?
- [ ] It cannot handle multi-column layouts
- [ ] It ignores tables entirely
- [x] It may produce fluent text that differs from the page, such as an altered or invented number
- [ ] It always requires a text layer to be present
> VLMs often handle complex layouts better than rule-based tools, but they generate rather than copy, so a
> smudged or unusual value can be silently changed. Cross-checking against OCR or the text layer catches this.
