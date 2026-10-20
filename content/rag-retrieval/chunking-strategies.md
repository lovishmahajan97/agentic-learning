---
id: chunking-strategies
module: rag-retrieval
name: Chunking strategies (fixed, recursive, semantic)
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - rag-retrieval/chunk-overlap
  - rag-retrieval/retrieval-granularity
  - rag-retrieval/document-parsing
  - rag-retrieval/parent-document-retrieval
  - rag-retrieval/contextual-retrieval
  - rag-retrieval/embedding-models
  - model-internals/tokenization
  - python-for-ai/tiktoken
sources:
  - Official docs: LangChain text splitters
  - Official docs: LlamaIndex node parsers
---

## TL;DR
An embedding model turns a span of text into one vector, so a whole document becomes a blurry average
in which the specific passage a question needs is drowned out, and text past the
encoder's input limit is lost. Chunking cuts documents into retrievable units first. Fixed-size is
predictable but blind to structure; recursive splitting tries natural boundaries (paragraph, line,
sentence) before falling back to hard cuts; semantic splitting places boundaries where adjacent
sentences stop being about the same thing. The strategy defines what "a hit" means downstream.

## Why it exists
Three constraints force chunking. Two are hard. Embedding models have a maximum input length, typically
512 tokens for BERT-derived encoders and around 8,000 for many newer long-context ones. Local libraries
such as sentence-transformers truncate silently (some hosted APIs return an error instead): the second
half of a long document simply never reaches the vector, so no query can ever retrieve it. The generation side has a budget too; stuffing ten full documents into every prompt is
slow, expensive and, past a point, hurts answer quality because the model has to find the needle itself.

The third constraint is softer but decides retrieval quality: dilution. Pooling a 30-page contract into
one vector gives you "a contract", not "the indemnification cap in clause 14.3". A query about that
cap scores middling against the whole-document vector and loses to a shorter document that happens to
mention caps. A chunk holding only clause 14 scores high. The vector of a small, focused span is a
sharper key.

The naive fix, cutting every N characters, trades one failure for another. It splits sentences, table
rows and function bodies in half, so chunks embed half-thoughts, and it separates headings from the
paragraphs they explain, so a chunk says "set this to 30 seconds" with no indication of what. Chunking
strategy is therefore a design decision about what unit of meaning gets its own address in the index,
not a preprocessing detail.

## How it works
### The common skeleton
Every splitter answers three questions: where a boundary is allowed to fall, how large a piece may be,
and what metadata travels with each piece. Size is measured in tokens rather than characters, because
both the embedder's limit and the LLM's context budget are in tokens and the characters-per-token
ratio varies by language and content (English prose is roughly four characters per token; code, CJK
text and tables differ). Count with the embedding model's own tokenizer when you can, or with a close
proxy such as tiktoken, and leave headroom under the model limit.

### Fixed-size
Walk the token stream and cut every N tokens, optionally with overlap. Because it ignores content, it
is trivial to implement, produces uniform chunk sizes (predictable index size and cost) and works on
text with no structure at all, such as OCR output or log files. It fails precisely where content has
structure: mid-sentence cuts, headings separated from bodies, a code function split between its
signature and its body, a table split between header row and data rows.

### Recursive (separator hierarchy)
Take an ordered list of separators from coarse to fine, for example `["\n\n", "\n", ". ", " ", ""]`.
Split on the coarsest separator first. Any piece that still exceeds the limit is split again with the
next separator, recursively; pieces smaller than the limit are merged greedily with their neighbours up
to the limit. The design logic: paragraphs are semantic units, so most chunks end on paragraph
boundaries; the fallback chain guarantees no piece can exceed the limit; and the greedy merge keeps
chunks from being tiny. Structure-aware splitters are this same algorithm with format-specific
separators: Markdown headings (carrying the heading path into each chunk's metadata or text), HTML
block tags, or language-aware separators for code (class and function boundaries). LangChain's
`RecursiveCharacterTextSplitter` and the LlamaIndex sentence and Markdown parsers are implementations
of this idea; the separator order is the main knob.

### Semantic
Split the text into sentences and embed each one. Compute the cosine similarity between consecutive
sentences (or between each sentence and a small window of its neighbours). Mark a breakpoint wherever
similarity drops below a threshold, which can be absolute, a percentile of all drops, or a number of
standard deviations below the mean. Sentences between breakpoints form a chunk, with a size cap and a
fallback split for runs that grow too long. An illustrative run over six transcript
sentences: consecutive similarities of 0.81, 0.78, 0.34, 0.80, 0.76. The drop to 0.34 between sentences
3 and 4 is far below the others, so a percentile rule (break on the largest few percent of distances,
where distance = 1 − similarity) places one boundary there and yields two chunks of three sentences.
The rule is relative on purpose: absolute cosine levels differ by embedding model, so a fixed
threshold such as 0.5 that works under one model splits everywhere or nowhere under another. Because
boundaries follow topic shifts rather than formatting, the result is topically coherent chunks even in unstructured prose such as meeting
transcripts or long narrative. The costs are real: one embedding call per sentence before you have
even embedded the chunks, a threshold that must be tuned per corpus, highly variable chunk sizes, and
little gain over recursive splitting on text that already has paragraphs and headings.

### Choosing the size
Smaller chunks give sharper matches but less context for the generator; larger chunks give more
context but blurrier vectors and fewer of them fit in the prompt. Practitioners usually start somewhere
between a couple of hundred and a thousand tokens (illustrative, not a benchmark) and then measure
recall@k and answer quality on a golden set across several sizes. Factoid lookups prefer small chunks;
"summarise the section" prefers large ones. The tension can be decoupled entirely by indexing small
spans and returning their larger parent, which is the subject of retrieval granularity and
parent-document retrieval.

### Metadata
Every chunk should carry its document id, position index, section or heading path, and source
location (page, URL, line range). This is what makes deduplication, reassembly, citations, metadata
filtering and incremental re-indexing possible later.

## Example
A minimal recursive splitter. It measures in characters to stay short; in production, replace `len`
with a token count from the embedding model's tokenizer. Note how an oversized paragraph falls through
to the next separator while small pieces are merged up to the limit.

```python
SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

def recursive_split(text: str, max_len: int, seps=SEPARATORS) -> list[str]:
    sep = seps[0]
    parts = text.split(sep) if sep else list(text)
    chunks, buf = [], ""
    for i, p in enumerate(parts):
        piece = p + (sep if sep and i < len(parts) - 1 else "")   # keep the separator attached
        if len(piece) > max_len and len(seps) > 1:
            if buf:
                chunks.append(buf); buf = ""
            chunks.extend(recursive_split(piece, max_len, seps[1:]))  # fall through to finer split
        elif len(buf) + len(piece) <= max_len:
            buf += piece                                              # greedy merge
        else:
            chunks.append(buf); buf = piece
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]

doc = ("# Timeouts\n\n" + "Set the connect timeout to 5 seconds. " * 6 + "\n\n"
       "# Retries\n\n" + "Retry idempotent calls at most three times with jitter. " * 4)
for c in recursive_split(doc, max_len=200):
    print(len(c), repr(c[:60]))
# Six chunks: 10, 189, 37, 9, 167, 55 chars, all <= 200 and ending on a paragraph or sentence.
# Note "# Timeouts" and "# Retries" come out as orphan chunks: the oversized paragraph after each
# heading forces a flush, so the body chunks lose their subject. That is exactly why structure-aware
# splitters copy the heading path into every chunk instead of treating it as ordinary text.
```

## Trade-offs
- Fixed-size gives predictability and zero tuning, but it breaks sentences, tables and code; the
  moment the corpus has real structure, recursive splitting wins at the same cost.
- Recursive splitting respects structure cheaply, yet it assumes the separators mean something; on OCR
  text or transcripts with no paragraph breaks it degenerates into fixed-size splitting.
- Semantic chunking produces coherent chunks from unstructured prose, at the price of embedding every
  sentence and tuning a corpus-specific threshold; on already-structured text the gain rarely pays.
- Small chunks sharpen matching but starve the generator of context; the trade disappears once you
  index small spans and return their parent section instead.
- Prepending the heading path to each chunk improves matching and disambiguation, but spends tokens
  on every chunk and can over-weight the heading words for very short chunks.

## Pitfalls
- Counting characters instead of tokens, so chunks exceed the embedder's limit and are truncated
  silently; the symptom is that queries about the tail of long chunks never retrieve anything.
- Splitting tables or code mid-structure; the symptom is retrieved chunks showing column headers with
  no rows, or a function body with no signature, and the model guessing the rest.
- Dropping heading context, so a chunk reads "set this to 30 seconds" with no subject; the symptom is
  plausible-looking retrievals that lead the model to answer for the wrong component.
- Tuning chunk size by intuition rather than against a golden set; the symptom is silent regressions
  after every "improvement" and an argument nobody can settle.
- Leaving tiny trailing chunks (a 12-token final paragraph) in the index; the symptom is short,
  near-empty chunks crowding the top-k for generic queries because their vectors are unspecific.
- Running semantic chunking with an absolute similarity threshold copied from another setup; the
  symptom is either one giant chunk per document or one chunk per sentence after an embedding-model swap.
- Running semantic chunking over short, single-topic documents; the symptom is one chunk per document,
  and the whole exercise collapses back into whole-document embedding with extra cost.

## When to use
Default to recursive, token-counted, structure-aware splitting with a few hundred tokens per chunk and
full metadata on every chunk; it is cheap and handles most corpora. Reach for semantic chunking only
when the corpus is unstructured prose and evaluation shows boundary problems, because its sentence
embedding cost and threshold tuning are only worth paying when structure is absent. Use fixed-size as
the fallback for structureless text or as the inner step of a recursive chain. Measure, do not guess:
hold chunking constant while you tune anything else, and compare strategies on recall@k and end-task
answer quality. In interviews, be ready to explain dilution, why recursive splitting is the sensible
default, and how parent-document retrieval decouples match size from context size.

## Role lens
- **Engineer:** Picks separators and chunk size per document type, counts tokens with the embedder's
  tokenizer, attaches metadata, and re-runs the retrieval eval after every chunking change.
- **Architect:** Owns the decision that chunk configuration is a versioned artifact, so changing it
  triggers a controlled re-index instead of silently mixing two chunkings in one index.
- **FDE:** Inspects a customer's real documents (scanned PDFs, wikis, code) to choose per-source
  strategies, and shows them failing chunks to justify parsing and chunking work.

## Check yourself
**Q:** A team runs RAG over a 2,000-page engineering handbook split into 512-token fixed chunks.
Retrieved chunks look relevant, but the model keeps answering for the wrong subsystem. What is going
wrong and what would you change first?
**A:** Fixed-size cuts have separated headings from bodies, so many chunks are instructions with no
subject ("set the timeout to 30 s") and the embedder cannot tell which subsystem they belong to; the
retriever returns the right-looking text for the wrong section. First switch to a structure-aware
recursive splitter on the handbook's heading hierarchy, prepend the heading path to each chunk's text
and store it as metadata, so both the vector and the generator see the subject. Then consider indexing
smaller spans and returning the parent section, add metadata filtering by subsystem where the question
makes it explicit, and verify the change with a golden set of questions whose expected section is
known, measuring recall@k before and after rather than eyeballing results.

## Quiz
### Q: Why are documents chunked before embedding rather than embedded whole?
- [ ] Vector databases cap the size of a record's payload
- [x] A single vector averages away specific passages, and encoders cannot take input past their limit
- [ ] Chunking is required to compute cosine similarity between texts of different lengths
- [ ] Each vector's dimensionality grows with input length, so long documents must be split to fit
> Pooling a long document into one vector dilutes the specific passage a query needs, and inputs
> beyond the encoder's limit are truncated or rejected. Cosine similarity works at any length, and an
> encoder emits the same fixed dimension whatever the input length, so neither is the reason.

### Q: In a recursive splitter, what happens to a paragraph that is larger than the chunk limit?
- [ ] It is truncated to the limit and the remainder is discarded
- [ ] It is kept intact as a single oversized chunk because paragraphs are atomic
- [x] It is split again using the next, finer separator in the list, recursively
- [ ] It is embedded as two overlapping halves of equal size
> The whole point of the separator hierarchy is the fallback: an oversized piece drops to the next
> separator (newline, then sentence, then word) until every piece fits. Nothing is discarded and no
> chunk may exceed the limit.

### Q: When does semantic chunking justify its cost over recursive splitting?
- [ ] Always, because embedding-based boundaries are strictly more accurate
- [ ] When documents are short and cover a single topic
- [x] When the text is unstructured prose with no paragraph or heading boundaries to exploit
- [ ] When the embedding model has a short maximum input length
> Semantic chunking embeds every sentence and needs a tuned threshold; that is worth it only when
> formatting gives no usable boundaries, such as transcripts. On structured text recursive splitting is
> cheaper and about as good, and on short single-topic documents it yields one chunk.
