---
id: bm25-internals
module: rag-retrieval
name: BM25 internals
kind: concept
difficulty: core
est_minutes: 15
related:
  - rag-retrieval/inverted-indexes
  - rag-retrieval/dense-vs-sparse-retrieval
  - rag-retrieval/hybrid-search
  - rag-retrieval/retrieval-failure-diagnosis
  - model-internals/tokenization
  - evals/retrieval-evaluation
sources:
  - Robertson & Zaragoza, 2009 — The Probabilistic Relevance Framework: BM25 and Beyond
  - Manning, Raghavan & Schütze, 2008 — Introduction to Information Retrieval
  - Official docs: Apache Lucene BM25Similarity
---

## TL;DR
BM25 scores a document for a query by summing, over query terms, a rarity weight (IDF) times a
term-frequency term that saturates and is normalized by document length. Each piece fixes a specific
failure of raw term counting: IDF stops common words dominating, saturation stops a term repeated 50
times beating one repeated 5 times by 10×, and length normalization stops long documents winning by
having more words. Two parameters, k1 and b, set how strong those corrections are.

## Why it exists
The simplest ranking function counts how many times each query term appears in a document and adds the
counts up. Three things go wrong immediately. Common words swamp the score: "the" appears everywhere, so
a query "the indemnification cap" is dominated by a term that carries no information. Raw counts scale
without limit: a document that mentions "cap" fifty times scores ten times higher than one that mentions
it five times, although the second is already clearly about caps and the first is probably a glossary.
And long documents win by default: a 400-page manual mentions almost every term more often than a focused
two-page note simply because it has more words.

TF-IDF addressed the first problem by weighting terms with inverse document frequency and partly the
second with a logarithm of term frequency, but it was a heuristic, not derived from any model of
relevance. BM25 came out of the probabilistic relevance framework developed by Robertson, Spärck Jones
and colleagues and tested at TREC in the 1990s: it asks what score ranks documents by probability of
relevance under a simple generative assumption, then approximates the intractable result with a
two-parameter function that behaves sensibly at the extremes. The output is cheap to compute from an
inverted index, has only two knobs, and has been the default lexical ranking in Lucene, Elasticsearch and
OpenSearch for years because it is hard to beat without training data.

## How it works
### The formula
For query Q with terms q₁…qₙ and document D:

```
score(D, Q) = Σᵢ IDF(qᵢ) ·            f(qᵢ, D) · (k1 + 1)
                            ─────────────────────────────────────────
                            f(qᵢ, D) + k1 · (1 − b + b · |D| / avgdl)
```

where `f(qᵢ, D)` is the term's count in D, `|D|` is D's length in tokens, `avgdl` is the mean document
length in the collection, and (in Lucene's form)

```
IDF(q) = ln( (N − n(q) + 0.5) / (n(q) + 0.5) + 1 )
```

with N the number of documents and n(q) the number containing q. Each piece answers one of the three
failures above.

### IDF: rarity as information
A term present in nearly every document tells you nothing about which one is relevant, so its weight
should approach zero; a term present in a handful should carry most of the score. The ratio
(N − n + 0.5)/(n + 0.5) does that: for n ≪ N it is large, for n ≈ N it is below one. The 0.5 terms
smooth the estimate for very small counts. The original Robertson–Spärck Jones form can go negative for
terms in more than half the documents, which would let a matching term lower a score; Lucene adds 1
inside the log so IDF is always positive and such terms simply contribute almost nothing. With N = 1,000:
a term in 10 documents has IDF ≈ ln(95.3) ≈ 4.56; a term in 600 has IDF ≈ ln(1.67) ≈ 0.51; a term in all
1,000 has IDF ≈ ln(1.0005) ≈ 0.0005.

### Term-frequency saturation: k1
Write L for the length factor `1 − b + b·|D|/avgdl`. The fraction `f·(k1 + 1) / (f + k1·L)` is zero at
f = 0, equals 1 at f = 1 when L = 1, and rises toward k1 + 1 as f → ∞. It is a saturating curve: the
second occurrence of a term adds a lot, the twentieth adds almost nothing, because once a document is
clearly about a term, more repetitions are weak extra evidence. k1 controls how fast saturation sets in.
With Lucene's default k1 = 1.2 and L = 1, f = 1, 2, 5, 10, 50 give 1.00, 1.38, 1.77, 1.96, 2.15; the curve
is already flat by ten. Set k1 = 0 and term frequency stops mattering entirely (a binary "contains"
model); set it large and the function approaches raw counts.

### Length normalization: b
L = 1 − b + b·|D|/avgdl rescales the denominator by how long the document is relative to average. A
document at average length has L = 1. A document twice the average with b = 0.75 has L = 1.75, so each
term count is effectively divided by 1.75 before saturation: the same five mentions in a long document
count less than in a short one. b = 0 switches normalization off (counts are absolute); b = 1 makes it
fully proportional. The default 0.75 encodes the observation that long documents are partly long because
they cover more topics (so their counts should be discounted) and partly because they say more about the
same topic (so not fully).

### Putting it together: one worked term
N = 1,000 documents, avgdl = 200 tokens, k1 = 1.2, b = 0.75. The term "indemnification" occurs in 10
documents, so IDF ≈ 4.56. Document A is 100 tokens and contains it 3 times: L = 1 − 0.75 + 0.75·0.5 =
0.625; denominator 3 + 1.2·0.625 = 3.75; term score 4.56 × 6.6 / 3.75 ≈ 8.0. Document B is 400 tokens
with the same 3 occurrences: L = 1.75; denominator 3 + 2.1 = 5.1; score 4.56 × 6.6 / 5.1 ≈ 5.9. Same
counts, roughly a quarter lower score for the document four times longer. A third document mentioning
"contract" (in 600 documents, IDF ≈ 0.51) ten times at average length scores only 0.51 × 1.96 ≈ 1.0 for
that term: a common word repeated ten times is worth an eighth of a rare word mentioned three times.

### What BM25 does not do
It treats terms as independent, ignores order and proximity (phrase matching is a separate query feature
over positions), and has no notion of synonymy; everything semantic is delegated to the analyzer
(tokenization, lowercasing, stemming, stopword removal) that runs before scoring. Because the analyzer
defines what a "term" is, index-time and query-time analysis must be identical, and most "BM25 is
broken" bugs are analyzer mismatches. Multi-field documents (title, body) are handled either by scoring
fields separately and combining with boosts, or by BM25F, which merges field counts with per-field
weights before saturation so a title hit is not saturated away by body mentions.

### Scores are query-relative
A BM25 score is a sum over query terms of unbounded positive quantities. It is not normalized: a
three-term query produces larger numbers than a one-term query, and scores from different indexes or
corpora are not comparable. This matters when you fuse BM25 with dense scores, which is why fusion
methods work on ranks or normalize per query.

## Example
A from-scratch implementation over three short contract passages makes every term in the formula
visible. The analyzer is deliberately trivial (lowercase, alphanumerics) and shared by index and query;
change it in one place and both sides change together.

```python
import math, re
from collections import Counter

docs = {
    "A": "indemnification cap: the indemnification obligations are capped at fees paid",
    "B": "this agreement is a contract between the parties; the contract term is one year",
    "C": "limitation of liability and indemnification survive termination of the contract",
}
analyze = lambda s: re.findall(r"[a-z0-9]+", s.lower())     # must be identical at query time
tf = {d: Counter(analyze(t)) for d, t in docs.items()}       # term counts per document
dl = {d: sum(c.values()) for d, c in tf.items()}             # document lengths
N, avgdl = len(docs), sum(dl.values()) / len(docs)
df = Counter(term for c in tf.values() for term in c)        # documents containing each term

def idf(term):                                               # Lucene form: +1 keeps it positive
    n = df.get(term, 0)
    return math.log((N - n + 0.5) / (n + 0.5) + 1)

def bm25(query, d, k1=1.2, b=0.75):
    score = 0.0
    for term in analyze(query):
        f = tf[d].get(term, 0)
        if f == 0:
            continue
        length_factor = 1 - b + b * dl[d] / avgdl              # >1 for long docs, <1 for short
        score += idf(term) * f * (k1 + 1) / (f + k1 * length_factor)
    return score

for q in ["indemnification cap", "contract term", "the"]:
    ranked = sorted(docs, key=lambda d: -bm25(q, d))
    print(f"{q!r:24}", [(d, round(bm25(q, d), 2)) for d in ranked])
```

The query "the" scores every document low and nearly equally because it appears in all three;
"indemnification cap" prefers A (two occurrences of a rarer term plus the only "cap") over C; "contract
term" prefers B. Set `k1=0` and watch A's advantage from repetition disappear.

## Trade-offs
- BM25 needs no training data and no GPU, so it is the cheapest retriever to stand up and the easiest to
  debug; it flips to insufficient once queries stop sharing vocabulary with documents, which no parameter
  tuning fixes.
- Term-frequency saturation makes ranking robust to keyword stuffing and long repetitive documents, but
  it also caps the signal from genuinely term-dense passages; raise k1 when your corpus is short, focused
  snippets where repetition is meaningful.
- Length normalization at b = 0.75 suits mixed-length prose; for corpora where length is itself a quality
  signal (full articles versus stubs), lowering b stops penalizing the complete documents.
- Field-aware scoring (BM25F or boosted fields) lets a title match outrank a body mention, at the cost of
  boosts that must be tuned per corpus and re-validated whenever the document schema changes.

## Pitfalls
- Index-time and query-time analyzers that differ (stemming on one side, a different tokenizer, or hyphen
  handling); the symptom is exact phrases copied from a document returning zero results.
- Leaving stopwords in a corpus where they are meaningful ("the Who", "to be or not to be") or stripping
  them where they are not; the symptom is either noisy hits or impossible queries, and IDF does not fully
  compensate.
- Treating BM25 scores as absolute relevance and thresholding them; the symptom is a cutoff that works
  for three-word queries and rejects everything for one-word queries, because the score scales with query
  length.
- Changing k1 or b on a live index without re-measuring; the defaults were chosen on TREC-style
  collections, and a corpus of tweets or of 200-page manuals can want very different values. The symptom
  is a silent shift in which documents win.
- Forgetting that avgdl and IDF are collection statistics; in sharded engines each shard may compute its
  own unless the engine fetches global statistics first, which makes the same document score differently
  depending on where it lives.

## When to use
BM25 is the right first retriever for any corpus, and the baseline every other retriever must beat on
your labeled queries before it earns its cost. Reach for it as the sole retriever when queries are
identifier-heavy, the corpus churns fast or the serving budget is CPU-only; reach for it as one arm of
hybrid retrieval everywhere else. What to measure: recall@k on labeled queries, broken down by query
type, with the analyzer and the k1, b values recorded as part of the experiment. In day-to-day work the
formula shows up when you explain why a long document ranks low, why a repeated keyword stopped helping,
or why a fused score needs normalization. Interviewers ask for the formula's pieces and, more
importantly, the failure each piece prevents.

## Role lens
- **Engineer:** Configures analyzers consistently across index and query, exposes k1 and b as tunables,
  and writes the per-query-type recall test that catches analyzer regressions.
- **Architect:** Treats BM25 as the system's mandatory baseline retriever and decides how its
  unnormalized scores combine with dense scores in the fusion layer.
- **FDE:** Reproduces a customer's "exact phrase returns nothing" complaint by diffing their index and
  query analyzers, then explains length normalization when a 300-page PDF ranks below a memo.

## Check yourself
**Q:** A customer indexes product manuals that range from two pages to four hundred pages. Users complain
that the complete manual for a product ranks below a short FAQ stub that mentions the product name once.
Using the BM25 formula, explain why and what you would change.
**A:** Two forces push the stub up. Length normalization with b = 0.75 divides the manual's term counts
by a factor near |D|/avgdl, which for a document many times longer than average is large, so even dozens
of mentions of the product name are discounted heavily; meanwhile the stub is shorter than average, so
its single mention is amplified. Saturation then flattens the manual's remaining advantage: beyond
roughly ten occurrences, extra mentions add almost nothing at k1 = 1.2. I would first ask whether the
whole manual is the right unit at all; indexing sections or pages as documents usually fixes this
outright because each becomes near average length. If whole manuals must be documents, lower b toward
0.3 to reduce the length penalty and raise k1 so term density keeps counting, then re-measure recall on
labeled queries rather than trusting the change.

## Quiz
### Q: Why does BM25 saturate term frequency instead of using raw counts?
- [ ] To make scores comparable across queries of different lengths
- [x] Because once a document clearly contains a term, each additional occurrence is weaker evidence of relevance, and unbounded counts would let repetition dominate
- [ ] Because inverted indexes store term frequency as a single bit
- [ ] To compensate for stopwords that were removed by the analyzer
> The saturating fraction rises steeply from zero to one occurrence and flattens toward k1 + 1, so a
> term repeated fifty times scores only slightly above one repeated five times. Score comparability
> across queries is not something BM25 provides at all, and inverted indexes store full counts.

### Q: With b = 0.75, how does a document twice the average length have its term counts treated?
- [ ] Counts are doubled to reward the extra coverage
- [ ] Counts are unchanged because b only affects IDF
- [x] Counts are effectively divided by 1.75 before saturation, so the same number of mentions scores lower than in an average-length document
- [ ] The document is excluded from results unless every query term matches
> The length factor is 1 − b + b·|D|/avgdl = 0.25 + 0.75·2 = 1.75, which multiplies k1 in the
> denominator and acts like dividing the count. IDF depends only on document frequency, not length.

### Q: Why does Lucene's BM25 add 1 inside the IDF logarithm?
- [ ] To make IDF proportional to document length
- [ ] To ensure every term in the query contributes exactly one point
- [x] To keep IDF positive for terms that occur in more than half the documents, so a matching term never lowers a document's score
- [ ] To correct for shards computing different average document lengths
> The classic form ln((N − n + 0.5)/(n + 0.5)) goes negative when n exceeds about half of N, meaning a
> document could be penalized for containing a query term. Adding 1 inside the log bounds IDF below by
> zero while leaving rare-term weights nearly unchanged.
