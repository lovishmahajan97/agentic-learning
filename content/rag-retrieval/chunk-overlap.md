---
id: chunk-overlap
module: rag-retrieval
name: Chunk overlap
kind: concept
difficulty: intro
est_minutes: 10
status: reviewed
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/retrieval-granularity
  - rag-retrieval/parent-document-retrieval
  - rag-retrieval/contextual-retrieval
  - rag-retrieval/lost-in-the-middle
  - data-architecture/vector-store-sizing
  - python-for-ai/tiktoken
sources:
  - Official docs: LangChain text splitters
  - Anthropic, 2024 — Introducing Contextual Retrieval
---

## TL;DR
Overlap makes consecutive chunks share their last and first few tokens, so a sentence or reference
that straddles a boundary appears whole in at least one chunk instead of being half in each. It is a
cheap hedge against boundary damage, paid for with a larger index and near-duplicate results that must
be deduplicated at query time. It does not replace good boundaries; structure-aware splitting and
parent retrieval fix the root cause that overlap only patches.

## Why it exists
Any chunker that cuts text into pieces will sometimes cut in the wrong place. A fixed-size splitter
does it constantly; even a recursive splitter that respects paragraphs will split a long paragraph
mid-argument. The damage is specific: the sentence "This limit does not apply to enterprise plans" lands
in chunk 8, while the definition of "this limit" sits at the end of chunk 7. A query about enterprise
limits matches chunk 8, the generator reads it, and "this limit" is unresolved. Worse, the embedding of
chunk 8 itself is weaker, because its first sentence is a dangling pronoun with no antecedent.

The naive fix is to make chunks bigger so boundaries are rarer. That trades a boundary problem for a
dilution problem (see chunking strategies) and does not eliminate boundaries, it only moves them.
Another naive fix is to return neighbouring chunks at query time, which helps the generator but does
nothing for the retrieval step, where the damaged chunk still scores low.

Overlap attacks the retrieval step directly. If chunk 8 begins by repeating the last 50 tokens of chunk
7, the antecedent travels with the pronoun, the chunk's vector is more specific, and whichever chunk
the retriever picks contains the complete thought. The idea is a sliding window with a stride smaller
than the window, and every cost it carries follows from that one choice.

## How it works
### The sliding window
Given a token stream, a chunk size `S` and an overlap `O`, the stride between chunk starts is
`S − O`. Chunk *i* covers tokens `[i·(S−O), i·(S−O) + S)`. Each token that is not near the document's
ends therefore appears in `S / (S − O)` chunks on average. With `S = 400` and `O = 100` the stride is
300 and each token appears in about 1.33 chunks; with `O = 200` the stride is 200 and every token is
indexed twice. This ratio is the index inflation factor, because the number of chunks for a document of
`T` tokens is roughly `(T − O) / (S − O)` rather than `T / S`.

### Why the last tokens, not random ones
Overlap always repeats the *tail* of the previous chunk at the *head* of the next, because the damage
to repair is local to the boundary: an antecedent is normally within a sentence or two of the pronoun,
and a table header is immediately above its rows. Repeating anything further back buys nothing.

### Aligning to boundaries
Overlap measured in raw tokens can itself cut mid-sentence, giving the next chunk a half-sentence
preamble. Better splitters snap the overlap to the nearest sentence boundary: "repeat the last one or
two complete sentences" rather than "repeat the last 64 tokens". Recursive splitters implement this by
choosing which trailing pieces to carry forward when they start a new chunk. The consequence is that
overlap there is a *ceiling*, not a guarantee: LangChain's recursive splitter, for example, carries
forward only whole trailing pieces whose combined length fits within `chunk_overlap`, so if the last
piece is a 150-token paragraph and the overlap is 100, nothing is carried and that boundary has zero
overlap.

### Choosing the amount
Common practice is an overlap of roughly 10–20% of the chunk size (illustrative, not a benchmark).
Below that, the overlap often fails to carry a whole sentence; above it, index inflation and duplicate
hits grow faster than retrieval improves. The right value is measured, not chosen: run recall@k on a
golden set with overlap at 0, 10% and 20% and keep the smallest value that stops boundary failures.
If zero overlap already works because the splitter respects structure, keep zero.

### Deduplication at query time
Because adjacent chunks share text, a query that matches the shared region returns both, with nearly
identical scores. Without handling, the top-5 can contain three copies of the same passage, wasting
context slots and starving the answer of diversity. The fix is to collapse neighbours: group hits by
document, merge chunks whose indices are adjacent into one span (keep the max score), and only then
take the top-k. Merging must use the stored token or character offsets, not plain concatenation:
two neighbours joined naively repeat the shared `O` tokens in the middle of the merged span. This is
the first step toward parent-document retrieval, where the index unit and the
returned unit are deliberately different.

### What overlap does not fix
Overlap patches *local* boundary damage. It does not restore a heading three pages up, a definition in
another section, or the document title. Those need structure-aware chunking (carry the heading path),
contextual retrieval (prepend a generated summary of where the chunk sits), or parent retrieval.

## Example
A token-based sliding window with tiktoken, plus the arithmetic that shows index inflation. The window
returns token ids, decoded for display; production splitters additionally snap the overlap to sentence
boundaries so a chunk never begins mid-word or mid-sentence.

```python
import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

def sliding_chunks(text: str, size: int = 400, overlap: int = 100) -> list[str]:
    assert 0 <= overlap < size, "overlap must be smaller than the chunk size"
    ids = enc.encode(text)
    stride = size - overlap
    chunks = []
    for start in range(0, len(ids), stride):
        chunks.append(enc.decode(ids[start:start + size]))
        if start + size >= len(ids):        # last window reached the end
            break
    return chunks

text = "Limits apply per project. This limit does not apply to enterprise plans. " * 150
for overlap in (0, 100, 200):
    n = len(sliding_chunks(text, 400, overlap))
    print(f"overlap={overlap:3d}  chunks={n:3d}  inflation={n / len(sliding_chunks(text, 400, 0)):.2f}x")
# This text is ~2,100 cl100k tokens: 6 chunks at overlap 0, 7 at 100 and 10 at 200 (1.17x, 1.67x).
# On a short text the partial last window hides some of the cost; on long documents the ratio
# approaches S / (S - O), i.e. 1.33x and 2x, as the worked numbers below show.
```

Worked numbers: a 100,000-token manual with `S = 400` and `O = 0` yields 250 chunks. With `O = 100`
the stride is 300, so about `(100,000 − 100) / 300 ≈ 333` chunks, a 33% larger index and embedding
bill. With `O = 200` the stride is 200 and the count is about 499, double the original. The retrieval
gain from 100 to 200 is usually small while the cost doubles, which is why moderate overlap is the norm.

## Trade-offs
- Overlap repairs boundary cuts for free at the splitting step, but every token of overlap is paid
  again in storage, embedding cost and index size, in proportion to `S / (S − O)`.
- More overlap raises the chance that a straddling sentence is whole somewhere, yet it also raises the
  chance that the top-k contains near-duplicates, so dedup logic becomes mandatory rather than optional.
- Token-measured overlap is simple and deterministic, while sentence-aligned overlap costs a sentence
  splitter but avoids chunks that open with a fragment; prefer sentence alignment on prose.
- Zero overlap with a structure-aware splitter is often as good as moderate overlap with a fixed one;
  if your boundaries already fall on paragraphs, overlap mostly buys inflation.

## Pitfalls
- Returning raw top-k without collapsing adjacent chunks; the symptom is the same passage appearing two
  or three times in the prompt and the answer missing information that ranked sixth.
- Setting overlap as a fraction of a tiny chunk, such as 20 tokens on a 128-token chunk; the symptom
  is overlaps that rarely hold a complete sentence, so boundary failures persist unchanged.
- Assuming the configured overlap is what the splitter produced; with piece-based recursive splitters
  the symptom is boundary failures persisting at exactly the boundaries where a long paragraph or
  sentence ended the previous chunk, because no whole piece fit inside the overlap budget. Inspect
  actual shared text between neighbours, not the config.
- Letting overlap exceed half the chunk size; the symptom is an index two or three times larger than
  the corpus with no measurable recall gain, and slower queries from the extra vectors.
- Treating overlap as the fix for missing headings or definitions far from the boundary; the symptom
  is chunks that still lack their subject, which overlap cannot supply because it only repeats
  neighbouring text.
- Forecasting chunk count as corpus tokens divided by chunk size, ignoring the stride, so capacity
  planning and cost forecasts undershoot; the symptom is a vector store filling up well before the
  projected corpus size.

## When to use
Use moderate overlap whenever your splitter can cut inside a sentence or argument, which is any fixed
size splitter and most recursive splitters on long paragraphs. Skip it when boundaries already follow
structure (one chunk per section, per function, per FAQ entry) or when you use small-to-big retrieval,
where the parent supplies the context overlap was trying to preserve. Always pair overlap with query
time deduplication of adjacent chunks. Measure recall@k at a few overlap settings and watch index size
and duplicate rate in the top-k. In interviews, the expected depth is the stride arithmetic, the dedup
requirement, and the honest statement that overlap patches local damage rather than fixing boundaries.

## Role lens
- **Engineer:** Sets size and overlap in the splitter, writes the adjacent-chunk collapse step in the
  retriever, and checks duplicate rate in top-k on the eval set.
- **Architect:** Accounts for the inflation factor in vector-store sizing and embedding budgets, and
  decides whether parent retrieval should replace overlap for the main corpus.
- **FDE:** Shows a customer a boundary failure from their own documents to justify overlap or
  structure-aware splitting, and tunes the amount against their questions rather than defaults.

## Check yourself
**Q:** A colleague proposes raising overlap from 50 to 300 tokens on 512-token chunks because "more
context is always better". What are the consequences, and what would you propose instead?
**A:** The stride drops from 462 to 212 tokens, so the chunk count and the embedding and storage bill
rise by about 2.2×, and every query now returns clusters of near-identical chunks that crowd out
diverse evidence unless adjacent hits are merged. The recall gain is likely small because overlap only
repairs damage within a sentence or two of each boundary; most of the extra 250 tokens duplicate
content that was already whole. I would keep overlap moderate, align it to sentence boundaries so the
repeated tail is complete sentences, add adjacent-chunk collapsing in the retriever, and if the real
problem is missing context, switch to structure-aware chunking with heading paths or small-to-big
retrieval, then confirm on a golden set with recall@k rather than on the intuition that more is better.

## Quiz
### Q: With 400-token chunks and a 100-token overlap, roughly how many chunks does a 100,000-token document produce?
- [ ] About 250, because overlap does not change the chunk count
- [x] About 333, because the stride between chunk starts is 300 tokens
- [ ] About 400, because each chunk effectively holds 300 unique tokens plus padding
- [ ] About 500, because every token is indexed twice
> Chunk starts advance by the stride, size minus overlap, which is 300 here, so the count is close to
> 100,000 / 300. Doubling (about 500) would need an overlap of 200, half the chunk size.

### Q: Why must a retriever over overlapping chunks deduplicate adjacent hits?
- [ ] Because the vector database rejects vectors with near-identical values
- [ ] Because overlapping chunks share the same id and overwrite each other
- [x] Because a query matching the shared region returns both neighbours, wasting context slots on duplicates
- [ ] Because overlapping chunks always have exactly equal cosine scores
> Shared text gives neighbouring chunks similar vectors, so both rank high for the same query. Without
> collapsing them, the prompt carries repeated text and loses lower-ranked but distinct evidence. Ids
> and the database are unaffected.

### Q: Which problem does chunk overlap NOT solve?
- [ ] A sentence cut in half at a chunk boundary
- [ ] A pronoun at the start of a chunk whose antecedent is in the previous chunk
- [x] A chunk that lacks the section heading three pages above it
- [ ] A table row separated from the header row immediately above it
> Overlap repeats only the tail of the previous chunk, so it repairs local damage near the boundary.
> Context far away, like a heading pages earlier, needs structure-aware chunking, contextual prefixes
> or parent retrieval.
