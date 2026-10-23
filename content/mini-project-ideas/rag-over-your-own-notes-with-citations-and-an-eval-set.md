---
id: rag-over-your-own-notes-with-citations-and-an-eval-set
module: mini-project-ideas
name: RAG over your own notes with citations and an eval set
kind: practice
difficulty: core
est_minutes: 22
status: typed
lesson_type: lab
agent_relevance: core
related:
  - rag-retrieval/chunking-strategies
  - rag-retrieval/hybrid-search
  - rag-retrieval/citations-grounding
  - rag-retrieval/citation-correctness
  - evals/retrieval-evaluation
  - evals/golden-dataset
  - evals/faithfulness-groundedness
  - rag-retrieval/retrieval-failure-diagnosis
sources:
  - Sentence Transformers documentation
  - rank_bm25 project documentation
  - Anthropic and OpenAI API documentation (text generation)
---

## TL;DR
Index your own Markdown notes, answer questions with inline citations to the exact chunks used, and grade
the system with a 30-50 question eval set. It proves you can measure retrieval and grounding, not just demo them.

## For agent builders
- **Skills it exercises.** Chunking with stable ids, embeddings plus BM25 hybrid search, citation-forcing
  prompts, citation validation, and retrieval and answer metrics on a golden set.
- **Earlier lessons it uses.** Chunking strategies, hybrid search, citations and grounding, citation
  correctness, retrieval evaluation and golden datasets.
- **Why it matters for agents.** A retrieval tool is the most common tool an agent calls; if you cannot tell
  whether it returned the right chunk, you cannot debug the agent above it.
- **What you can skip.** A hosted vector database and a web UI. NumPy over a few thousand chunks is enough.

## Lab
### Task
Build the measurement layer of the notes RAG: reciprocal rank fusion, recall@k and MRR at note level, a
citation validator and refusal scoring, then show on a small golden set that hybrid search fixes misses that
dense and BM25 each make alone. Time box: 45-60 min.
- **Prerequisites:** Hybrid search; Retrieval evaluation; Citation correctness; Golden datasets.
- **Setup:** Python 3.10+ and `pip install pytest` (checked with Python 3.12, pytest 9.1). Save the starter
  as `lab.py` and the tests as `test_lab.py`.
- **Recorded rankings:** `DENSE` and `SPARSE` hold top-5 chunk ids as a dense retriever and BM25 returned
  them, so no embedding model download is needed. Swap in the `search` function from Examples later.

### Starter code
```python
"""Lab: hybrid fusion, retrieval metrics and citation checks for notes RAG. Run: python lab.py"""
import re

# Recorded top-5 rankings (chunk ids) from a dense retriever and from BM25 for each golden question,
# so the lab runs without downloading an embedding model. Chunk ids are "<note path>#<heading slug>".
DENSE = {
    "When do we ship?": ["deploy.md#deploys", "deploy.md#rollback", "oncall.md#rotation", "db.md#backups", "db.md#restore"],
    "How do I undo a bad deploy?": ["db.md#restore", "oncall.md#rotation", "db.md#backups", "deploy.md#rollback", "deploy.md#deploys"],
    "What time do postgres backups run?": ["oncall.md#rotation", "deploy.md#deploys", "deploy.md#rollback", "db.md#backups", "db.md#restore"],
    "When does the pager rotate?": ["oncall.md#rotation", "deploy.md#deploys", "db.md#backups", "deploy.md#rollback", "db.md#restore"],
    "What is our vacation policy?": ["oncall.md#rotation", "deploy.md#deploys", "db.md#backups", "db.md#restore", "deploy.md#rollback"],
}
SPARSE = {
    "When do we ship?": ["oncall.md#rotation", "db.md#backups", "db.md#restore", "deploy.md#rollback", "deploy.md#deploys"],
    "How do I undo a bad deploy?": ["deploy.md#rollback", "deploy.md#deploys", "db.md#restore", "db.md#backups", "oncall.md#rotation"],
    "What time do postgres backups run?": ["db.md#backups", "db.md#restore", "deploy.md#deploys", "oncall.md#rotation", "deploy.md#rollback"],
    "When does the pager rotate?": ["db.md#backups", "deploy.md#deploys", "deploy.md#rollback", "oncall.md#rotation", "db.md#restore"],
    "What is our vacation policy?": ["db.md#restore", "db.md#backups", "deploy.md#deploys", "deploy.md#rollback", "oncall.md#rotation"],
}
GOLD = [
    {"question": "When do we ship?", "expected_sources": ["deploy.md"]},
    {"question": "How do I undo a bad deploy?", "expected_sources": ["deploy.md"]},
    {"question": "What time do postgres backups run?", "expected_sources": ["db.md"]},
    {"question": "When does the pager rotate?", "expected_sources": ["oncall.md"]},
    {"question": "What is our vacation policy?", "expected_sources": []},       # unanswerable
]
REFUSAL = "Not in my notes."

def rrf_fuse(rankings: list[list[str]], k: int = 60, top: int = 5) -> list[str]:
    # YOUR CODE HERE: reciprocal rank fusion. Each id scores sum(1 / (k + rank + 1)) over the
    # rankings it appears in (rank is 0-based). Return the `top` ids by score, ties broken by id.
    raise NotImplementedError

def recall_at_k(ranked: list[str], expected_sources: list[str], k: int) -> float:
    # YOUR CODE HERE: 1.0 if any of the first k chunk ids comes from an expected note path, else 0.0
    raise NotImplementedError

def reciprocal_rank(ranked: list[str], expected_sources: list[str]) -> float:
    # YOUR CODE HERE: 1 / (1-based rank of the first chunk from an expected note), 0.0 if none
    raise NotImplementedError

def citations_valid(answer: str, n_hits: int) -> bool:
    # YOUR CODE HERE: True only if the answer cites at least one [n] and every n is in 1..n_hits
    raise NotImplementedError

def refusal_correct(answer: str, answerable: bool) -> bool:
    # YOUR CODE HERE: an unanswerable question must get REFUSAL; an answerable one must not
    raise NotImplementedError

def report(k: int = 3) -> dict:
    answerable = [g for g in GOLD if g["expected_sources"]]
    out = {}
    for name, ranks in (("dense", lambda q: DENSE[q]), ("bm25", lambda q: SPARSE[q]),
                        ("hybrid", lambda q: rrf_fuse([DENSE[q], SPARSE[q]]))):
        rec = sum(recall_at_k(ranks(g["question"]), g["expected_sources"], k) for g in answerable)
        mrr = sum(reciprocal_rank(ranks(g["question"]), g["expected_sources"]) for g in answerable)
        out[name] = (round(rec / len(answerable), 2), round(mrr / len(answerable), 2))
    return out

if __name__ == "__main__":
    for name, (rec, mrr) in report(k=3).items():
        print(f"{name:<7} recall@3={rec:.2f} mrr={mrr:.2f}")
    print(rrf_fuse([DENSE["How do I undo a bad deploy?"], SPARSE["How do I undo a bad deploy?"]], top=3))
    print(citations_valid("Deploys run on Fridays [1]. Roll back within 10 minutes [2].", 3),
          citations_valid("Deploys run on Fridays [4].", 3), refusal_correct(REFUSAL, answerable=False))
```

### Expected output
`python lab.py` with a correct solution prints exactly the lines below. Hybrid lifts recall@3 from 0.50 to
1.00 but its MRR is lower than either retriever alone: fusion pulls each hit into the top 3 without always
putting it first. Recall and rank quality are separate numbers, so report both.

```
dense   recall@3=0.50 mrr=0.62
bm25    recall@3=0.50 mrr=0.62
hybrid  recall@3=1.00 mrr=0.58
['db.md#restore', 'deploy.md#rollback', 'deploy.md#deploys']
True False True
```

### Tests
Run `pytest -q test_lab.py`. On the starter all 5 fail with `NotImplementedError`; a correct solution passes
all 5.

```python
import pytest
from lab import (REFUSAL, citations_valid, recall_at_k, reciprocal_rank, refusal_correct, report,
                 rrf_fuse)

def test_rrf_rewards_agreement_and_breaks_ties_by_id():
    assert rrf_fuse([["a", "b", "c"], ["b", "c", "a"]], top=3) == ["b", "a", "c"]
    assert rrf_fuse([["x"], ["y"]], top=2) == ["x", "y"]                    # equal scores: by id
    assert rrf_fuse([["a", "b"], ["c"]], k=60, top=1) == ["a"]

def test_recall_and_rr_match_on_note_path_not_chunk():
    ranked = ["db.md#restore", "deploy.md#rollback", "deploy.md#deploys"]
    assert recall_at_k(ranked, ["deploy.md"], k=1) == 0.0
    assert recall_at_k(ranked, ["deploy.md"], k=2) == 1.0
    assert reciprocal_rank(ranked, ["deploy.md"]) == pytest.approx(0.5)
    assert reciprocal_rank(ranked, ["oncall.md"]) == 0.0

def test_citation_validator_rejects_out_of_range_and_uncited():
    assert citations_valid("Fridays [1]. Ten minutes [2].", 2)
    assert not citations_valid("Fridays [3].", 2)
    assert not citations_valid("Fridays [0].", 2)
    assert not citations_valid("Fridays, trust me.", 2)

def test_refusal_scoring_both_ways():
    assert refusal_correct(REFUSAL, answerable=False)
    assert not refusal_correct(REFUSAL, answerable=True)
    assert not refusal_correct("We get 25 days [1].", answerable=False)

def test_report_shows_hybrid_fixes_recall():
    r = report(k=3)
    assert r["dense"] == (0.5, 0.62) and r["bm25"] == (0.5, 0.62)
    assert r["hybrid"] == (1.0, 0.58)
```

### Failure scenarios
- **Drop the `+ 1` in RRF** (`1 / (k + rank)` with a 0-based rank). The order barely moves, so these tests
  can still pass; add a test on a hand-computed score: the top item at `k=60` must score `1/61`.
- **Compare full chunk ids with note paths.** Every recall is 0.0 because `deploy.md#rollback` never equals
  `deploy.md`. Print one ranked id beside its expected source; split on `#` before matching.
- **Count the unanswerable question in recall.** The denominator becomes 5 and every recall drops by a fifth.
  Score unanswerable questions only with `refusal_correct`, and report the two subsets separately.
- **Accept `[0]` or uncited answers as valid.** A model that cites nothing or an off-by-one index looks
  grounded. Log the parsed citation set per answer and fail on empty sets.

### Submission
- **`lab.py`** with every `YOUR CODE HERE` part implemented and the recorded rankings unchanged.
- **A green test run** (5 passed) and the output of `python lab.py`.
- **A rerun on your own notes:** replace `DENSE` and `SPARSE` with live `search` results on 10 questions and
  paste the three-row table.
- **A short note:** one question where hybrid hurt rank and why, in two or three sentences.
- **Evaluated on:** tests passing, note-level matching, refusals scored apart from recall, and both recall
  and MRR reported.

## Why it exists
- **Demos hide retrieval failures.** A RAG demo answers the five questions its author tried. An eval set of
  30-50 questions exposes the ones that fail, and shows whether retrieval or generation is to blame.
- **Your own notes make labelling cheap.** You know which note answers each question, so building a golden
  set with expected sources takes an evening instead of an SME workshop.
- **Citations make answers checkable.** Each claim points to a chunk id, so you can verify faithfulness
  automatically and a reader can click through.
- **It separates the two failure layers.** Recall@k says whether the right chunk was retrieved; citation
  correctness says whether the answer used it. Without both you fix the wrong layer.

## How it works
### Stack and data
- **Data:** 100-500 of your own Markdown notes (or a docs folder you know well), kept in a git repo.
- **Retrieval:** `sentence-transformers` for embeddings, `rank_bm25` for keyword search, NumPy cosine
  similarity, reciprocal rank fusion to merge the two lists.
- **Generation:** any chat model; the prompt numbers the retrieved chunks and requires `[n]` citations.
- **Eval:** a `golden.jsonl` with `question`, `expected_sources` (note paths) and a short reference answer.

### Milestones
1. **Chunk and index.** Split notes by heading, then by ~300-word windows; give each chunk an id like
   `path#heading-2`. Acceptance: re-indexing produces identical ids.
2. **Dense retrieval.** Embed chunks once, cache to disk, return top-k by cosine. Acceptance: a CLI prints
   top-5 chunks with scores for any query.
3. **Golden set.** Write 30-50 questions: lookups, multi-note questions and 5 with no answer in the notes.
   Acceptance: each has expected sources or is marked unanswerable.
4. **Retrieval metrics.** Report recall@5 and MRR per question type. Acceptance: one command prints a table.
5. **Hybrid search.** Add BM25 and fuse with RRF. Acceptance: recall@5 is reported for dense, BM25 and hybrid.
6. **Cited answers and checks.** Generate with `[n]` citations, reject answers citing chunks that were not
   retrieved, and score citation correctness and refusal on unanswerable questions.

### Metrics that go in the README
| Metric | Measures | Computed by |
|---|---|---|
| Recall@5 | right note in top 5 | string match on `expected_sources` |
| MRR | how high the right note ranks | 1 / rank of first hit |
| Citation validity | every `[n]` refers to a retrieved chunk | parser, no LLM |
| Citation support | the cited chunk supports the sentence | LLM judge, spot-checked by you |
| Correct refusal | says "not in my notes" when it should | unanswerable subset |

## Diagram
Indexing runs once per change; each question goes through hybrid retrieval, a cited answer and the checks
that feed the eval report.

```mermaid
flowchart TD
    N["Markdown notes"] --> C["Chunk with stable ids"]
    C --> E["Embeddings cache"]
    C --> B["BM25 index"]
    Q["Question"] --> H["Hybrid search with RRF"]
    E --> H
    B --> H
    H --> G["Answer with numbered citations"]
    G --> V["Validate citations"]
    V --> R["Eval report: recall, citations, refusals"]
    GS[("golden.jsonl")] --> R
```

## Examples
### Starter code: hybrid retrieval and recall@5
The core piece is retrieval plus its metric; generation is a thin layer on top.

```python
import json, pathlib, re, numpy as np
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi

def slug(text):                        # heading-based id part, stable when other sections move
    first = text.strip().splitlines()[0]
    head = first.lstrip("#").strip().lower() if first.startswith("#") else ""
    return re.sub(r"[^a-z0-9]+", "-", head).strip("-") or "top"

def chunk_notes(root):
    chunks, seen = [], {}
    for p in sorted(pathlib.Path(root).rglob("*.md")):
        src = str(p.relative_to(root))
        for text in re.split(r"\n(?=#{1,3} )", p.read_text()):
            if text.strip():
                cid = f"{src}#{slug(text)}"
                seen[cid] = seen.get(cid, 0) + 1       # repeated heading in one note
                if seen[cid] > 1:
                    cid += f"-{seen[cid]}"
                chunks.append({"id": cid, "src": src, "text": text})
    return chunks

chunks = chunk_notes("notes")
model = SentenceTransformer("all-MiniLM-L6-v2")
emb = model.encode([c["text"] for c in chunks], normalize_embeddings=True)
bm25 = BM25Okapi([c["text"].lower().split() for c in chunks])

def search(q, k=5, rrf_k=60):
    dense = np.argsort(-(emb @ model.encode(q, normalize_embeddings=True)))[:50]
    sparse = np.argsort(-bm25.get_scores(q.lower().split()))[:50]
    score = {}
    for ranking in (dense, sparse):
        for rank, i in enumerate(ranking):
            score[i] = score.get(i, 0) + 1 / (rrf_k + rank + 1)
    return [chunks[i] for i in sorted(score, key=score.get, reverse=True)[:k]]

gold = [json.loads(l) for l in open("golden.jsonl")]
answerable = [g for g in gold if g["expected_sources"]]
hits = sum(any(c["src"] in g["expected_sources"] for c in search(g["question"])) for g in answerable)
print(f"recall@5 = {hits}/{len(answerable)} = {hits/len(answerable):.2f}")
```

### Citation prompt and validator
Number the chunks in the prompt, require `[n]` after each claim, then check the answer mechanically.

```python
def build_prompt(question, hits):
    ctx = "\n\n".join(f"[{i+1}] ({h['id']})\n{h['text']}" for i, h in enumerate(hits))
    return (f"Answer only from the numbered notes. Put [n] after every sentence. "
            f"If the notes do not contain the answer, say 'Not in my notes.'\n\n{ctx}\n\nQ: {question}")

def citations_valid(answer, n_hits):   # score refusals ("Not in my notes.") separately
    cited = {int(x) for x in re.findall(r"\[(\d+)\]", answer)}
    return bool(cited) and all(1 <= c <= n_hits for c in cited)
```

### Milestone plan with time estimates
| Milestone | Time | Done when |
|---|---|---|
| 1. Chunk and index | 2 h | stable chunk ids across two runs |
| 2. Dense retrieval | 2 h | top-5 CLI works, embeddings cached |
| 3. Golden set | 2-3 h | 30-50 questions, 5 unanswerable |
| 4. Retrieval metrics | 1-2 h | recall@5 and MRR table per question type |
| 5. Hybrid search | 2 h | dense vs BM25 vs hybrid compared |
| 6. Cited answers | 3-4 h | citation validity, support and refusal scores in README |

## Production patterns
- **Version the index with the eval.** Store the embedding model name, chunker settings and a hash of the
  notes next to every eval result, so a recall change can be traced to a chunking or model change.
- **Report by question type.** Split lookup, multi-note and unanswerable questions in every table, because
  hybrid search often helps names and codes while hurting paraphrased questions.
- **Validate citations without an LLM first.** A regex check that every `[n]` exists catches most broken
  answers for free; use an LLM judge only for "does the chunk support the claim", and spot-check 20 of its verdicts.
- **Trace retrieval as its own span.** Log query, chunk ids, scores and latency per question; when an answer is
  wrong, the trace shows at once whether retrieval or generation failed.
- **Run the eval in CI.** A GitHub Action that fails when recall@5 drops more than a few points keeps chunking
  experiments honest and makes a strong README badge.

## Trade-offs
- **Small chunks vs large chunks.** Small chunks give precise citations but lose context across headings; large
  ones answer more but cite vaguely. Measure both on the golden set rather than picking by taste.
- **Hybrid vs dense only.** Hybrid adds a second index and a fusion step, and pays off when notes contain names,
  error codes or acronyms that embeddings blur.
- **Local embeddings vs an API.** A small local model is free and offline but weaker on long or multilingual
  notes; an API model may raise recall at a per-token cost and a data-sharing decision.
- **Strict citation rules vs fluent answers.** Requiring `[n]` on every sentence makes checking easy but can make
  answers stilted; relax to per-paragraph citations only if validity stays high.

## Pitfalls
- **Writing questions from the chunks.** Copying phrases into questions inflates recall because BM25 matches
  them verbatim. Write questions the way you would actually ask, before looking at the notes.
- **No unanswerable questions.** The system never learns to refuse and confidently cites unrelated chunks. Keep
  at least 10% of the set unanswerable and score refusals.
- **Unstable chunk ids.** Ids based on list position shift when a note is edited, breaking the golden set. Use
  path plus heading-based ids.
- **Trusting the judge blindly.** An LLM judge marks weak support as fine. Hand-label a sample and report the
  agreement rate next to its scores.
- **Re-embedding on every run.** Indexing becomes slow and costly. Cache embeddings keyed by chunk hash.

## When to use
- **Prerequisites.** Python, basic NumPy, and the chunking, hybrid search and retrieval evaluation lessons.
- **Suits.** Anyone building agents that read documents, and anyone whose RAG experience is demo-only.
- **Avoid when.** Your notes are under 20 pages; long-context prompting is simpler and you will not learn retrieval.
- **Measure.** Recall@5, MRR, citation validity, citation support and correct-refusal rate, all per question type.

## Role lens
- **Engineer:** Builds the chunker, hybrid retriever, citation validator and eval script, and uses the
  numbers to choose chunk size and fusion settings.
- **Architect:** Treats the golden set and metrics as the contract for any RAG feature, so model or index
  changes ship only when the numbers hold.
- **FDE:** Reuses the same harness on a customer's documents in week one to show where retrieval fails
  before anyone argues about models.

## Check yourself
**Q:** Your notes assistant scores recall@5 of 0.9 but readers say answers are often wrong. Where do you look,
and what do you measure next?
**A:** High recall means the right note is usually retrieved, so the failure is likely downstream. Measure
citation validity (are `[n]` markers real), citation support (does the cited chunk say what the sentence
claims) and refusal on unanswerable questions. Read 10 failing transcripts: common causes are the right chunk
ranking fifth behind distractors, chunks cut mid-explanation, or the model blending notes. Fixes follow the
cause: rerank, adjust chunk boundaries, or tighten the prompt and reject answers with unsupported citations.

## Interview questions
### How did you build the eval set and why should I trust it?
- **Questions came before reading chunks,** phrased naturally, with expected source notes, a mix of lookup,
  multi-note and about 10% unanswerable questions.
- **I report per type and keep it versioned,** so improvements on one type that hurt another are visible.

### Your hybrid search lowered recall on some questions. How do you debug it?
- **Compare dense, BM25 and fused rankings per question** to see which list put the right chunk where; paraphrased
  questions often lose when BM25 pushes keyword matches up.
- **Then tune the fusion,** for example weight lists differently or rerank the fused top 20, and re-run the full set.

### How do you verify citations at scale?
- **Mechanically first:** every `[n]` must refer to a retrieved chunk, checked by a parser at zero cost.
- **Then support:** an LLM judge checks whether each cited chunk supports its sentence, calibrated against my own
  labels on a sample so I know its error rate.

### How would this change if the notes were 2 million documents for a company?
- **Index and permissions:** a real vector store with metadata filters and ACL-aware retrieval, plus incremental
  re-indexing and deletion handling.
- **Evaluation:** a golden set built with subject-matter experts and sampled from real queries, because my own
  intuition no longer covers the corpus.

### When would you not use RAG for this?
- **When the corpus fits comfortably in context** and changes rarely, long-context prompting with caching is simpler.
- **When answers need computation** over structured data, a SQL or API tool beats retrieving text chunks.

## Quiz
### Q: Recall@5 is high but answers are often unsupported. Which layer is most likely failing?
- [ ] The embedding model
- [ ] The chunk index
- [x] Generation and citation use after retrieval
- [ ] The golden set format
> High recall means the right chunk is usually in the context. Unsupported answers then point to how the model
> uses or cites that context, so measure citation validity and support next.

### Q: Why include unanswerable questions in the golden set?
- [ ] To raise the average score
- [x] To measure whether the system refuses instead of citing unrelated chunks
- [ ] Because BM25 needs negative examples
- [ ] To test embedding speed
> Without them you never measure refusals, and a system that always answers looks perfect. Real users ask about
> things your notes do not cover.

### Q: What does reciprocal rank fusion combine in this project?
- [ ] Two embedding models into one vector
- [x] The ranked lists from dense search and BM25 into one ranking
- [ ] Chunks from neighbouring headings
- [ ] The judge score and the recall score
> RRF sums 1 / (k + rank) for each item across ranked lists, so a chunk ranked well by either dense or keyword
> search rises in the fused list.

## Use cases & projects
### Use cases
- **Agent retrieval tool.** Wrap `search()` as a tool for the CLI assistant, so the agent decides when to look
  things up and you can measure whether it searched when it should.
- **Reranking experiment.** Add a cross-encoder reranker over the fused top 20 and report the recall@5 and
  latency change per question type.
- **Freshness and deletion.** Re-index only changed notes by hash and prove deleted notes never appear in answers.
- **Team docs assistant.** Point the same pipeline at a team wiki with metadata filters by space or owner.
- **Eval in CI.** Fail the build on a recall or citation-validity drop and publish the report as a CI artifact.

### Mini project
**Warm-up: recall@5 on 15 questions (1-3 hours).**
- **Goal:** measure dense retrieval quality on your own notes before writing any generation code.
- **Steps:** chunk 50-100 notes by heading; embed with a small local model; write 15 questions with expected
  note paths; compute recall@5 and list the misses with their top-5 results.
- **Done when:** you have a recall number, a list of misses, and one sentence per miss saying why it failed
  (wrong chunking, vocabulary mismatch, or the note does not really answer it).
