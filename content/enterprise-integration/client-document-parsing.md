---
id: client-document-parsing
module: enterprise-integration
name: Client document parsing (PDF, scans, OCR)
kind: practice
difficulty: core
est_minutes: 15
related:
  - rag-retrieval/document-parsing
  - multimodal/ocr
  - multimodal/document-understanding
  - multimodal/vision-language-models
  - data-architecture/document-processing-pipelines
  - enterprise-integration/data-profiling-and-quality-assessment
  - safety-security/pii-redaction
  - rag-retrieval/citations-grounding
sources:
  - Official docs: pdfplumber
  - Official docs: Tesseract OCR
---

## TL;DR
A client's document estate is a mix: born-digital PDFs with a text layer, scanned PDFs that are only
images, faxes, photos of forms, password-protected files and decades of templates. Parsing it well is
less about one parser than about triage: detect what each file is, route it to the cheapest method that
handles it (text extraction, OCR, or a layout or vision model), score confidence per page, send the
doubtful pages to review, and keep page and position provenance for citations.

## Why it exists
Most enterprise knowledge an AI system is asked about lives in documents: contracts, claims files,
policies, invoices, engineering reports. In a demo the corpus is a dozen clean PDFs exported from Word.
At a client it is hundreds of thousands of files accumulated over years, and the share that is scanned,
skewed, stamped, handwritten on, or exported by an old system with a broken font encoding is often large.
How large is unknown until it is profiled, which is why parsing is a per-client problem rather than a
solved library choice.

The naive approach is to run one text extractor over everything. It fails silently. A scanned PDF has
no text layer, so the extractor returns an empty string and the document disappears from the index
without an error. A born-digital PDF with a bad character map returns plausible-looking garbage that is
embedded and retrieved. A two-column contract is read across the columns, interleaving clauses. A table
of policy limits becomes a run of numbers no longer attached to their headers, so the model answers with
the wrong limit and cites a real page. The opposite naive approach, sending every page through OCR or a
vision-language model, is slow and expensive at enterprise volume and can degrade born-digital text that
was already exact.

The mechanics of OCR, layout analysis and table extraction are covered in the RAG document-parsing and
multimodal OCR lessons. This lesson is about applying them to a specific client's corpus: triage,
routing, confidence, review and provenance.

## How it works
### 1. Profile the corpus first
From a stratified sample, measure file type mix, share of PDFs with a usable text layer, encrypted or
corrupt files, page counts, languages, and recurring templates (the same form from the same system).
This sets the routing rules and the cost and time estimate for the backfill.

### 2. Triage each file, ideally each page
- **Encrypted or corrupt:** report to the client with the file list; only they can supply passwords or
  exclude files.
- **Born-digital with a text layer:** extract directly. This is the cheapest and most exact route,
  *because* the characters are already in the file.
- **Image-only pages:** route to OCR. Hybrid PDFs (a scanned contract with a digital cover page) are
  common, so decide per page, not per file.
- **Text layer present but unusable:** detect it with a quality check, such as the share of characters
  that are letters, the share of tokens found in a dictionary for the expected language, or replacement
  and private-use characters. Broken font encodings produce text that exists but means nothing; treat
  those pages as images.

### 3. OCR with preprocessing and confidence
Render pages at sufficient resolution (Tesseract's documentation recommends at least 300 DPI), deskew,
and set the expected languages explicitly, *because* OCR accuracy depends heavily on image quality and on
the language model the engine applies. Keep word-level confidence: OCR engines report it, and the page's
mean or low-percentile confidence is the signal for routing.

### 4. Escalate the hard pages
Pages with low OCR confidence, complex layouts, dense tables, handwriting or stamps go to a stronger and
more expensive route: a layout-aware document model, a cloud document-AI service if the data may leave
the boundary, or a vision-language model prompted to transcribe; and, below a further threshold, to human
review. A tiered route spends the cost where it buys accuracy, instead of on every page.

### 5. Recover structure
Keep reading order, headings, list items and tables (cells tied to row and column headers), and drop
repeated headers, footers and page numbers, *because* chunking and retrieval quality depend on structure.
Recurring client templates are worth a dedicated extraction rule or schema; one form type can be a large
share of a corpus.

### 6. Keep provenance
Store for each text span the source file, page number and, where available, bounding box and parse route
and confidence. Citations then point to a page the user can open, and a later parser improvement can
target exactly the pages that used the weak route.

### 7. Evaluate on the client's documents
Build a small labelled set from their corpus, stratified by route and template, and measure text accuracy
(character or word error rate against a transcription), table cell accuracy and, end to end, whether
retrieval finds the right page. Rerun it when you change a parser or a threshold.

### Boundary and sensitivity
Scanned documents frequently contain signatures, ID numbers and health or financial details. Where
residency or contract requires it, OCR and any vision model must run inside the client's boundary, which
constrains which services you can use; settle this before choosing tools.

## Example
Per-page triage with pdfplumber and Tesseract, routing each page to text extraction, OCR, or escalation.
Thresholds are illustrative and should be tuned on a labelled sample.

```python
import pdfplumber, pytesseract

def text_is_usable(text: str, min_chars=200, min_alpha=0.6):
    if len(text) < min_chars: return False
    alpha = sum(c.isalpha() or c.isspace() for c in text) / len(text)
    return alpha >= min_alpha and "�" not in text     # replacement char hints at broken encoding

def parse_pdf(path, lang="eng+deu", ocr_ok=70):
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text_is_usable(text):
                pages.append({"page": i, "route": "text_layer", "text": text, "conf": None})
                continue
            img = page.to_image(resolution=300).original          # PIL image
            data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
            words = [(w, float(c)) for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 0]
            conf = sum(c for _, c in words) / len(words) if words else 0.0
            route = "ocr" if conf >= ocr_ok else "escalate"           # VLM / layout model / human
            pages.append({"page": i, "route": route, "conf": round(conf, 1),
                          "text": " ".join(w for w, _ in words)})
    return pages

for p in parse_pdf("claim_88213.pdf"):
    print(p["page"], p["route"], p["conf"], len(p["text"]))
```

On a typical scanned claim file this might print a text-layer cover page, several OCR pages in the
80s, and one handwritten page in the 40s routed to escalation. Across the backfill, the share of pages
per route becomes a cost forecast, and the escalation queue size tells you how much review capacity the
client must provide.

## Trade-offs
- Per-page routing spends expensive OCR and vision calls only where they are needed, but adds triage
  logic and thresholds to maintain; for a small corpus, running the strongest route on everything can be
  simpler and still affordable.
- Vision-language models handle messy layouts, tables and handwriting better than classic OCR in many
  cases, but are slower, costlier, and can produce fluent transcriptions of text that is not there; use
  them where classic OCR confidence is low and verify on a labelled sample.
- Cloud document-AI services are accurate and low-effort, but send document images outside the client's
  boundary; residency or contractual limits flip the choice to a self-hosted engine inside the boundary.
- Human review of low-confidence pages gives the highest accuracy, but costs client staff time; reserve it
  for pages that matter to the use case, such as limits, amounts and signatures, rather than every page.
- Template-specific extraction rules are very accurate for recurring forms, but break when the client
  changes the form; detect template version and fall back to the generic route on a mismatch.

## Pitfalls
- Treating empty extraction as an empty document; the symptom is a coverage gap of scanned files that
  never appear in retrieval, found only when a user asks about one.
- Trusting any non-empty text layer; the symptom is garbled text from broken font encodings embedded and
  retrieved, producing confident answers grounded in nonsense.
- Deciding route per file instead of per page; the symptom is hybrid PDFs where the digital cover page is
  indexed and the scanned body is lost.
- OCR with the wrong language setting; the symptom is high error rates on accented and non-Latin
  characters, which also breaks keyword search on names and terms.
- Discarding page numbers and positions; the symptom is citations that point to a whole 200-page file,
  so users cannot verify answers and stop trusting them.
- Evaluating the parser on public sample PDFs; the symptom is a parser that looked accurate in testing
  and fails on the client's faxed, stamped and skewed originals.

## When to use
Apply this triage whenever a client corpus is more than a handful of born-digital files, which in
enterprise work is nearly always. Start by profiling a stratified sample to learn the route mix, then set
thresholds on a small labelled set from the client's own documents. Measure share of pages per route,
OCR confidence distribution, escalation queue size, parse failures, and end-to-end retrieval of the right
page. Revisit routing when a new document source or template appears. In interviews, the strong answer
names silent failures (empty and garbled text layers) and per-page routing rather than a favourite parser.

## Role lens
- **Engineer:** Implements per-page triage, text-layer quality checks, OCR with confidence capture and an
  escalation path, and stores page and bounding-box provenance with every chunk.
- **Architect:** Chooses the parsing tiers and where each runs relative to the client boundary, and sets
  the accuracy targets and review policy for each document class.
- **FDE:** Profiles the client's actual document estate, gets passwords or exclusions for protected files,
  arranges reviewers for escalated pages, and builds the labelled sample with client staff.

## Check yourself
**Q:** After indexing a client's 120,000-document archive, users say the assistant "doesn't know about"
many older contracts, though they are in the source library. What is your diagnosis process?
**A:** First compare per-document coverage: how many files produced zero or near-zero text, grouped by
year and folder. Older contracts are likely scans with no text layer, extracted as empty strings and
silently indexed as nothing, or hybrid PDFs where only a digital cover page was read. Also check for
garbled text layers by measuring alphabetic ratio and dictionary hit rate. The fix is per-page triage:
route image-only and unusable pages to OCR with the right languages, escalate low-confidence pages, and
reprocess from the stored raw files. Then add a coverage check per batch so empty extractions alert.

## Quiz
### Q: Why should the parsing route be decided per page rather than per file?
- [ ] OCR engines can only process one page at a time
- [x] Hybrid PDFs mix digital and scanned pages, so a per-file decision loses one kind or overpays for the other
- [ ] Per-page decisions make provenance unnecessary
- [ ] Text layers are always stored per page in the PDF format
> A scanned contract with a digital cover page passes a per-file text check, and the scanned body is lost.
> Per-page triage extracts digital pages directly and OCRs only the image pages.

### Q: A PDF's text layer extracts to non-empty text. What is the main remaining risk?
- [ ] The text is always in the wrong reading order
- [ ] The file must still be OCRed to obtain confidence scores
- [x] A broken font encoding can produce text that exists but is meaningless, so it needs a quality check
- [ ] Non-empty text layers cannot contain tables
> Some PDFs map glyphs to the wrong characters. The extractor returns garbage without error; checks such
> as alphabetic ratio, dictionary hits or replacement characters catch it before it is indexed.

### Q: A client forbids document images from leaving their network. Which choice does this most directly constrain?
- [ ] Whether you can chunk documents by heading
- [x] Whether you can use a hosted cloud OCR or document-AI service instead of an engine running inside their boundary
- [ ] Whether you keep page numbers for citations
- [ ] Whether you can extract text layers from born-digital PDFs
> Text-layer extraction and chunking can run anywhere approved, but a hosted OCR or vision service sends
> page images to a third party. Residency constraints push OCR and vision models inside the boundary.
