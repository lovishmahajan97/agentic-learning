---
id: speech-to-text
module: multimodal
name: Speech-to-text (Whisper)
kind: concept
difficulty: core
est_minutes: 16
status: reviewed
related:
  - multimodal/tts
  - multimodal/vision-language-models
  - model-internals/transformer
  - model-internals/tokenization
  - serving-inference/streaming
  - fastapi-for-ai/websockets
  - infra-capacity-economics/latency-budgets
  - safety-security/pii-redaction
sources:
  - Radford et al., 2022 — Robust Speech Recognition via Large-Scale Weak Supervision (Whisper)
  - Graves et al., 2006 — Connectionist Temporal Classification
  - Bain et al., 2023 — WhisperX: Time-Accurate Speech Transcription of Long-Form Audio
  - Official docs: faster-whisper
---

## TL;DR
Speech-to-text (automatic speech recognition, ASR) converts audio into a transcript. Whisper is an
encoder-decoder transformer that reads a log-Mel spectrogram of a 30-second window and generates text
tokens, trained on roughly 680,000 hours of weakly labelled web audio so it transcribes, identifies
languages and translates robustly across accents and noise without per-domain tuning. It exists because
earlier systems needed carefully aligned data per language and domain and broke outside their training
conditions.

## Why it exists
Voice is the input modality for call centres, meetings, dictation, voice agents and accessibility, and
all of it is useless to text systems until transcribed. Classic ASR pipelines (acoustic model plus
pronunciation lexicon plus language model, as in Kaldi-era systems) delivered good accuracy on the
conditions they were tuned for and fell apart on a new microphone, accent or vocabulary. They needed
phonetically aligned data and expert tuning per language, which few teams had.

End-to-end neural models (CTC and RNN-transducer models) removed the lexicon but were trained on
curated, human-transcribed corpora of a few thousand hours, so they were excellent on their benchmark
and brittle elsewhere: a model that scores superbly on read audiobook speech can double its error rate
on a noisy phone call. The naive fix, "fine-tune on your domain", only works if you have transcribed
audio, which is exactly what you were trying to produce.

Whisper's bet was scale with weak supervision: collect hundreds of thousands of hours of audio paired
with whatever transcripts existed on the web, filter out machine-generated ones, and train one model to
do transcription, translation to English and language identification. The pay-off was zero-shot
robustness: strong results on datasets it never trained on, which is what a product shipping to unknown
users actually needs.

## How it works
### Input: audio becomes a spectrogram
Audio is resampled to 16 kHz mono and cut into 30-second windows (padded with silence if shorter). Each
window is converted to a log-Mel spectrogram: a short-time Fourier transform with 25 ms frames every
10 ms, mapped onto 80 Mel-scaled frequency bands (128 in large-v3), log-compressed and normalised. The
Mel scale mimics human frequency sensitivity, and the log makes loud and quiet speech comparable. The
result is a 2-D array of 3,000 frames × 80 features, an "image" of the sound that a transformer can read.

### Encoder-decoder
Two convolutional layers (the second with stride 2) shrink the time axis to 1,500 positions and then a
transformer encoder produces a contextual representation of the whole window. Following the shapes makes
several later behaviours obvious:

```
30 s × 16,000 samples/s           = 480,000 samples
hop of 10 ms (160 samples)        → 3,000 spectrogram frames × 80 (or 128) Mel bands
conv stack, stride 2              → 1,500 encoder positions, each covering 20 ms
```

Each encoder position spans 20 ms, which is exactly the resolution of the timestamp tokens, and the window
length is fixed by the encoder's positional embeddings, which is why audio cannot simply be fed longer. The decoder is a standard autoregressive text
transformer that cross-attends to the encoder output and emits text tokens from a byte-pair vocabulary.
This is the same architecture as translation: audio in one "language", text out in another, and it is
why Whisper can translate to English as a decoding task rather than a separate model.

### The multitask token format
Whisper controls behaviour through special tokens in the decoder prompt rather than separate heads: a
start token, then a language token (predicted if not given, which is how language identification
works), then a task token (transcribe or translate), then optionally a no-timestamps token. During
transcription the decoder can interleave timestamp tokens with text, quantised to 20 ms, which is what
gives segment start and end times. A dedicated no-speech token lets the model declare that the window
is silence or music. Everything the model can do is one sequence-to-sequence problem, which is why one
checkpoint serves many languages and tasks.

### Long-form audio and decoding heuristics
Because the encoder sees exactly 30 seconds, longer audio is processed as a sliding sequence of windows:
transcribe a window, read the last predicted timestamp, advance the window to that point, and carry the
previous text as decoder context for consistency (the decoder's text context is small, so this carried
prompt, like an `initial_prompt`, is truncated to roughly the last couple of hundred tokens). This is also
where failures concentrate. The decoder is
a language model, so on silence, music or garbled audio it can hallucinate fluent text or loop on a
phrase. The reference implementation guards against this with heuristics: if the output's compression
ratio is too high (repetition), or the average log-probability too low, decoding is retried at higher
temperature; if the no-speech probability is high (and, in the reference implementation, the average
log-probability is also low) the window is treated as silence and skipped. Voice activity detection
(VAD) before transcription removes most of the silence that triggers hallucinations.

### Model sizes and runtimes
Whisper ships from tiny (tens of millions of parameters) to large (about 1.5 billion). Smaller models
are several times faster and noticeably less accurate, especially on accents and non-English speech.
Optimised runtimes (faster-whisper on CTranslate2, whisper.cpp for CPUs and Apple silicon) deliver the
same weights with lower memory and higher throughput; WhisperX adds forced alignment with a phoneme
model for accurate word timestamps and plugs in speaker diarization, which Whisper itself does not do.

### Streaming is not native
A 30-second encoder window means Whisper is a batch model. "Streaming" with it means chunking audio into
short pieces with overlap and re-decoding, which adds latency and can flicker. Models built for
streaming (RNN-transducer or chunked-attention encoders, as in several managed ASR APIs) emit words as
they arrive with sub-second delay. If you are building a live voice agent, that difference drives the
architecture more than the error rate does.

### Measuring it
Word error rate is (substitutions + deletions + insertions) / reference words, computed after text
normalisation (casing, punctuation, number formats), otherwise "twenty five" versus "25" counts as two
errors. Track WER on your own audio by domain, and separately track entity accuracy (names, product
codes), which WER underweights but users notice most.

## Example
Transcribe a call recording with faster-whisper, using VAD to suppress silence hallucinations and word
timestamps for alignment, then write out segments. The `initial_prompt` seeds the decoder with domain
vocabulary so product names are spelled consistently.

```python
from faster_whisper import WhisperModel

model = WhisperModel("large-v3", device="cuda", compute_type="float16")  # or "int8" on CPU

segments, info = model.transcribe(
    "support_call.wav",
    beam_size=5,
    vad_filter=True,                       # drop silence before decoding: fewer hallucinations
    vad_parameters={"min_silence_duration_ms": 500},
    word_timestamps=True,
    initial_prompt="Acme Cloud, Kubernetes, Terraform, ticket ID, SLA.",  # vocabulary hint
    condition_on_previous_text=False,      # trade context for resistance to repetition loops
)
print(f"language={info.language} p={info.language_probability:.2f} duration={info.duration:.0f}s")

transcript = []
for seg in segments:                       # a generator: decoding happens as you iterate
    # Same signals the reference decoder uses for fallback: low avg log-prob or repetitive output.
    flag = " [low-conf]" if seg.avg_logprob < -1.0 or seg.compression_ratio > 2.4 else ""
    shaky = [w.word.strip() for w in (seg.words or []) if w.probability < 0.5]  # review candidates
    transcript.append(f"[{seg.start:7.2f} -> {seg.end:7.2f}]{flag} {seg.text.strip()}"
                      + (f"  (check: {', '.join(shaky)})" if shaky else ""))

open("support_call.txt", "w").write("\n".join(transcript))
print(len(transcript), "segments")
# Throughput: real-time factor = wall time / info.duration; at RTF 0.05 one GPU stream clears an hour of
# audio in ~3 minutes (illustrative), and that, times concurrency, is what you size hardware against.
# WER check (on a labelled sample): normalise both texts (case, punctuation, numerals) before scoring,
# e.g. with the jiwer package; report per-domain WER and separately the error rate on entity names.
```

## Trade-offs
- Weak supervision at scale buys zero-shot robustness, but a model fine-tuned on a few hundred hours of
  your domain's audio will still beat it on your jargon when you can afford the transcription work.
- Larger checkpoints cut error rate, especially off-English, at several times the compute; for clean
  English narration a small model is often indistinguishable and far cheaper.
- Batch encoder-decoder design gives high accuracy with context, while native streaming models give
  sub-second partial results; a live agent needs the latter even at a slightly higher WER.
- Conditioning on previous text improves consistency across windows but propagates repetition loops; turn
  it off when inputs contain long silences or music.

## Pitfalls
- Transcribing without VAD; the symptom is fluent sentences appearing in silent stretches, often generic
  phrases that never occurred in the audio.
- Letting language detection run on the first 30 seconds only; the symptom is a bilingual call transcribed
  in the wrong language, or a translation when you wanted a transcription.
- Trusting Whisper's own segment timestamps for word-level alignment; the symptom is subtitles or
  redactions offset by a second or more, fixed by forced alignment (WhisperX-style).
- Scoring WER without normalisation; the symptom is a model that reads better to humans but "scores worse"
  because it wrote numerals and punctuation differently from the reference.
- Feeding 8 kHz telephone audio without considering bandwidth; the symptom is a higher error rate on
  calls than on meetings with no code change, because half the spectrum the model expects is missing.
- Treating a transcript as speaker-attributed when it is not; the symptom is a summary that credits the
  customer with the agent's promise, because Whisper emits one undifferentiated stream and diarization
  (who spoke when) is a separate model whose errors cluster at fast turn-taking and crosstalk.
- Forgetting the transcript contains PII; the symptom is a compliance finding over stored call text that
  nobody classified, since the audio was treated as the sensitive asset and the text was not.

## When to use
Use Whisper-class models for offline or near-real-time transcription where accuracy across accents,
noise and languages matters more than sub-second latency: call analytics, meeting notes, media
indexing, voice memos, and as the first stage before an LLM summarises or extracts. Choose a
streaming-native model or managed streaming API for live voice agents and captions. Measure WER on
your own normalised audio per domain, entity accuracy, hallucination rate on silence, and real-time
factor (processing time divided by audio duration) at your target concurrency. In interviews, be able
to explain the spectrogram-encoder-decoder flow, why 30-second windows cause both the long-form
heuristics and the streaming problem, and what weak supervision bought.

## Role lens
- **Engineer:** Wires VAD, chunking and decoding options, aligns timestamps for downstream features, and
  monitors WER and hallucination rate on sampled production audio.
- **Architect:** Picks batch versus streaming ASR per product surface, decides self-hosted versus managed
  on cost, latency and data residency, and sets the latency budget for voice flows.
- **FDE:** Collects a labelled sample of the customer's real calls, measures WER by accent and line
  quality, and tunes vocabulary prompts and thresholds before promising accuracy figures.

## Check yourself
**Q:** A team transcribes hour-long recorded support calls with Whisper and reports two problems:
sentences that were never spoken appear during hold music, and a product name is spelled three
different ways. How would you address each, and what metric would you add?
**A:** The phantom sentences are decoder hallucinations: the decoder is a language model and, given
music or silence, continues generating plausible text. Add voice activity detection so non-speech is
never decoded, keep the compression-ratio and log-probability fallbacks on, consider disabling
conditioning on previous text so a loop cannot propagate, and drop segments the model marks as
no-speech. The spelling drift comes from the decoder choosing among acoustically similar spellings with
no prior; seed the decoder with an initial prompt that contains the product name and other domain
terms, and apply a post-transcription normalisation map for known entities. Add entity accuracy on a
labelled sample as a metric beside normalised WER, because WER barely moves on a single misspelled
product name while users and search break on it.

## Quiz
### Q: Why does Whisper process audio in fixed 30-second windows?
- [ ] Audio files longer than 30 seconds cannot be stored as spectrograms
- [x] The encoder consumes a fixed-size spectrogram, so longer audio is handled by sliding windows guided by predicted timestamps
- [ ] Human speech naturally pauses every 30 seconds
- [ ] The decoder vocabulary only covers 30 seconds of text
> The encoder expects a fixed 3,000-frame input; long-form transcription advances a window using the
> last timestamp the decoder emitted. This design is also why streaming is not native.

### Q: What is the most effective first step against hallucinated text during silence or music?
- [ ] Switch to a larger Whisper checkpoint
- [ ] Increase the beam size
- [x] Run voice activity detection and skip non-speech regions before decoding
- [ ] Lower the sampling rate to 8 kHz
> Hallucinations occur because the decoder generates language even when the encoder saw no speech;
> removing non-speech input removes the trigger. Larger models and beams do not change this behaviour.

### Q: How did Whisper's training approach differ from earlier end-to-end ASR models, and what did it buy?
- [ ] It used a phonetic lexicon to improve pronunciation modelling
- [x] It trained on a very large, weakly labelled multilingual corpus, buying zero-shot robustness across domains
- [ ] It was trained only on a small curated audiobook dataset for maximum accuracy
- [ ] It replaced the transformer with a recurrent network for streaming
> Earlier models trained on curated thousands of hours and were brittle off-benchmark; Whisper scaled to
> hundreds of thousands of weakly supervised hours and generalised to unseen datasets without tuning.
