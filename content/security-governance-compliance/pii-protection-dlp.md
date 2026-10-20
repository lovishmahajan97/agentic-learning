---
id: pii-protection-dlp
module: security-governance-compliance
name: PII protection / DLP
kind: practice
difficulty: core
est_minutes: 16
related:
  - safety-security/pii-redaction
  - safety-security/data-leakage
  - security-governance-compliance/data-classification
  - security-governance-compliance/data-residency
  - system-design/ai-gateway
  - data-architecture/data-retention-and-deletion-architecture
  - observability/logging
sources:
  - Official docs: Microsoft Presidio
  - NIST, 2010 — SP 800-122, Guide to Protecting the Confidentiality of Personally Identifiable Information
  - Regulation (EU) 2016/679 — General Data Protection Regulation
---

## TL;DR
PII protection decides which personal data may reach which destination; data loss prevention (DLP) is
the machinery that finds sensitive data in motion, in use and at rest and enforces that decision with
allow, redact, tokenize, block or alert. LLM systems need it more than ordinary apps because they copy
free text into prompts, providers, logs, traces, indexes and weights, and each copy is a new place for
personal data to leak or outlive its purpose.

## Why it exists
Classic applications kept personal data in typed columns: a `ssn` field could be encrypted, masked and
access-controlled by name. An LLM application moves the same data as unstructured text. A support
transcript containing a card number is pasted into a prompt, sent to a third-party model, written to a
trace store, chunked into a vector index, summarised into "memory", and perhaps used as fine-tuning data.
None of those systems knows a card number is inside the string, so column-level controls never fire.

The naive approaches fail in opposite directions. "Tell users not to paste personal data" fails because
users paste whatever solves their problem, and documents arrive full of it. "Block anything that looks
sensitive" fails because detectors have false positives, and a gateway that rejects every prompt with a
nine-digit number breaks the product; users route around it to personal accounts, which is worse.

DLP exists to make the decision contextual: the same email address is fine in an internal ticketing
tool, must be pseudonymised before an external model, and must never appear in a debug log. Regulation
adds force: GDPR's data minimisation and purpose limitation principles, sector rules such as HIPAA, and
contractual terms all require you to show where personal data went and why. The redaction techniques
themselves are covered in the safety module's PII-redaction lesson; this lesson is about the policy and
enforcement program around them.

## How it works
### Know what counts
PII is any information that identifies a person directly (name, government ID, email, phone) or
indirectly in combination (date of birth plus postcode plus employer). Regulators also define special or
sensitive categories, such as health, biometric and financial data, with stricter rules. A policy that
lists only "SSN and card number" misses most of what an LLM actually sees, which is names and free-text
descriptions of people.

### Detect with layered techniques
Each detector covers a different failure:
- **Patterns plus validation.** Regexes for card numbers, IBANs and national IDs, confirmed with
  checksums such as Luhn, *because* a bare 16-digit regex fires on order numbers.
- **Dictionaries and context words.** "DOB", "patient", "account no." raise confidence of nearby matches.
- **NER models.** Find names, addresses and organisations that no regex can (Presidio combines patterns,
  context and NER in one analyzer).
- **Exact data match / fingerprinting.** Hash the real customer table and match against it, which finds
  your actual customers' records with very few false positives.
- **Classification labels.** If the source document is already labelled Restricted, you do not need to
  detect anything; the label drives the policy (see data classification).

### Enforce at the points where data crosses a boundary
For an LLM system the enforcement points are: user input before it reaches the model, the egress to any
external model provider (usually the AI gateway), ingestion into vector stores and memory, writes to logs
and traces, model output before display, and tool-call arguments leaving the system. Placing DLP at the
gateway and the logging pipeline covers the most traffic with the fewest integration points, *because*
every call already passes through them.

### Choose the action per destination
Actions, from least to most disruptive: allow and record; mask for display (`****1234`); pseudonymise
with typed placeholders and a short-lived mapping so the model still reasons about "PERSON_1"; tokenize
irreversibly; block; alert a human. The policy matrix is data class by destination, not data class alone.

### Cover data at rest and retention
DLP also scans stores: trace databases, prompt logs, evaluation datasets and vector indexes, where
personal data accumulates silently. Pair findings with retention limits and with deletion paths that
reach derived copies, because a right-to-erasure request must also remove the embeddings and cached
summaries built from the record.

### Measure it
Track detector precision and recall on a labelled sample of your own traffic, block and redact rates per
endpoint, and override requests. A rising override rate means the policy is wrong for the workflow.

## Example
A destination-aware policy at the gateway. The same input gets different treatment depending on where
it is going.

```python
import re

CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return total % 10 == 0

POLICY = {  # (entity, destination) -> action
    ("CARD", "external_llm"): "block",   ("CARD", "log"): "mask",
    ("EMAIL", "external_llm"): "pseudonymise", ("EMAIL", "log"): "mask",
}

def enforce(text: str, dest: str):
    mapping, n = {}, 0
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if luhn_ok(digits) and POLICY[("CARD", dest)] == "block":
            return None, {"blocked": "CARD"}
        if luhn_ok(digits):
            text = text.replace(m.group(), "****" + digits[-4:])
    for m in set(EMAIL.findall(text)):
        if POLICY[("EMAIL", dest)] == "pseudonymise":
            n += 1; mapping[f"<EMAIL_{n}>"] = m; text = text.replace(m, f"<EMAIL_{n}>")
        else:
            text = text.replace(m, m[0] + "***@" + m.split("@")[1])
    return text, mapping   # mapping stays server-side, short TTL, never logged

msg = "Refund jane.doe@example.com, card 4111 1111 1111 1111"
print(enforce(msg, "log"))           # masked card and email for the log line
print(enforce(msg, "external_llm"))  # (None, {'blocked': 'CARD'})
```

`4111 1111 1111 1111` is a well-known test card number that passes Luhn; an order number such as
`1234 5678 9012 3456` fails it and is left alone, which is exactly the false positive the checksum removes.

## Trade-offs
- Blocking gives the strongest guarantee but drives users to unsanctioned tools; it is right only for
  data whose exposure is catastrophic, such as card numbers bound for an external provider.
- Pseudonymisation keeps model quality while hiding raw values, but the mapping becomes a sensitive store
  itself and fails when the model must reason about the actual value, such as validating an address.
- NER-based detection finds names that patterns miss, yet costs latency and has language-dependent recall,
  so multilingual traffic needs evaluation per language before you trust it.
- Centralising DLP at the gateway is cheap to operate, but anything that bypasses the gateway, such as a
  direct SDK call or a local notebook, is invisible to it.
- Scanning stores at rest finds historical leaks, but large scans are expensive and their findings are
  useless without an owner who deletes or reclassifies what they find.

## Pitfalls
- Redacting prompts sent to the provider but logging the raw prompt in your own traces; the symptom is
  a DLP scan of the trace store returning thousands of hits.
- Regex-only detection without checksums or context; the symptom is a high block rate on order IDs and a
  flood of user complaints.
- Forgetting derived artifacts; an erasure request deletes the source record while its chunks, embeddings
  and summaries keep answering questions about the person.
- Logging the pseudonymisation mapping for debugging, which recreates the exact store you were trying to
  avoid in a place with weaker access control.
- Measuring only recall on synthetic test data; real traffic has names, nicknames and descriptions that
  synthetic sets lack, so production recall is far lower than reported.

## When to use
Treat DLP as mandatory whenever user-supplied or customer data flows to an external model, or into any
store you retain: logs, traces, indexes, eval sets, training data. Start from the destinations, not the
detectors: list where text goes, then decide per data class and destination what action applies. A
fully internal, self-hosted pipeline over public documents may need only logging hygiene. Measure
precision and recall on a labelled sample of real traffic, block and override rates, and time-to-delete
across derived stores. In interviews, the strong signal is knowing that the destination, not the data
alone, determines the action.

## Role lens
- **Engineer:** Wires detectors into the gateway and logging pipeline, adds checksum validation, keeps
  mappings out of logs, and tests recall on real traffic samples.
- **Architect:** Owns the data-class-by-destination policy matrix and ensures every path to an external
  model or retained store passes an enforcement point.
- **FDE:** Learns the customer's regulated identifiers and local formats, and agrees what must never leave
  their environment versus what may be pseudonymised.

## Check yourself
**Q:** Your gateway pseudonymises names and emails before calling an external model, and legal is
satisfied. Six months later an audit finds raw customer emails in a vector index. How did that happen,
and what should the design have been?
**A:** The gateway protected only the egress path to the provider. Ingestion into the vector store took
a separate path, documents were chunked and embedded without passing an enforcement point, and traces may
hold the same raw text. The design should enumerate every destination (provider, index, memory, logs,
traces, eval sets) and put each behind a policy decision, ideally driven by source classification labels
so detection is not the only signal. Erasure must also reach derived chunks and embeddings, with periodic
at-rest scans to catch whatever slipped through.

## Quiz
### Q: Why validate candidate card numbers with the Luhn checksum after a regex match?
- [ ] Luhn decrypts the card number so it can be safely tokenized
- [x] It removes many false positives, since most arbitrary digit strings such as order numbers fail the check
- [ ] Regulators require Luhn validation before any card data is redacted
- [ ] It identifies which card network issued the number
> A 13–19 digit regex matches order numbers and IDs; requiring a valid checksum discards most of them,
> cutting false positives. Network identification comes from the leading digits, not from Luhn.

### Q: The same email address appears in a prompt. What should primarily decide whether it is allowed, masked or pseudonymised?
- [ ] The confidence score of the detector that found it
- [ ] Whether the user who typed it is authenticated
- [x] The destination the text is flowing to, combined with the data's class
- [ ] Whether the email domain belongs to a free mail provider
> DLP policy is a matrix of data class by destination: an email may be fine in an internal tool, need
> pseudonymising for an external model and masking in logs. Confidence affects detection, not policy.

### Q: Which store is most often missed when a team adds PII redaction to an LLM feature?
- [ ] The model provider's request endpoint
- [ ] The browser rendering the response
- [ ] The identity provider's user directory
- [x] The application's own traces and prompt logs
> Teams focus on the provider egress and forget that their observability pipeline captures full prompts
> and responses, often with broad access and long retention, making it the largest silent PII store.
