---
id: image-embeddings
module: multimodal
name: Image embeddings
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - multimodal/clip
  - multimodal/vision-language-models
  - multimodal/document-understanding
  - model-internals/embeddings
  - rag-retrieval/similarity-search
  - rag-retrieval/ann-indexes
  - rag-retrieval/embedding-dimension-and-normalization
  - data-architecture/embedding-versioning-re-indexing-strategy
sources:
  - Radford et al., 2021 — Learning Transferable Visual Models From Natural Language Supervision
  - Oquab et al., 2023 — DINOv2: Learning Robust Visual Features without Supervision
  - Liang et al., 2022 — Mind the Gap: Understanding the Modality Gap in Multi-modal Contrastive Representation Learning
  - Faysse et al., 2024 — ColPali: Efficient Document Retrieval with Vision Language Models
---

## TL;DR
An image embedding is a fixed-length vector produced by a neural encoder such that visually or
semantically similar images have nearby vectors, usually under cosine similarity. It exists because
pixels are not comparable (two photos of the same object differ in every pixel) and labels are
expensive; an embedding turns images into something a vector index can search, cluster and deduplicate.
Which encoder you pick decides what "similar" means.

## Why it exists
Ask a database "find images like this one" and it has nothing to work with. Raw pixel distance says a
photo and its slightly cropped copy are far apart while a dark photo of a cat and a dark photo of a car
are close. Perceptual hashes (pHash and friends) fix the first problem, near-duplicate detection, by
hashing coarse frequency structure, but they still know nothing about content: a different photo of the
same product hashes to something unrelated. Tags and metadata solve content search only if someone
wrote the tags, which does not scale and does not cover what the tagger did not think of.

What is needed is a function that maps an image to a point in a space where distance tracks meaning.
Deep networks produce such a function as a by-product of training on a task: the activations just
before the final classifier of an ImageNet model already group dogs with dogs. The modern versions are
trained *for* this property directly, by contrastive or self-distillation objectives, and some are
trained jointly with text so that a sentence and an image can be compared in the same space. Once
images are vectors, every tool built for text embeddings, approximate nearest-neighbour indexes,
clustering, outlier detection, works unchanged.

## How it works
### The encoder decides the geometry
Three families of encoder give three different notions of similarity, and choosing wrong is the most
common failure:

- **Supervised classifier backbones** (ResNet or ViT trained on ImageNet, penultimate layer): cheap and
  fast, but the space is organised around the training label set. Images that share a label cluster;
  distinctions the labels never made (which brand, which colour variant) are weak.
- **Self-supervised visual encoders** (DINO, DINOv2): trained to make different augmentations of the
  same image agree, with no text and no labels. A student network sees small crops, a teacher (a moving
  average of the student's weights) sees larger crops of the same image, and the student is trained to
  match the teacher's output, so the features must encode what survives cropping, colour jitter and
  scale changes: object identity and shape rather than pixels. They produce strong *visual* similarity, including dense
  per-patch features useful for localisation, but they cannot be queried with text.
- **Contrastive image-text encoders** (CLIP, SigLIP, OpenCLIP): trained to match images to captions.
  Their image vectors live in a space shared with text vectors, so one index serves both "find images
  like this" and "find images of a red forklift". Similarity is semantic, caption-like, and weaker on
  purely visual detail than DINOv2.

Commercial multimodal embedding APIs are usually of the third kind. For scanned pages, a fourth option
is a document-screenshot embedder such as ColPali, which keeps one vector per patch and scores queries by
late interaction (sum over query tokens of the best-matching patch), trading index size (on the order
of a thousand small vectors per page instead of one) for far better retrieval of text-heavy page images
than a single pooled vector.

### From pixels to one vector
The image is resized and normalised to the encoder's input size (often 224 or 336 pixels on a side;
many pipelines centre-crop, which silently discards the edges), split into patches, and run through
the network. The patch outputs are pooled into one vector, either by taking a dedicated CLS token or by
averaging patches, then passed through a projection head. The vector is L2-normalised *because* the
training loss compared directions, not magnitudes; after normalisation, cosine similarity equals the dot
product and a vector index can use inner product directly. Dimensions in common models run from a few
hundred to a bit over a thousand; higher is not better by itself, it is only what the model was trained
to emit.

### Similarity, thresholds and the modality gap
Nearest-neighbour search ranks candidates correctly without any threshold. Deciding whether a match is
"good enough" (duplicate or not, on-topic or not) needs one, and thresholds are model-specific: CLIP
image-image cosines for near-duplicates may sit above 0.9 while a correct text-to-image match may score
around 0.3, illustrative numbers that vary by checkpoint. The reason text and image scores live in
different ranges is the modality gap: in contrastive models, all image vectors occupy one cone of the
space and all text vectors another, so cross-modal similarities are systematically lower than
within-modal ones. Tune separate thresholds per query direction on labelled examples.

### When the off-the-shelf geometry is wrong
If your notion of "similar" is not what any pretrained objective rewarded (same SKU across colourways,
same defect type across parts), no threshold will fix it, because the neighbours themselves are wrong.
The usual escalation is: re-rank top-k candidates with metadata or a VLM; train a small linear or MLP
adapter on frozen embeddings using labelled positive and negative pairs (cheap, and the base index can
be re-projected without re-encoding images); or fine-tune the encoder with a contrastive or triplet loss
on your pairs, which moves the space most but forces a full re-embed and risks forgetting general
features. Start from the cheapest step and measure recall@k on held-out pairs after each one.

### Where embeddings go
Each vector is stored with an identifier and metadata in a vector index; an approximate nearest-neighbour
structure (HNSW or IVF) makes search sublinear. The same practices as text embeddings apply: record the
model name and version with every vector, because vectors from two checkpoints are not comparable and a
model upgrade means a full re-index; keep the preprocessing (resize, crop, colour conversion) identical
at index and query time, because it is part of the model.

Storage is simple arithmetic: `n_images × dim × bytes_per_value`. Ten million 768-d float32 vectors are
10⁷ × 768 × 4 ≈ 31 GB before index overhead; float16 halves that, int8 scalar quantisation quarters it,
and binary or product quantisation shrink it further at a recall cost you measure, typically recovered by
re-scoring the top candidates with full-precision vectors.

## Example
Embed a folder of images with a CLIP checkpoint via sentence-transformers, which accepts PIL images
directly, then answer both an image query and a text query from the same matrix. Normalisation is done
by the library so cosine similarity is a plain matrix product. Sizes are small enough for NumPy; swap
in an ANN index once the matrix stops fitting in memory.

```python
import numpy as np
from pathlib import Path
from PIL import Image
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("clip-ViT-B-32")               # image + text encoders, 512-d, normalised
paths = sorted(Path("catalog").glob("*.jpg"))
images = [Image.open(p).convert("RGB") for p in paths]
E = model.encode(images, batch_size=64, normalize_embeddings=True)   # shape [n_images, 512]
np.save("catalog_clip-ViT-B-32.npy", E)                    # name the file after the model version

def top_k(query_vec, k=5):
    sims = E @ query_vec                                    # cosine similarity, since both are unit length
    idx = np.argsort(-sims)[:k]
    return [(paths[i].name, float(sims[i])) for i in idx]

q_img = model.encode(Image.open("query.jpg").convert("RGB"), normalize_embeddings=True)
print("image->image", top_k(q_img))     # near-duplicates score high, e.g. > 0.9 (illustrative)

q_txt = model.encode("a red forklift in a warehouse", normalize_embeddings=True)
print("text->image ", top_k(q_txt))     # correct matches score lower, e.g. ~0.3 (modality gap)

# Duplicate sweep: pairwise sims above a tuned threshold within the catalogue itself.
S = E @ E.T
np.fill_diagonal(S, 0)
dupes = np.argwhere(np.triu(S) > 0.95)
print(len(dupes), "candidate duplicate pairs")
# memory: E is 10,000 x 512 x 4 bytes = ~20 MB, but S is 10,000 x 10,000 x 4 bytes = 400 MB;
# past ~10k images compute S in row blocks or ask an ANN index for each row's top-k instead.
```

## Trade-offs
- Contrastive image-text encoders give one index for text and image queries, but their purely visual
  similarity is weaker than DINOv2's; pick the self-supervised encoder when queries are always images.
- One pooled vector per image is compact and fast to search, while per-patch (late-interaction)
  embeddings retrieve dense pages far better at roughly two orders of magnitude more storage per page
  (about a thousand 128-d vectors versus one) and a costlier scoring step.
- Centre-cropping and resizing to a small square keep encoding cheap but drop edge content and fine
  print; when small details decide relevance, tile the image and index each tile.
- A bigger model yields better neighbours yet multiplies indexing cost over millions of images; for
  dedup or coarse grouping a small checkpoint is usually sufficient and worth measuring first.

## Pitfalls
- Mixing vectors from different checkpoints or preprocessing in one index; the symptom is retrieval
  quality that collapses after a "minor" model upgrade without any error being raised.
- Reusing a threshold across query directions; the symptom is text queries returning nothing at a
  threshold tuned for image-to-image, or everything at one tuned the other way.
- Using a semantic encoder for exact-duplicate detection; the symptom is "duplicates" that are merely
  similar products, or missed duplicates that differ by a watermark the model considers meaningful.
- Skipping EXIF orientation and colour-space handling; the symptom is phone photos embedded upside down
  or in CMYK garbage, so matches fail for a subset of uploads only.
- Forgetting that the index stores content; embeddings of sensitive images are still sensitive data and
  can be inverted or matched, and the symptom is a privacy review that blocks launch.

## When to use
Reach for image embeddings when the job is search, deduplication, clustering, recommendation or
nearest-neighbour moderation over many images, where each item is compared with many others and a
per-image LLM call would be too slow or expensive. Use a VLM instead when you need a reasoned answer
about one image, and OCR or a document parser when the content is text. Decide the encoder by the query
type: text queries need a contrastive image-text model; image-only queries favour self-supervised
encoders; page images favour late-interaction document embedders. Measure recall@k against labelled
judgements and the precision at your chosen threshold. Interview questions typically probe why cosine
similarity, why normalisation, and why text-to-image scores differ from image-to-image.

## Role lens
- **Engineer:** Owns the preprocessing-encode-normalise-store path, versions the embedding model in the
  index metadata, and keeps query-time preprocessing byte-identical to index-time preprocessing.
- **Architect:** Picks the encoder family per query type, sizes the index (vectors × dimension × bytes),
  and plans re-indexing as a scheduled migration rather than an emergency.
- **FDE:** Runs a labelled similarity study on the customer's own images to set thresholds, and shows
  them where embedding search ends and human or VLM review must begin.

## Check yourself
**Q:** A moderation team wants to block re-uploads of previously removed images, including recompressed,
resized and lightly cropped copies, with near-zero false positives. Which embedding approach and matching
policy would you propose, and why not a general CLIP index alone?
**A:** The task is near-duplicate detection, not semantic similarity. A CLIP space clusters by meaning,
so two different photos of a similar scene can score higher than a cropped copy of the banned image,
giving both false positives and misses. Propose a two-stage policy: a cheap perceptual hash or a
self-supervised visual encoder tuned for copy detection as the first filter, with a conservative
threshold set on a labelled set of true copies versus near-miss lookalikes, then a second check (pixel
alignment or human review) before blocking. Store the model version with each vector, keep preprocessing
identical at ingest and query, and measure false-positive rate on legitimate uploads because that is the
constraint the team stated.

## Quiz
### Q: Why are image embeddings L2-normalised before they are stored in a vector index?
- [ ] Normalisation compresses the vectors so they take less disk space
- [x] The encoders were trained on direction, so cosine similarity is the meaningful metric and it becomes a plain dot product
- [ ] Vector databases cannot store values larger than one
- [ ] It removes the modality gap between text and image vectors
> Contrastive and self-supervised losses compare angles, not magnitudes. Unit vectors make inner product
> equal cosine similarity, which ANN indexes compute fast; normalisation does not change storage or the gap.

### Q: A team needs "find photos matching this sentence" and "find photos like this photo" from one index. Which encoder family fits?
- [ ] An ImageNet-supervised ResNet's penultimate layer
- [ ] A self-supervised encoder such as DINOv2
- [x] A contrastive image-text encoder such as CLIP or SigLIP
- [ ] A perceptual hash
> Only image-text contrastive models place text and images in one shared space, so both query types hit
> the same vectors. DINOv2 and ResNet features cannot be queried with text at all.

### Q: After tuning a similarity threshold of 0.85 on image-to-image matches, text queries return no results. What is the most likely cause?
- [ ] The text encoder was not loaded correctly
- [ ] Text embeddings have a different dimension from image embeddings
- [x] Cross-modal similarities are systematically lower than within-modal ones because of the modality gap
- [ ] The vector index only supports image vectors
> In contrastive models, image and text vectors occupy separate regions, so a correct text-to-image match
> scores well below a correct image-to-image match. Thresholds must be tuned per query direction.
