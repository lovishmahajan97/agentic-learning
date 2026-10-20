---
id: document-processing-pipelines
module: data-architecture
name: Document processing pipelines
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
related:
  - rag-retrieval/document-parsing
  - multimodal/ocr
  - multimodal/document-understanding
  - enterprise-integration/client-document-parsing
  - data-architecture/ingestion-pipelines
  - data-architecture/etl-elt-for-rag
  - rag-retrieval/chunking-strategies
  - rag-retrieval/parent-document-retrieval
sources:
  - Official docs: PyMuPDF
  - Official docs: Docling
  - Official docs: Unstructured
---

## TL;DR
A document processing pipeline turns heterogeneous files (PDF, Office, HTML, email, scans, images) into
one canonical structured representation: ordered blocks with type, text, page, position and hierarchy,
plus tables as cells. The architecture is a tiered router: detect what each document really is, send it
to the cheapest extractor that preserves its structure, score the output, and escalate (native text →
OCR → layout model or vision LLM) only when the score fails, because extraction quality bounds every
downstream step and the expensive extractors cannot be run on everything.

## Why it exists
The first parser most teams reach for is a single library call that returns a string per page. It works
on the sample PDFs and breaks on the corpus. Scanned PDFs have no text layer, so the call returns empty
strings and the pipeline happily embeds nothing. Two-column papers come out with the lines of both columns
interleaved. Tables flatten into a run of numbers with no column association, so "what was Q3 revenue"
retrieves a chunk where the right number sits next to the wrong label. Every page contributes its header
and footer, so a chunker produces thousands of chunks that begin with "Confidential — Page 14 of 62".
Hyphenated line breaks split words. Encoding glitches turn ligatures into garbage. And none of this fails
loudly: an embedding model will produce a perfectly valid vector for a garbled chunk, so the damage
appears months later as retrieval that "sometimes misses obvious passages".

The opposite naive approach, sending every page to a vision-capable LLM, fixes most of the quality
problems and creates a cost and latency problem: it is orders of magnitude more expensive per page than
native text extraction (as an illustrative order of magnitude, somewhere between a tenth of a cent and a
few cents per page depending on the model, versus milliseconds of CPU time), it is slow, and it is
non-deterministic, so a re-run produces slightly different text and therefore different chunks and
embeddings. Neither extreme is a pipeline. The pattern is to classify, route, verify and escalate, so that
the expensive path is spent only where the cheap path demonstrably failed. The rag-retrieval parsing
lesson covers the extractors themselves (OCR, layout, tables); this lesson covers the system around them.

## How it works
### 1. Intake and classification
Identify the real type by magic bytes, not extension; customers rename files. Record page count, file
size and, for PDFs, whether pages carry a text layer: a page with an image covering the page and near
zero extractable characters is a scan. Detect language, because OCR engines need the right language
packs and the quality heuristics below (dictionary-word ratios, hyphen repair) are language-specific. Detect encrypted or malformed files early and park them.

### 2. Routing tiers
Order extractors by cost and by what they preserve, and route each document to the first tier that is
expected to succeed:

- **Native extraction with layout heuristics** (PyMuPDF, pdfplumber, python-docx, HTML parsers) for
  born-digital files. Fast, deterministic, cheap; recovers reading order from glyph positions reasonably
  well and tables poorly.
- **OCR** (Tesseract, cloud OCR services) for scans and images, producing text with per-word confidence
  and bounding boxes. Quality depends on resolution, skew and language; preprocessing (deskew, binarize)
  matters as much as the engine.
- **Layout-aware document models** (Docling, Unstructured's high-resolution strategy, LayoutLM-family
  models) that detect regions (title, paragraph, table, figure, caption) and reconstruct table structure.
  Slower and heavier, but they are what makes multi-column and table-heavy documents usable.
- **Vision LLMs** for the residue: handwritten forms, charts that need describing, pages where the layout
  model's output fails verification. Most expensive, non-deterministic, so used last and cached.

The reason for the ladder is that cost per page differs by orders of magnitude between tiers while most
pages in a typical corpus are born-digital text that tier one handles well.

Route at the level of the *page* where the format allows it. Real files are mixed: a born-digital
report with a scanned appendix, a slide deck where one slide is a screenshot of a table. A per-document
decision has to choose one tier for the whole file, which either loses the odd pages or overpays for
the ordinary ones. Some decisions must stay at document level, because they need several pages at once:
header and footer detection, tables that continue across a page break, and section hierarchy. These
run after the per-page extraction, over the assembled blocks.

### 3. The canonical document model
Every tier emits the same structure: a list of blocks, each with `type` (title, heading with level,
paragraph, list item, table, figure, caption, header, footer, footnote), `text`, `page`, `bbox`, and a
`parent` reference giving section hierarchy. Tables are stored as cell grids with row and column spans
and additionally serialized to Markdown or HTML for embedding. Figures carry captions and optionally an
LLM-written description. This model exists so that chunkers, metadata extractors and citation rendering
depend on one schema rather than on each extractor's quirks, and so that a document re-processed by a
better tier later slots into the same downstream pipeline.

### 4. Cleaning
Headers and footers are found by text that repeats at the same position across many pages and are
dropped. Hyphenation at line ends is repaired. Unicode is normalized (NFKC), control characters removed,
whitespace collapsed while paragraph boundaries are kept. Boilerplate (cookie banners, navigation) is
stripped for HTML. Each of these is cheap and each removes a class of chunk-level noise that would
otherwise be embedded thousands of times.

### 5. Quality scoring and escalation
The output is scored before it is accepted: characters per page against an expectation for the document
type, OCR mean confidence, fraction of tokens that are dictionary words, ratio of single-character
"words", presence of known garbage patterns (replacement characters, repeated glyphs). A score below
threshold routes the document to the next tier or, after the last tier, to a human review queue. This
gate is the mechanism that keeps extraction failures from becoming silent retrieval failures.

### 6. Output, versioning and isolation
The canonical JSON is stored next to the raw bytes with `parser_tier` and `parser_version`, so an upgrade
to the layout model can re-process only documents parsed by the old version or the lower tier. Parsers
run in isolated workers with timeouts and memory limits, because a malformed PDF can hang or exhaust a
process, and a failure is recorded per document rather than failing the batch. An evaluation set of
documents with gold text and gold tables (measured by character error rate and table cell accuracy) is
run on every parser change.

## Example
A per-page router with a text-layer check, a quality score and escalation. The extractors are stand-ins
for the real tiers that take one page and return canonical blocks; the control flow is the point.

```python
import fitz, re                                   # PyMuPDF

MIN_CHARS_PER_PAGE, MIN_WORD_RATIO = 200, 0.85

def quality(blocks):                              # English prose heuristic; tables scored separately
    text = " ".join(b["text"] for b in blocks if b["type"] not in ("header", "footer", "table"))
    tokens = text.split()
    if not tokens:
        return 0.0
    wordlike = sum(1 for t in tokens if re.search(r"[A-Za-z]{2,}", t))
    garbage = text.count("\ufffd") + len(re.findall(r"(.)\1{5,}", text))
    return (wordlike / len(tokens)) * (0.0 if garbage > 20 else 1.0)

def process(path, tiers):
    """tiers: ordered list of (name, extract_page) returning canonical blocks for one page."""
    pages, errors = [], []
    for page in fitz.open(path):
        native = page.get_text().strip()
        if not native and not page.get_images():
            pages.append({"page": page.number, "tier": "blank", "blocks": []}); continue
        start = 0 if len(native) >= MIN_CHARS_PER_PAGE else 1   # no usable text layer: skip native
        for name, extract in tiers[start:]:
            try:
                blocks = extract(page)                          # worker timeout in production
            except Exception as e:                              # a bad page must not kill the batch
                errors.append((page.number, name, repr(e))); continue
            if (q := quality(blocks)) >= MIN_WORD_RATIO:
                pages.append({"page": page.number, "tier": name, "quality": q, "blocks": blocks})
                break
        else:
            pages.append({"page": page.number, "tier": "needs_review", "blocks": []})
    return {"pages": pages, "errors": errors}

# tiers = [("native", native_page), ("ocr", ocr_page), ("layout", layout_page), ("vlm", vlm_page)]
```

Walk a real document through it: a 60-page born-digital contract whose last 8 pages are a scanned,
signed annex. The 52 text pages pass the native tier at near-zero cost. The 8 annex pages have no text
layer, so they start at OCR. Suppose 2 of them are handwritten and fail the word-ratio gate; they
escalate through layout to the vision model. The document costs 52 native pages, 8 OCR pages and 2
vision-LLM pages. A per-document router would have done worse either way. It would have sent the whole
file to native extraction, because 87 % of pages have text, and silently lost the annex. Or, with a
stricter threshold, it would have OCR'd 52 pages that already had perfect text. The cost argument
scales. With an illustrative vision-model price of one cent per page, a 1-million-page corpus costs
about $10,000 per full pass when every page goes to the vision model, and about $500 when only the 5 %
that fail the cheaper tiers do. Re-processing after a parser change multiplies whichever figure you
chose.

Each record is stored with the raw file's hash and per-page tiers, and `needs_review` pages are counted
on the pipeline dashboard rather than silently indexed as empty. The word-ratio score is deliberately
crude and English-specific. It excludes table blocks, because a correct financial table is mostly
numbers and would otherwise fail the gate; tables need their own check (consistent column counts per
row, numeric cells that parse). Header and footer removal runs after routing, across all pages, because
it depends on text repeating from page to page.

## Trade-offs
- Tiered routing keeps average cost near the cheapest extractor, but it adds a scoring step and a second
  pass for escalated documents; for a corpus that is almost entirely scans, start at the OCR tier.
- Layout models and vision LLMs recover tables and reading order that native extraction loses, at a cost
  and latency that make them wrong for the whole corpus until retrieval evals prove the gain on your
  document types.
- A rich canonical model with bounding boxes and hierarchy enables precise citations and structure-aware
  chunking, but it is more work to populate from simple sources such as plain HTML or email.
- Deterministic extractors make re-runs reproducible, which vision LLM output is not; cache LLM
  extractions by page hash and treat them as versioned artifacts to recover reproducibility.

## Pitfalls
- Trusting the file extension; the symptom is parser crashes on files that are secretly HTML, images or
  zip archives, and documents classified as processed that produced nothing.
- Not detecting scanned PDFs; the symptom is documents in the index with zero or one chunk, found when a
  user asks about a contract that "should be there".
- Skipping header and footer removal; the symptom is chunks that begin with a confidentiality notice and
  a page number, and retrieval that favours documents with the longest boilerplate.
- Flattening tables to text; the symptom is numeric questions answered with the right number from the
  wrong column or year.
- No parser version in the output; the symptom is an inability to re-process only the documents an old,
  buggy extractor touched, so every parser fix becomes a full re-run.
- Routing whole documents by an average text-layer ratio; the symptom is a contract whose scanned
  signature annex is missing from the index while the rest of the document retrieves fine.
- Running parsers in the orchestrator's process; the symptom is one malformed file hanging or OOM-killing
  an entire nightly batch.

## When to use
Reach for a tiered pipeline as soon as a corpus mixes formats or includes scans, tables or multi-column
layouts, which describes nearly every enterprise document set. A homogeneous corpus of Markdown or HTML
needs only the cleaning and canonical-model steps. Measure text-layer ratio, per-tier volume and cost,
quality-score distribution, escalation rate, review-queue size, and extraction accuracy on the gold set;
escalation rate climbing is the earliest sign of a new document type entering the corpus. Day to day this
is where retrieval incidents are traced to and fixed; in interviews, "how would you ingest 50,000 mixed
PDFs for RAG" should be answered with classification, routing by cost, a canonical structure, quality
gates and selective re-processing by parser version, not with the name of a library.

## Role lens
- **Engineer:** Implements classification, the extractor adapters that emit the canonical block model,
  header and footer removal, and the quality score, and runs the gold-set accuracy check on every change.
- **Architect:** Defines the canonical document schema and the tier ladder with its cost and quality
  thresholds, and decides where vision LLM extraction is allowed given cost, latency and data policy.
- **FDE:** Profiles the customer's real documents in the first week, finds the scans and the table-heavy
  forms the demo never saw, and sets expectations about which document classes need review or custom
  handling.

## Check yourself
**Q:** A customer's RAG system answers well for policy documents but fails on their financial reports,
which are multi-column PDFs with many tables; some older reports are scans. The current pipeline uses one
text-extraction library for everything. Describe how you would restructure processing and how you would
know it worked.
**A:** First, classify: detect the scans by text-layer ratio and send them to OCR with deskewing instead of
native extraction, which currently yields nothing for them. Second, route the born-digital reports to a
layout-aware extractor that reconstructs reading order across columns and emits tables as cell grids,
serialized to Markdown for embedding, with the original cells kept for citation. Third, define a canonical
block model so both routes produce the same structure, remove repeated headers and footers, and store
parser tier and version with each output. Fourth, add a quality score with escalation to a vision LLM for
pages that fail, cached by page hash. To know it worked: build a small gold set from the failing reports
with hand-verified text and table cells, measure character error rate and cell accuracy before and after,
and re-run the retrieval evaluation on the questions that used to fail, expecting the numeric questions to
retrieve the correct table chunk.

## Quiz
### Q: Why route documents through tiers rather than sending every page to the best available extractor?
- [ ] The best extractors cannot read born-digital PDFs
- [x] Cost and latency per page differ by orders of magnitude across tiers, and most pages are handled well by the cheapest one, so escalation should be driven by measured failure
- [ ] Vision LLMs are not permitted to process documents in most jurisdictions
- [ ] Layout models require a text layer to operate
> A typical corpus is mostly born-digital text where native extraction is accurate and nearly free. A
> quality gate identifies the minority of pages that need OCR, layout analysis or a vision model, keeping
> spend proportional to difficulty.

### Q: What is the purpose of a canonical document model shared by all extractors?
- [ ] It compresses documents for cheaper storage
- [ ] It lets the vector database index documents without chunking
- [x] It decouples chunking, metadata and citation logic from each extractor's output format, so documents can be re-processed by a better tier without downstream changes
- [ ] It guarantees deterministic output from vision LLMs
> Downstream stages should depend on one schema of typed, ordered blocks with hierarchy and tables as
> cells. Then swapping or upgrading an extractor, or escalating a document to a higher tier, changes
> nothing after the parsing stage.

### Q: A quality gate flags a batch of documents with chars-per-page near zero after native extraction. What does this most likely indicate?
- [ ] The documents are written in a language the chunker does not support
- [x] The pages are scanned images without a text layer and need OCR
- [ ] The embedding model rejected the text as too short
- [ ] The headers and footers were removed too aggressively
> Native extraction reads the PDF's text layer; scans have none, so the output is empty even though the
> pages are full of content. The gate exists precisely to catch this and route the documents to OCR
> instead of indexing them as empty.
