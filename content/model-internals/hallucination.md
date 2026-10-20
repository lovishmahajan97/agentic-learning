---
id: hallucination
module: model-internals
name: Hallucination
kind: concept
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - model-internals/sampling-strategies
  - model-internals/small-language-models
  - ml-fundamentals/calibration
  - evals/hallucination-evaluation
  - evals/faithfulness-groundedness
  - evals/judge-calibration-and-bias
  - rag-retrieval/citations-grounding
  - agents/tool-schemas-and-argument-validation
sources:
  - Ji et al., 2023 — Survey of Hallucination in Natural Language Generation
  - Maynez et al., 2020 — On Faithfulness and Factuality in Abstractive Summarization
  - Manakul, Liusie and Gales, 2023 — SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection for Generative Large Language Models
  - Farquhar et al., 2024 — Detecting hallucinations in large language models using semantic entropy
---

## TL;DR
Hallucination is fluent, confident output that is not supported by the model's input or by reality. It is
a consequence of how language models work, not a bug awaiting a patch: they are trained to produce the
most probable continuation, not to know what they know, and sampling, long-tail knowledge gaps and rewards
for confident answers all push toward plausible fabrication. Manage it like any error rate: measure it,
ground the model in evidence, verify claims, and make abstention a valid output.

## Why it exists
A next-token model learns p(next token | context) from text. Nothing in that objective distinguishes "the
capital of France is Paris", seen millions of times, from "the 2019 revenue of a small private company",
seen once or never. At inference the model must emit a token regardless. When the true answer is not
recoverable from the weights, the distribution still peaks somewhere plausible, and the sampler emits that
with the same fluency as a well-known fact. Three mechanisms reinforce this. Parametric knowledge is lossy
compression: rare facts are stored weakly or not at all, and there is no explicit "not stored" signal to
read. Autoregression commits: once a wrong token is sampled, say a fabricated paper title, every later
token is conditioned on it, and the model's strong prior for coherence makes it elaborate rather than
retract, so errors snowball. And post-training sharpens the problem in a specific way: human raters and
binary-graded benchmarks reward complete, confident answers and penalise hedging, so the policy learns that
a guess scores better than an abstention, exactly as students guess on exams with no negative marking. The
GPT-4 technical report showed calibration getting worse after RLHF.

The naive fixes fail for the same reasons. "Train on more data" shrinks the long tail but cannot close it.
"Set temperature to zero" removes sampling noise, but greedy decoding still takes the argmax of a wrong
distribution; it cuts variance, not bias. "Tell it not to hallucinate" helps at the margin, because the
model cannot reliably introspect on what it knows. "Fine-tune on the facts" can teach some facts, but
fine-tuning on knowledge the base model lacks has been reported to increase hallucination elsewhere. The
durable fix is architectural: supply evidence, verify outputs, and make abstention cheap.

## How it works
### A taxonomy that determines the fix
- **Faithfulness hallucination**: the output is not supported by the source it was given. The NLG
  literature (Maynez et al., Ji et al.) splits this further into *intrinsic* (the output contradicts the
  source, such as a summary that reverses a figure in the document) and *extrinsic* (the output adds content
  the source neither states nor contradicts, such as an invented figure, which may or may not be true in the
  world). Both are detectable by comparing against the source; reduced by grounding and verification.
- **Factuality hallucination**: the output contradicts the world, judged against reality rather than against
  a given source, such as a wrong date or an invented citation in a closed-book answer. Needs external
  knowledge to detect; reduced by retrieval, tools and abstention. Note the axes are different: an extrinsic
  addition can be factually true, and a faithful summary of a wrong document is factually wrong.
- **Structural hallucination**: invented tool names, parameters that do not exist, API functions that were
  never defined. Common in agents and code generation; eliminated almost entirely by schema validation.

The split matters because each type has a different evaluation and a different mitigation, and a
"hallucination rate" that mixes them is not actionable.

### Where in the pipeline it arises
1. **Knowledge.** The question is outside the training distribution or after the training cutoff.
2. **Retrieval.** In a RAG system the retriever returns irrelevant, partial, conflicting or stale passages,
   and a model instructed to answer bridges the gaps with its priors. A large share of "model
   hallucinations" reported for RAG products are retrieval failures.
3. **Decoding.** High temperature or top-p widens the tail; long generations accumulate risk; the most
   surprising tokens are usually the fabricated specifics, such as numbers, names and URLs.
4. **Prompt pressure.** "Always answer", "be definitive", role-play framing, and questions with false
   premises ("Why did X do Y?" when X never did) all push the distribution toward fabrication.
5. **Sycophancy.** The user asserts something and the model agrees, because agreement was rewarded in
   preference tuning.

### Detection mechanisms
- **Sampling consistency.** Sample N answers; facts the model actually knows are stable across samples,
  fabrications vary (SelfCheckGPT). Costs N× compute and misses confident, consistent errors.
- **Semantic entropy.** Cluster the samples by meaning and compute entropy over clusters. High entropy flags
  unreliable answers better than token-level entropy, because paraphrases of one answer no longer count as
  disagreement.
- **Token-level signals.** Low log-probability on entity and number tokens is a useful cheap flag, but
  these probabilities are miscalibrated after RLHF, so thresholds must be tuned per model.
- **Claim-level verification.** For grounded tasks, split the answer into atomic claims and check each
  against the retrieved passages with an NLI model or an LLM judge, reporting an unsupported-claim rate. This
  is the faithfulness metric in evals, and it is the most actionable measurement because it names the
  claim that failed.
- **Tool-based verification.** Run the code, call the API, look up the citation. Cheapest and most reliable
  wherever a checker exists.

### Reduction mechanisms
- **Grounding.** Retrieve evidence, instruct the model to quote or cite spans, verify the citations after
  generation, and show the sources to the user.
- **Abstention paths.** Put an explicit "insufficient information" option in the output schema and score it
  as correct on unanswerable items in your evals, so neither prompt iteration nor fine-tuning trains it away.
- **Constrained outputs.** Enumerations, JSON schemas and tool-call validation against the real tool
  registry remove whole classes of structural hallucination.
- **Decoding.** Lower temperature for factual and extractive steps; keep sampling diversity for creative
  ones.
- **Verification loops.** Draft, generate verification questions, answer them independently, revise.
  Reasoning models perform a version of this inside their thinking trace.
- **Training-side.** Preference data that rewards calibrated abstention and penalises confident errors more
  than "I don't know"; this is a vendor-level lever, but it tells you what to look for in model choice.
- **Product design.** Surface confidence and sources, make verification one click, keep a human in the loop
  for consequential outputs.

## Example
A claim-level faithfulness check for a RAG answer. It costs one call to extract claims plus one call per
claim, so it is run as an offline eval on sampled traffic and as an online gate only for high-stakes
categories. The judge is itself a model and can be wrong in the same direction as the generator, so its
verdicts must be calibrated against human labels before the rate goes on a dashboard.

```python
import json

def llm(prompt, temperature=0.0):
    """Call your model provider here and return the text."""
    raise NotImplementedError

def extract_claims(answer):
    out = llm("Split this answer into atomic factual claims. "
              f"Return a JSON list of strings.\n\nANSWER:\n{answer}")
    return json.loads(out)

def verify(claim, passages):
    ctx = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages))
    out = llm("Decide whether the CLAIM is supported by the PASSAGES. Reply with JSON "
              '{"verdict": "supported" | "contradicted" | "not_found", "source": <index or null>}.\n\n'
              f"PASSAGES:\n{ctx}\n\nCLAIM: {claim}")
    return json.loads(out)

def faithfulness_report(answer, passages):
    claims = extract_claims(answer)
    verdicts = [verify(c, passages) for c in claims]
    unsupported = [(c, v["verdict"]) for c, v in zip(claims, verdicts) if v["verdict"] != "supported"]
    return {"claims": len(claims), "unsupported": unsupported,
            "unsupported_rate": len(unsupported) / max(1, len(claims))}

retrieved_passages = ["Refunds are issued within 14 days of a returned item being received."]
answer_text = "Refunds take 14 days and are paid to the original card."   # second claim is unsupported
report = faithfulness_report(answer_text, retrieved_passages)
if report["unsupported_rate"] > 0:
    # Options: regenerate with the unsupported claims listed as "do not state",
    # return only the supported claims with their [source] indices, or route to review.
    ...
```

The same decomposition gives you error analysis for free: unsupported claims that are "not_found" with
weak retrieval scores point at the retriever, while "contradicted" claims point at the generator.

## Trade-offs
- Retrieval grounding cuts factual hallucination sharply, but moves the failure to retrieval quality; when
  the corpus lacks the answer, a model told to "answer from context" either abstains or bridges the gap, so a
  check is still needed.
- Sampling-based detection is model-agnostic and needs no labels, but multiplies cost by N and only catches
  instability, not confident errors the model learned consistently from training data.
- Lower temperature reduces variance on factual tasks, but does not fix a wrong argmax and hurts tasks that
  need diversity; the trade flips for brainstorming and creative generation.
- Rewarding abstention reduces confident errors, but overdone it produces a model that refuses or hedges on
  answerable questions; users punish both failures, so tune the abstention rate to your task's cost
  asymmetry.
- Claim-level verification gives precise, actionable numbers, but adds latency and a second model that can
  be wrong; it earns its cost on high-stakes outputs and as an offline eval, rarely on every chat turn.

## Pitfalls
- Blaming the model for a retrieval failure; the symptom is hallucinations clustered on questions whose
  top-k passages have low relevance scores or whose answers are not in the corpus at all.
- Reporting a hallucination rate from an LLM judge that was never calibrated against humans; the symptom is
  a judge that shares the generator's priors and a dashboard reading 2% while SMEs find 15%.
- Treating temperature 0 as "hallucination off"; the symptom is deterministic, repeatable fabrications that
  look like facts precisely because they are consistent.
- Scoring "I don't know" as a failure in evals and demos; the symptom is that after a round of prompt or
  fine-tuning iteration, abstentions vanish and confident errors rise.
- Fine-tuning on domain facts the model never saw to "teach it the knowledge"; the symptom is a model that
  learned the format and a few facts and now fabricates fluently about the rest.
- Accepting a cited answer without checking that the citation supports the claim; the symptom is real
  document titles attached to statements they do not contain, since citation hallucination survives
  retrieval.
- Letting an agent invent tool names or arguments; the symptom is "function not found" errors or calls with
  parameters that do not exist, fixed by validating every call against the live schema and reflecting the
  error back.

## When to use
You do not choose whether hallucination applies; you choose how much to spend against it per use case. For
low-stakes creative or conversational features, light measures suffice: sources where available and a
temperature appropriate to the task. For extraction and summarisation, ground every output in the input and
measure faithfulness on a golden set. For factual question answering, use retrieval with citations, an
abstention path and offline claim-level verification. For agents, validate every tool call against the real
schema and verify consequential actions before executing them. Measure the unsupported-claim rate, the
abstention rate on unanswerable items, calibration of confidence against correctness, and per-category
error analysis. In interviews, explain the mechanism (objective, decoding, incentives), the
faithfulness/factuality split and the intrinsic/extrinsic subdivision, three mitigations with their costs,
and why temperature 0 is not a fix.

## Role lens
- **Engineer:** Builds the grounding pipeline, adds an "insufficient evidence" output path, validates tool
  calls against schemas, and ships a claim-level faithfulness eval that runs in CI.
- **Architect:** Sets the per-use-case hallucination budget and verification tier, decides where human review
  is mandatory, and makes abstention a first-class product state rather than an error.
- **FDE:** Runs error analysis with customer SMEs to separate retrieval failures from generation failures,
  and calibrates the judge on their labelled examples before any hallucination rate appears in a report.

## Check yourself
**Q:** A customer reports that your RAG assistant "hallucinates about 10% of the time" on their policy
documents. Walk through how you would diagnose and reduce it, and name one thing you would refuse to do.
**A:** First get the failing examples labelled and split them. Did retrieval return the passage containing
the answer? If not, it is a corpus or retrieval problem: chunking, embeddings, missing documents. If it did,
did the model contradict the passage (intrinsic) or add detail the passage does not support (extrinsic)?
Both are faithfulness failures with different fixes. Was the question
answerable at all, in which case abstention is the correct output and the eval must reward it? Then fix
retrieval first, since it is usually the largest share; require citations with quoted spans; add an
"insufficient information" path; lower the temperature on the answer step; add claim-level verification for
high-stakes categories; and calibrate the LLM judge against the SME labels before reporting any rate. I
would refuse to fine-tune the model on the policy documents as a knowledge fix, or to add "do not
hallucinate" to the prompt and call it done.

## Quiz
### Q: Why does setting temperature to 0 not eliminate hallucination?
- [ ] Temperature only affects the first generated token
- [x] Greedy decoding still picks the most probable token from a distribution that may be wrong; it removes sampling variance, not the underlying knowledge gap
- [ ] Most APIs ignore the temperature parameter
- [ ] Temperature 0 disables retrieval
> Temperature rescales the distribution the sampler draws from. When the model does not know the answer,
> the peak of that distribution is already a fabrication, and taking the argmax reproduces it every time.
> That is why greedy decoding yields consistent, not correct, errors.

### Q: In a RAG system, which observation most strongly indicates that a reported "hallucination" is actually a retrieval failure?
- [ ] The answer is longer than usual
- [ ] The model was run at a low temperature
- [x] The retrieved passages do not contain the needed information, and the model's answer fills the gap with plausible detail
- [ ] The answer contradicts a passage that was retrieved
> If the evidence was never retrieved, no generator could have produced a grounded answer, so the fix is in
> chunking, embeddings or corpus coverage. Contradicting a retrieved passage is the opposite case, a
> faithfulness failure of the generator.

### Q: Why can post-training with human preferences or binary-graded benchmarks increase confident wrong answers?
- [ ] Reward models prefer short answers
- [x] Hedging and abstention are scored as failures, so the policy learns that a confident guess has higher expected reward than "I don't know"
- [ ] RLHF removes the model's access to its training data
- [ ] It raises the default sampling temperature
> When an abstention earns zero and a guess earns a reward whenever it happens to be right, the optimal
> policy guesses. Calibration measurably worsens under such training, which is why evals must give credit
> for correct abstention on unanswerable items.
