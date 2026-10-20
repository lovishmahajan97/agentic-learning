---
id: multimodal
title: Multimodal
---

## Overview
This module covers how AI systems take in and produce things other than plain text: images, scanned
pages and audio. It starts with vision-language models, the encoder-connector-LLM design that lets a
language model attend over visual tokens, then goes underneath to CLIP, the contrastive image-text
training that produced the vision towers most VLMs reuse. Image embeddings generalise that idea into
vectors you can index, search, cluster and deduplicate, and they make the point that the encoder you
choose decides what "similar" means. OCR and document understanding cover the enterprise workhorse:
recovering characters, positions and confidences from scans, then putting layout back into the model
so that fields, tables and answers come out structured and verifiable. Speech-to-text and TTS close
the loop for voice, from Whisper-style spectrogram transcription to neural synthesis built for
time-to-first-audio. The topics connect through one recurring decision: convert the input to text
early (OCR, ASR, captions) and accept the loss, embed it for cheap comparison at scale, or hand the
raw signal to a model that reasons over it directly. After the module you can pick the right
modality pipeline for a task, estimate its token and latency cost, measure it with the right metric
(recall@k, CER/WER, per-field accuracy, RTF) and route what the system cannot verify to a human. It
matters most for AI Engineers building these pipelines and for FDEs who meet scanned documents and
call recordings in nearly every enterprise deployment.

## Why it matters
- **Engineer:** You will wire OCR, VLM calls, embedding search and ASR/TTS into products, and you need to know which stage is failing, what each image or second of audio costs in tokens and latency, and how to measure accuracy on your own data rather than trusting a benchmark.
- **Architect:** You decide whether a workload converts to text early, embeds for search, or sends pixels and audio to a VLM, and that choice sets cost per request, latency budget, auditability and how much human review the system needs at volume.
- **FDE:** Customer data arrives as skewed scans, image-only PDFs, phone-quality audio and screenshots; you must prove extraction accuracy on their own documents, explain confidence-based review routing to stakeholders, and pick the cheapest pipeline that meets their bar.

## Mental model
Picture every multimodal system as a funnel from a raw signal (pixels or a waveform) to a decision,
with one question at each stage: how much information do I throw away, and when? Converting early,
with OCR, ASR or a caption, is cheap, gives you per-word confidence and plugs into text tooling, but
it compresses before the question is known, so layout, tables, tone and visual detail can vanish.
Embedding (CLIP, image embeddings) keeps a fixed-length summary tuned for comparison: ideal for
searching or filtering millions of items, useless for a reasoned answer about one. Passing the raw
signal to a VLM keeps the most information and reasons jointly, but costs tokens per image, adds
latency and hallucinates with confidence. Real systems chain these: embed to narrow, OCR to ground,
VLM to reason, validate to trust, and speech models at either end when the user talks and listens.
Always measure where the loss happened.

## Suggested order
1. clip — the contrastive image-text training that underpins both multimodal search and the vision tower of most VLMs.
2. image-embeddings — generalises CLIP into vectors for search, dedup and clustering, and shows how encoder choice defines similarity.
3. vision-language-models — builds on the vision encoder to explain how images become tokens an LLM reasons over, and what resolution costs.
4. ocr — the convert-to-text-early path, with positions and confidences, that grounds LLMs on scanned material.
5. document-understanding — combines OCR, layout and VLMs with validation and review routing into structured, auditable extraction.
6. speech-to-text — moves to audio, with Whisper's spectrogram encoder-decoder, WER measurement and the streaming problem.
7. tts — closes the voice loop with neural synthesis, text normalisation and the time-to-first-audio budget of voice agents.

## Capstone
### Brief
Build a multimodal intake assistant for an insurance claims team. Claimants submit a mix of inputs: a
scanned or photographed claim form, an invoice or repair estimate as an image-only PDF, a few photos
of the damage, and a short voice note describing what happened. Your system OCRs and parses the form
and invoice into a typed claim record with per-field confidence, transcribes the voice note with a
Whisper-class model and extracts the incident date and location from it, embeds the damage photos
with a CLIP-style encoder to flag near-duplicates of photos already in the claims archive, and uses a
VLM to check whether the photos are consistent with the described damage. Cross-field validation
(invoice total equals line items, dates agree between form, invoice and voice note) decides whether a
claim goes straight through or to a reviewer queue with the reasons attached. Finally, a TTS
read-back confirms the extracted summary to the claimant over the phone. Build it on a labelled set of
at least 30 synthetic or public claim packets so every stage can be measured independently.

### Deliverables
- A runnable pipeline (API or CLI) that ingests a claim packet and returns a JSON claim record with field values, source page or audio span, per-field confidence and a straight-through or review decision with reasons.
- A labelled evaluation set of 30+ packets with ground-truth fields, transcripts and known duplicate photos, plus an evaluation script that reports metrics per stage rather than only end to end.
- A photo deduplication index built from image embeddings, with a tuned similarity threshold and a short note on why that encoder was chosen.
- A TTS read-back of the claim summary with measured time-to-first-audio and a pronunciation check on names, amounts and dates.
- A one-page design note covering where the pipeline converts to text versus sends raw pixels or audio, cost per packet in tokens and seconds, and the failure modes found during evaluation.

### Rubric
- The evaluation reports CER on OCR, WER on the voice notes, per-field extraction accuracy, recall@k and precision at threshold for duplicate photos, and straight-through rate, each computed on the labelled set.
- Every extracted field links back to its source region or audio timestamp, and low-confidence or validation-failing fields are routed to review instead of being silently accepted.
- At least three real failure cases are traced to the stage that caused them (for example OCR misread versus LLM extraction error) with a fix or mitigation, and re-running the evaluation shows the change.
- Cost and latency per packet are measured, including image tokens at the shipped resolution and ASR real-time factor, and the design note justifies the resolution and model choices against them.
- The TTS read-back pronounces currency amounts, dates and claimant names correctly on the test set, verified by an ASR round-trip or a listed manual check.
