---
id: tts
module: multimodal
name: TTS (text-to-speech)
kind: concept
difficulty: core
est_minutes: 15
related:
  - multimodal/speech-to-text
  - serving-inference/streaming
  - fastapi-for-ai/websockets
  - infra-capacity-economics/latency-budgets
  - prompting/structured-outputs-json-mode
  - model-internals/tokenization
sources:
  - Shen et al., 2018 — Natural TTS Synthesis by Conditioning WaveNet on Mel Spectrogram Predictions (Tacotron 2)
  - Kong et al., 2020 — HiFi-GAN: Generative Adversarial Networks for Efficient and High Fidelity Speech Synthesis
  - Kim et al., 2021 — Conditional Variational Autoencoder with Adversarial Learning for End-to-End Text-to-Speech (VITS)
  - Wang et al., 2023 — Neural Codec Language Models are Zero-Shot Text to Speech Synthesizers (VALL-E)
---

## TL;DR
Text-to-speech turns a string into a waveform. Modern systems normalise the text, predict a compact
acoustic representation (a mel-spectrogram or neural-codec tokens) with prosody, and then a vocoder or
codec decoder turns that into audio. It exists because the output side of every voice product, agents,
accessibility, IVR, narration, needs speech that is intelligible, natural and generated fast enough to
feel conversational, and concatenating recorded fragments cannot do that for arbitrary text.

## Why it exists
A voice agent that can hear but not speak is a chatbot. The first generations of synthesis either
glued recorded snippets together (concatenative) or simulated the vocal tract with rules (formant
synthesis). Concatenation sounds human on the exact phrases that were recorded and seams badly between
them; rules are intelligible but unmistakably robotic. Neither can voice an LLM's unbounded, freshly
generated sentences with natural rhythm and emphasis.

The problem has three hard parts that a naive "look up each word's sound" approach ignores. First,
the text is ambiguous before it is pronounced: "read" has two sounds, "1/2" might be a date or a
fraction, "St." might be street or saint. Second, prosody (timing, pitch, loudness) carries meaning,
and it depends on the whole sentence, not the word. Third, producing raw audio is expensive: 24,000
samples per second, each depending on the ones before. Early neural vocoders that generated sample by
sample sounded excellent and were far too slow for anything interactive.

Neural TTS exists because learned models solve the first two problems from data and the third by
splitting the job into a cheap sequence model over a compressed representation plus a fast decoder.
For an AI engineer the stakes are concrete: time-to-first-audio and pronunciation errors are what make a
voice agent feel broken, regardless of how good the LLM behind it is.

## How it works
### Stage 1: text normalisation and front end
The raw text is rewritten into what should be *said*: numbers expanded ("$1,250.50" to "one thousand two
hundred fifty dollars and fifty cents", "2024" as a year or a quantity depending on context),
abbreviations resolved, symbols and markdown removed, sentence boundaries found. A grapheme-to-phoneme
step then converts words to phonemes using a lexicon plus a learned model for unseen words. This stage
is rule-heavy and language-specific, and it is where most "the voice said something wrong" bugs live;
neural models downstream faithfully pronounce whatever the front end hands them.

### Stage 2: acoustic model
The phoneme (or character) sequence is mapped to an intermediate acoustic representation, classically
a mel-spectrogram: a time-frequency picture at roughly 80 bands per frame, hundreds of frames per
second of speech. Two designs matter. Autoregressive models (Tacotron 2) generate frames one at a time
with attention over the input text; natural, but slow and prone to skipping or repeating words when
attention wanders. Non-autoregressive models (FastSpeech 2) first predict each phoneme's *duration*,
expand the sequence accordingly, predict pitch and energy per phoneme, and then generate all frames in
parallel. They exist because explicit duration makes synthesis fast, controllable and immune to the
skipping failure, at the cost of a slightly flatter delivery unless the prosody predictors are good.

### Stage 3: vocoder
The spectrogram is an abstraction without phase; a vocoder reconstructs the waveform. WaveNet did it
autoregressively per sample with superb quality and impractical latency. GAN vocoders (HiFi-GAN) upsample
the spectrogram with convolutions in one pass, trained with a discriminator that judges realism; they run
far faster than real time on a GPU and are the default in production pipelines.

### End-to-end and codec-language-model designs
VITS trains acoustic model and vocoder jointly as one variational model with normalising flows, removing
the spectrogram seam and the quality loss from mismatched stages. The more recent family (VALL-E and its
descendants, and most "voice cloning" products) treats TTS as language modelling: a neural audio codec
(EnCodec-style) compresses audio into discrete tokens at a low frame rate, and a decoder-only transformer
predicts audio tokens conditioned on text tokens plus a few seconds of reference audio. Because the
reference is just a prompt, the model clones an unseen voice zero-shot, with the same hallucination and
repetition risks as any autoregressive LM. The codec decoder then turns tokens back into waveform.

### Streaming and latency
A conversational agent cannot wait for the whole reply to be synthesised. The pipeline is streamed at
two levels: the LLM's text is split at sentence or clause boundaries as it arrives, and each piece is
synthesised and played while the next is produced; within a piece, chunked vocoding emits audio in
small frames. The metrics are time-to-first-audio (TTFA) and real-time factor (RTF = synthesis time /
audio duration; below 1 means faster than real time, and the margin is your concurrency headroom).
Barge-in, stopping playback and generation when the user starts talking, is a product requirement that
the architecture must support, which means cancellable synthesis.

### Quality and control
Naturalness is measured by mean opinion score (MOS, listeners rate 1 to 5), intelligibility by running
ASR on the output and computing WER against the input text, and cloning fidelity by cosine similarity
in a speaker-embedding space. Control comes from SSML-style markup (breaks, emphasis, say-as for
digits and dates, explicit phonemes) or from style prompts in newer models.

## Example
The engineering around TTS matters as much as the model: normalise text an LLM produced (markdown,
currency, acronyms), chunk at sentence boundaries, and stream chunks through any synthesiser so
time-to-first-audio is bounded by the first sentence, not the whole reply. `synthesize` is any function
returning PCM bytes for a string; the timing code is what you would ship as metrics.

```python
import re, time
from num2words import num2words

def normalize(text: str) -> str:
    text = re.sub(r"[*_`#>]", "", text)                       # strip markdown an LLM may emit
    text = re.sub(r"\$(\d[\d,]*)(?:\.(\d{2}))?",                # "$1,250.50" -> spoken dollars/cents
                  lambda m: num2words(int(m.group(1).replace(",", ""))) + " dollars"
                  + (f" and {num2words(int(m.group(2)))} cents" if m.group(2) else ""), text)
    text = re.sub(r"\b(\d+)%", lambda m: num2words(int(m.group(1))) + " percent", text)
    text = text.replace("SLA", "S L A").replace("K8s", "kubernetes")   # per-domain lexicon
    return text

def sentences(text: str):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]

def stream_speech(reply: str, synthesize, sample_rate=24_000, bytes_per_sample=2):
    t0, first, audio_s = time.perf_counter(), None, 0.0
    for s in sentences(reply):
        pcm = synthesize(normalize(s))                         # any TTS backend: bytes of 16-bit PCM
        if first is None:
            first = time.perf_counter() - t0                   # time-to-first-audio
        audio_s += len(pcm) / (sample_rate * bytes_per_sample)
        yield pcm                                              # play/send while the next sentence renders
    rtf = (time.perf_counter() - t0) / max(audio_s, 1e-6)
    print(f"TTFA={first:.2f}s  audio={audio_s:.1f}s  RTF={rtf:.2f}")

reply = "Your **SLA** credit is $1,250.50. That is 12% of the invoice. K8s nodes are back."
for chunk in stream_speech(reply, synthesize=lambda s: b"\x00" * 48_000):   # stub: 1 s of silence
    pass   # in production: write chunk to the WebSocket / audio device
```

## Trade-offs
- Non-autoregressive acoustic models give fast, stable, controllable synthesis, while autoregressive and
  codec-LM models give richer prosody and zero-shot cloning at higher latency and hallucination risk.
- Sentence-level streaming minimises time-to-first-audio but loses cross-sentence prosody; it flips when
  the content is narration rather than dialogue, where whole-paragraph synthesis sounds better.
- Managed TTS APIs deliver quality and voices without GPUs, but per-character pricing, data residency
  and vendor voice licensing push high-volume or regulated deployments toward self-hosting.
- Voice cloning from seconds of audio is a product feature and a liability; consent, verification and
  watermarking cost engineering effort that a fixed stock voice does not.

## Pitfalls
- Sending raw LLM output to the synthesiser; the symptom is the voice reading "asterisk asterisk" or
  spelling out URLs and JSON keys that should never have reached speech.
- Skipping number and date normalisation; the symptom is "two thousand twenty-four" for a year, phone
  numbers read as large integers, and "1/2" spoken as a date.
- Synthesising the whole reply before playback; the symptom is a multi-second pause before every answer
  that users read as the agent being broken, even though total synthesis time is fine.
- Ignoring sample-rate and codec conversion; the symptom is chipmunk or slowed audio on telephony paths
  that expect 8 kHz μ-law when the model emitted 24 kHz PCM.
- No cancellation path for barge-in; the symptom is the agent talking over the user for several seconds
  after being interrupted.
- Evaluating only by listening to a few demos; the symptom is mispronounced customer names and product
  terms in production that an ASR round-trip WER test on your own vocabulary would have caught.

## When to use
Reach for neural TTS whenever text must become speech for dynamic content: voice agents, IVR replacing
recorded menus, accessibility read-aloud, generated narration and alerts. Use recorded audio when the
set of phrases is small and fixed and legal sign-off on exact wording matters. Choose a fast
non-autoregressive or streaming-capable model for interactive use and a higher-quality, slower model for
offline narration. Measure time-to-first-audio, RTF at target concurrency, ASR round-trip WER on your
own text, and mispronunciation rate on your entity list. Interviews tend to probe the three-stage
pipeline, why vocoders exist, and how you would hit a conversational latency budget.

## Role lens
- **Engineer:** Owns text normalisation and the lexicon, streams at sentence boundaries, implements
  cancellation for barge-in, and tracks TTFA and RTF as service metrics.
- **Architect:** Sets the end-to-end voice latency budget across ASR, LLM and TTS, chooses managed
  versus self-hosted synthesis, and defines the consent and watermarking policy for cloned voices.
- **FDE:** Builds the customer's pronunciation lexicon from their product and people names, validates
  the voice on their telephony path, and demonstrates the latency budget on real calls.

## Check yourself
**Q:** A voice agent's users complain it "takes forever to answer" even though logs show LLM first
token at 400 ms and TTS synthesis running at RTF 0.3. What is the likely cause and how would you
restructure the pipeline?
**A:** RTF 0.3 only says synthesis is faster than playback once it starts; the delay is sequencing. The
pipeline most likely waits for the complete LLM reply, then synthesises the whole text, then plays,
so perceived latency is LLM generation time plus full synthesis time. Restructure so the LLM streams
tokens, a splitter emits the first sentence as soon as its boundary appears, TTS synthesises that
sentence while the LLM continues, and audio plays while the next sentence renders. Time-to-first-audio
then approximates first-sentence LLM latency plus one short synthesis. Add cancellation so barge-in
stops both generation and playback, normalise each chunk before synthesis, and instrument TTFA as the
metric that matches what users feel.

## Quiz
### Q: Why do most TTS pipelines predict a mel-spectrogram first and then run a vocoder, instead of generating waveform samples directly from text?
- [ ] Waveforms cannot be produced by neural networks
- [x] The spectrogram is a compact representation that a sequence model can predict cheaply, and a fast vocoder reconstructs the waveform
- [ ] Vocoders perform the text normalisation step
- [ ] Spectrograms contain phase information that text lacks
> Raw audio is tens of thousands of samples per second; predicting a low-rate spectrogram then upsampling
> it with a one-pass vocoder keeps synthesis fast. End-to-end models exist but share this cost logic.

### Q: Which design choice most directly prevents the "skipped or repeated word" failure of attention-based autoregressive TTS?
- [ ] A larger vocoder
- [ ] Training on more speakers
- [x] Explicit per-phoneme duration prediction in a non-autoregressive acoustic model
- [ ] Higher output sample rate
> FastSpeech-style models decide how many frames each phoneme gets before generating, so every phoneme
> is rendered exactly once; attention drift in Tacotron-style models is what causes skips and repeats.

### Q: For a conversational agent, which metric best captures what users perceive as responsiveness?
- [ ] Real-time factor of the vocoder
- [ ] Mean opinion score of the voice
- [x] Time-to-first-audio measured from the end of the user's utterance
- [ ] Total synthesis time for the full reply
> Users judge the gap before the agent starts speaking. RTF and MOS matter for throughput and quality,
> but a low RTF with whole-reply synthesis still produces a long silent gap.
