---
id: tokenization
module: model-internals
name: Tokenization (BPE, SentencePiece)
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - model-internals/embeddings
  - model-internals/context-window
  - model-internals/logits
  - python-for-ai/tiktoken
  - observability/token-cost-tracking
  - prompting/structured-outputs-json-mode
  - rag-retrieval/chunking-strategies
sources:
  - Sennrich et al., 2016 — Neural Machine Translation of Rare Words with Subword Units
  - Kudo and Richardson, 2018 — SentencePiece: A simple and language independent subword tokenizer and detokenizer for Neural Text Processing
  - Kudo, 2018 — Subword Regularization: Improving Neural Network Translation Models with Multiple Subword Candidates
  - Radford et al., 2019 — Language Models are Unsupervised Multitask Learners
---

## TL;DR
A tokenizer turns text into the integer ids a model consumes. Byte-pair encoding, the dominant method,
builds a vocabulary of frequent subword chunks by repeatedly merging the most common adjacent pair, so
common words become one token and rare words split into pieces without ever being out of vocabulary.
SentencePiece is a toolkit that trains BPE or unigram models on raw text with whitespace as an ordinary
symbol, making it language-independent. Tokens are the unit of cost, context and many odd model
behaviours.

## Why it exists
A language model needs a fixed, finite set of symbols: an embedding row for each and a softmax over all
of them at every step. Three ways to define the symbols were tried, and two fail. Characters or raw bytes
give a tiny vocabulary (256 for bytes) and nothing is ever unknown, but English text becomes roughly
four times longer than with a subword tokenizer, which multiplies attention cost and forces the model to spend capacity
learning spelling before it can learn meaning; each token carries very little information. Whole words
give short, meaningful sequences but an unbounded vocabulary: every typo, URL, identifier, number and new
coinage is a new word, the embedding table explodes, and anything unseen at inference becomes an
`<unk>` token. Morphologically rich languages such as Finnish or Turkish make the word approach fail even
on ordinary text.

Subword tokenization is the compromise. Frequent words stay whole, rare words split into frequent pieces,
and a byte or character fallback guarantees any input can be encoded. Sennrich et al. adapted Gage's
1994 byte-pair compression algorithm to neural translation for exactly the rare-word problem, and
byte-level BPE in GPT-2 removed the last unknown-token case. The result bounds the vocabulary (tens of
thousands to a few hundred thousand entries), keeps sequences short, and makes the embedding and output
layers a fixed, affordable size. Everything about cost and context in practice is downstream of this
choice.

## How it works
### BPE training
1. **Pre-tokenize.** Split the text into chunks with a regular expression, typically at spaces,
   punctuation and digit runs, so merges never cross a word boundary. GPT-2's pattern attaches the
   leading space to the word, which is why `" hello"` and `"hello"` are different tokens; GPT-4-era
   tokenizers split digit runs into groups of at most three.
2. **Start from a base vocabulary.** Every character in the corpus, or all 256 byte values for byte-level
   BPE, so any string can be represented from the start.
3. **Count adjacent pairs** of symbols across the corpus, weighted by frequency, and **merge the most
   frequent pair** into a new symbol. Record the merge as a rule.
4. **Repeat** until the vocabulary reaches the target size: 50,257 for GPT-2, 32,000 for Llama 2,
   roughly 100k for GPT-4's `cl100k_base`, 128,256 for Llama 3.

The greedy frequency criterion is a compression objective: each merge removes the most occurrences of a
pair, so the corpus shrinks fastest in token count. It is cheap, deterministic and good enough that
nothing has displaced it at scale.

### BPE encoding
To encode a new string, pre-tokenize it, start each chunk as bytes or characters, and repeatedly apply
the merge with the lowest rank (earliest learned) that matches, until no merge applies. The resulting
symbols map to ids. Decoding is concatenation; with byte-level BPE the bytes are then decoded as UTF-8.

### SentencePiece
SentencePiece (Kudo and Richardson) is a library and model format rather than a single algorithm, and it
differs from GPT-style BPE in how it sees text. It operates on the raw Unicode stream with whitespace
treated as a normal character (the `▁` symbol, U+2581), so it needs no language-specific pre-tokenizer
and works unchanged for Chinese, Japanese or Thai, which have no spaces. Decoding is lossless with
respect to the *normalized* text: join the pieces and replace `▁` with a space. It applies Unicode
normalization (NFKC-based by default, which folds full-width characters and ligatures, so
`decode(encode(x))` can differ from `x`), offers a
`byte_fallback` option so unknown characters decompose into bytes, and ships as one self-contained model
file, so there is no separate regex to keep in sync. It trains either BPE or the unigram model. Llama 1
and 2, T5 and Mistral used SentencePiece models; Llama 3 moved to a tiktoken-style byte-level BPE with a
larger vocabulary for better multilingual efficiency.

### The unigram model
Unigram tokenization (Kudo, 2018), SentencePiece's default, works in the opposite direction from BPE.
Start from a large candidate vocabulary of frequent substrings, assign each a probability, and define
the tokenization of a string as the most probable segmentation under the assumption that tokens are
independent (found with the Viterbi algorithm). Then iteratively remove the tokens whose removal hurts
corpus likelihood least until the vocabulary reaches its target size. Because it is probabilistic, it can
sample alternative segmentations during training as a form of data augmentation (subword
regularization), and its pieces tend to align more with morphology. WordPiece, used by BERT, is a BPE
variant that picks merges by likelihood gain rather than raw count (a pair scores high when it occurs
together more often than its parts' frequencies predict), marks word-internal pieces with `##`, and
encodes by greedy longest-match from the left rather than by replaying merges.

### Consequences you will meet
- **Cost and context are counted in tokens.** English prose runs around four characters or three
  quarters of a word per token with OpenAI-style tokenizers; code, numbers and non-Latin scripts cost
  more per character, often several times more (an order of magnitude figure that varies by tokenizer
  and language). Vocabularies were enlarged in part to fix this.
- **Numbers.** Llama 2 splits every digit into its own token; GPT-4-era tokenizers group up to three.
  Arithmetic behaviour and number formatting sensitivity follow from this.
- **Spaces and case.** A prompt ending in a trailing space pushes the next token off the distribution
  the model saw, because the space would normally be attached to the following word.
- **Character-level tasks fail.** Counting letters, reversing strings or spelling rules are hard because
  the model never sees characters, only chunks.
- **Glitch tokens.** Tokens that exist in the vocabulary but barely appeared in training produce erratic
  behaviour; the "SolidGoldMagikarp" family in GPT-2/3 vocabularies is the known example.
- **The tokenizer is part of the model.** Fine-tuning must use the same tokenizer and the same special
  tokens and chat template; adding tokens means resizing and initializing embeddings; constrained
  decoding and grammars operate on token boundaries, not characters.

## Example
A complete, runnable BPE trainer and encoder in about thirty lines. The corpus is tiny so you can trace
the merges by hand; the mechanism is identical to what produced GPT-2's 50k merges.

```python
from collections import Counter

corpus = "low low low lower lower lowest newer newer newest wider widest".split()
words = Counter(tuple(w) + ("</w>",) for w in corpus)      # symbols per word, with end-of-word marker

def pair_stats(words):
    stats = Counter()
    for sym, freq in words.items():
        for a, b in zip(sym, sym[1:]):
            stats[(a, b)] += freq
    return stats

def apply_merge(seq, pair):
    out, i = [], 0
    while i < len(seq):
        if i < len(seq) - 1 and (seq[i], seq[i + 1]) == pair:
            out.append(pair[0] + pair[1]); i += 2
        else:
            out.append(seq[i]); i += 1
    return out

merges = []
for step in range(8):                                        # vocab = base symbols + 8 merges
    stats = pair_stats(words)
    best = max(stats, key=stats.get)                         # most frequent adjacent pair
    merges.append(best)
    words = Counter({tuple(apply_merge(list(s), best)): f for s, f in words.items()})
    print(step, best, stats[best])
# 0 ('l','o') 6 | 1 ('lo','w') 6 | 2 ('e','r') 5 | 3 ('er','</w>') 5 | 4 ('low','</w>') 3 | ...

def encode(word):                                            # apply merges in learned order
    seq = list(word) + ["</w>"]
    for pair in merges:
        seq = apply_merge(seq, pair)
    return seq

print(encode("lowest"), encode("slowest"))
# ['low', 'est</w>'] ['s', 'low', 'est</w>']  <- unseen "slowest" still encodes, no <unk>
```

For real counting use the model's own tokenizer: `tiktoken.get_encoding("cl100k_base").encode(text)`
for OpenAI models, `AutoTokenizer.from_pretrained(name)(text).input_ids` for open models.

## Trade-offs
- A larger vocabulary means fewer tokens per text, so lower cost, longer effective context and faster
  generation, but a bigger embedding and output layer (128k × 4096 in fp16 is about 1 GB per untied
  matrix) and rarer tokens that are undertrained; it pays off when the workload is multilingual or
  code-heavy.
- Byte-level BPE can encode anything and never emits unknown tokens, but scripts whose merges were
  underrepresented in training fall apart into byte fragments; character-level SentencePiece with byte
  fallback handles CJK more gracefully at the cost of normalization decisions.
- BPE is greedy and deterministic; unigram is probabilistic, supports subword regularization and tends
  toward morphological pieces, which helps most when training a tokenizer and model from scratch on
  limited data.
- Aggressive pre-tokenization (splitting digits, isolating punctuation) makes behaviour on numbers and
  code more consistent but raises token counts on numeric or structured text.

## Pitfalls
- Counting tokens with the wrong tokenizer, such as `cl100k_base` for a Llama model; the symptom is
  context-overflow errors or silently truncated prompts in production although the estimate said they fit.
- Ending a prompt with a trailing space or stitching completions at a non-token boundary; the symptom is
  a first generated token that starts mid-word, doubles a space or is otherwise odd.
- Chunking documents by a fixed characters-per-token ratio; the symptom is code or non-English chunks
  that exceed the embedding model's limit and get truncated without error.
- Resizing the vocabulary for new special tokens without initializing the new embedding rows sensibly;
  the symptom is a loss spike in the first steps of fine-tuning.
- Asking the model for character-level work (letter counts, exact string reversal, regex-like
  validation); the symptom is confident, inconsistent answers that no prompt reliably fixes.
- Streaming output by decoding each token id on its own; a multi-byte UTF-8 character (accented
  letters, CJK, emoji) can span two byte-level tokens, so the symptom is `�` replacement characters in
  streamed text that disappear when the full response is decoded at once. Buffer incomplete bytes.
- Mismatched chat-template special tokens between training and serving; the symptom is a fine-tuned
  model that ignores its system prompt or never emits a stop token.

## When to use
You do not choose a tokenizer per request, but it shapes several decisions you do make. When selecting a
model for a market, measure tokens per character on a sample of your real corpus across candidate
tokenizers; the ratio can differ by multiples between languages and drives cost and latency directly.
Budget prompts and chunk documents with the model's own tokenizer, never a character heuristic. Train a
new tokenizer only when you are also training a model from scratch on a domain whose symbols existing
vocabularies handle badly, such as DNA, logs or a new programming language. In interviews, be ready to
walk through the merge loop, contrast byte-level BPE with SentencePiece and unigram, and explain from
tokenization why a model miscounts letters or why Hindi costs more than English.

## Role lens
- **Engineer:** Counts tokens with the correct tokenizer before every call, handles chat-template special
  tokens exactly, and tokenizes before chunking rather than after.
- **Architect:** Builds the cost model per language and content type from measured tokens per character,
  and weighs tokenizer efficiency alongside benchmark scores when choosing a model.
- **FDE:** Explains to a customer in Japan or India why bills and latency per character exceed the
  English pilot, and why a chunker tuned on English truncates their documents.

## Check yourself
**Q:** A customer's Hindi support assistant costs about three times more per conversation than the
English pilot on the same model, with the same number of turns. Explain the mechanism and what you would
do about it.
**A:** The model's tokenizer learned its merges from a corpus dominated by English, so English words map
to one or two tokens while Devanagari text falls apart into many short pieces or, with byte-level BPE,
into byte fragments; the same message costs several times more tokens, and cost, latency and context
consumption all scale with tokens. I would first measure tokens per character on a sample of real Hindi
conversations across the candidate tokenizers to quantify the ratio. Then I would evaluate a model whose
vocabulary was built with multilingual data, such as one with a larger vocabulary, since fewer tokens
per message cuts cost and leaves more room in the context window; check that quality holds on their
tasks; and revisit the chunking and context budgets, which were tuned on English token counts and are
probably truncating Hindi content.

## Quiz
### Q: What does BPE do at each step of training?
- [ ] Removes the least likely token from a large initial vocabulary
- [x] Merges the most frequent adjacent pair of symbols in the pre-tokenized corpus into a new symbol
- [ ] Splits the longest word in the corpus into two halves
- [ ] Assigns a probability to every substring and keeps the top-k
> BPE grows a vocabulary bottom-up by greedy frequency merges. Pruning a large vocabulary by likelihood is
> the unigram model, which SentencePiece also implements and which runs in the opposite direction.

### Q: What distinguishes SentencePiece from a GPT-style BPE tokenizer?
- [ ] It can only produce character-level tokens
- [ ] It requires a language-specific word splitter before training
- [x] It treats raw text, including whitespace, as a stream of symbols and trains BPE or unigram models without a pre-tokenizer
- [ ] It cannot be used for languages that use spaces
> SentencePiece encodes whitespace as the ▁ symbol and segments the raw Unicode stream directly, which is
> why it works for Japanese or Thai without a word splitter and decodes losslessly.

### Q: Why do LLMs make mistakes counting the letters in a word?
- [ ] Because the softmax cannot represent small integers
- [ ] Because attention cannot look at more than one token at a time
- [x] Because the model sees subword tokens, not characters, so the letters inside a token are not individually visible
- [ ] Because the tokenizer removes repeated letters
> A word like "strawberry" arrives as a few chunks whose internal spelling is only indirectly learned.
> Character-level questions ask the model to recover information the tokenizer collapsed.
