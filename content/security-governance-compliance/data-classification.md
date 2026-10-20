---
id: data-classification
module: security-governance-compliance
name: Data classification
kind: practice
difficulty: intro
est_minutes: 13
related:
  - security-governance-compliance/pii-protection-dlp
  - security-governance-compliance/data-residency
  - security-governance-compliance/model-governance
  - rag-retrieval/acl-aware-retrieval
  - data-architecture/data-lineage
  - system-design/ai-gateway
  - scoping/data-access-and-permissions-discovery
sources:
  - NIST, 2004 — FIPS 199, Standards for Security Categorization of Federal Information and Information Systems
  - ISO/IEC 27001:2022 — Information security management systems (Annex A, classification and labelling controls)
---

## TL;DR
Data classification assigns every dataset and document a sensitivity label, such as Public, Internal,
Confidential or Restricted, so that controls can be decided once per label instead of argued per
request. In AI systems the hard part is propagation: chunks, embeddings, summaries, caches and fine-tuned
weights derived from data must inherit its label, or the most sensitive source silently ends up in the
least protected store.

## Why it exists
Every security control asks the same question: how bad would it be if this leaked, was altered, or was
unavailable? Without classification, each team answers it from scratch for each feature. One engineer
sends board minutes to an external model because "it's just a summary"; another refuses to send public
marketing copy anywhere. Controls end up inconsistent, reviews take weeks, and nobody can answer an
auditor's question "which systems hold restricted data?".

The naive alternative is to protect everything at the highest level. It fails because the controls for
restricted data (self-hosted models only, no third-party logging, short retention, approval for access)
are expensive, and applying them universally makes most AI use cases uneconomic. Teams then quietly
bypass the policy, which is worse than having a weaker one.

AI raises the stakes in a specific way. An LLM pipeline transforms data constantly: a contract becomes
chunks, the chunks become vectors, a conversation becomes a memory entry, a support corpus becomes a
fine-tuned adapter. Each transformation creates a new artifact that looks harmless, with no column name
or file path announcing what it was derived from. A label that does not follow the data through these
transformations protects the original while leaving its copies wide open.

## How it works
### Define a small scheme
Most organisations use three to five levels, for example:
- **Public:** approved for release; harm if leaked is negligible.
- **Internal:** ordinary business data; limited harm if leaked.
- **Confidential:** customer data, contracts, unreleased financials; material harm.
- **Restricted:** regulated or high-impact data such as health records, card data, credentials, M&A
  material; severe or legal harm.

Keep it small *because* every extra level must be understood by every data owner and mapped to distinct
controls; a seven-level scheme where adjacent levels have identical controls is noise. FIPS 199 frames
impact separately for confidentiality, integrity and availability, which is worth borrowing: a public
price list is low-confidentiality but high-integrity, since an altered price is harmful.

### Assign labels
Three sources, in order of reliability. **Owner assignment:** the data owner labels the system or dataset
at onboarding. **Inheritance from source:** everything from the HR system is Confidential by default.
**Content scanning:** DLP detectors upgrade a label when they find card numbers or health terms in a
document labelled Internal. Content scanning should only raise labels automatically, never lower them,
*because* a missed detection would otherwise declassify data.

### Propagate through derivation
The rule is high-water mark: a derived artifact carries the highest label of any input. A chunk inherits
its document's label; an embedding inherits its chunk's; a summary of five documents inherits the
highest of the five; a fine-tuned model inherits the highest label in its training set, *because* models
can memorise and reproduce training data. Store the label as metadata next to the artifact (vector
payload, trace attribute, registry entry) so enforcement can read it without tracing lineage at runtime.

### Map labels to controls
The payoff is a single control matrix: for each label, which model providers and regions are allowed,
whether prompts may be logged, retention periods, who may retrieve it, encryption and approval
requirements. The AI gateway and retrieval layer read the label and enforce the matrix.

### Handle declassification explicitly
Downgrading requires a named owner and a recorded reason, for example a redacted or aggregated dataset
reviewed before it is labelled Internal. Without that path, high-water mark makes everything Restricted
over time and the scheme collapses.

## Example
A label-driven router for a RAG assistant. Retrieved chunks carry labels; the prompt's label is the
maximum, and that decides which model endpoint may receive it.

```python
from enum import IntEnum

class Label(IntEnum):
    PUBLIC = 0; INTERNAL = 1; CONFIDENTIAL = 2; RESTRICTED = 3

# Control matrix: highest label each endpoint is approved to receive.
ENDPOINTS = {
    "external-frontier-api": Label.INTERNAL,      # no customer data under current contract
    "external-api-zdr-eu":   Label.CONFIDENTIAL,  # zero-retention terms, EU processing
    "self-hosted-vpc":       Label.RESTRICTED,
}
PREFERENCE = ["external-frontier-api", "external-api-zdr-eu", "self-hosted-vpc"]

def prompt_label(user_label: Label, chunks: list[dict]) -> Label:
    return max([user_label, *(Label[c["label"]] for c in chunks)])   # high-water mark

def route(user_label, chunks):
    lvl = prompt_label(user_label, chunks)
    for ep in PREFERENCE:                      # cheapest approved endpoint first
        if ENDPOINTS[ep] >= lvl:
            return ep, lvl.name
    raise PermissionError(f"no endpoint approved for {lvl.name}")

chunks = [{"id": "faq-12", "label": "PUBLIC"}, {"id": "contract-7#3", "label": "CONFIDENTIAL"}]
print(route(Label.INTERNAL, chunks))           # ('external-api-zdr-eu', 'CONFIDENTIAL')
print(route(Label.INTERNAL, chunks[:1]))       # ('external-frontier-api', 'INTERNAL')
```

One confidential chunk moves the whole request to a different endpoint. The endpoint names and the
contract terms in the comments are illustrative; the point is that the decision reads labels, not content.

## Trade-offs
- High-water-mark propagation is safe and simple, but labels only ratchet upward; it stays workable only
  if you also run a reviewed declassification path for redacted or aggregated outputs.
- Owner assignment is accurate for known systems but slow and incomplete; content scanning scales to
  unlabelled data but has false negatives, so use it to raise labels, never to lower them.
- A coarse scheme of three or four levels is easy to apply consistently; finer schemes flip to worthwhile
  only when the extra levels map to genuinely different controls.
- Labelling at document level is cheap but over-protects documents with one sensitive paragraph; section
  or chunk labels are more precise and cost more ingestion work and harder audits.

## Pitfalls
- Labelling source systems but not derived stores; the symptom is a vector index or trace store that
  nobody can classify when an auditor asks.
- Letting a fine-tuned adapter or cached summary carry a lower label than its training or source data;
  it later gets deployed to a tenant or provider the source data was never approved for.
- Defining labels with no mapped controls; teams label diligently and nothing behaves differently, so the
  labels decay into decoration within a year.
- Allowing automated scanners to downgrade labels; one missed detection declassifies a restricted file.
- Classifying only confidentiality; an editable public knowledge base whose integrity matters gets no
  write protection, and poisoned content flows into answers.

## When to use
Introduce classification before the second or third AI use case, when inconsistent per-feature decisions
start costing review time, and always before connecting an assistant to broad enterprise data. Reuse the
organisation's existing scheme if one exists; your job is mapping AI artifacts and destinations onto it.
Measure the share of stores and indexes with labels, the share of gateway requests carrying a label, and
how often a request is rerouted or blocked by label. In interviews, show that you know labels must
propagate through embeddings, summaries and weights.

## Role lens
- **Engineer:** Stores labels as metadata on chunks, vectors and traces, computes the high-water mark per
  request, and makes the gateway and retriever enforce it.
- **Architect:** Owns the label-to-controls matrix: which providers, regions, retention and logging each
  label permits, and the declassification process.
- **FDE:** Maps the customer's own classification scheme onto the product's labels and finds the
  unlabelled shares and inboxes the pilot will ingest.

## Check yourself
**Q:** A team fine-tunes a small model on support tickets labelled Confidential, then proposes deploying
it to a public-facing chatbot because "the model is just weights, not data". How do you respond?
**A:** Weights derived from Confidential data inherit that label under high-water mark, because models
can memorise and regurgitate training examples, including names, account details and verbatim ticket
text. Deploying publicly is effectively publishing the training data with an unknown leakage rate. To
change the label you need a declassification argument: training data redacted or pseudonymised before
fine-tuning, memorisation and extraction tests on the trained model, and a named owner's sign-off.
Otherwise the model may only run where Confidential data is allowed.

## Quiz
### Q: Under high-water-mark propagation, what label does a summary of one Public, one Internal and one Restricted document carry?
- [ ] Internal, the median of the inputs
- [ ] Public, because a summary is a new work
- [x] Restricted, the highest label among its inputs
- [ ] Whatever label a content scanner assigns to the summary text
> A derived artifact can contain information from any input, so it inherits the most sensitive label.
> A scanner may miss paraphrased sensitive content, which is why it must not lower the label.

### Q: Why should automated content scanning be allowed to raise a label but not lower it?
- [ ] Scanners cannot read documents that are already labelled
- [x] A false negative would otherwise declassify sensitive data with no human decision
- [ ] Lowering labels requires re-encrypting the data, which scanners cannot do
- [ ] Regulations forbid any automated change to classification labels
> Detectors miss things. Raising on a hit costs little; lowering on a miss exposes data. Downgrades
> therefore need an owner and a recorded reason, not an algorithm's silence.

### Q: What makes a classification scheme actually change system behaviour?
- [ ] Using at least five levels so distinctions are precise
- [ ] Printing the label in each document's header
- [ ] Training every employee annually on the definitions
- [x] Mapping each label to enforced controls such as allowed endpoints, retention and logging
> Labels only matter when gateways, retrievers and stores read them and enforce different behaviour.
> Without a control matrix, labelling is documentation that nothing checks.
