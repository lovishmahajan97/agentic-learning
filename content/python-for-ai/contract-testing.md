---
id: contract-testing
module: python-for-ai
name: Contract testing
kind: practice
difficulty: advanced
est_minutes: 15
status: reviewed
related:
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/integration-and-end-to-end-testing
  - python-for-ai/pydantic
  - fastapi-for-ai/openapi-contract-testing
  - agent-runtime-architecture/cross-language-api-contracts
  - agents/typed-tool-contracts
  - data-architecture/data-contracts
sources:
  - Robinson, 2006 — Consumer-Driven Contracts: A Service Evolution Pattern
  - Official docs: Pact
  - Official docs: Schemathesis
---

## TL;DR
A contract test checks that two components agree on the interface between them (request shapes,
response fields, status codes, error formats) without running both at once. The consumer records what
it relies on; the provider verifies, in its own build, that it still satisfies every consumer. It exists
because mocks drift from the real service silently, and full end-to-end environments are too slow and
fragile to catch every interface break.

## Why it exists
An AI system is a set of services talking over interfaces: an agent service calls a retrieval service,
a tool gateway, a model gateway and customer APIs; tools exchange JSON with the model; an MCP server
exposes schemas to clients. Each side is tested in isolation with doubles. The agent's tests stub the
retrieval service's response as `{"hits": [{"doc_id", "score", "text"}]}`, and they pass. Then the
retrieval team renames `text` to `content` in a release. Both test suites stay green, because each
side's tests only check that side against its own assumptions, and the break appears in production as
the agent quietly answering without context.

The two obvious fixes both fail at scale. Shared end-to-end environments that run every service
together do catch the rename, but they are slow, flaky, expensive to keep in sync, and they tell you
something broke long after the commit that broke it. Writing an API spec document and asking teams
to "follow it" does not work either, because nothing executes the spec against the code. The provider
also cannot know which fields consumers actually use, so every change feels risky, and APIs ossify.

Contract testing makes the agreement executable and splits it in two: each consumer's expectations
become a file, and the provider runs that file against its real code on every build. A breaking change
then fails the provider's CI, in the commit that caused it, naming the consumer it would break.

## How it works
### Consumer-driven contracts
1. **Consumer test.** The consumer's test talks to a mock provider that is configured with expected
   interactions: "given the index contains a refunds document, `POST /search` with this body returns
   200 and a list of hits each having a string `doc_id`, a number `score` and a string `text`." The
   consumer code runs for real against that mock, so the expectations are exactly what the code uses.
2. **Contract file.** The run writes the interactions to a contract (a pact file in Pact's terms).
   Matchers express "a string" or "a list of at least one like this" rather than exact values, because
   the consumer depends on shape, not on today's data.
3. **Provider verification.** The provider's CI fetches every consumer's contract, puts its real
   application into each named **provider state** (seeds the test index), replays each request, and
   checks the response matches. Extra fields are fine; missing or retyped ones fail.
4. **Broker and deploy gate.** Contracts are published to a shared store (the Pact Broker or similar)
   tagged by version and environment, so the provider verifies against what is actually deployed and a
   "can I deploy" check blocks releasing a version that breaks a live consumer.

The design choice that matters is direction: the consumer states only what it uses, so the provider
learns exactly which fields are load-bearing and is free to change everything else. The same
mechanism covers asynchronous messages, where there is no request to replay: the consumer records the
event payload it expects (a `document.indexed` event on a queue), and the provider's verification
calls the producing code and checks the message it would publish.

### Evolving a contract without a flag day
Contracts make breaking changes visible; they do not make them free. Renaming `text` to `content`
safely is an expand-and-contract sequence, and the broker's version matrix is what tells you when
each step is allowed:
1. **Expand.** The provider returns both `text` and `content`. Every existing contract still passes,
   because extra fields are allowed.
2. **Migrate.** Each consumer switches to `content` and publishes a new contract that no longer
   mentions `text`.
3. **Contract.** The provider removes `text`. Verification now passes only if *every deployed*
   consumer version has stopped relying on it, so the "can I deploy" check blocks the removal until
   the last old consumer has actually been redeployed, not merely merged.
The order matters because services deploy independently: at any moment several versions of each are
live, and the contract that must hold is the one between the versions actually running together.

### Provider-driven, schema-based contracts
The alternative is that the provider publishes a schema (OpenAPI, JSON Schema, protobuf) and both
sides test against it. Schemathesis generates requests from an OpenAPI document and checks the real
service's responses conform, which is property-based testing applied to an API. Schema checks catch
"the service does not do what its spec says"; they do not tell you which consumers break when the spec
changes, which is what consumer-driven contracts add.

### Contracts you cannot verify
You cannot run provider verification against a model vendor's API. For third-party providers the
pattern is: validate every response at your boundary with strict models, keep recorded real responses
as consumer test fixtures, pin SDK versions, and run a small scheduled canary against the live API
that validates real responses against your models, so a vendor-side change fails a check before
users notice. Tool schemas and structured-output schemas shown to a model are contracts too, and they
are verified the same way: by validating what actually comes back.

## Example
An agent service consumes a retrieval service. The consumer models only the fields it reads and
publishes one interaction; the provider's test replays it against the real app in a seeded state.
This hand-rolled version shows the mechanism that Pact automates with matchers and a broker.

```python
from pydantic import BaseModel, ConfigDict

class Hit(BaseModel):                      # consumer side: only fields the agent reads
    model_config = ConfigDict(extra="ignore", strict=True)   # strict: "0.9" is not a float
    doc_id: str
    score: float
    text: str

class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    hits: list[Hit]

CONTRACT = {                               # published by the consumer's CI
    "consumer": "agent-service", "provider": "retrieval-service",
    "interactions": [{
        "description": "search returns scored hits",
        "provider_state": "index contains doc d1 about refunds",
        "request": {"method": "POST", "path": "/search", "json": {"query": "refund policy", "k": 3}},
        "response": {"status": 200, "schema": SearchResponse},  # shipped as model_json_schema()
    }],
}

# provider side, in the retrieval service's own test suite
from fastapi.testclient import TestClient
from retrieval.app import app, seed_state  # the provider's real code

def test_retrieval_honours_agent_contract():
    client = TestClient(app)
    for it in CONTRACT["interactions"]:
        seed_state(it["provider_state"])
        req = it["request"]
        r = client.request(req["method"], req["path"], json=req["json"])
        assert r.status_code == it["response"]["status"]
        body = it["response"]["schema"].model_validate_json(r.content)  # missing/retyped field raises
        assert body.hits, "provider state promised at least one hit"
```

On the consumer side, the matching test runs the agent's real retrieval adapter against a stub
server that returns a response built from the same interaction, so the contract cannot list a field
the adapter does not parse, or omit one it does. That pairing is what Pact's mock provider automates.

If the retrieval team renames `text` to `content`, the provider test fails in their pull request, while adding
a new `chunk_id` field passes because the consumer ignores extras. `strict=True` matters: Pydantic's
default lax mode would coerce a `score` that became the string `"0.9"` back into a float, and the
retype would pass verification while breaking any consumer that does not coerce. In a real setup the
consumer publishes the JSON Schema (or Pact matchers) rather than a Python class.

## Trade-offs
- Contracts catch interface breaks in seconds per build, but only interface breaks; a provider that
  returns the right shape with the wrong documents still passes, which integration tests or evals cover.
- Consumer-driven contracts tell providers exactly which fields matter, but need a broker, versioning
  discipline and buy-in from both teams; with one team owning both sides, a shared schema is often enough.
- Schema-first contracts are simple to adopt and serve many unknown consumers, but cannot report which
  consumer a change breaks; they flip to the better choice for public APIs with external clients.
- Loose matchers (type, not value) keep contracts stable, but can admit semantically wrong data; tighten
  them only where a value is genuinely part of the agreement, such as an enum of error codes.

## Pitfalls
- Contracts that pin exact values from the consumer's fixtures; the symptom is provider verification
  failing whenever test data changes, until teams stop trusting and then stop running it.
- Consumers recording fields they do not actually read; the symptom is a provider that cannot remove
  or rename anything, because every field appears to be load-bearing.
- Missing provider states, so verification runs against an empty database; the symptom is contracts that
  only ever verify the "no results" path while the populated path breaks unnoticed.
- Treating a vendor LLM API as stable because your mocks are; the symptom is a production parse error
  after an SDK or response-format change that no test of yours could have seen.
- Removing a field as soon as consumers' main branches stop using it; the symptom is an outage in
  the window before the last consumer is redeployed, which a deploy gate on deployed versions prevents.
- Verifying against the main-branch contract instead of the deployed one; the symptom is a provider
  release that passes CI and still breaks the consumer version currently running in production.

## When to use
Use consumer-driven contracts when separately deployed services are owned by different teams and
interface breaks have actually reached production; that is when the broker and process pay for
themselves. Use schema-based checks (OpenAPI plus Schemathesis, or shared Pydantic models) when one team
owns both sides or the API is public. For third-party model and SaaS APIs, use boundary validation plus
a live canary. Do not use contracts as a substitute for integration tests of behaviour. In interviews,
contract testing is the expected answer to "how do you stop mocks drifting from the real service?"

## Role lens
- **Engineer:** Writes consumer tests that record only the fields the code reads, and provider tests
  with explicit states, so contract failures point at real breaks.
- **Architect:** Decides where contracts sit (service APIs, tool schemas, MCP surfaces, event payloads)
  and wires a deploy gate so breaking changes cannot ship to a live consumer.
- **FDE:** Captures the customer system's real request and response shapes as a contract early, so an
  upstream change in their API fails a check instead of a live integration.

## Check yourself
**Q:** Your agent calls an internal tool service owned by another team and a public LLM API. Mocks on
both sides keep passing while production breaks. Design the testing for each boundary.
**A:** For the internal tool service, I would use consumer-driven contracts: the agent's tests run
against a mock built from recorded interactions that use type matchers and name provider states; the
contract is published to a broker; the tool team's CI verifies it against their real service and a
deploy gate checks deployed versions. For the LLM API, I cannot run provider verification, so I would
validate every response with strict models at the boundary, keep recorded real responses as fixtures,
pin the SDK, and run a scheduled canary that calls the live API and validates the shape. Neither
replaces evals of answer quality; contracts only guarantee the shape of the conversation.

## Quiz
### Q: In consumer-driven contract testing, who verifies the contract against real code?
- [ ] The consumer, by calling the real provider in its test suite
- [x] The provider, by replaying the consumer's recorded interactions against its real application
- [ ] A shared end-to-end environment that runs both services together
- [ ] Nobody; the contract file is documentation reviewed by humans
> The consumer runs against a mock to produce the contract; the provider verifies it in its own build.
> That split is what removes the need for a shared environment to catch interface breaks.

### Q: Why should consumer contracts use type matchers rather than exact values?
- [ ] Exact values make contract files too large to store
- [x] The consumer depends on response shape, so exact values cause failures whenever test data changes
- [ ] Type matchers let the provider omit fields the consumer uses
- [ ] Exact values cannot be serialised to JSON
> Pinning values couples the contract to fixtures rather than to the interface, making verification
> brittle. Matchers still fail when a used field is missing or changes type.

### Q: What can schema-based contract checks not tell a provider that consumer-driven contracts can?
- [ ] Whether responses match the published OpenAPI schema
- [ ] Whether a field has the declared type
- [x] Which specific consumers would break if a field were removed
- [ ] Whether the service handles malformed requests
> A schema describes what the provider offers, not what each consumer uses. Consumer-driven contracts
> record actual usage, so a removal fails against exactly the consumers that read that field.
