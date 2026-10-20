---
id: pii-redaction
module: safety-security
name: PII redaction
kind: practice
difficulty: core
est_minutes: 15
status: fact-checked
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
PII redaction finds personal identifiers in text and replaces them before the text reaches a place it
should not: a third-party model, a log, a trace, a vector index, or another user's screen. It matters
more in LLM systems than in ordinary apps because they copy text into far more places, each a new
retention point. Reversible pseudonymisation, typed placeholders with a short-lived mapping, keeps the
model useful while raw values stay inside your boundary.

## Why it exists
An LLM feature multiplies the places where user text lives. The prompt goes to a vendor. Tracing tools
store full prompts and completions by default, because that is what makes them useful for debugging.
Eval sets get built from production traces. Chunks and their embeddings sit in a vector index
indefinitely, and an embedding of a sentence containing a phone number is still that phone number for
anyone who can query the index. Fine-tuning sets are copied from the same traces. Finally, model
output is text shown to a person, and the person may not be the one the data belongs to.

Data-protection law (GDPR's data-minimisation principle, HIPAA for health data, PCI DSS for card data,
state privacy laws) and the data-processing agreement you signed with the model vendor both assume you
know what personal data leaves your boundary and where it is retained. Without a redaction step you
cannot answer that question.

The shortcuts fail. "The vendor has zero retention" covers the vendor, not your own logs, traces,
indexes and outputs. "Tell the model not to repeat personal data" is a probabilistic instruction.
"Don't log prompts at all" destroys debuggability, so teams quietly turn logging back on. What works is
a deterministic transformation applied at each boundary, with a documented policy for which identifiers
are transformed, how, and whether the transformation can be reversed.

## How it works
### What counts as PII
Direct identifiers point at one person: name, email, phone, national id, card and account numbers, IP
and device identifiers. Quasi-identifiers (date of birth, postcode, employer, a rare diagnosis)
identify in combination. Special categories (health, biometrics, religion) carry stricter rules. The
entity list for an application is a policy decision made with legal or privacy owners, not a default
you inherit from a library.

### Detection: three detector families
1. **Patterns with checksums.** Regex plus a validity check: Luhn for card numbers, mod-97 for IBANs,
   structural rules for national ids. The checksum exists *because* format alone matches too much; any
   16-digit order number looks like a card.
2. **NER models.** Statistical taggers (spaCy, transformer NER, the recognizers in Microsoft Presidio)
   find names, locations and organisations that have no fixed format. Recall is good on common,
   well-cased English text and drops on unusual names, other languages, lowercase chat and OCR noise.
3. **LLM-based detection.** Best recall on messy text and novel identifier types, at the price of
   another model call, non-determinism, and the irony of sending PII to a model to find PII; use a model
   inside your boundary and give it only the text and an entity list.

Presidio-style engines combine the first two, raise scores when context words appear ("phone" near a
digit run) and expose per-entity confidence so thresholds can differ by type.

### Transformations
- **Remove / redact:** `[REDACTED]`. Safest, loses the most.
- **Typed, numbered placeholder:** `<PERSON_1>`, `<EMAIL_2>`. The model can still reason ("PERSON_1
  emailed PERSON_2 twice") *because* the type and the coreference survive.
- **Mask:** `****0134`. Keeps a few characters for human verification.
- **Keyed hash (HMAC):** a stable pseudonym for joins and analytics. Keyed, *because* small spaces such
  as phone numbers can be enumerated and plain hashes reversed by table lookup.
- **Tokenise / encrypt with a vault:** reversible; the placeholder-to-value mapping is stored per request
  or session and used to re-hydrate output for an authorised viewer.
- **Generalise:** age 34 becomes "30s", a postcode becomes a region.

### Where the step runs
Five boundaries, each with its own policy: before the model call (depends on your vendor agreement),
before logging and tracing (always, irreversibly), before indexing (redact in chunk text, keep
identifiers in access-controlled metadata), on output (scan for identifiers that were not in the
requester's own input or authorised records), and before building eval or fine-tuning sets. The vault
for reversible mappings lives separately from logs with a time-to-live measured in minutes, not months.

### Consistency and re-hydration
The same value must map to the same placeholder within a session so the model can follow references.
Re-hydration replaces placeholders only when the viewer is authorised to see the original, which ties
redaction to the same access-control decisions as retrieval.

### Measuring
Label a sample of real traffic per entity type and track precision and recall; recall failures are the
dangerous ones. Run a stricter, slower detector over logs after the fact to estimate the leak rate of
the fast path.

## Example
A self-contained redactor: checksum-validated card detection, consistent typed placeholders, and a
per-request vault for re-hydration. The output shows what a regex layer catches and what it misses.

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
```

## Trade-offs
- Higher recall means more false positives and lost utility: a travel assistant that redacts every city
  or an HR tool that redacts every name becomes useless. When the task is about the people and the vendor
  is covered by an agreement, redact for logs and indexes but not for the model call.
- Reversible pseudonymisation keeps the model useful but concentrates the most sensitive mapping in a
  vault; it is only safer than passing raw values if the vault is short-lived and access-controlled.
- Pattern-plus-checksum detectors are fast, deterministic and explainable; NER and LLM detectors catch
  names and free-form identifiers but add latency, non-determinism and, for LLM detectors, one more
  place the data goes.
- Redacting before embedding protects the index but makes "find the emails from Dana" impossible; split
  the data so the chunk text is redacted and identifiers live in metadata behind access control.
- A pseudonym that is consistent across sessions preserves linkage for analytics but becomes a
  persistent identifier itself; number placeholders per session unless you specifically need linkage.

## Pitfalls
- Redacting the prompt in application code while the tracing SDK captured the raw request earlier in the
  call chain; the symptom is PII in the trace UI for a request the model never saw PII in.
- Detecting cards by format alone; the symptom is order numbers and tracking ids being redacted and
  support staff unable to do their jobs.
- Shipping US-centric patterns; the symptom is UK, Indian or Brazilian phone numbers and national ids
  passing straight through.
- Assuming redaction is anonymisation; the model re-identifies "<PERSON_1>, the CEO of the company in
  the previous paragraph" from context, and the symptom is the name appearing in the answer anyway.
- Redacting only the user turn; the symptom is PII from a CRM lookup or an uploaded document landing in
  logs through tool results.
- Re-hydrating for whoever receives the output rather than checking authorisation; the symptom is one
  tenant's phone number in another tenant's shared dashboard.
- Using plain hashing as a pseudonym; the symptom is a security review reversing every phone-number hash
  by enumerating the number space in minutes.

## When to use
Apply it whenever text containing people moves across a trust change: to a vendor, into logs or traces,
into an index, onto another user's screen, or into an eval or training set. The minimum for any
production LLM feature is irreversible redaction in the logging and tracing path plus an output scan;
everything else follows from your data classification and vendor agreements. Decide the entity list
with the privacy owner, measure recall per entity on a labelled sample of real traffic, and set the
transformation per boundary rather than globally. In interviews, the expected answer names the
boundaries, explains reversible pseudonymisation with typed placeholders, and knows why a keyed HMAC is
required instead of a plain hash.

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
