---
id: tiktoken
module: python-for-ai
name: tiktoken
kind: tool
difficulty: intro
est_minutes: 11
status: fact-checked
related:
  - model-internals/tokenization
  - model-internals/context-window
  - python-for-ai/openai-sdk
  - python-for-ai/hugging-face
  - observability/token-cost-tracking
  - rag-retrieval/chunking-strategies
  - infra-capacity-economics/cost-modeling
sources:
  - Sennrich et al., 2016 — Neural Machine Translation of Rare Words with Subword Units
  - Official docs: tiktoken
---

## TL;DR
tiktoken is OpenAI's fast byte-pair-encoding tokenizer, a Rust core with Python bindings, that turns
text into exactly the token ids OpenAI models see. You use it locally to count tokens before a request,
budget context windows, chunk documents by tokens and estimate cost. It is only exact for OpenAI's
encodings; other model families tokenise differently, so its counts are an approximation for them.

## Why it exists
Everything an LLM API constrains is measured in tokens, not characters or words: the context window, the
maximum output, rate limits (tokens per minute) and the bill. If you only learn the count after the
request, from the response's usage field, you cannot prevent the failures that matter: a prompt that
overflows the context window and is rejected, a retrieved context that crowds out the answer budget, or
a batch job whose cost you could not estimate in advance.

The naive alternative is a heuristic such as "about four characters per token". That is a reasonable
average for English prose, but it is wrong by large factors exactly where it hurts: code, JSON, URLs,
numbers, and most non-English scripts tokenise into many more tokens per character, so a heuristic that
is fine for a blog post overflows the window on a stack trace or a Japanese contract. The other naive
option, calling an API to count, adds latency and a network dependency to every prompt you assemble.

tiktoken solves this by shipping the exact vocabulary and merge ranks for OpenAI encodings and applying
them locally, quickly enough to run on every request and over whole corpora during chunking.

## How it works
### Byte-level BPE
Text is first split into chunks by a regular expression (separating words, numbers, punctuation and
whitespace runs in an encoding-specific way), then each chunk is converted to UTF-8 bytes. BPE then
repeatedly merges the adjacent pair of byte sequences with the lowest rank in the learned merge table
until no mergeable pair remains; each final piece maps to one token id. Working on bytes means any
string, including emoji or invalid-looking input, can be encoded without an unknown-token fallback,
because every byte is in the base vocabulary.

### Encodings
An encoding is the regex plus the merge ranks plus special tokens. `r50k_base` and `p50k_base` belong to
GPT-2/GPT-3-era models, `cl100k_base` to the GPT-3.5/GPT-4 generation, and `o200k_base` to GPT-4o and
later models. `tiktoken.encoding_for_model(name)` maps a model name to its encoding; use it rather than
hard-coding, because newer models may use a different encoding. Larger vocabularies tend to encode the
same text in fewer tokens, especially for non-English text.

### API surface
`enc.encode(text)` returns a list of ints, `enc.decode(ids)` returns text, `enc.encode_ordinary(text)`
ignores special tokens entirely, and `encode_batch` parallelises over many strings. By default `encode`
raises an error if the text contains a special-token string such as `<|endoftext|>`; this is a deliberate
safety choice, because silently encoding user text as a real control token would let input inject
document boundaries. You opt in with `allowed_special` or treat them as plain text with
`disallowed_special=()`.

### Chat overhead
Chat requests add per-message formatting tokens (role markers and separators) that are not in your text.
The exact overhead depends on the model and is not a stable public contract, so treat local counts of
message content as close estimates and reconcile against the `usage` field the API returns.

### Caching
The first time an encoding is used, tiktoken downloads its rank file and caches it on disk
(configurable with the `TIKTOKEN_CACHE_DIR` environment variable). In air-gapped or locked-down
environments the first call fails unless the file is pre-placed in that cache.

## Example
Budgeting a RAG prompt so retrieved chunks never crowd out the answer.

```python
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")      # resolves to o200k_base
CONTEXT, RESERVED_OUTPUT, OVERHEAD = 128_000, 4_000, 200   # overhead: rough allowance for chat formatting

def n_tokens(s: str) -> int:
    return len(enc.encode(s, disallowed_special=()))   # treat '<|endoftext|>' in user text as plain text

def pack(system: str, question: str, chunks: list[str]) -> list[str]:
    budget = CONTEXT - RESERVED_OUTPUT - OVERHEAD - n_tokens(system) - n_tokens(question)
    kept = []
    for c in chunks:                              # chunks already sorted by relevance
        cost = n_tokens(c)
        if cost > budget:
            break
        kept.append(c)
        budget -= cost
    return kept

for s in ["hello world", '{"user_id": 98213, "ok": true}', "東京都の天気"]:
    ids = enc.encode(s)
    print(len(s), "chars ->", len(ids), "tokens", ids[:6])
# The chars-to-tokens ratio differs sharply between English, JSON and Japanese; that spread is
# exactly why a fixed chars/4 heuristic misbudgets prompts.
```

Truncating by tokens, `enc.decode(ids[:n])`, can cut a multi-byte character in half; the decoder then
emits a replacement character, so truncate on chunk or sentence boundaries where possible.

## Trade-offs
- Local exact counts for OpenAI models are fast and free, but they are only estimates for Anthropic,
  Gemini or open models; use those providers' own token-counting endpoints or tokenizers when exactness matters.
- Counting every prompt prevents context-overflow errors before they happen, but adds CPU work per
  request; it is negligible for chat, but worth batching with `encode_batch` over large corpora.
- Token-based chunking gives predictable chunk sizes against model limits, but cuts across sentences
  unless combined with structural splitting, which can hurt retrieval quality.
- The `usage` field in responses is authoritative and costs nothing, but arrives after the money is
  spent; use local counts for admission control and the usage field for billing and reconciliation.

## Pitfalls
- Using tiktoken counts to budget a non-OpenAI model: prompts that "fit" get rejected or truncated by
  the provider, especially for code and non-English text where tokenizers diverge most.
- Hard-coding `cl100k_base` for every model: counts drift for newer models on a different encoding, and
  the symptom is cost estimates that disagree with billed usage.
- Calling `encode` on raw user input without setting special-token handling: a document containing
  `<|endoftext|>` raises an exception and the request fails with a confusing error.
- Forgetting chat-format and tool-schema overhead: requests near the limit fail even though your
  content-only count said they fit, because tool definitions and role markers also consume tokens.
- Deploying to an environment without internet access: the first encode call fails trying to download
  the encoding file, unless the cache directory was pre-populated.

## When to use
Use tiktoken whenever you assemble prompts dynamically for OpenAI models: packing retrieved context,
trimming conversation history, enforcing per-user token limits, chunking documents to a token size, and
estimating cost for a batch job before you run it. For other providers, use it only as a rough
estimate and prefer their official counter, or the Hugging Face tokenizer for an open model. Measure the
gap between your local estimate and the API's reported input count (`usage.prompt_tokens` in Chat
Completions, `usage.input_tokens` in the Responses API) on real traffic; a stable
small gap is chat overhead, a large or growing gap means the wrong encoding. Interviewers like asking why
character heuristics fail for code and non-English text.

## Role lens
- **Engineer:** Counts tokens to pack context, trims history to a budget, and chunks corpora by
  token length using the encoding that matches the target model.
- **Architect:** Sets context-budget policy (reserved output tokens, retrieval share) and decides where
  local estimates suffice versus where provider-reported usage drives billing.
- **FDE:** Estimates a customer's monthly token spend from a sample of their real documents, and
  pre-seeds the tiktoken cache for offline deployments.

## Check yourself
**Q:** A team estimates monthly cost for a code-review assistant by counting characters in sample
diffs and dividing by four. Actual bills come in much higher. Explain why and how you would fix the
estimate.
**A:** The four-characters-per-token rule is an average for English prose. Code has short identifiers,
symbols, indentation runs and numbers that BPE splits into more tokens per character, so the heuristic
undercounts. The fix is to tokenise a representative sample of real diffs with the target model's
encoding, via `encoding_for_model`, measure the actual token distribution including system prompt,
tool definitions and chat overhead, and multiply by expected volume and output length. Then reconcile
with `usage` numbers from a pilot week, because billed input also includes formatting and any retries.

## Quiz
### Q: Why does tiktoken's `encode` raise an error by default when text contains `<|endoftext|>`?
- [ ] Because that string cannot be represented in UTF-8
- [x] To avoid silently turning user-supplied text into a real control token
- [ ] Because special tokens are only valid in the output, never the input
- [ ] Because the encoding file does not contain special tokens
> Encoding the string as the actual special token would let untrusted text insert document boundaries.
> The default forces you to choose: allow it explicitly, or treat it as ordinary text.

### Q: Your tiktoken count says a prompt is 7,500 tokens, but a non-OpenAI model reports 9,000. What is the most likely explanation?
- [ ] tiktoken skips whitespace when counting
- [ ] The provider double-counts the system prompt
- [x] The other model uses a different tokenizer, so the same text produces a different number of tokens
- [ ] tiktoken counts only the last message in a conversation
> Token counts are a property of a specific vocabulary and merge table. tiktoken is exact only for OpenAI
> encodings; for other providers, use their own token-counting tool when the number matters.

### Q: Why does byte-level BPE never need an "unknown token"?
- [ ] Because its vocabulary includes every word in every language
- [ ] Because unknown characters are removed before encoding
- [x] Because every possible byte is in the base vocabulary, so any string can be encoded
- [ ] Because it falls back to character-level encoding using Unicode code points
> Text is converted to UTF-8 bytes and all 256 byte values are base tokens, so any input decomposes into
> known pieces. Merges only make common sequences shorter; they are never required for coverage.
