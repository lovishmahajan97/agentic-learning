---
id: llm-architecture
module: model-internals
name: LLM architecture (encoder–decoder vs decoder-only)
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - model-internals/transformer
  - model-internals/attention-mechanism
  - model-internals/self-attention
  - model-internals/kv-cache
  - model-internals/context-window
  - training-adaptation/pre-training
  - rag-retrieval/reranking
  - multimodal/speech-to-text
sources:
  - Vaswani et al., 2017 — Attention Is All You Need
  - Raffel et al., 2020 — Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer
  - Radford et al., 2018 — Improving Language Understanding by Generative Pre-Training
  - Wang et al., 2022 — What Language Model Architecture and Pretraining Objective Work Best for Zero-Shot Generalization?
---

## TL;DR
The original transformer had two stacks: an encoder that reads the input bidirectionally and a decoder
that writes the output token by token while cross-attending to the encoder. Modern chat LLMs keep only
the decoder and treat prompt and answer as one causal stream, because one stack with one next-token
objective scales on raw text, handles multi-turn chat by appending, and reuses its KV cache.
Encoder–decoder still wins for fixed, read-heavy inputs with short outputs, such as translation and
speech recognition.

## Why it exists
Sequence-to-sequence tasks such as translation have a clear shape: a complete input you may read in any
order, then an output you must produce left to right. The 2017 transformer mirrored that shape. The
encoder lets every source token attend to every other, in both directions, *because* understanding a
sentence benefits from seeing its end. The decoder is masked so each output token sees only earlier
output tokens, *because* at inference time later tokens do not exist yet. Cross-attention connects the
two.

Three questions then forced the architecture choice that defines today's LLMs. First, which single
objective yields the most general model from unlabelled text? Next-token prediction over a causal stream
turns every token into a training example, with no masking scheme or input/output split to design.
Second, how do you handle a conversation, where this turn's output is next turn's input? In a two-stack
model you must re-encode the growing transcript on every turn; in a one-stack model you append tokens and
keep the cache. Third, how does few-shot prompting work? Examples and the request live in the same
stream as the answer, so in-context learning falls out of the training objective instead of needing a
separate mechanism. Decoder-only answered all three with less machinery, and at scale that simplicity
compounded.

## How it works
### Three families
- **Encoder-only** (BERT and descendants). Bidirectional self-attention, trained by masking tokens and
  predicting them. Produces representations, not text. Used for classification, token tagging, embedding
  models and cross-encoder rerankers, where the job is to *read* an input and score it. (Some recent
  embedding models are fine-tuned decoder-only LLMs with last-token pooling; the family describes the
  attention pattern, not a fixed job.)
- **Encoder–decoder** (T5, BART, mT5, Whisper). The encoder is bidirectional; the decoder is causal and
  adds a cross-attention block in every layer whose queries come from the decoder state and whose keys
  and values come from the encoder's final output. Because the encoder output never changes during
  generation, each layer's cross-attention K and V are projected once and cached for the whole decode,
  next to the decoder's ordinary self-attention cache, which grows with the output. T5 was pre-trained
  with span corruption: hide spans of the input, generate them in the output.
- **Decoder-only** (the GPT line, Llama, Mistral, and essentially every frontier chat model). One causal
  stack. Prompt, retrieved documents, tool results and the model's own answer are a single token sequence;
  the only difference between "input" and "output" is which positions the loss was computed on.

### What the mask changes
In a decoder-only model a prompt token at position *i* attends only to positions ≤ *i*. The
representation of the first word of a question is built without seeing the rest of the question. An
encoder sees everything at once, so each token's representation can use the whole input. This is the
standard argument that encoders "understand" input more richly per token; decoder-only models compensate
with depth and scale, and later positions in the prompt do see everything before them, so by the end of
the prompt the model has a complete picture.

### Where the compute goes
In an encoder–decoder, the encoder runs once per input and its outputs are reused by every decoding step
through cross-attention; the decoder's self-attention KV cache grows with the output only. That lets you
pair a large encoder with a small decoder when the input is long and the output short, which is why
speech models like Whisper keep the design: the audio encoder is expensive and runs once, the text decoder
is cheap and runs per token. In a decoder-only model the prompt is processed by the same full stack as
the answer (the prefill phase), populating one KV cache that the answer then extends. Prefill is fast in
wall-clock terms because all prompt positions run in parallel, but in FLOPs per token "reading" costs
the same full stack as "writing"; there is no smaller encoder to delegate it to. The compensation is that
nothing has to be re-encoded when the conversation grows: the cache from turn one is still valid for turn
two.

Parameter counts mislead when comparing the two. Each token passes through only one stack of an
encoder–decoder, so a model with 2N parameters split evenly across encoder and decoder costs roughly
the FLOPs per token of an N-parameter decoder-only model (Raffel et al. make this comparison when
choosing T5's baseline). "T5-11B versus a 7B decoder" is therefore not an apples-to-apples cost
comparison; compare FLOPs per token and memory, not headline size.

### Why decoder-only scaled better for general use
- **Objective density.** Every position in a causal stream contributes a loss term. Span-corruption and
  masked-LM objectives compute loss on a fraction of positions, so each document yields less signal.
- **Zero-shot behaviour.** Controlled comparisons (Wang et al., 2022) found causal decoder-only models
  trained with the plain language-modelling objective generalized best zero-shot after pre-training
  alone; encoder–decoders caught up only after additional multitask fine-tuning.
- **Serving.** One stack means one set of weights, one KV cache and one batching scheme. Prefix caching
  and multi-turn reuse are natural because history is just earlier tokens.
- **Unification.** Instruction following, tool calling and multi-document prompts all become "put it in
  the stream". A prefix-LM variant (bidirectional attention over the prompt, causal over the answer) is a
  hybrid that keeps one stack; it has been used in some models but did not displace the plain causal
  design.

### What encoder–decoder still does better
Fixed input, short output, no multi-turn: translation, summarization pipelines, speech-to-text, OCR
post-correction. The bidirectional encoder gives a better per-token view of the source, cross-attention
gives a direct alignment between output and input, and the encoder cost is paid once. Encoder-only
models remain the right tool for embeddings and rerankers, where no generation happens at all.

## Example
The two loops below make the structural difference visible. The T5 encoder runs once and its output is
handed, unchanged, to every decoder step; the GPT-2 "input" grows by one token per step, with no
separate encoder at all. Outputs from these tiny base models will be rough; the point is the data flow.

```python
import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, AutoModelForCausalLM

# Encoder–decoder: two streams joined by cross-attention
t5_tok = AutoTokenizer.from_pretrained("google/flan-t5-small")
t5 = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-small").eval()
src = t5_tok("Translate English to German: The cat sleeps.", return_tensors="pt")
with torch.no_grad():
    enc = t5.get_encoder()(**src)                        # bidirectional pass, done ONCE
    dec = torch.tensor([[t5.config.decoder_start_token_id]])
    for _ in range(8):
        out = t5(encoder_outputs=enc, attention_mask=src.attention_mask, decoder_input_ids=dec)
        dec = torch.cat([dec, out.logits[:, -1].argmax(-1, keepdim=True)], dim=1)
print(t5_tok.decode(dec[0], skip_special_tokens=True))

# Decoder-only: prompt and completion are one causal stream
g_tok = AutoTokenizer.from_pretrained("gpt2")
g = AutoModelForCausalLM.from_pretrained("gpt2").eval()
ids = g_tok("English: The cat sleeps. German:", return_tensors="pt").input_ids
with torch.no_grad():
    for _ in range(8):
        ids = torch.cat([ids, g(ids).logits[:, -1].argmax(-1, keepdim=True)], dim=1)
print(g_tok.decode(ids[0]))
# T5 re-reads a fixed encoding each step; GPT-2's "input" is the whole growing sequence.
# A production decoder-only loop would pass past_key_values instead of re-running the prefix.
```

## Trade-offs
- Decoder-only gives one stack, one cache and natural multi-turn reuse, but every prompt token costs a
  full-stack forward pass; encoder–decoder pays encoding once and can decode with a smaller stack, which
  wins when inputs are long and outputs short.
- Bidirectional encoding sees the whole input per token, while causal prompts see only the past; the
  gap matters for small models on reading-heavy tasks and shrinks as decoder-only models scale.
- A single next-token objective is dense and label-free, but it bakes in left-to-right generation; tasks
  that are naturally classification or scoring are served more cheaply by an encoder with a head.
- Cross-attention gives an explicit source-to-output alignment (Whisper's word-level timestamps are
  derived from cross-attention weights), at the cost of a second attention block per layer; the
  advantage disappears when the "source" keeps changing, as in multi-turn use, because the alignment
  target has to be re-encoded every time it grows.

## Pitfalls
- Reaching for a decoder-only chat model to do bulk classification or reranking; cost per item is far
  higher than an encoder with a head, and latency budgets blow up at scale.
- Assuming an encoder–decoder can "continue the conversation" cheaply; the transcript must be re-encoded
  every turn, so latency grows with history even for short replies.
- Treating the prompt as if the model read it bidirectionally; instructions placed after a long document
  cannot influence how earlier tokens were represented. The symptom is extraction that misses fields the
  task asked for because the document was "read" before the model knew what to look for. Vendor
  guidance on placement differs (several recommend the question after long documents), so stating the
  task before the document and repeating it after is a common hedge; measure on your task.
- Confusing the architecture family with the training recipe; a decoder-only base model that was never
  instruction-tuned will happily continue your prompt instead of answering it.

## When to use
Default to decoder-only for anything conversational, agentic, tool-using or open-ended: it is where the
best-trained weights are, and its serving stack (KV cache, prefix caching, continuous batching) is the
most mature. Choose encoder–decoder when the task is a fixed transformation of a known input with no
dialogue (translation at volume, speech recognition, structured summarization) and the input is much
longer than the output. Choose encoder-only when you need a score or a vector, not text: embeddings,
rerankers, classifiers and PII detectors. Measure cost per request and p95 latency under your real
input/output length ratio before deciding; the ratio is what decides. In interviews, be ready to draw the
three families, explain what cross-attention connects, and argue why one causal stack won.

## Role lens
- **Engineer:** Picks `AutoModelForCausalLM` versus `AutoModelForSeq2SeqLM` versus an encoder with a
  head, and knows the generation loop and cache semantics differ between them.
- **Architect:** Decides the model family per workload in a system, keeping the expensive decoder-only
  model for dialogue and routing scoring and retrieval tasks to encoders.
- **FDE:** Explains to a customer why their translation or transcription pipeline should not be rebuilt
  on the chat model, and sizes the two differently.

## Check yourself
**Q:** A customer wants to transcribe call-centre audio and then let agents chat about each transcript.
Which architecture family fits each half, and why not one model for both?
**A:** Transcription is a fixed-input, read-heavy, write-light task with no dialogue: an encoder–decoder
such as Whisper runs its expensive audio encoder once and decodes text with a small decoder, which is
cheap per token and well aligned to the source. The chat half is multi-turn with growing history, tool
calls and open-ended answers, which is exactly what a decoder-only model handles by appending tokens and
reusing its KV cache. One model for both would either force the chat model to consume audio tokens it was
not trained on, or force the transcription model to re-encode the whole transcript plus conversation on
every turn. Two models, with the transcript passed as text, is cheaper and better at each job. If the
customer already uses a natively audio-capable chat model, the argument becomes one of cost: audio
typically consumes several times more tokens per second of speech than its transcript, and every chat
turn would pay prefill over it unless it is prefix-cached, so transcribing once is still usually cheaper.

## Quiz
### Q: What does cross-attention in an encoder–decoder connect?
- [ ] Decoder queries to decoder keys from earlier layers
- [x] Decoder queries to the encoder's final keys and values
- [ ] Encoder queries to decoder keys and values
- [ ] Positional encodings to token embeddings
> Each decoder layer has a self-attention block over the output so far and a cross-attention block whose
> keys and values come from the encoder output, letting every generated token consult the full input.

### Q: Why is multi-turn chat cheaper in a decoder-only model than in an encoder–decoder?
- [ ] Decoder-only models have fewer parameters
- [ ] Encoder–decoders cannot use a KV cache
- [x] History is just earlier tokens in one stream, so the existing cache stays valid; an encoder–decoder must re-encode the growing transcript
- [ ] Decoder-only models skip attention over the prompt
> In a single causal stream, nothing before the new tokens changes, so cached keys and values are reused.
> An encoder–decoder treats the transcript as input, and input is re-encoded whenever it changes.

### Q: Which task is the weakest fit for a decoder-only chat model?
- [ ] Multi-step agent with tool calls
- [ ] Few-shot question answering
- [x] Scoring ten million query–document pairs for relevance
- [ ] Open-ended summarization in a conversation
> Scoring is a read-and-rate task with no generation; an encoder with a classification head, or a
> cross-encoder reranker, does it at a fraction of the cost per pair.
