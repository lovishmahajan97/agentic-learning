---
id: model-internals
title: Model internals
---

## Overview
This module opens up the large language model and follows a token all the way through it. It starts
with the transformer block and the attention mechanism at its centre: self-attention as the only
operation that moves information between positions, multi-head attention as several lookups in
parallel, and grouped-query attention and FlashAttention as the two fixes for attention's memory cost.
Positional encoding, tokenization and embeddings explain how text becomes ordered vectors in the first
place, and llm-architecture explains why one causal decoder stack won over encoder–decoder designs.
The serving half follows from the same shapes: the context window and the KV cache set how much a
request can hold and how many requests a GPU can serve, logits and softmax are the raw output and its
normalisation, sampling strategies and speculative decoding turn that output into text quickly and
predictably. The final group covers the model as a whole: mixture-of-experts and small language models
as two answers to the cost of parameters, scaling laws as the rule that links size, data and compute,
perplexity as the native quality metric, hallucination as a consequence of the training objective, and
reasoning models as the move from bigger weights to more inference compute. Afterwards you can read a
model config and derive parameters, FLOPs per token and KV-cache bytes; explain latency, cost and odd
behaviours from first principles; choose decoding settings from a task's error model; and pick or
reject a model for a deployment with numbers instead of benchmark tables. It matters most for AI
Engineers, and it is the shared vocabulary Architects and FDEs need to defend model and hardware choices.

## Why it matters
- **Engineer:** You will tune temperature and top-p, budget prompts with the real tokenizer, read logprobs, enable prefix caching and speculative decoding, and debug truncation, NaNs and repetition loops, and each of these is only predictable once you know the logits-to-sampler path and what the KV cache holds.
- **Architect:** Concurrency per GPU, cost per request and the dense-versus-MoE-versus-SLM decision all follow from head counts, KV-cache bytes per token, active versus total parameters and the 2N-FLOPs-per-token rule, so capacity plans and model choices are arithmetic you can check rather than vendor claims.
- **FDE:** Customers ask why their language costs three times more tokens, why the model invents facts, whether their older GPUs can run long context, and whether a small fine-tuned model can replace the API; you need first-principles answers and a quick measurement on their own data.

## Mental model
Hold one picture: text goes through a pipeline of tensors with fixed shapes, and every cost and
behaviour can be traced to one stage of it. The tokenizer turns characters into ids, which sets the
token bill. Embeddings turn ids into vectors, positional encoding stamps order onto them, and a stack of
identical blocks alternates attention (positions exchange information, cost n² in length, cache grows
with length times KV heads) with a feed-forward network (each position transformed alone, cost tracks
parameters). The last hidden state becomes logits, softmax turns them into a distribution, and a
sampler picks one token, which is appended and fed back. Prefill is that pipeline run once over the
prompt and limited by compute; decode is the loop, limited by memory bandwidth. When something is slow,
expensive or wrong, ask which stage and which shape is responsible: token count, sequence length, KV
heads, active parameters, the logit vector or the sampler. Hallucination and reasoning sit at the end
of the loop: the objective rewards plausible continuations, and more loop iterations buy more compute.

## Suggested order
1. tokenization — text must become token ids before anything else happens, and tokens are the unit of cost and context used everywhere after.
2. embeddings — turns token ids into the vectors the network computes on, and separates the LLM's embedding table from embedding models.
3. attention-mechanism — introduces the query, key and value lookup that every later attention topic refines.
4. softmax — the normalisation inside every attention head and at the output, needed before scaling, masking and sampling make sense.
5. self-attention — applies attention within one sequence, adds masking and the O(n²) cost that drives the serving topics.
6. multi-head-attention — splits the width into parallel heads and introduces the head counts read from every model config.
7. positional-encoding — supplies the order signal self-attention lacks and explains RoPE and context extension.
8. transformer — assembles attention, the feed-forward network, residuals and normalisation into the block and derives 2N FLOPs per token.
9. llm-architecture — compares encoder, decoder and encoder–decoder stacks and explains why causal decoders dominate chat models.
10. logits — the raw per-vocabulary scores the stack outputs, where logit bias, constrained decoding and logprobs act.
11. sampling-strategies — turns the softmaxed logits into tokens with greedy, beam, temperature, top-k, top-p and min-p.
12. context-window — the budget every request shares, constrained by attention cost, cache size and trained positions.
13. kv-cache — makes decoding incremental and turns sequence length into GPU memory, the limit on concurrency.
14. grouped-query-attention — shrinks the KV cache by sharing key/value heads, which only lands after the cache formula.
15. flashattention — removes the n² memory traffic with tiling and online softmax, building on self-attention and softmax.
16. speculative-decoding — uses idle compute during bandwidth-bound decode, relying on logits, sampling and the cache.
17. perplexity — the native fit metric computed from logits, needed before comparing models, quantisation and scale.
18. scaling-laws — links parameters, tokens and compute to loss, and frames over-training and inference cost.
19. mixture-of-experts — separates active from total parameters, building on the FFN and on scaling arguments.
20. small-language-models — applies scaling and distillation results to narrow, cheap and private deployments.
21. hallucination — explains fabrication from the objective, decoding and incentives, using logits, sampling and calibration.
22. reasoning-models-test-time-compute — closes the module by spending more decode tokens instead of more parameters, tying cost back to the loop.

## Capstone
### Brief
A customer wants to move a multilingual support-ticket triage and reply-drafting workload off a
frontier API onto self-hosted open weights, on a node with two 48 GB GPUs, and asks you to recommend a
model and serving configuration with evidence. Shortlist three open models: one dense mid-size model,
one mixture-of-experts model and one small language model of 8B parameters or fewer. From each model's
config alone, derive parameter count, active parameters, FLOPs per token and KV-cache bytes per token,
then compute the maximum concurrent requests at the customer's real prompt and output lengths. Measure
tokens per character for each tokenizer on a sample of the customer's tickets in at least three
languages. Serve at least two candidates with an inference engine such as vLLM or llama.cpp and measure
throughput and time per output token at several concurrency levels, with and without prefix caching
and speculative decoding. Compare a quantised build against the original with perplexity on held-out
tickets. Run a small grid of sampling settings for the classification step and the drafting step, and
measure hallucination on drafts as unsupported claims against the ticket and knowledge-base article.
Finally, test whether a reasoning mode or best-of-n voting improves the hardest triage cases enough to
justify its token cost, and route only those cases to it.

### Deliverables
- A config-derived capacity sheet (script or notebook) that computes parameters, active parameters, 2N FLOPs per token, KV-cache bytes per token and maximum concurrency at the target context for each candidate, checked against the engine's reported memory.
- A tokenizer cost table giving tokens per character and estimated monthly token volume per language for each candidate, measured on the customer's own ticket sample with each model's real tokenizer.
- A benchmark report of throughput, p50 and p95 time per output token and KV-cache utilisation across concurrency levels, with prefix caching and speculative decoding toggled, including the speculative acceptance rate.
- An evaluation set of 50+ labelled tickets with triage labels and reference facts, plus results for the sampling grid, the quantised-versus-original perplexity check, the unsupported-claim rate on drafts and the accuracy-versus-tokens curve for the reasoning or best-of-n escalation.
- A two-page recommendation naming the model, quantisation, decoding settings per step, context limit and routing rule, with cost per resolved ticket compared to the current API.

### Rubric
- Every capacity number traces to named config fields (`hidden_size`, `num_hidden_layers`, `num_key_value_heads`, `head_dim`, expert counts) through a written formula, and predicted KV-cache memory agrees with the engine's measurement within 15 percent.
- Benchmarks are run at production-like concurrency and prompt lengths, and the report explains each result with the right mechanism (bandwidth-bound decode, cache pressure, preemptions, low draft acceptance) rather than only listing numbers.
- Sampling settings differ between triage and drafting and are justified by measured accuracy and format-failure rate from the grid, not copied defaults.
- Hallucination is reported as a claim-level unsupported rate on the labelled set with at least five traced examples, and the recommendation includes a concrete mitigation such as grounding, abstention or verification, with its measured effect.
- The escalation rule is backed by an accuracy-versus-tokens curve showing the chosen budget sits at the knee, and the recommendation states which tickets are routed to it and what that adds to cost per ticket.
