---
id: saml-sso
module: enterprise-integration
name: SAML SSO
kind: concept
difficulty: advanced
est_minutes: 17
status: reviewed
related:
  - enterprise-integration/sso-iam-integration
  - enterprise-integration/scim-provisioning
  - enterprise-integration/rbac-abac-mapping-to-customer-identity
  - backend-fundamentals/oauth-2-0-oidc
  - backend-fundamentals/authentication-vs-authorization
  - security-governance-compliance/threat-modeling
sources:
  - OASIS, 2005 — Security Assertion Markup Language (SAML) V2.0 Core and Bindings
  - Somorovsky et al., 2012 — On Breaking SAML: Be Whoever You Want to Be
  - Official docs: OWASP SAML Security Cheat Sheet
---

## TL;DR
SAML 2.0 lets a customer's identity provider tell your application, in a signed XML assertion delivered
through the user's browser, who the user is and what attributes they have. It predates OIDC and remains
mandatory for many enterprises. The protocol is simple; safe implementation is not, because XML signature
handling is subtle, so you use a maintained library and still validate audience, recipient, time window,
request correlation and replay yourself.

## Why it exists
Before SAML, every web application kept its own passwords, and cross-domain single sign-on meant cookies
that browsers would not share between companies. Enterprises needed a way for their directory (Active
Directory, later Okta or Entra ID) to vouch for a user to an outside application without that application
ever seeing a password. SAML, standardized in its 2.0 form in 2005, solved it with signed XML statements
passed through the browser, so the IdP and the service provider (SP) never need a direct network
connection, which suited firewalled corporate networks.

The naive alternatives do not work. Sharing the customer's LDAP with the vendor exposes credentials and
violates every security policy. Passing a username in a URL parameter is trivially forged. Even "signed
XML" is not enough by itself: early implementations verified that *a* signature in the document was valid
and then read the user from a *different*, unsigned element, letting attackers impersonate anyone. SAML
SSO is therefore both a protocol and a checklist of validations that a vendor must get exactly right.

## How it works
### Setup: metadata exchange
The SP and IdP exchange metadata once per customer. The SP publishes its entity ID (a unique URI) and
Assertion Consumer Service (ACS) URL; the IdP publishes its entity ID, SSO URL and signing certificate.
You store the IdP's certificate per tenant, *because* that certificate is the only thing that makes an
assertion trustworthy.

### SP-initiated flow
1. The user opens your app; home realm discovery finds their tenant.
2. You redirect the browser to the IdP's SSO URL with an `AuthnRequest` (HTTP-Redirect binding: deflated,
   base64, in the query string) containing a fresh request ID and your entity ID. Save the ID.
3. The IdP authenticates the user (password, MFA, conditional access).
4. The IdP returns an HTML form that auto-POSTs a base64 `SAMLResponse` to your ACS URL (HTTP-POST
   binding, *because* assertions are too large for a URL).
5. You validate and create a session.

### What the assertion contains
- `Issuer`: the IdP entity ID.
- `Subject/NameID`: the user identifier, in a format such as persistent, email or unspecified.
- `SubjectConfirmationData`: `Recipient` (your ACS URL), `NotOnOrAfter`, `InResponseTo` (your request ID).
- `Conditions`: `NotBefore`, `NotOnOrAfter`, `AudienceRestriction` (your entity ID).
- `AttributeStatement`: email, name, groups, roles.
- An XML digital signature over the assertion, the response, or both.

### The validation checklist
Each check exists *because* skipping it enables a known attack:
- Verify the signature with the tenant's stored certificate, require signing, and read every value **from
  the element that the signature covers**; otherwise signature-wrapping attacks insert a forged unsigned
  assertion next to a genuine signed one.
- `Issuer` matches the tenant's IdP, preventing one customer's IdP from asserting into another's workspace.
- `Audience` equals your entity ID, so an assertion minted for another SP cannot be replayed at you.
- `Recipient` equals your ACS URL and the time window holds with small clock skew.
- `InResponseTo` matches a request ID that *this browser session* started, and assertion IDs are recorded
  until expiry to reject replays. Binding to the session, not to a global set of pending IDs, matters
  *because* otherwise an attacker can start a login, then feed their own valid response into a victim's
  browser and sign the victim in as the attacker (login CSRF).
- If you accept IdP-initiated SSO (no `AuthnRequest`, so no `InResponseTo`), you lose request correlation;
  many teams disable it or accept it only with strict replay caching.

### Parsing, encryption, RelayState and logout
- **Parse defensively.** The response is attacker-controlled bytes until the signature checks out, and it is
  parsed *before* that check. Disable DTDs and external entity resolution, *because* otherwise an XXE payload
  can make your server read local files or call internal URLs during parsing.
- **Read text nodes carefully.** In 2018 several SAML libraries were found to read only the first text node
  of an element. An attacker who legitimately held `ana@acme.com.evil.com` at the IdP could insert a comment
  into their own signed assertion, `<NameID>ana@acme.com<!---->.evil.com</NameID>`; the signature still
  verified (canonicalization drops comments) but the SP read the user as `ana@acme.com`. Use a library version that has fixed this,
  and read the full canonicalized text.
- **Encrypted assertions.** The IdP can encrypt the assertion to the SP's public key so the browser cannot read
  the attributes. You decrypt, then verify the signature on the decrypted assertion; encryption hides data but
  does not authenticate it, so it never replaces the signature checks.
- **RelayState.** The opaque value the SP sends with the request and gets back with the response, usually the
  page to return to. Treat it as untrusted and accept only relative paths or an allow-list, *because*
  redirecting to whatever it contains turns your login into an open redirect.
- **Logout.** SAML Single Logout exists but depends on every party's front-channel redirects succeeding, and
  is unreliable in practice. Do not rely on it for revocation; use short sessions and SCIM deactivation.

### Certificates
IdP signing certificates expire and rotate. Support two active certificates per tenant during rotation,
*because* a single hard-coded certificate turns a routine rotation into an outage for that customer.

## Example
Post-decoding validation of a SAML response using `signxml` for the signature and `lxml` for reading. The
key design point is that all values are read from `signed`, the element the verifier returns, never from
the raw document.

```python
from datetime import datetime, timedelta, timezone
from lxml import etree
from signxml import XMLVerifier

NS = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion"}
SKEW = timedelta(minutes=2)

def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def consume(xml: bytes, tenant: dict, pending_ids: set, seen_ids: set) -> dict:
    # pending_ids: request IDs started by THIS browser session; seen_ids: shared, kept until expiry
    signed = XMLVerifier().verify(xml, x509_cert=tenant["idp_cert_pem"]).signed_xml
    a = signed if etree.QName(signed).localname == "Assertion" else signed.find("saml:Assertion", NS)
    if a is None:
        raise PermissionError("no signed assertion")
    now = datetime.now(timezone.utc)
    if a.findtext("saml:Issuer", namespaces=NS) != tenant["idp_entity_id"]:
        raise PermissionError("wrong issuer")
    cond = a.find("saml:Conditions", NS)
    nb = cond.get("NotBefore")                                # optional; NotOnOrAfter missing fails closed
    if (nb and now < ts(nb) - SKEW) or now >= ts(cond.get("NotOnOrAfter")) + SKEW:
        raise PermissionError("outside validity window")
    if tenant["sp_entity_id"] not in [x.text for x in cond.iterfind(".//saml:Audience", NS)]:
        raise PermissionError("wrong audience")
    scd = a.find("saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS)
    if scd.get("Recipient") != tenant["acs_url"] or scd.get("InResponseTo") not in pending_ids:
        raise PermissionError("wrong recipient or unsolicited response")
    if now >= ts(scd.get("NotOnOrAfter")) + SKEW:             # bearer confirmation has its own expiry
        raise PermissionError("subject confirmation expired")
    if a.get("ID") in seen_ids:
        raise PermissionError("replayed assertion")
    seen_ids.add(a.get("ID")); pending_ids.discard(scd.get("InResponseTo"))
    attrs = {at.get("Name"): [v.text for v in at.iterfind("saml:AttributeValue", NS)]
             for at in a.iterfind(".//saml:Attribute", NS)}
    return {"name_id": a.findtext("saml:Subject/saml:NameID", namespaces=NS), "attrs": attrs}
```

In production, prefer a full SAML library per framework (it also handles encrypted assertions and
metadata), but this checklist is what you should be able to point to in its configuration.

## Trade-offs
- SAML works with nearly every enterprise IdP and corporate policy, but XML signatures, canonicalization and
  certificate handling make it harder to implement safely than OIDC; prefer OIDC when the customer allows it.
- Browser-mediated delivery needs no network path from the IdP to you, which suits locked-down networks,
  but it is only for interactive web login; API and agent calls still need OAuth tokens.
- IdP-initiated SSO gives users a convenient tile in their portal, but removes `InResponseTo` correlation and
  widens replay and login-CSRF risk; enable it only with strict replay caching and short validity.
- Persistent opaque NameIDs survive email changes, while email NameIDs are easy to map but change; the
  choice flips if the customer cannot emit a persistent identifier.

## Pitfalls
- Reading the user from the raw document after checking that some signature verifies; the symptom is a
  pen tester logging in as an arbitrary user with a modified response.
- Not checking audience or issuer per tenant; the symptom is an assertion from one customer's IdP being
  accepted for another customer's workspace.
- Hard-coding one IdP certificate; the symptom is a single customer's logins failing on the day their IdP
  rotates its signing certificate.
- Skipping replay protection; the symptom is a captured `SAMLResponse` from browser history granting a new
  session hours later within a lax validity window.
- Parsing the response with DTDs and external entities enabled; the symptom is a pen-test finding that the
  ACS endpoint fetched an attacker-supplied URL or echoed local file contents in an error.
- Following `RelayState` to any URL; the symptom is phishing links that pass through your genuine login page
  and land on an attacker's site.
- Server clock drift; the symptom is intermittent "assertion not yet valid" errors that appear only on some
  application nodes.

## When to use
Implement SAML when customers require it, which in practice means most larger enterprise deals; offer OIDC
alongside for those who can use it. Never hand-roll XML signature verification; use a maintained library and
verify its configuration against the checklist above. Measure login failures by reason (audience, signature,
expiry) per tenant, and alert ahead of IdP certificate expiry dates captured from metadata. In interviews,
being able to walk the SP-initiated flow and explain signature wrapping and audience checks is the expected
depth for anyone shipping B2B software.

## Role lens
- **Engineer:** Configures the SAML library per tenant, stores certificates and entity IDs, enforces replay
  caches and writes tests with tampered, expired and wrong-audience responses.
- **Architect:** Decides whether IdP-initiated SSO is allowed, how SAML coexists with OIDC, and how
  certificate rotation is handled without downtime across all tenants.
- **FDE:** Exchanges metadata with the customer's IAM team, maps their attribute names to required fields,
  and debugs failed logins using a SAML tracer on a test account.

## Check yourself
**Q:** Your app verifies the SAML response signature and then extracts NameID with an XPath query over the
whole document. Why is this vulnerable, and what is the fix?
**A:** XML signatures cover specific elements referenced by ID, not the whole document. An attacker can take a
genuine signed response, insert a second forged assertion elsewhere in the tree, and leave the signed one
intact so verification still passes. A document-wide XPath may then read the forged NameID. The fix is to
read every value only from the element the verifier reports as signed, require that exactly one assertion
exists and that it is the signed one, and use a maintained library with signature wrapping defenses.
Then still check issuer, audience, recipient, time window and InResponseTo.

## Quiz
### Q: Why does the SAML HTTP-POST binding deliver the response through the user's browser?
- [ ] Browsers can decrypt assertions that servers cannot
- [x] The IdP and SP need no direct network connection, and assertions are too large for a redirect URL
- [ ] It avoids the need to sign the assertion
- [ ] POST requests cannot be replayed
> Browser-mediated delivery works across firewalls because both parties talk only to the browser, and POST
> carries a large signed XML payload; it is still replayable, which is why replay caches exist.

### Q: What does the AudienceRestriction check protect against?
- [ ] An expired IdP signing certificate
- [ ] A user without MFA
- [x] An assertion issued for a different service provider being replayed at yours
- [ ] Clock skew between servers
> The audience names the SP the assertion was minted for; without checking it, a valid signed assertion
> for another application trusted by the same IdP could be accepted by yours.

### Q: What is the main security cost of allowing IdP-initiated SSO?
- [ ] Users must re-enter passwords
- [ ] Assertions can no longer be signed
- [x] There is no AuthnRequest, so InResponseTo cannot tie the response to a request you made
- [ ] It disables attribute statements
> Unsolicited responses lose request correlation, which helps replay and login-CSRF attacks; mitigate with
> strict replay caching and short validity windows, or disable the flow.
