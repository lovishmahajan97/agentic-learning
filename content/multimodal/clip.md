---
id: clip
module: multimodal
name: CLIP
kind: concept
difficulty: core
est_minutes: 15
status: fact-checked
related:
  - multimodal/image-embeddings
  - multimodal/vision-language-models
  - model-internals/embeddings
  - rag-retrieval/similarity-search
  - rag-retrieval/embedding-models
  - ml-fundamentals/supervised-vs-unsupervised-vs-self-supervised-learning
  - prompting/zero-shot
sources:
  - Radford et al., 2021 — Learning Transferable Visual Models From Natural Language Supervision
  - van den Oord et al., 2018 — Representation Learning with Contrastive Predictive Coding
  - Zhai et al., 2023 — Sigmoid Loss for Language Image Pre-Training (SigLIP)
  - Cherti et al., 2023 — Reproducible Scaling Laws for Contrastive Language-Image Learning (OpenCLIP)
---

## TL;DR
CLIP trains an image encoder and a text encoder together so that an image and its caption land close
in one shared vector space, using a contrastive loss over large batches of (image, text) pairs scraped
from the web. Because the labels are free-form text rather than a fixed class list, the result can
classify, search and filter images by *description*, zero-shot, and its encoders became the standard
visual front end for vision-language models and multimodal search.

## Why it exists
Supervised image classifiers learn a closed vocabulary. An ImageNet model knows 1,000 nouns and nothing
else; to add "forklift blocking a fire exit" you collect and label thousands of images, retrain, and
still cannot ask about "a forklift blocking a *different* exit". The labelling cost is the limiting
factor, and the learned features are biased toward whatever distinguishes the chosen classes.

The web already contains hundreds of millions of images with text next to them: alt text, captions,
product titles. That text is noisy but free and open-vocabulary. The question is how to learn from it.
The obvious route, train a captioning model that predicts the exact words, spends enormous capacity on
predicting the *phrasing* of a caption ("a photo of", "stock image") rather than its content, and the
CLIP authors found it learned far more slowly than a bag-of-words baseline.

The reframing that works is to stop predicting words and instead predict *which caption goes with which
image*. That is a matching problem: among a batch of pairs, pick the right partner. It only needs the
representation to capture content that distinguishes captions from each other, which is exactly the
semantic content a downstream search or classifier wants. CLIP exists because matching scales where
generation and labelling did not.

## How it works
### Two encoders, one space
An image encoder (ResNet or, in the stronger variants, a Vision Transformer such as ViT-B/32 or
ViT-L/14) maps an image to a vector. A text encoder (a transformer over byte-pair-encoded text with a
maximum length of 77 tokens) maps a caption to a vector. Each is followed by a linear projection to a
common dimension (512 for ViT-B/32, 768 for ViT-L/14) and L2-normalised, so similarity is a cosine
similarity, a dot product of unit vectors.

### The contrastive objective
Take a batch of N (image, text) pairs. Compute all N × N cosine similarities between every image and
every text, scaled by a learned temperature (initialised so that logits are multiplied by about 1/0.07
and clipped so the scale never exceeds 100). The N diagonal entries are the true pairs; the N² − N
off-diagonal entries are negatives. The loss is a symmetric cross-entropy: for each image, a softmax
over the N texts should put its mass on the matching text, and for each text, a softmax over the N
images should pick the matching image. The two directions are averaged. This is the InfoNCE loss; it
pushes matching pairs together and all other pairs in the batch apart.

Two consequences follow. First, the batch *is* the negative set, so larger batches give harder, more
informative negatives; CLIP used a batch of 32,768 pairs, and the training data was about 400 million
pairs. Second, the loss is relative: it never says what absolute similarity a match should have, only
that the match must beat the others in the batch. That is why raw CLIP scores are not calibrated
probabilities and why thresholds must be tuned per task.

### Zero-shot classification by prompting
To classify an image into classes you never trained on, write each class as a sentence, "a photo of a
{label}", encode all sentences, encode the image, and take the softmax over the cosine similarities. The
text encoder turns a class list into a classifier weight matrix on the fly. Prompt ensembling (averaging
"a photo of a {}", "a blurry photo of a {}", "a drawing of a {}") improves accuracy because the training
captions were varied too. Reported zero-shot ImageNet top-1 for the largest CLIP variant was in the
mid-70s percent, matching a supervised ResNet-50 without using any ImageNet labels; the exact number is
in the paper.

### Why this representation transfers
Because training only rewarded distinguishing captions, the encoder learns whatever visual evidence
correlates with words: objects, scenes, styles, text in the image, brands. It does not learn to count
or to encode precise spatial layout, because captions rarely make those distinctions. The representation
is therefore excellent for retrieval and coarse classification and weak for compositional reasoning,
which is the reason VLMs bolt an LLM on top of it rather than using it alone.

### Successors and variants
OpenCLIP reproduced and scaled the recipe on public data (LAION). SigLIP replaces the batch-wide softmax
with an independent sigmoid loss per pair, so each (image, text) pair is a binary "match or not"
decision; this removes the need for a global normalisation across the batch, makes memory scale better,
and gives somewhat better encoders that many recent VLMs use. The mechanism, two encoders aligned by
positives-versus-negatives, is unchanged.

## Example
Zero-shot classification with the Hugging Face CLIP implementation, then the same thing by hand with
the raw embeddings so the cosine-similarity-and-softmax mechanism is visible. The second form is what
you reuse for search: encode once, compare many times.

```python
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

name = "openai/clip-vit-base-patch32"
model = CLIPModel.from_pretrained(name).eval()
proc = CLIPProcessor.from_pretrained(name)

image = Image.open("warehouse.jpg").convert("RGB")
labels = ["a forklift", "a pallet of boxes", "an empty aisle", "a person in a hi-vis vest"]
prompts = [f"a photo of {l}" for l in labels]

with torch.no_grad():
    out = model(**proc(text=prompts, images=image, return_tensors="pt", padding=True))
    probs = out.logits_per_image.softmax(dim=-1)[0]        # scaled cosine sims -> softmax
for l, p in zip(labels, probs):
    print(f"{p:.3f}  {l}")

# Same mechanism by hand: unit vectors, dot product, temperature.
with torch.no_grad():
    img = model.get_image_features(**proc(images=image, return_tensors="pt"))
    txt = model.get_text_features(**proc(text=prompts, return_tensors="pt", padding=True))
img = img / img.norm(dim=-1, keepdim=True)                # [1, 512]
txt = txt / txt.norm(dim=-1, keepdim=True)                # [4, 512]
cos = img @ txt.T                                          # cosine similarities, typically 0.2-0.35
print(cos, (cos * model.logit_scale.exp()).softmax(-1))   # logit_scale.exp() is the learned 1/T
```

The hand-computed cosines are small and close together (the shared space is not spread out), which is
why the learned scale of roughly 100 is needed before the softmax produces a decisive distribution.

## Trade-offs
- Open-vocabulary classification with no labelling costs, but accuracy on fine-grained or domain-specific
  classes (medical, industrial parts) lags a supervised model trained on even a few thousand labels.
- Contrastive training is sample-efficient compared with caption generation, but needs very large
  batches and web-scale data, so training your own CLIP is rarely worth it versus fine-tuning OpenCLIP.
- A single shared space enables text-to-image and image-to-image search from one index, yet scores are
  uncalibrated, so each use direction needs its own threshold tuned on labelled examples.
- Captions as supervision teach what is in an image, not how many or where; when the task is counting,
  reading layout or spatial relations, a VLM or a detector is the right tool instead.

## Pitfalls
- Treating softmax outputs as probabilities of correctness; the symptom is high-confidence predictions
  for images that match none of the offered labels, because softmax must sum to one over your list.
- Using bare labels instead of sentences; the symptom is a measurable accuracy drop, since training text
  looked like captions, and "a photo of a dog" matches the distribution far better than "dog".
- Expecting robustness to text in images; the symptom is an object with a handwritten label being
  classified as the label's word (the typographic-attack effect), which matters for moderation and retail.
- Comparing scores across encoders or across text-to-image versus image-to-image queries; the symptom
  is a threshold that works in one direction and silently returns garbage in the other.
- Truncating long descriptions silently at 77 tokens; the symptom is that a detailed query's later
  constraints have no effect on the results.

## When to use
Use CLIP-family encoders when you need to search, filter, cluster or coarsely classify images by
description without training data: content moderation prefilters, product search, deduplication,
labelling candidate images for a dataset, or as the vision tower when assembling a VLM. Avoid it as the
final decision maker where errors are costly and fine-grained; there, use it to narrow candidates and
let a supervised model or a VLM decide. Measure recall@k on a labelled retrieval set and accuracy at a
tuned threshold, not raw similarity values. Interviewers ask you to write the contrastive loss on a
whiteboard, explain why batch size matters, and say why zero-shot works at all; those three answers
cover the idea.

## Role lens
- **Engineer:** Encodes images and text with the same checkpoint, stores normalised vectors, and tunes
  per-direction thresholds on a labelled sample before exposing search or filtering.
- **Architect:** Chooses between a CLIP-style retrieval layer and a VLM per task, and fixes the encoder
  version as an index dependency so re-embedding is planned, not accidental.
- **FDE:** Builds a quick zero-shot classifier from a customer's taxonomy written as sentences, shows
  the accuracy ceiling on their images, and uses the gaps to justify labelling or fine-tuning.

## Check yourself
**Q:** You index a retailer's catalogue with CLIP so shoppers can type queries. Text-to-image search
works, but the "similar items" feature built on image-to-image similarity returns near-identical
colours and ignores category. Why, and what would you do?
**A:** The contrastive loss shapes the space around what captions distinguish, and image-to-image
distances are dominated by global appearance (colour, background, style) rather than by category,
because no caption ever told the model that two differently coloured jackets are the same product type.
Text and image embeddings also occupy different regions, so a threshold tuned on text queries does not
transfer. Options: compute similarity in the text space by first classifying each image into the catalogue
taxonomy with zero-shot prompts and restricting neighbours to the same class; fine-tune the encoder with
product pairs as positives; or combine CLIP similarity with structured attributes in a hybrid ranker and
evaluate recall@k on labelled "similar item" judgements.

## Quiz
### Q: In CLIP's contrastive loss, where do the negative examples come from?
- [ ] A separately curated set of mismatched captions written by annotators
- [x] The other pairs in the same training batch
- [ ] Random noise vectors added to the image embeddings
- [ ] Images with the same label from a classification dataset
> Every image is contrasted against all other captions in the batch and vice versa, so a batch of N
> pairs supplies N² − N negatives for free; this is why very large batches help.

### Q: Why can CLIP classify into categories it never saw during training?
- [ ] Its image encoder was pretrained on ImageNet with all possible labels
- [ ] The text encoder stores a lookup table of class names
- [x] Class names written as captions are encoded into the shared space and act as classifier weights
- [ ] It retrieves similar images from the training set at inference time
> Zero-shot classification is retrieval over text: encode "a photo of a {label}" for each class and pick
> the closest to the image embedding. No training-set images are stored or needed.

### Q: Which task is CLIP's representation least suited to on its own?
- [ ] Finding duplicate product photos across a catalogue
- [ ] Filtering out images that do not contain a logo
- [x] Reporting how many people stand to the left of a doorway
- [ ] Grouping stock photos by scene type
> Captions rarely encode counts or precise spatial relations, so the contrastive objective never rewarded
> learning them. Retrieval, filtering and scene grouping are exactly what caption matching teaches.
