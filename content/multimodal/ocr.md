---
id: ocr
module: multimodal
name: OCR
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - multimodal/document-understanding
  - multimodal/vision-language-models
  - rag-retrieval/document-parsing
  - data-architecture/document-processing-pipelines
  - enterprise-integration/client-document-parsing
  - rag-retrieval/chunking-strategies
  - prompting/prompt-injection
sources:
  - Smith, 2007 — An Overview of the Tesseract OCR Engine
  - Graves et al., 2006 — Connectionist Temporal Classification
  - Shi et al., 2017 — An End-to-End Trainable Neural Network for Image-based Sequence Recognition (CRNN)
  - Li et al., 2021 — TrOCR: Transformer-based Optical Character Recognition with Pre-trained Models
---

## TL;DR
OCR turns pixels that depict text into text: it finds where text is, recognises the characters in each
region, and returns strings with positions and confidences. It exists because a large share of
enterprise information lives in scans, photos and image-only PDFs that have no text layer, and every
downstream step, search, extraction, RAG, LLM reasoning, needs characters rather than pixels. The hard
parts are layout, image quality and knowing when the output is wrong.

## Why it exists
A PDF generated from Word carries its text as text; a PDF produced by a scanner is a picture of a page.
Faxes, phone photos of receipts, screenshots, ID cards and engineering drawings are all pictures. To a
text pipeline they are opaque blobs. The naive fix, "ask a vision-language model to read it", works for
a page or two but fails as a system: it is slow and expensive per page, it gives no per-word
confidence, it cannot return coordinates for highlighting or redaction, and when a digit is unclear it
produces a plausible digit instead of a flagged one. Invoices and medical records cannot absorb that.

OCR exists as a dedicated capability because recognising printed characters is a well-posed problem
with decades of engineering behind it: deterministic output, word-level confidence, bounding boxes,
thousands of pages per hour on a CPU. The real difficulty was never the letter "A" in a clean font. It
is deciding which pixels are text, in what order they should be read, what to do with a skewed
low-resolution scan, and how a table's cells map to rows. Modern OCR is a pipeline that attacks each of
those separately, and the engineering judgement is about which stage is failing when the text comes out
wrong.

## How it works
### Stage 1: preprocessing
Recognition accuracy depends on input quality more than on the recogniser. Typical steps: convert to
greyscale, upscale small images (classic engines are tuned for roughly 300 dots per inch, a widely
used rule of thumb), deskew by estimating the dominant text angle, binarise with an adaptive threshold
so uneven lighting does not turn half the page black, and remove noise and borders. Each step exists
because the recogniser was trained on clean, horizontal, high-contrast glyphs and degrades sharply
outside that distribution.

### Stage 2: detection and layout analysis
The engine must find text before reading it. Classic engines (Tesseract) use connected-component
analysis: group blobs of ink into characters, characters into lines, lines into blocks, and infer
reading order from block geometry. Deep detectors (CRAFT, DBNet, used by EasyOCR and PaddleOCR) are
segmentation networks that predict a text-probability map and extract word or line polygons, which is
far more robust on photos, curved text and cluttered backgrounds. Layout analysis also assigns a page
segmentation mode: single column, multi-column, single line, sparse text. Choosing the wrong mode is
the most common reason columns get interleaved.

### Stage 3: recognition
Each detected line or word image is recognised as a sequence. The dominant design is a CNN or
transformer feature extractor over the image width, producing one feature vector per horizontal slice,
followed by a sequence model and the CTC loss (Connectionist Temporal Classification). CTC lets the
network emit a character or a blank (written ∅ here) at every slice without knowing character
boundaries in advance. Decoding applies two rules in order: collapse runs of the same symbol, then delete
blanks.

```
slices:          h h e ∅ l l ∅ l o o
collapse runs:   h e ∅ l ∅ l o
drop blanks:     h e l l o          -> "hello"
without the ∅ between the l's, "h e l l l o" collapses to "helo"
```

The blank is therefore what lets the network express a genuine double letter. Training works because
many slice sequences decode to the same string; CTC's loss is the negative log of the *sum* of the
probabilities of all of them, computed efficiently with a forward-backward dynamic programme, so the
network is never told which slice holds which glyph. The design choice matters for failure analysis:
because each slice is predicted largely independently, a CTC recogniser has no strong notion of which
character sequences are plausible, which is why a separate language model is bolted on afterwards.
Tesseract moved to an LSTM-plus-CTC recogniser in version 4. Transformer encoder-decoder recognisers (TrOCR) drop CTC and generate characters
autoregressively, which handles handwriting and irregular text better at higher cost.

### Stage 4: post-processing and output
Raw recognitions are refined with a dictionary or a character-level language model (which fixes "rn"
versus "m" in words but can *introduce* errors in codes and IDs, which is why engines let you disable
it). The output is structured: for each word, its text, a confidence score, and a bounding box, often in
hOCR or ALTO formats. Confidence is the handle for everything downstream: route low-confidence fields to
a human, re-run a region at higher resolution, or decline to extract.

### Measuring it
Character error rate (CER) is (substitutions + deletions + insertions) / characters in the reference;
word error rate (WER) is the same over words. Measure on your own scans, because published numbers come
from clean benchmarks. Separately track layout errors (wrong reading order, merged columns), which CER
on a flattened string hides.

### The alternatives named in practice
Cloud document OCR (AWS Textract, Google Document AI, Azure Document Intelligence) bundles detection,
recognition, tables and form key-value pairs with strong accuracy, charged per page and subject to data
residency. VLM-based OCR reads anything a human can, including messy layouts, but is slower, costlier,
and hallucinates under uncertainty with no confidence to warn you. Classic engines are free, run
offline on CPUs and give boxes and confidences, but need preprocessing care and struggle with handwriting
and photos. Most production systems combine them: a cheap engine first, escalation on low confidence.

## Example
Tesseract through pytesseract, with the preprocessing steps that make the biggest difference and the
word-level data structure that lets you act on confidence rather than trusting a flat string. Requires
the Tesseract binary and OpenCV.

```python
import cv2, pytesseract
from pytesseract import Output

def preprocess(path, min_width=2000):
    img = cv2.imread(path)
    if img.shape[1] < min_width:                      # upscale small scans toward ~300 dpi
        s = min_width / img.shape[1]
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 15)   # handles uneven lighting

img = preprocess("invoice_scan.jpg")
cfg = "--oem 1 --psm 6 -l eng"       # LSTM engine; psm 6 = assume a uniform block of text
d = pytesseract.image_to_data(img, config=cfg, output_type=Output.DICT)

words = []
for text, conf, x, y, w, h in zip(d["text"], d["conf"], d["left"], d["top"], d["width"], d["height"]):
    if text.strip():                                   # conf is -1 for non-word boxes
        words.append({"text": text, "conf": float(conf), "box": (x, y, w, h)})

low = [w for w in words if w["conf"] < 60]             # threshold tuned on your scans
print(len(words), "words;", len(low), "below confidence threshold")
for w in low[:5]:
    print(w)                                           # e.g. {'text': '1O2.5O', 'conf': 41.0, 'box': (...)}
# Route pages with many low-confidence amount fields to a VLM pass or human review,
# and keep the boxes: they let you highlight, redact, and crop regions for re-OCR.
```

## Trade-offs
- Classic engines are free, offline and give per-word confidence and boxes, but need preprocessing and
  degrade on photos and handwriting; cloud OCR flips the trade when layout and tables matter most.
- A character language model raises accuracy on prose while corrupting serial numbers and codes, so
  disable dictionary correction for fields that are not words.
- Higher resolution improves recognition up to a point, after which cost rises with no gain; find the
  plateau on your own documents rather than scanning everything at maximum DPI.
- VLM OCR handles arbitrary layouts in one call but trades away confidence scores and determinism, which
  is acceptable for summarisation and unacceptable for amounts that feed a ledger.

## Pitfalls
- Feeding low-resolution or skewed images straight in; the symptom is characters merged or split and a
  CER that halves after simple upscaling and deskewing.
- Leaving the default page segmentation mode on multi-column or sparse layouts; the symptom is lines from
  neighbouring columns interleaved into nonsense sentences.
- Flattening a table to text before chunking for RAG; the symptom is retrieved chunks containing numbers
  with no column header, so the LLM attaches the wrong label to the right value.
- Ignoring confidence and treating OCR as truth; the symptom is "1O2.5O" style errors reaching a database
  where a quick confidence gate would have flagged them.
- Treating an engine's confidence as a calibrated probability or comparing it across engines; the
  symptom is a "95-confidence" field that is wrong far more often than 5% of the time, found only when
  you plot error rate against confidence on a labelled sample and set the threshold from that curve.
- Forgetting that OCR text is untrusted input; the symptom is an agent that follows instructions printed
  on a scanned document, a prompt-injection path via images.
- Assuming the PDF has no text layer and OCR-ing born-digital pages; the symptom is avoidable errors and
  cost on text that was already perfect, when a text-layer check would have skipped OCR.

## When to use
Use dedicated OCR whenever you need text with positions and confidence from scans, photos or image
PDFs at volume: search indexing, redaction, extraction into systems of record, and as grounding text
for an LLM so it reads rather than guesses. Check first whether the document already has a text layer.
Escalate to cloud document OCR when tables and forms dominate, and to a VLM when the layout is
irregular and the output feeds a human rather than a ledger. Measure CER and WER on a labelled sample
of your own scans, plus the fraction of fields below your confidence threshold, since that is your
human-review workload. In interviews, be ready to walk the pipeline, explain what CTC solves, and say
how you would detect that OCR, not the LLM, is the failing stage.

## Role lens
- **Engineer:** Builds the preprocess-detect-recognise path, chooses segmentation modes per document
  type, and surfaces confidence and boxes rather than a flat string to downstream code.
- **Architect:** Decides the escalation ladder from cheap engine to cloud OCR to VLM, with data-residency
  and per-page cost as the constraints, and where human review sits in the flow.
- **FDE:** Profiles the customer's real scan quality, finds the DPI and preprocessing that hit the
  accuracy plateau, and sets the confidence threshold against their tolerance for review work.

## Check yourself
**Q:** An extraction pipeline on scanned purchase orders has acceptable accuracy on item descriptions but
frequent errors in part numbers and totals. The team proposes replacing the OCR engine with a VLM. What
would you investigate first and why might the swap make things worse?
**A:** Part numbers and totals are codes and digits, where two specific OCR behaviours bite: dictionary
or language-model correction rewrites unfamiliar codes into dictionary-like strings, and low resolution
makes 0/O and 1/l indistinguishable. First check the per-field confidence on those regions, disable
dictionary correction for code fields, upscale and re-OCR the low-confidence crops, and confirm the
correct page segmentation mode so table cells are not merged. A VLM swap can make it worse because a VLM
under the same visual ambiguity emits a confident plausible digit with no confidence score, so errors
stop being detectable. Use the VLM, if at all, only as an escalation for low-confidence regions, and add
arithmetic validation (line items sum to total) so residual errors are caught.

## Quiz
### Q: What problem does CTC solve in a text-line recogniser?
- [ ] It detects where text lines are located on the page
- [x] It aligns a per-slice sequence of predictions to a shorter character string without labelled character boundaries
- [ ] It corrects recognised words against a dictionary
- [ ] It deskews the input image before recognition
> The network emits one output per horizontal slice of the image; CTC collapses repeats and blanks so a
> variable-width image maps to the right character sequence without knowing where each glyph starts.

### Q: Columns from a two-column scanned report come out interleaved line by line. Which stage is the most likely culprit?
- [ ] The character recogniser confusing similar glyphs
- [ ] Insufficient scan resolution
- [x] Layout analysis using a page segmentation mode that assumes a single block of text
- [ ] The language-model post-processor
> Interleaving is a reading-order error, which is decided by layout analysis before any character is
> recognised. Resolution and glyph confusion produce wrong characters, not scrambled line order.

### Q: Why is per-word confidence the main reason to prefer a dedicated OCR engine over a VLM for amounts feeding a ledger?
- [ ] VLMs cannot read digits
- [ ] Confidence scores make the OCR engine faster
- [x] Confidence lets uncertain values be routed to re-OCR or human review instead of silently accepted
- [ ] VLMs are not allowed to process financial documents
> A VLM resolves ambiguity by producing a plausible value with no signal that it guessed. A confidence
> score turns the same ambiguity into a flag, which is what a review workflow needs.
