---
id: openapi-contract-testing
module: fastapi-for-ai
name: OpenAPI contract testing
kind: practice
difficulty: core
est_minutes: 14
status: agentic-verified
agent_relevance: useful
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
When agents call your API as tools, the OpenAPI schema is a promise to a model as well as to code.
Contract tests pin it: a CI snapshot diff fails on breaking changes, and schema fuzzing (Schemathesis)
checks responses match it, with the LLM stubbed.

## For agent builders
- **Why it matters for agents.** Agents are unusually brittle consumers. Tool definitions are generated
  from your schema, often cached in prompts or agent configs, and the model learned to call them with the
  old argument names. Renaming a field or narrowing an enum silently breaks runs that worked yesterday.
- **Where it shows up in your stack.** A CI job beside your agent service: snapshot `app.openapi()`,
  classify changes with a breaking-change tool, fuzz with Schemathesis, and replay recorded tool calls
  from agent traces against the new schema.
- **What you control.** What counts as breaking (the asymmetric rule below), the release baseline you
  diff against, the fake LLM client used in the contract suite, and the trace sample you replay.
- **What you can skip.** Writing your own fuzzer or diff engine. Use the tools; understand the rule they
  enforce.

## Why it exists
- **Unit tests move with the code.** The same people change both: rename `usage.total_tokens` to
  `usage.tokens`, update the test, everything is green, and every client reading `total_tokens` breaks in
  production. No provider test encodes what consumers depend on.
- **AI response shapes change constantly.** They grow (tool calls, reasoning content, citations), enums
  gain values (`finish_reason`), and fields move as prompt pipelines are refactored.
- **Accuracy is not stability.** FastAPI's schema updates itself silently with each change, so it
  documents the break rather than preventing it. An agent importing tools picks up the broken version on
  its next refresh.
- **Naive defences miss it.** A reviewer reading a 3,000-line `openapi.json` diff misses that a field
  became required; hand-written "golden response" tests cover only the payloads someone thought of.
- **What contract testing buys.** The schema becomes an explicit, versioned artifact, the machine
  classifies changes as breaking or not, and the implementation is fuzzed against its own declared contract.

## How it works
### Layer 1: schema snapshot and diff
1. **Snapshot.** Export `app.openapi()` to a committed file such as `openapi.snapshot.json`.
2. **Compare in CI.** Regenerate and compare with the snapshot. Any difference fails the build until the
   snapshot is updated in the same pull request, which forces the change to be visible in review.
3. **Classify against the last *released* schema**, not just the previous commit, with a breaking-change
   tool (oasdiff and openapi-diff are common choices).

**The one principle (asymmetric):** a server may *accept more* (a looser request schema) and *promise
more* (extra response fields, tighter guarantees) than before, but never accept less or promise less.

| Change | Direction | Verdict |
|---|---|---|
| New required field; narrowed type or enum; tighter constraint (`max_length` from 4096 to 1024) | request | breaking |
| Removing an optional field clients send | request | breaking (sneaky, see below) |
| Removed field; field becomes nullable or optional; changed type | response | breaking |
| New optional request field | request | usually safe |
| New response field | response | usually safe, if clients ignore unknown fields ("tolerant reader") |
| New value in a response enum | response | ambiguous: treat as breaking for public APIs |

- **Why removing an optional request field is sneaky.** With Pydantic's default `extra="ignore"` the
  request still succeeds and the field is silently dropped. The client believes it set `temperature` when
  it did not, which is worse than a 422. An agent gets no error to learn from, so it never adapts.
- **Why a new enum value is ambiguous.** It is additive, but a client that `switch`es exhaustively over
  `finish_reason` hits an unhandled case. Agent loops that branch on a tool result's `status` enum fail
  the same way.

### Layer 2: does the implementation honour the schema
- **A stable schema can still be violated.** A handler returns `None` where the schema says string, or a
  validation edge case produces a 500 that was never documented.
- **Schema-driven testing** reads the OpenAPI document and generates many requests per operation: valid,
  boundary and deliberately invalid inputs, in the spirit of property-based testing.
- **For each response it checks:** the status code is one the schema declares, the body validates against
  the declared response schema, and the server did not return a 500.
- **Where it runs.** Schemathesis runs in-process against the ASGI app, or over HTTP against a deployment.
- **Why agents care.** A model generates odd but schema-valid arguments far more often than a human
  client does, so fuzzing is a fair rehearsal for what an agent will send.

### Layer 3: consumer-driven contracts, including the agent
- **Layers 1 and 2 protect what the provider *declared*.**
- **Consumer-driven contracts** (Pact is the usual tool) record what each consumer actually *uses*, such
  as "the web app reads `choices[0].message.content`", and replay those expectations against the provider
  in CI.
- **The question only this answers:** is the field you want to remove used by anyone?
- **For an agent, traces are the pact.** Recorded tool calls from production traces show which arguments
  the model really sends; replaying a sample against the new request models answers the same question
  without asking the model team to write pacts.

### Why AI endpoints need special handling
- **Fuzzed inputs must not reach the real model**, because thousands of fuzzed requests would cost money
  and make results nondeterministic.
- **Override the LLM client dependency** with a fake that returns schema-valid outputs, so the test
  exercises routing, validation and serialization, which is where contract bugs live.

## Diagram
Follow a pull request through the three layers: the schema diff asks whether the promise changed, the
fuzzer asks whether the code keeps it, and pacts or replayed agent traces ask whether anyone relies on it.

```mermaid
flowchart TD
    PR["Pull request changes code"] --> G["Regenerate app.openapi()"]
    G --> S{"Equal to openapi.snapshot.json?"}
    S -->|"no"| F1["Fail: update snapshot in same PR"]
    S -->|"yes"| B
    F1 --> B{"Breaking vs last released schema?"}
    B -->|"yes"| F2["Block: restore field, deprecate or new version"]
    B -->|"no"| Z["Schemathesis fuzzes app, LLM client stubbed"]
    Z --> V{"Declared status and on-schema body, no 5xx?"}
    V -->|"no"| F3["Fail: fix handler or declare response"]
    V -->|"yes"| P{"Pacts and agent trace replay pass?"}
    P -->|"no"| F4["Block: a consumer uses what changed"]
    P -->|"yes"| OK["Merge and deploy"]
```

## Examples
### CI test file with both layers
A snapshot test that fails on any schema change, and a Schemathesis test that fuzzes the app with the
model provider stubbed out.

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

On a pull request that renamed `usage.total_tokens`:
1. The snapshot test fails (visible).
2. The diff tool flags a removed response property (breaking).
3. The reviewer must restore the field, add it alongside the new name with a deprecation, or cut a new
   version.

### Agent scenario: replay recorded tool calls against the new request models
Sample real tool calls from agent traces (exported as JSON Lines) and validate each one against the
current Pydantic request model. Any call that used to succeed and now fails is a break for the agent.

```python
# tests/test_agent_tool_replay.py
import json, pathlib
from pydantic import BaseModel, ConfigDict, ValidationError
from app.tools import SearchIn, RefundIn        # the request models behind tool routes

TOOL_MODELS: dict[str, type[BaseModel]] = {"tools_search": SearchIn, "tools_refund": RefundIn}
TRACES = pathlib.Path(__file__).parent / "agent_tool_calls.sample.jsonl"
# one line per recorded call: {"tool": "tools_search", "arguments": {...}}

def strict(model: type[BaseModel]) -> type[BaseModel]:
    """Same fields, but unknown arguments fail instead of being silently ignored."""
    return type(f"Strict{model.__name__}", (model,), {"model_config": ConfigDict(extra="forbid")})

def test_recorded_agent_calls_still_validate():
    failures = []
    for line in TRACES.read_text().splitlines():
        call = json.loads(line)
        model = TOOL_MODELS.get(call["tool"])
        if model is None:
            failures.append(f"tool {call['tool']} no longer exists")
            continue
        try:
            strict(model).model_validate(call["arguments"])
        except ValidationError as e:
            failures.append(f"{call['tool']}: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}")
    assert not failures, "agent tool calls the new schema rejects:\n" + "\n".join(failures[:20])
```

- **`extra="forbid"` in the replay only** surfaces the sneaky case: an argument the agent sends that the
  new model would silently drop with the default `extra="ignore"`.
- **Refresh the sample** from production traces on a schedule, so it reflects what the current model and
  prompt actually send.

### A tiny breaking-change classifier (runnable, stdlib only)
Not a replacement for oasdiff, but it shows the asymmetric rule as code. Requests must not accept less;
responses must not promise less.

```python
def fields(schema, model):
    s = schema["components"]["schemas"][model]
    return set(s.get("properties", {})), set(s.get("required", []))

def breaking(old, new, requests, responses):
    out = []
    for m in requests:                                   # server must not accept less
        (op, oreq), (np_, nreq) = fields(old, m), fields(new, m)
        out += [f"{m}: new required request field '{f}'" for f in sorted(nreq - oreq)]
        out += [f"{m}: request field '{f}' removed (silently ignored)" for f in sorted(op - np_)]
    for m in responses:                                  # server must not promise less
        (op, oreq), (np_, nreq) = fields(old, m), fields(new, m)
        out += [f"{m}: response field '{f}' removed" for f in sorted(op - np_)]
        out += [f"{m}: response field '{f}' no longer required" for f in sorted((oreq - nreq) & np_)]
    return out

old = {"components": {"schemas": {
    "ChatIn": {"properties": {"messages": {}, "temperature": {}}, "required": ["messages"]},
    "Usage": {"properties": {"prompt_tokens": {}, "total_tokens": {}},
              "required": ["prompt_tokens", "total_tokens"]}}}}
new = {"components": {"schemas": {
    "ChatIn": {"properties": {"messages": {}, "tenant_id": {}}, "required": ["messages", "tenant_id"]},
    "Usage": {"properties": {"prompt_tokens": {}, "tokens": {}}, "required": ["prompt_tokens", "tokens"]}}}}

for line in breaking(old, new, requests=["ChatIn"], responses=["Usage"]):
    print(line)
# ChatIn: new required request field 'tenant_id'
# ChatIn: request field 'temperature' removed (silently ignored)
# Usage: response field 'total_tokens' removed
```

### Worked scenario: a break that slips into the baseline
| Pull request | Diff vs previous commit | Diff vs last release |
|---|---|---|
| PR 1: rename `usage.total_tokens` to `usage.tokens`, merged with an override ("alias before release") | breaking, overridden once | breaking |
| PR 2 to PR 9: unrelated work | clean: the break is now in the baseline | still breaking |
| Release day, alias never added | nothing flags it | blocks until the alias returns or the version is bumped |

- **Why the previous-commit diff goes quiet:** once a break lands on `main`, every later diff starts from
  the broken schema, so the break is invisible from then on.
- **Why the release diff does not:** it compares against what clients actually run, so the cumulative
  change keeps showing until it is fixed or released on purpose. That is why Layer 1 step 3 insists on it.
- **Agents make the release baseline matter more:** a tool list cached in an agent's config is pinned to
  whatever schema was released when it was built.

## Production patterns
- **Snapshot plus release baseline.** Keep `openapi.snapshot.json` for "something changed" and store the
  schema of each release (a git tag or a published artifact) as the baseline for the breaking-change
  report, for example `oasdiff breaking released.json current.json --fail-on ERR` in CI (without
  `--fail-on` it only prints the report and exits 0). Monitor: breaking changes caught before release.
- **Replay agent traces as a consumer contract.** Agent teams export a sample of real tool calls from
  their tracing system and validate them against the new request models in CI, as above. Monitor:
  replay failures per release, and production 422 rates on tool routes after deploy.
- **Stub the model, keep the wiring.** Override the LLM client with `app.dependency_overrides` in the
  contract suite, as above, and never put real provider keys in that CI job. Monitor: provider spend from
  CI, which should be zero.
- **Shallow fuzz on PRs, deep fuzz nightly.** Cap generated examples per operation on pull requests (for
  example with Hypothesis `settings(max_examples=...)` on the test) and run full depth on a schedule
  against staging. Monitor: suite duration and 500s found per run.
- **Document auth errors once.** Declare 401 and 403 via router-level `responses` so the fuzzer stops
  reporting them as undocumented, and make the custom validation handler's envelope match the declared
  422 schema. Monitor: the count of "undocumented status code" findings, trending to zero.
- **Pacts for many consumers.** When several teams consume the API, have each publish Pact expectations
  and verify them in the provider pipeline before deploy. Monitor: consumers with unverified pacts, and
  fields no pact uses (safe removal candidates).

## Trade-offs
- **Strict equality vs noise.** Strict snapshot equality catches every change, but it also fails on
  harmless description edits; that noise is acceptable only because updating the snapshot is a
  one-command, reviewed step. For tool routes, a description edit changes the model's prompt, so it
  deserves review anyway.
- **Fuzz coverage vs CI time.** Schema fuzzing finds 500s and off-schema responses nobody wrote tests
  for, but it lengthens CI; when the suite gets slow, cap examples per operation on pull requests and run
  the full depth nightly.
- **Consumer knowledge vs upkeep.** Consumer-driven contracts tell you who uses a field, but every
  consumer team must publish and maintain pacts; for one internal consumer, the schema diff alone is
  usually enough. Trace replay is cheaper for agent consumers but only covers what the sample saw.
- **Strict enums vs evolution speed.** Treating additive enum values as breaking protects strict clients,
  but it slows evolution; flip to "additive is safe" only when you document that clients must handle
  unknown values.

## Pitfalls
- **Diffing against the previous commit instead of the last released schema.** Symptom: a breaking change
  split across two harmless-looking pull requests that each pass review.
- **Letting fuzzed requests hit the real model provider.** Symptom: a surprise bill, rate-limit errors and
  flaky contract tests that fail differently on every run.
- **Regenerating a red snapshot without reading the diff.** Symptom: a breaking change reaches clients
  with a green, "reviewed" pipeline behind it.
- **Custom error envelope, default 422 schema.** A custom `RequestValidationError` handler returns your
  own envelope while the schema still advertises FastAPI's automatic 422 `HTTPValidationError`. Symptom:
  the fuzzer flags a schema-conformance failure on nearly every invalid-input case.
- **Ignoring undocumented status codes the fuzzer reports**, such as 401s or 403s raised by an auth
  dependency (FastAPI documents its own 422 automatically, but not these) or 500s on empty lists. Symptom:
  client SDKs that throw on responses their types say cannot happen.
- **Treating the agent as a tolerant client.** Symptom: a renamed tool argument passes every contract
  check because no human consumer used it, and agent success rate drops after deploy. Fix: include
  replayed agent calls in the gate and re-run the agent eval set when tool schemas change.

## When to use
- **Snapshot test:** as soon as any client you do not deploy with (a front end in another repo, a
  customer, a generated SDK, an agent importing tools) depends on the API. It costs minutes and catches
  the most expensive class of mistake.
- **Schema fuzzing:** when the API has many operations or complex request models, since that is where
  unvalidated edge cases hide.
- **Consumer-driven contracts:** when several teams consume the API and you need to know whether removing
  something is safe.
- **Measure:** breaking changes caught before release, and 500s found per run.
- **Interviews:** distinguishing "the schema is accurate" from "the schema is stable" is the key insight.

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

## Interview questions
### What does a contract test catch that a unit test does not?
- **Unit tests move with the code:** the same author renames `usage.total_tokens` and updates the test, so
  everything stays green while every client breaks.
- **Contract tests pin what clients were told:** the committed schema snapshot fails on any change, and a
  breaking-change classifier says whether that change accepts less or promises less.

### Agents call your API as tools. How would you stop a schema change from silently breaking them?
- **Gate on the schema:** snapshot plus a breaking-change report against the last released schema, since
  tool lists are generated from it and often cached.
- **Gate on real usage:** replay a sample of recorded tool calls from agent traces against the new request
  models with unknown fields forbidden, so dropped arguments fail loudly.
- **Gate on behaviour:** when a tool's schema or description changes, re-run the agent eval set, because a
  compatible schema can still change which tool the model picks.

### You must rename a response field that customers read. How do you ship it without breaking them?
- **Expand first:** add the new field alongside the old one, both populated, and mark the old one
  deprecated in the schema.
- **Then wait out a deprecation window**, using consumer pacts or access patterns to see who still reads
  the old field.
- **Contract last:** remove it in a release whose breaking-change report you accept on purpose, or in a
  new API version. Diff against the last release so the two steps cannot slip through as harmless PRs.

### Why stub the LLM client in a contract suite, and what do you lose?
- **Why:** thousands of fuzzed requests against a real provider cost money, hit rate limits and make
  results nondeterministic.
- **What still gets tested:** routing, validation and serialization, which is where contract bugs live.
- **What you lose:** coverage of real model output shapes, so keep a small separate test that parses
  recorded real responses through the same response models.

### The Schemathesis job now takes 25 minutes on every pull request. What do you change?
- **Split depth by trigger:** cap generated examples per operation on pull requests and run full depth
  nightly against staging, so pull request feedback stays fast while the deep search still runs daily.
- **Remove noise:** declare 401, 403 and the real 422 envelope so findings are real bugs, not
  undocumented auth errors.
- **Track** suite duration and 500s found per run to tune the cap.

### When is a schema diff enough, and when do you need consumer-driven contracts?
- **Schema diff is enough** for one internal consumer, because you can check its usage directly.
- **Add Pact when several teams consume the API**, because only recorded consumer expectations answer
  whether removing a field is safe.
- **The cost:** every consumer team must publish and maintain pacts, and unverified pacts must block
  deploys or the system decays.

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

## Use cases & projects
### Use cases
- **Tool APIs consumed by agents.** A snapshot diff in CI stops a renamed argument or narrowed enum from
  silently breaking agent runs that call the tool with the old shape.
- **Agent run APIs with many clients.** Web apps, SDKs and other agents read run payloads, so a field
  that becomes required or disappears is caught before release.
- **Streaming and tool-call payloads that evolve.** New content parts or `finish_reason` values are
  classified as additive or breaking by the machine instead of a reviewer.
- **Replay of recorded tool calls.** Arguments captured from agent traces are validated against the new
  schema, so real model behaviour, not only invented payloads, gates the release.

### Mini project
**Add a breaking-change gate to an agent tool service (2-3 hours).**
- **Goal:** make CI fail when a change to the tool API would break agents that call it.
- **Steps:** snapshot `app.openapi()` into a committed baseline file; add a CI step that regenerates the
  schema and diffs it with a breaking-change tool; run Schemathesis against the app with the LLM client
  replaced by a schema-valid fake; replay ten recorded tool-call arguments from agent traces against the
  new request schemas.
- **Done when:** adding an optional field passes, renaming a required argument fails the diff, and a
  handler that breaks its declared response (returns `None` for a required string, which FastAPI turns
  into a 500) is caught by the fuzz run.

### Major project
This lesson feeds the module Capstone (see the module guide), which requires an OpenAPI snapshot diff
plus a Schemathesis run in CI, and a deliberately removed field that fails the contract check.
