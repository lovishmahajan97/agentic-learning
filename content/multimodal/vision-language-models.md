---
id: vision-language-models
module: multimodal
name: Vision-language models
kind: concept
difficulty: core
est_minutes: 15
related:
  - multimodal/clip
  - multimodal/image-embeddings
  - multimodal/ocr
  - multimodal/document-understanding
  - model-internals/transformer
  - model-internals/context-window
  - prompting/structured-outputs-json-mode
  - agents/computer-use
sources:
  - Dosovitskiy et al., 2020 — An Image is Worth 16x16 Words (ViT)
  - Alayrac et al., 2022 — Flamingo: a Visual Language Model for Few-Shot Learning
  - Li et al., 2023 — BLIP-2: Bootstrapping Language-Image Pre-training with Frozen Image Encoders and Large Language Models
  - Liu et al., 2023 — Visual Instruction Tuning (LLaVA)
---

## TL;DR
A vision-language model (VLM) lets a language model take images as input by turning each image into a
sequence of "visual tokens" that sit in the same embedding space as text tokens. A pretrained vision
encoder produces patch features, a small connector projects them into the LLM's input space, and the
LLM attends over image and text jointly. It exists because describing an image as text first throws
away exactly the details the question will turn out to need.

## Why it exists
Before VLMs, the way to get an LLM to "see" was a pipeline: run OCR or an image captioner, paste the
text into the prompt, and hope. That fails for a structural reason. A caption is a lossy compression
chosen *before* the question is known. "A bar chart of quarterly revenue" cannot answer "which quarter
dropped, and by how much?"; an OCR dump of an invoice loses which number sits in the *Total* box. Every
stage in the pipeline also has its own error rate, and errors compound: the LLM reasons confidently
over a mis-read digit it never had the chance to doubt.

The second problem is that text-only models have no grounding for spatial and visual concepts. They
know the word "left of" but have never been shown anything to the left of anything. Tasks like reading
a UI screenshot to click a button, checking whether a product photo matches its listing, or
interpreting a radiology scan need the model to reason over pixels and words in the same forward pass.

The naive fix, "train a transformer on raw pixels and text from scratch", is possible (early-fusion
models do it) but expensive: the LLM has already learned language from trillions of tokens, and the
vision encoder has already learned visual features from hundreds of millions of images. The dominant
VLM design exists to *reuse* both and spend training only on the glue between them.

## How it works
### Step 1: the image becomes a sequence
Transformers consume sequences, so the image must be serialised. A Vision Transformer (ViT) cuts the
image into fixed-size patches, flattens each patch's pixels, projects it with a linear layer, adds a
position embedding, and runs standard transformer blocks over the patch sequence. With 16×16-pixel
patches a 224×224 image becomes 14 × 14 = 196 patch tokens; with 14-pixel patches at 336×336 (the
CLIP ViT-L/14-336 encoder used by LLaVA-1.5) it becomes 24 × 24 = 576 tokens. The encoder is usually a
contrastively pretrained one (CLIP or SigLIP) *because* those encoders were trained to align visual
features with language already, which makes the later alignment step much cheaper than starting from a
classifier backbone.

### Step 2: a connector maps vision features into the LLM's token space
The encoder's output vectors live in its own space, with its own dimension and statistics. A connector
translates them into vectors the LLM can treat as if they were token embeddings. Three designs recur:

- **Linear or MLP projector** (LLaVA): one vector per patch, projected to the LLM hidden size. Simple,
  keeps all spatial detail, but every patch becomes an LLM token, so cost scales with resolution.
- **Resampler / Q-Former** (Flamingo's Perceiver resampler, BLIP-2's Q-Former): a fixed set of learned
  query vectors cross-attend to the patch features and emit a fixed number of tokens (e.g. 32 or 64)
  regardless of image size. Cheaper per image, at the cost of compressing fine detail.
- **Cross-attention into the LLM** (Flamingo, some Llama-vision variants): image features are not
  inserted as tokens at all; gated cross-attention layers added between LLM blocks read them. The text
  context is untouched, and the gates start at zero so the pretrained LLM is not disturbed at init.

### Step 3: the LLM reasons over both
After the connector, the input is a single sequence: text tokens, then image tokens at the point where
the image appears, then more text. Self-attention does the rest: a text query token can attend to the
patch tokens that contain the relevant region. Nothing about the decoder changes; it still predicts the
next text token. This is why a VLM inherits its parent LLM's instruction following, tool calling and
weaknesses almost unchanged.

### Training recipe
Most open VLMs follow a two-stage recipe. Stage one freezes both the vision encoder and the LLM and
trains only the connector on image-caption pairs, so the projector learns to speak the LLM's language
without the LLM drifting. Stage two unfreezes the LLM (sometimes the encoder) and trains on visual
instruction data: questions about images, OCR-style tasks, charts, documents, grounding with bounding
boxes. The instruction data is what turns "can describe an image" into "can answer a question about it".

### Resolution, tiling and token cost
Fine detail (small text, chart labels) needs pixels, but a fixed-resolution encoder downsamples
everything to one size. Common answers: tile a high-resolution image into several crops and feed each
tile's tokens plus a downscaled global view (LLaVA-NeXT's "AnyRes"), or let the encoder accept variable
numbers of patches so token count follows native resolution (Qwen2-VL-style dynamic resolution). Either
way, image tokens consume context and are billed as tokens; a single high-resolution page can cost on the
order of a thousand tokens or more, an illustrative figure that varies by model and vendor. Pre-resizing
images to the detail you actually need is the cheapest optimisation available.

### Early-fusion variants
Some models skip the separate encoder: Fuyu feeds linearly projected raw patches straight into the
decoder, and models described as "natively multimodal" are pretrained on interleaved image and text
from the start. They avoid the encoder's resolution bottleneck and modality seam, at the price of far
more pretraining compute. For an application engineer the interface is identical.

## Example
Any VLM served behind an OpenAI-compatible chat API (vLLM does this for open models; hosted vendors use
the same shape) accepts an image as a base64 data URL inside the message content. The snippet downsizes
the image first, asks a question that a caption could not answer, and forces a JSON reply for parsing.

```python
import base64, io, json
from PIL import Image
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")  # e.g. a vLLM server
MODEL = "<served-vlm-name>"

def to_data_url(path, max_side=1024):
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))            # fewer pixels -> fewer image tokens
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

question = ("This is a revenue bar chart. Which quarter had the largest drop versus the "
            "previous quarter, and what were the two values? Reply as JSON with keys "
            "quarter, previous_value, value, units.")
resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": to_data_url("q_revenue.png")}},
    ]}],
    temperature=0,
)
answer = json.loads(resp.choices[0].message.content)
print(answer)   # {'quarter': 'Q3', 'previous_value': 4.2, 'value': 3.1, 'units': 'USD millions'}
print(resp.usage.prompt_tokens)  # watch this grow with image resolution
```

## Trade-offs
- Reusing a pretrained encoder and LLM makes training cheap and inherits strong language skills, but the
  encoder's fixed resolution caps fine detail; it flips when your inputs are dense documents or tiny text.
- Projector designs keep every patch and preserve detail, while resamplers emit a fixed token budget and
  cut cost; choose the resampler when you process many images per request and detail matters less.
- Tiling high-resolution images recovers small text at the cost of several times the tokens and latency,
  which is worth it for OCR-like tasks and wasteful for "what is in this photo" tasks.
- A general VLM replaces a chain of specialised models (captioner, OCR, classifier) with one call, but
  gives no per-field confidence, so specialised models win when you need calibrated scores or audits.

## Pitfalls
- Sending full-resolution screenshots by default; the symptom is prompt-token bills and latency that
  dwarf the text part of the request, with no accuracy gain over a resized image.
- Trusting read-out numbers and strings without a check; VLMs hallucinate plausible digits in tables, and
  the symptom is answers that look right until reconciled against a total or a source system.
- Asking for counts or exact spatial relations; many VLMs still count and localise poorly, and the symptom
  is confident wrong answers on "how many" and "which is left of which" that a human spots instantly.
- Assuming image input is immune to prompt injection; text rendered inside an image is read as text, and
  the symptom is an agent following instructions embedded in a screenshot or a scanned document.
- Evaluating on captioning quality when the task is question answering; the symptom is a model that
  describes images beautifully and fails the actual extraction your product needs.

## When to use
Reach for a VLM when the answer depends on information that a caption or OCR dump would discard: charts,
UI screenshots for computer-use agents, product photos checked against text, forms with layout, visual
inspection with a natural-language rubric. Prefer plain OCR when you only need the text and want per-word
confidence, and prefer CLIP-style embeddings when the job is search or classification over many images
at low cost. Measure three things: task accuracy on a held-out set of your own images, image tokens per
request at the resolution you ship, and hallucination rate on fields you can verify. In interviews,
expect to explain the encoder-connector-LLM pipeline, why the connector is trained first, and how
resolution turns into tokens and cost.

## Role lens
- **Engineer:** Resizes and tiles images deliberately, pins image token counts in tests, and wraps VLM
  outputs in structured parsing plus validation before anything downstream trusts them.
- **Architect:** Decides where a VLM replaces an OCR-plus-rules pipeline and where it merely supplements
  it, and sets the cost and latency budget per page or screenshot.
- **FDE:** Benchmarks the candidate VLM on the customer's real scans and screenshots, finds the resolution
  at which accuracy plateaus, and documents the failure modes the customer's reviewers must catch.

## Check yourself
**Q:** A customer wants to extract line items from scanned invoices with a VLM. Accuracy is good on
clean samples but small-font quantities are frequently wrong. What is happening and what would you
change, in order of cost?
**A:** The vision encoder runs at a fixed input resolution, so a full page is downsampled until small
digits occupy a patch or less and become unreadable; the LLM then fills in plausible values. Cheapest
fix: crop the line-item region or tile the page so each tile is near the encoder's native resolution,
sending a downscaled global view alongside. Next: choose a model with dynamic resolution or a larger
tile budget and measure the token cost increase. Then: run a classic OCR pass and give the VLM the text
with coordinates as extra context so it reads rather than guesses. Finally, add validation such as
quantity × unit price ≈ line total so remaining errors are caught instead of silently accepted.

## Quiz
### Q: Why do most open VLMs train the connector first with the LLM frozen?
- [ ] The connector is the largest component and needs the most data
- [x] It aligns visual features to the LLM's input space without letting the LLM's language ability drift
- [ ] Freezing the LLM is required because image tokens cannot carry gradients
- [ ] It allows the vision encoder to be trained from scratch on captions
> The LLM already knows language; stage one only teaches the small projector to emit vectors the LLM
> understands. Unfreezing everything on caption data would degrade the LLM's instruction following.

### Q: What primarily determines how many context tokens an image consumes in a projector-style VLM?
- [ ] The file size in bytes after JPEG compression
- [ ] The number of distinct objects detected in the image
- [x] The number of encoder patches, which follows image resolution and tiling
- [ ] The length of the user's text question
> Each patch becomes one token after projection, so a 24 × 24 patch grid yields 576 tokens and tiling
> multiplies that. Compression and object count do not change the patch grid.

### Q: A pipeline captions images with one model and sends the caption to an LLM. What is the core limitation a VLM removes?
- [ ] Captioners cannot run on GPUs efficiently
- [ ] LLMs cannot read captions longer than a sentence
- [x] The caption discards details before the question is known, so later questions cannot recover them
- [ ] Captions are always in a different language from the prompt
> A caption is a lossy summary fixed in advance; a VLM attends to the pixels in the same forward pass as
> the question, so it can look up details the question demands rather than relying on what was kept.
