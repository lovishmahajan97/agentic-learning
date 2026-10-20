---
id: openapi-contract-testing
module: fastapi-for-ai
name: OpenAPI contract testing
kind: practice
difficulty: core
est_minutes: 14
related:
  - fastapi-for-ai/openapi-docs
  - fastapi-for-ai/testclient-plus-pytest-asyncio
  - fastapi-for-ai/response-model
  - python-for-ai/contract-testing
  - python-for-ai/property-based-testing
  - backend-fundamentals/api-versioning-and-backward-compatibility
  - agent-runtime-architecture/cross-language-api-contracts
sources:
  - Official docs: FastAPI
  - Official docs: Schemathesis
  - Official docs: Pact
---

## TL;DR
OpenAPI contract testing treats your generated schema as a promise and checks it two ways: a snapshot
diff in CI that fails on breaking changes (a removed field, a new required input, a narrowed enum),
and schema-driven tests (for example Schemathesis) that generate requests from the schema and verify
every response matches what the schema declares. Unit tests check that code does what you meant; contract
tests check that it still does what clients were told.

## Why it exists
Your unit tests are written by the same people who change the code, so they move together: rename
`usage.total_tokens` to `usage.tokens`, update the test, and everything is green while every client that
reads `total_tokens` breaks in production. The breakage is invisible to the provider because no provider
test encodes what consumers depend on. In AI services this is constant: response shapes grow (tool calls,
reasoning content, citations), enums gain values (`finish_reason`), and fields move as prompt pipelines
are refactored.

FastAPI already produces an accurate OpenAPI document, but accuracy is not stability. The schema updates
itself silently with each change, so it documents the break rather than preventing it. The naive defences
fail too: a reviewer reading a 3,000-line `openapi.json` diff misses that a field became required, and
hand-written "golden response" tests cover only the handful of payloads someone thought of. Contract
testing makes the schema an explicit, versioned artifact, gives the machine the job of classifying changes
as breaking or not, and fuzzes the implementation against its own declared contract.

## How it works
### Layer 1: schema snapshot and diff
1. Export `app.openapi()` to a committed file such as `openapi.snapshot.json`.
2. In CI, regenerate the schema and compare it with the snapshot. Any difference fails the build until
   the snapshot is updated in the same pull request, which forces the change to be visible in review.
3. Classify the diff with a breaking-change tool (oasdiff and openapi-diff are common choices) against the
   last *released* schema, not just the previous commit. The rules follow from what an existing client
   can tolerate:
   - **Breaking for requests:** a new required field, a removed optional field the client sends, a
     narrowed type or enum, a tighter constraint (`max_length` from 4096 to 1024).
   - **Breaking for responses:** a removed field, a field becoming nullable or optional, a changed type.
   - **Usually safe:** a new optional request field, a new response field (if clients ignore unknown
     fields, the "tolerant reader" rule).
   - **Ambiguous:** adding a value to a response enum. It is additive, but a client that `switch`es
     exhaustively over `finish_reason` will hit an unhandled case, so treat it as breaking for public APIs.

### Layer 2: does the implementation honour the schema
A schema can be stable while the code violates it: a handler returns `None` where the schema says string,
or a validation edge case produces a 500 that was never documented. Schema-driven testing reads the
OpenAPI document and generates many requests per operation (valid, boundary and deliberately invalid
inputs, in the spirit of property-based testing). For each response it checks that the status code is one
the schema declares, that the body validates against the declared response schema, and that the server did
not return a 500. Schemathesis runs this in-process against the ASGI app or over HTTP against a deployment.

### Layer 3: consumer-driven contracts
Layers 1 and 2 protect what the provider *declared*. Consumer-driven contracts (Pact is the usual tool)
record what each consumer actually *uses*, such as "the web app reads `choices[0].message.content`", and
replay those expectations against the provider in CI. This answers a question the schema cannot: is the
field you want to remove used by anyone?

### Why AI endpoints need special handling
Generated inputs must not trigger real model calls, because thousands of fuzzed requests would cost money
and make results nondeterministic. Override the LLM client dependency with a fake that returns
schema-valid outputs, so the test exercises routing, validation and serialization, which is where contract
bugs live.

## Example
A CI test file with both layers: a snapshot test that fails on any schema change, and a Schemathesis test
that fuzzes the app with the model provider stubbed out.

```python
# tests/test_contract.py
import json, pathlib
import schemathesis
from app.main import create_app
from app.deps import get_llm_client

SNAPSHOT = pathlib.Path(__file__).parent / "openapi.snapshot.json"

class FakeLLM:                                   # schema-valid, free, deterministic
    async def complete(self, messages, model):
        return {"content": "ok", "finish_reason": "stop",
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}

app = create_app()
app.dependency_overrides[get_llm_client] = lambda: FakeLLM()

def test_schema_matches_snapshot():
    current = app.openapi()
    expected = json.loads(SNAPSHOT.read_text())
    assert current == expected, (
        "OpenAPI changed. Run `python scripts/export_openapi.py`, commit the snapshot, "
        "and check the breaking-change report in CI.")

# Schemathesis 4.x entry point; 3.x used schemathesis.from_asgi(...)
schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
def test_api_honours_schema(case):
    case.call_and_validate()   # fails on 5xx, undocumented status codes, or bodies off-schema
```

Run against a pull request that renamed `usage.total_tokens`: the snapshot test fails (visible), the diff
tool flags a removed response property (breaking), and the reviewer must either restore the field, add it
alongside the new name with a deprecation, or cut a new version.

## Trade-offs
- Strict snapshot equality catches every change, but it also fails on harmless description edits; that
  noise is acceptable only because updating the snapshot is a one-command, reviewed step.
- Schema fuzzing finds 500s and off-schema responses nobody wrote tests for, but it lengthens CI; when
  the suite gets slow, cap examples per operation on pull requests and run the full depth nightly.
- Consumer-driven contracts tell you who uses a field, but they require every consumer team to publish
  and maintain pacts; for one internal consumer, the schema diff alone is usually enough.
- Treating additive enum values as breaking protects strict clients, but it slows evolution; flip to
  "additive is safe" only when you document that clients must handle unknown values.

## Pitfalls
- Diffing against the previous commit instead of the last released schema; the symptom is a breaking
  change split across two harmless-looking pull requests that each pass review.
- Letting fuzzed requests hit the real model provider; the symptom is a surprise bill, rate-limit errors
  and flaky contract tests that fail differently on every run.
- Accepting a red snapshot test by regenerating the file without reading the diff; the symptom is a
  breaking change that reaches clients with a green, "reviewed" pipeline behind it.
- Ignoring undocumented status codes the fuzzer reports, such as 422s on streaming endpoints or 500s on
  empty lists; the symptom is client SDKs that throw on responses their types say cannot happen.

## When to use
Add the snapshot test as soon as any client you do not deploy with (a front end in another repo, a
customer, a generated SDK, an agent importing tools) depends on the API; it costs minutes and catches the
most expensive class of mistake. Add schema fuzzing when the API has many operations or complex request
models, since that is where unvalidated edge cases hide. Add consumer-driven contracts when several teams
consume the API and you need to know whether removing something is safe. Measure: breaking changes caught
before release, and 500s found per run. In interviews, distinguishing "the schema is accurate" from "the
schema is stable" is the key insight.

## Role lens
- **Engineer:** Maintains the snapshot test, stubs the LLM dependency for fuzzing, and fixes the 500s and
  off-schema responses the generated tests surface.
- **Architect:** Defines what counts as breaking, the deprecation window, and when a change forces a new
  API version, then makes CI enforce those rules.
- **FDE:** Pins the customer's integration to a released schema version and runs the contract suite
  before upgrading their deployment, so upgrades do not break their systems.

## Check yourself
**Q:** A pull request adds `"content_filter"` as a new possible value of `finish_reason` in the chat
response and adds a new required `tenant_id` request field. CI's snapshot test fails. How do you classify
each change, and what would you do?
**A:** The required `tenant_id` is unambiguously breaking: every existing client request now fails
validation with a 422. I would make it optional with a sensible default (derive tenant from the API key),
or ship it in a new version. The enum addition is additive, so tolerant clients are fine, but any client
that handles `finish_reason` exhaustively will hit an unknown case; for a public API I would treat it as
breaking unless the docs already require clients to handle unknown values, and announce it. Either way the
snapshot gets updated in the same pull request so reviewers see the diff, and the breaking-change report is
run against the last released schema, not the previous commit.

## Quiz
### Q: Which change is breaking for existing clients of a response schema?
- [ ] Adding a new optional field to the response body
- [ ] Adding a description to an existing field
- [x] Making a previously always-present response field optional
- [ ] Adding a new endpoint under the same tag
> Clients written against the old schema assume the field is present, so making it optional means they can
> receive bodies without it and fail. New optional fields and new endpoints are additive and safe.

### Q: What does schema-driven fuzzing (such as Schemathesis) catch that a schema snapshot diff does not?
- [ ] A field that was renamed between releases
- [x] Handlers that return 500s or bodies that violate the schema they themselves declare
- [ ] Which consumers depend on a given field
- [ ] Description changes that confuse client developers
> A snapshot diff compares schema versions; it cannot see runtime behaviour. Fuzzing calls the app and checks
> responses against the declared schema. Knowing which consumers use a field needs consumer-driven contracts.

### Q: Why should fuzzed contract tests override the LLM client dependency with a fake?
- [ ] The OpenAPI schema cannot describe model outputs
- [ ] Schemathesis refuses to call endpoints that make outbound requests
- [x] Real calls cost money, hit rate limits and are nondeterministic, while contract bugs live in validation and serialization
- [ ] FastAPI dependencies are disabled during contract tests
> Fuzzers send many requests per operation; real model calls would be expensive and flaky. A schema-valid fake
> isolates what the contract test is about: routing, validation and response serialization.
