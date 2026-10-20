---
id: pii-redaction
module: safety-security
name: PII redaction
kind: practice
difficulty: core
est_minutes: 15
status: crisp
related:
  - security-governance-compliance/pii-protection-dlp
  - safety-security/data-leakage
  - safety-security/input-output-guardrails
  - observability/tracing
  - observability/logging
  - data-architecture/data-retention-and-deletion-architecture
  - rag-retrieval/acl-aware-retrieval
sources:
  - Official docs: Microsoft Presidio
  - NIST, 2010 — SP 800-122, Guide to Protecting the Confidentiality of PII
---

## TL;DR
PII redaction replaces personal identifiers before text reaches a vendor model, log, trace, index or the
wrong screen. LLM systems copy text everywhere, so it matters more; typed placeholders with a short-lived
mapping keep the model useful.

## Why it exists
- **LLM features multiply where user text lives.** The prompt goes to a vendor. Tracing tools store full
  prompts and completions by default, because that is what makes them useful for debugging. Eval sets
  and fine-tuning sets are copied from production traces. Output is shown to a person who may not be the
  data's owner. Each copy is a new retention point.
- **Indexes retain identifiers indefinitely.** Chunks and embeddings sit in a vector index; the index
  usually stores the chunk text next to the vector, and embeddings themselves can be partially inverted
  back to text, so a phone number embedded is a phone number retained.
- **Law and contracts assume you know.** GDPR's data-minimisation principle, HIPAA for health data, PCI
  DSS for card data, state privacy laws and your data-processing agreement with the model vendor all
  assume you know what personal data leaves your boundary and where it is retained. Without redaction you
  cannot answer that.
- **The shortcuts fail.** "The vendor has zero retention" covers the vendor, not your logs, traces,
  indexes and outputs. "Tell the model not to repeat personal data" is a probabilistic instruction.
  "Don't log prompts at all" destroys debuggability, so teams quietly turn logging back on.
- **What works:** a deterministic transformation at each boundary, with a documented policy for which
  identifiers are transformed, how, and whether the transformation can be reversed.

## How it works
### What counts as PII
| Class | Examples | Note |
|---|---|---|
| Direct identifiers | name, email, phone, national id, card and account numbers, IP and device identifiers | point at one person |
| Quasi-identifiers | date of birth, postcode, employer, a rare diagnosis | identify in combination |
| Special categories | health, biometrics, religion | stricter rules |

- **The entity list is a policy decision** made with legal or privacy owners, not a default you inherit
  from a library.

### Detection: three detector families
1. **Patterns with checksums.** Regex plus a validity check: Luhn for card numbers, mod-97 for IBANs,
   structural rules for national ids. The checksum exists *because* format alone matches too much; any
   16-digit order number looks like a card.
2. **NER models.** Statistical taggers (spaCy, transformer NER, the recognizers in Microsoft Presidio)
   find names, locations and organisations that have no fixed format. Recall is good on common,
   well-cased English text and drops on unusual names, other languages, lowercase chat and OCR noise.
3. **LLM-based detection.** Best recall on messy text and novel identifier types, at the price of another
   model call, non-determinism, and the irony of sending PII to a model to find PII; use a model inside
   your boundary and give it only the text and an entity list.

- **Presidio-style engines combine the first two,** raise scores when context words appear ("phone" near
  a digit run) and expose per-entity confidence so thresholds can differ by type.

### Transformations
| Transformation | Example | Reversible | Why use it |
|---|---|---|---|
| Remove / redact | `[REDACTED]` | no | safest, loses the most |
| Typed, numbered placeholder | `<PERSON_1>`, `<EMAIL_2>` | with a vault | the model can still reason ("PERSON_1 emailed PERSON_2 twice") *because* type and coreference survive |
| Mask | `****0134` | no | keeps a few characters for human verification |
| Keyed hash (HMAC) | stable pseudonym | no | joins and analytics; keyed *because* small spaces such as phone numbers can be enumerated and plain hashes reversed by table lookup |
| Tokenise / encrypt with a vault | token | yes | mapping stored per request or session, used to re-hydrate output for an authorised viewer |
| Generalise | age 34 becomes "30s", postcode becomes region | no | keeps analytic value |

### Where the step runs
Five boundaries, each with its own policy:
1. **Before the model call:** depends on your vendor agreement.
2. **Before logging and tracing:** always, irreversibly.
3. **Before indexing:** redact in chunk text, keep identifiers in access-controlled metadata.
4. **On output:** scan for identifiers that were not in the requester's own input or authorised records.
5. **Before building eval or fine-tuning sets.**

- **The vault** for reversible mappings lives separately from logs, with a time-to-live measured in
  minutes, not months.

### Consistency and re-hydration
- **Same value, same placeholder** within a session, so the model can follow references.
- **Re-hydrate only for authorised viewers.** This ties redaction to the same access-control decisions as
  retrieval.
- **Streaming splits placeholders.** One can arrive as `<EMA` then `IL_1>`, so the re-hydrator must buffer
  from any `<` until the placeholder closes.
- **The model rewrites placeholders.** It may emit `EMAIL_1` or `<Email 1>`, or invent one not in the
  vault; match leniently, and leave anything unknown unresolved rather than guessing.

### Redaction is not anonymisation
- **Quasi-identifiers remain.** A handful together (birth date, postcode, sex, employer, a rare
  condition) can single out one person; this re-identification by linkage is a well-documented result in
  privacy research.
- **World knowledge makes it worse.** A model can resolve "<PERSON_1>, the CEO of the company in the
  previous paragraph" by itself.
- **So treat redacted text as pseudonymised personal data,** which is what data-protection law calls it,
  not as anonymous data that is free to share.

### Measuring
- **Label real traffic per entity type** and track precision (share of redactions that were really PII)
  and recall (share of real PII that was caught).
- **Recall failures are the dangerous ones and never reach zero.** At an illustrative 99% recall and
  10,000 identifiers a day, about 100 a day still land in the logs.
- **So redaction *reduces* exposure;** the redacted stores still need access control and retention limits.
- **Audit the fast path** with a stricter, slower detector over logs after the fact to estimate its leak
  rate.
- **Precision is a utility metric:** over-redaction quietly breaks the task.

## Diagram
Each boundary gets its own transformation; only the model path is reversible, and re-hydration is gated
by the same authorisation as retrieval.

```mermaid
flowchart TD
    IN["User text, tool results, documents"] --> DET["Detect: patterns, NER, LLM"]
    DET --> PS["Typed placeholders"]
    PS --> V[("Vault, TTL in minutes")]
    PS --> M["Model call"]
    DET --> RL["Irreversible redact"]
    RL --> LOG[("Logs and traces")]
    DET --> IX["Redact chunk text"]
    IX --> VI[("Vector index, ids in metadata")]
    M --> OS["Output scan"]
    OS --> AZ{"Viewer authorised?"}
    AZ -->|"yes, re-hydrate"| U["User sees originals"]
    AZ -->|"no"| UP["User sees placeholders"]
```

## Examples
### A self-contained redactor
Checksum-validated card detection, consistent typed placeholders, and a per-request vault for
re-hydration. The output shows what a regex layer catches and what it misses.

```python
import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)")
CARDISH = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")   # 13-19 digits, ends on a digit

def luhn_ok(s: str) -> bool:                       # checksum: an order number is not a card
    d = [int(c) for c in s if c.isdigit()]
    total = sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])
    return len(d) >= 13 and total % 10 == 0

@dataclass
class Redactor:
    vault: dict = field(default_factory=dict)      # placeholder -> original, lives for one request
    counts: dict = field(default_factory=dict)

    def _slot(self, kind: str, original: str) -> str:
        for ph, val in self.vault.items():         # same value -> same placeholder (coreference)
            if val == original:
                return ph
        self.counts[kind] = self.counts.get(kind, 0) + 1
        ph = f"<{kind}_{self.counts[kind]}>"
        self.vault[ph] = original
        return ph

    def redact(self, text: str) -> str:
        text = CARDISH.sub(lambda m: self._slot("CARD", m.group()) if luhn_ok(m.group()) else m.group(), text)
        text = EMAIL.sub(lambda m: self._slot("EMAIL", m.group()), text)
        text = PHONE.sub(lambda m: self._slot("PHONE", m.group()), text)
        return text

    def rehydrate(self, text: str) -> str:         # only for a viewer authorised to see the originals
        for ph, val in self.vault.items():
            text = text.replace(ph, val)
        return text

r = Redactor()
msg = "Hi, I'm Dana (dana.k@example.com, +1 415 555 0134). Card 4111 1111 1111 1111 was charged twice."
safe = r.redact(msg)
print(safe)  # Hi, I'm Dana (<EMAIL_1>, <PHONE_1>). Card <CARD_1> was charged twice.
# `safe` is what the model, the trace and the log see. The model answers "I'll refund <CARD_1>"
# and r.rehydrate(reply) restores the value on the way back to the authorised user only.
# "Dana" survived: names have no format, so they need an NER recognizer such as Presidio's.

print(Redactor().redact("Order 1234567890123456 shipped 2026-09-30; call me on 07700 900123."))
# Order <PHONE_1> shipped <PHONE_2>; call me on <PHONE_3>.
# The Luhn check correctly refused to call the order number a card, but the loose phone pattern then
# swallowed it and the date: precision failures that break a support workflow. Real phone detection
# needs per-locale rules (or a library such as phonenumbers) plus context words, not one regex.
```

### Keyed pseudonyms and a streaming re-hydrator
Standard library only. The HMAC pseudonym is stable for joins but cannot be reversed by enumerating
phone numbers without the key; the re-hydrator buffers split placeholders and matches lenient spellings.

```python
import hmac, hashlib, re

KEY = b"rotate-me-from-a-secrets-manager"      # never hard-code in real code

def pseudonym(value: str, kind: str) -> str:
    digest = hmac.new(KEY, value.encode(), hashlib.sha256).hexdigest()[:12]
    return f"{kind}_{digest}"                  # stable across records, useless without the key

print(pseudonym("+1 415 555 0134", "PHONE") == pseudonym("+1 415 555 0134", "PHONE"))  # True: joins work

PH = re.compile(r"<\s*([A-Za-z]+)[ _](\d+)\s*>")   # lenient: <EMAIL_1>, <Email 1>

def rehydrate_stream(chunks, vault):
    buf = ""
    for chunk in chunks:
        buf += chunk
        cut = buf.rfind("<")
        if cut != -1 and ">" not in buf[cut:]:     # a placeholder may still be arriving
            ready, buf = buf[:cut], buf[cut:]
        else:
            ready, buf = buf, ""
        yield PH.sub(lambda m: vault.get(f"<{m[1].upper()}_{m[2]}>", m[0]), ready)
    yield PH.sub(lambda m: vault.get(f"<{m[1].upper()}_{m[2]}>", m[0]), buf)

vault = {"<EMAIL_1>": "dana.k@example.com"}
print("".join(rehydrate_stream(["Sent to <EMA", "IL_1> and <Email 1>, not <PHONE_9>."], vault)))
# Sent to dana.k@example.com and dana.k@example.com, not <PHONE_9>.   (unknown stays unresolved)
```

### Worked scenario: what 99% recall means in practice
Illustrative numbers for a support product, one day of traffic.

| Quantity | Value |
|---|---|
| Identifiers seen | 10,000 |
| Recall of the fast redactor | 99% |
| Identifiers missed, landing in logs | about 100 |
| Over a 30-day log retention | about 3,000 retained identifiers |

The conclusion: redaction lowers exposure but the log store still needs access control, a retention
limit, and a nightly stricter-detector audit to estimate the real leak rate.

## Production patterns
- **Redact in the trace exporter, not just the handler.** Observability SDKs capture the request early,
  so register the redactor as a processor in the logging and tracing export path and test it with a
  fixture containing every entity type. Monitor PII hits found by the after-the-fact audit.
- **Presidio with custom recognizers.** Use `AnalyzerEngine` for detection and `AnonymizerEngine` for
  transformation, and add a `PatternRecognizer` for the customer's own formats (employee ids, policy
  numbers). Monitor per-entity recall on a labelled sample.
- **Short-lived vault per session.** Store placeholder mappings in a separate store with a TTL measured in
  minutes and re-hydrate only after the same authorisation check as retrieval. Monitor vault reads by
  caller.
- **Split index text from identifiers.** Redact chunk text before embedding and keep customer ids in
  access-controlled metadata filters, so search by customer still works for authorised users.
- **Eval and fine-tuning data only from the redacted path.** Build datasets from the redacted trace store,
  never from raw request logs, so the training corpus inherits redaction automatically.

## Trade-offs
- **Recall vs utility.** Higher recall means more false positives: a travel assistant that redacts every
  city or an HR tool that redacts every name becomes useless. When the task is about the people and the
  vendor is covered by an agreement, redact for logs and indexes but not for the model call.
- **Reversibility vs concentrated risk.** Reversible pseudonymisation keeps the model useful but puts the
  most sensitive mapping in a vault; it is only safer than raw values if the vault is short-lived and
  access-controlled.
- **Patterns vs models.** Pattern-plus-checksum detectors are fast, deterministic and explainable; NER and
  LLM detectors catch names and free-form identifiers but add latency, non-determinism and, for LLM
  detectors, one more place the data goes.
- **Index protection vs search.** Redacting before embedding protects the index but makes "find the
  emails from Dana" impossible; redact the chunk text and keep identifiers in metadata behind access
  control.
- **Cross-session consistency vs linkage risk.** A consistent pseudonym preserves analytics linkage but
  becomes a persistent identifier itself; number placeholders per session unless you need linkage.

## Pitfalls
- **Redacting after the tracing SDK captured the request.** Symptom: PII in the trace UI for a request the
  model never saw PII in. Fix: redact in the exporter.
- **Detecting cards by format alone.** Symptom: order numbers and tracking ids redacted and support staff
  unable to do their jobs. Fix: checksum validation.
- **Shipping US-centric patterns.** Symptom: UK, Indian or Brazilian phone numbers and national ids
  passing straight through.
- **Assuming redaction is anonymisation.** The model re-identifies "<PERSON_1>, the CEO of the company in
  the previous paragraph" from context, and the name appears in the answer anyway.
- **Redacting only the user turn.** Symptom: PII from a CRM lookup or an uploaded document landing in
  logs through tool results.
- **Re-hydrating for whoever receives the output.** Symptom: one tenant's phone number in another
  tenant's shared dashboard. Fix: check authorisation first.
- **Assuming placeholders come back verbatim.** Symptom: users see raw `EMAIL_1` or `<Email 1>` strings
  because re-hydration matched only the exact token, or a split placeholder in streamed output.
- **Plain hashing as a pseudonym.** Symptom: a security review reverses every phone-number hash by
  enumerating the number space in minutes. Fix: keyed HMAC.

## When to use
- **Use when:** text containing people crosses a trust change: to a vendor, into logs or traces, into an
  index, onto another user's screen, or into an eval or training set.
- **Minimum for any production LLM feature:** irreversible redaction in the logging and tracing path plus
  an output scan; everything else follows from data classification and vendor agreements.
- **Decide** the entity list with the privacy owner and set the transformation per boundary, not globally.
- **Measure:** recall per entity on a labelled sample of real traffic.
- **Interviews:** name the boundaries, explain reversible pseudonymisation with typed placeholders, and
  know why a keyed HMAC is required instead of a plain hash.

## Role lens
- **Engineer:** Implements the redactor as a pipeline step and as a processor in the logging and tracing
  exporter, with fixture tests per entity type and locale.
- **Architect:** Owns data classification, which boundary gets which transformation, where the vault
  lives and for how long, and the rule that eval and fine-tuning data only come through the redacted path.
- **FDE:** Discovers the customer's own identifier formats (employee ids, policy numbers), extends the
  recognizers, and reports leak-rate numbers the customer's privacy office can sign off on.

## Check yourself
**Q:** A support copilot uses a hosted LLM, a tracing tool that stores prompts, and a vector index of
past tickets. Where must redaction run, and which transformation fits each point?
**A:** Four boundaries. Before the model call: if the vendor agreement does not cover personal data,
pseudonymise reversibly with typed placeholders so the model can still reason about who did what, and
re-hydrate only for the authorised agent. Before the trace exporter: redact irreversibly, always,
because traces are long-lived and widely viewed. Before indexing: redact the chunk text and keep
identifiers in metadata behind the same access controls as the tickets, so search by customer still
works for people allowed to do it. On output: scan for identifiers that were not in the requester's
input or authorised records, which catches the model echoing another customer's data. Eval sets built
from traces inherit the redacted form automatically. Measure recall per entity type on labelled traffic
and extend patterns for the customer's locales.

## Interview questions
### Why is a typed placeholder better than a blanket [REDACTED] for an LLM call?
- **It preserves type and coreference.** `<PERSON_1>` and `<PERSON_2>` let the model reason about who did
  what without seeing identities.
- **It is reversible with a vault,** so the answer can be re-hydrated for an authorised viewer, while the
  vendor, traces and logs only ever see placeholders.

### A privacy review finds customer phone numbers in your trace UI even though the handler redacts. What happened?
- **The tracing SDK captured the raw request earlier** in the call chain than the application-level
  redaction.
- **Fix it in the exporter:** register redaction as a processor in the tracing and logging export path,
  and purge or re-redact the affected trace store.
- **Prove it:** a fixture test per entity type, plus an after-the-fact audit with a stricter detector.

### When would you not redact before the model call?
- **When the task is about the people,** such as an HR or CRM assistant, and redacting names would make it
  useless.
- **Only if the vendor agreement covers personal data;** you still redact irreversibly for logs, traces and
  indexes, because those are your own long-lived stores.

### Your redactor has 99% recall. Is that good enough?
- **It depends on volume:** at 10,000 identifiers a day about 100 still land in logs every day.
- **So redaction reduces exposure, it does not remove it:** keep access control and retention limits on
  redacted stores, and audit with a slower detector to estimate the true leak rate.

### How would you redact PII from a RAG index without breaking search by customer?
- **Redact the chunk text before embedding,** because the index stores text next to vectors and
  embeddings can be partially inverted.
- **Keep identifiers in access-controlled metadata** and filter on them, so authorised users can still
  search by customer.

## Quiz
### Q: Why pseudonymise phone numbers with a keyed HMAC rather than a plain SHA-256 hash?
- [ ] SHA-256 is too slow for request-time use
- [x] The phone-number space is small enough to enumerate and hash, so plain hashes are reversible by lookup
- [ ] HMAC output is shorter and fits the placeholder format
- [ ] Plain hashes collide too often on numeric input
> A plain hash of a low-entropy value is a lookup problem, not a secret; an attacker hashes every possible
> number once. A keyed HMAC cannot be reproduced without the key, so the pseudonym stays unlinkable.

### Q: Which boundary is most commonly forgotten in practice?
- [ ] The model API call
- [x] The tracing or logging exporter, which often captures the raw request before application-level redaction
- [ ] The user interface
- [ ] The database write
> Teams redact in the request handler and assume the job is done, but observability SDKs hook in earlier
> and store full prompts by default, so the raw text lands in a long-lived store anyway.

### Q: What does a typed, numbered placeholder such as <PERSON_2> preserve that [REDACTED] loses?
- [ ] The original value, for later auditing
- [ ] The exact character length, for layout
- [x] The entity type and the coreference, so the model can reason about who did what without the identity
- [ ] The language of the surrounding text
> Replacing every identifier with the same token destroys the information that two mentions refer to the
> same person and that it was a person rather than an address; typed numbering keeps both while hiding the value.
