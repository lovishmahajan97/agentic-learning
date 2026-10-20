---
id: embeddings
module: model-internals
name: Embeddings
kind: concept
difficulty: core
est_minutes: 14
status: reviewed
related:
  - model-internals/tokenization
  - model-internals/transformer
  - model-internals/positional-encoding
  - model-internals/logits
  - rag-retrieval/embedding-models
  - rag-retrieval/similarity-search
  - rag-retrieval/embedding-dimension-and-normalization
  - ml-fundamentals/loss-functions
sources:
  - Mikolov et al., 2013 — Efficient Estimation of Word Representations in Vector Space
  - Press & Wolf, 2017 — Using the Output Embedding to Improve Language Models
  - Reimers & Gurevych, 2019 — Sentence-BERT
  - Kusupati et al., 2022 — Matryoshka Representation Learning
---

## TL;DR
An embedding is a learned dense vector that stands in for a discrete thing (a token, a sentence) so a
network can do arithmetic on it. Inside an LLM, the embedding table turns token ids into the vectors
the transformer consumes, and a matrix of the same shape (sometimes the very same one) turns hidden
states back into logits. Outside the LLM, an
embedding *model* compresses whole passages into vectors whose distance tracks meaning, which is what
makes semantic search possible. Same word, two different training objectives.

## Why it exists
A transformer is a stack of matrix multiplications. It cannot consume the string "cat"; it needs a
vector. The obvious encoding is one-hot: a vector of length *V* (the vocabulary size, tens of thousands
to over a hundred thousand entries) with a single 1. One-hot has two fatal properties. It is enormous
and almost entirely zero, so the first layer would multiply a 100k-wide sparse vector on every token.
Worse, every pair of one-hot vectors is equidistant and orthogonal, so "cat" is exactly as far from "kitten"
as from "parliament". The representation carries no information the model can exploit; every regularity
would have to be relearned from scratch for every token.

An embedding replaces the one-hot with a short dense vector, typically a few hundred to several thousand
dimensions, whose values are learned. Because the vectors are trained by gradient descent on the task
(next-token prediction), tokens that behave similarly in context are pushed to similar places, and that
geometry is free information for every later layer. It also makes learning transfer: a gradient step
taken because of a sentence about "cat" moves a vector that sits near "kitten", so evidence about one
token partly informs its neighbours instead of being locked in one orthogonal axis. The same reasoning applies one level up: if you want
to find the document most relevant to a question, comparing strings fails on paraphrase, while comparing
learned vectors succeeds, because the vectors were trained so that meaning, not spelling, determines
distance.

## How it works
### Token embeddings inside an LLM
The model owns a matrix `E` of shape `[V, d_model]`. Looking up token id *i* means taking row *i*; this
is exactly `one_hot(i) · E`, so an embedding layer is a linear layer applied to a one-hot input, with the
multiplication skipped *because* all but one row would be multiplied by zero. The row is a trainable
parameter like any weight, updated by backpropagation through the whole network. Nothing about the
geometry is hand-designed; it emerges because tokens that predict similar continuations receive similar
gradients.

The table is a real fraction of the parameter budget. GPT-2 small has 50,257 × 768 ≈ 38.6M embedding
parameters out of ~124M. Llama-2-7B has 32,000 × 4,096 ≈ 131M; Llama 3's 128,256-token vocabulary at the
same width is ≈ 525M. Larger vocabularies tokenize text into fewer tokens (cheaper context, better
multilingual coverage) at the price of a bigger table, which is one of the trade-offs behind choosing a
tokenizer.

After lookup, the model needs to know *where* each token is, because attention itself is permutation
invariant. Older models add a learned positional vector to the token embedding; most current models apply
rotary position embeddings inside attention instead, so the input embedding carries only identity. The
`positional-encoding` topic covers that choice. (The original transformer also multiplied the looked-up
vector by √d_model to put it on a scale comparable to the positional signal; some current models, Gemma
among them, keep a similar scaling.)

### The unembedding and weight tying
At the top of the stack, the final hidden state `h` (dimension `d_model`) is projected to a score per
vocabulary entry: `logits = h · W_Uᵀ` with `W_U` of shape `[V, d_model]`. This matrix has the same shape
as `E`, and some models (GPT-2 among them) **tie** them, using one matrix for both. Tying saves a full
table of parameters and encourages consistency, *because* the vector used to recognize a token on the
way in is the same one used to predict it on the way out. Many larger models (Llama-2, for instance)
untie them, trading parameters for flexibility. Either way, the output of the model is a dot product
between a hidden state and every token's output embedding, which is why embeddings and logits are two
ends of one mechanism.

### Sentence and document embeddings
An embedding *model* produces one vector for an entire input. Architecturally it is usually a
transformer encoder followed by a pooling step: mean-pool the last hidden states, take a special
first-token state, or take the last token for decoder-based embedders. Pooling alone is not enough. The
raw hidden states of a language model trained only on next-token prediction make poor sentence vectors,
because nothing in that objective asks the pooled vector of two paraphrases to coincide. Embedding models
are therefore trained **contrastively**: given (query, relevant passage) pairs, pull each pair together
and push the query away from the other passages in the batch (in-batch negatives, InfoNCE loss). For a
batch of *B* pairs with similarity `s(q, p) = cos(q, p)` and temperature τ:

```
L = − (1/B) Σ_i log [ exp(s(q_i, p_i)/τ) / Σ_j exp(s(q_i, p_j)/τ) ]
```

It is a *B*-way softmax classification: "which passage in this batch is mine?". That framing explains
the training recipe. Bigger batches mean more negatives per query and a harder, more informative task;
**hard negatives** (passages that look relevant but are not, often mined with BM25 or an earlier model)
are added because random in-batch negatives are too easy to teach fine distinctions; and τ (small, for
example 0.05) sharpens the softmax so near-misses are penalized. The objective directly optimizes the
quantity you later query with, which is what makes cosine similarity meaningful.

Practical consequences of that training:
- Vectors are compared with cosine similarity or dot product; with L2-normalized vectors these are
  identical, and most models expect normalization.
- Dimension is a design choice, from 384 (the `all-MiniLM-L6-v2` family) to 1,536 or 3,072 (OpenAI's
  `text-embedding-3` models). Matryoshka-trained models order information so the first *k* dimensions
  are usable on their own, letting you truncate to save index memory with a measured recall cost.
- Some models are **asymmetric**: queries and passages are prefixed differently (the E5 family uses
  `query:` and `passage:`), because a short question and a long document should not be embedded the same
  way even when they mean the same thing.
- A vector is only comparable to vectors from the *same model and version*; re-embedding everything is
  mandatory when the model changes.

## Example
The first block shows that an LLM's embedding layer is a row lookup equal to a one-hot matmul, and that
GPT-2 ties its input and output embeddings. The second uses a contrastively trained encoder to show the
geometry that retrieval relies on: two paraphrases score far higher than an unrelated sentence.

```python
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from sentence_transformers import SentenceTransformer

tok = AutoTokenizer.from_pretrained("gpt2")
lm = AutoModelForCausalLM.from_pretrained("gpt2").eval()
E = lm.get_input_embeddings().weight                   # [50257, 768] trainable table
ids = tok("The cat sat", return_tensors="pt").input_ids # [1, n_tokens]
x = E[ids]                                             # row lookup -> [1, n_tokens, 768]
onehot = torch.nn.functional.one_hot(ids, E.shape[0]).float()
assert torch.allclose(onehot @ E, x)                   # lookup == one-hot matmul
print(E.shape, x.shape)
# Weight tying: the output projection is the very same matrix.
print(lm.get_output_embeddings().weight.data_ptr() == E.data_ptr())   # True for GPT-2

st = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")   # 384-dim encoder
sents = ["How do I reset my password?",
         "Steps to recover access to my account",
         "Best pizza places in Naples"]
v = st.encode(sents, normalize_embeddings=True)        # [3, 384], unit length
print((v @ v.T).round(2))     # cosine matrix: [0,1] clearly highest, [0,2] and [1,2] low
```

## Trade-offs
- A bigger vocabulary means fewer tokens per document and cheaper context, but a larger embedding table
  and more rarely-updated rows; it pays off when the corpus is multilingual or code-heavy.
- Higher embedding dimension stores finer distinctions but multiplies vector-index memory and search
  cost linearly; the recall gained per extra dimension shrinks quickly, so the bigger vector pays off
  only on large, diverse corpora, and the break-even point must be measured with recall@k, not assumed.
- Rows for tokens that are rare in training get few gradient updates and stay near their initialization;
  a large vocabulary built on one corpus and trained on another can contain such under-trained tokens,
  which surface as bizarre outputs when a prompt contains them (the "glitch token" phenomenon).
- Tying input and output embeddings saves parameters and helps small models, while untying gives large
  models more capacity; the benefit flips as the table becomes a small fraction of total parameters.
- A general-purpose embedding model works out of the box, whereas a fine-tuned one on domain pairs
  improves retrieval on jargon-heavy corpora at the cost of a training pipeline and re-indexing on
  every model change.

## Pitfalls
- Mixing vectors from two embedding models or two versions of one model in a single index; the symptom
  is retrieval that looks random for part of the corpus, exactly the part embedded with the other model.
- Using an LLM's raw hidden states, or mean-pooled token embeddings, as sentence vectors; similarity
  scores cluster in a narrow band and nearest neighbours are dominated by length and frequent words.
- Forgetting the query/passage prefixes an asymmetric model was trained with; recall silently drops,
  often by a lot, with no error anywhere.
- Comparing unnormalized vectors with cosine in one place and dot product in another; rankings disagree
  between your offline evaluation and the production index.
- Embedding chunks that are longer than the model's input limit; the tail is truncated silently and
  content past the cut is never retrievable.
- Copying a similarity threshold ("treat cosine > 0.8 as relevant") from another model; score
  distributions differ by model, some put unrelated pairs well above zero, and the symptom is a filter
  that passes everything or nothing. Calibrate thresholds per model on labelled pairs.

## When to use
Token embeddings are not a choice; every LLM has them, and you meet them when you count parameters,
extend a vocabulary (new rows must be initialized and trained), or inspect why two tokens behave alike.
Sentence embeddings are a choice: reach for them whenever the problem is "find things that mean the
same as this" at scale, including semantic search, RAG retrieval, deduplication, clustering and
classification with a light head on top. Do not reach for them when exact lexical match matters (IDs,
error codes, rare names), where BM25 or hybrid search wins. Measure with recall@k on a labelled set of
query–passage pairs before and after any change of model, dimension or chunking. In interviews, expect
to explain the difference between the LLM's embedding table and an embedding model, why cosine
similarity works for one and not the other, and what re-embedding costs when a model is upgraded.

## Role lens
- **Engineer:** Picks the embedding model, normalizes and stores vectors, versions them alongside the
  model name, and writes the recall@k evaluation that gates any swap.
- **Architect:** Owns the embedding contract across the platform: one model per index, dimension and
  storage budgets, and the re-indexing plan when a model is deprecated.
- **FDE:** Diagnoses "search returns nonsense" at a customer by checking model mismatch, missing
  prefixes and truncated chunks before touching the retrieval algorithm.

## Check yourself
**Q:** A team upgraded their embedding model, re-embedded new documents only, and now users report that
older documents never show up in search. Explain the mechanism and what you would do.
**A:** Vectors are only comparable within one model's learned space. The new model places new documents
in a geometry that has no relationship to the old model's, so a new-model query vector lands near new
documents and effectively at random distance from old ones; cosine scores between the spaces are
meaningless, not merely slightly off. The fix is a full re-embedding of the corpus with the new model
into a fresh index, cut over atomically once it is complete, with the model name and version stored
with every vector so the mismatch is detectable next time. While the backfill runs, either keep
querying the old index or run both and merge results per index, never across them; raw scores from
two models are on different scales, so merge by rank (for example reciprocal rank fusion) rather than by
score. A strong answer also mentions the evaluation that should have caught it: recall@k on a labelled
set covering old documents, run before the cutover.

## Quiz
### Q: Why is an embedding lookup mathematically identical to a linear layer applied to a one-hot vector?
- [ ] Because the embedding table is initialized from the identity matrix
- [x] Because multiplying a one-hot vector by the table selects exactly one row, so the lookup just skips the zero multiplications
- [ ] Because embeddings are normalized to unit length after lookup
- [ ] Because both operations are followed by a softmax
> A one-hot vector has a single 1, so its product with the table is that one row. The lookup is an
> optimization that avoids a V-wide multiplication whose result is known in advance.

### Q: Why do contrastively trained embedding models produce useful cosine similarities when an LLM's pooled hidden states do not?
- [ ] Because embedding models use a larger vocabulary
- [ ] Because LLM hidden states are not normalized
- [x] Because the contrastive objective explicitly pulls paraphrase pairs together and pushes unrelated texts apart, while next-token prediction never asks for that
- [ ] Because embedding models have more layers
> Geometry follows the training objective. Next-token prediction shapes vectors for predicting the next
> token, not for comparing whole texts; contrastive training directly optimizes the distance you later
> query.

### Q: What does weight tying between the input embedding and the output projection buy?
- [ ] A larger context window
- [ ] Faster attention computation
- [x] Fewer parameters and a shared representation for recognizing and predicting each token
- [ ] Immunity to tokenizer changes
> Both matrices have shape [V, d_model], so sharing one saves a whole table and forces the same vector to
> serve on the way in and on the way out. It matters most when the table is a large fraction of the model.
