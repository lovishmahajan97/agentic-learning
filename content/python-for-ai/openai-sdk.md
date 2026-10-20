---
id: openai-sdk
module: python-for-ai
name: OpenAI SDK
kind: tool
difficulty: core
est_minutes: 16
status: reviewed
related:
  - python-for-ai/anthropic-sdk
  - python-for-ai/litellm
  - python-for-ai/tiktoken
  - python-for-ai/pydantic
  - python-for-ai/httpx
  - prompting/structured-outputs-json-mode
  - prompting/function-tool-calling
  - serving-inference/retry-strategies
sources:
  - Official docs: OpenAI API reference
  - Official docs: openai-python
---

## TL;DR
The `openai` Python package is the official typed client for OpenAI's API: sync and async clients,
Pydantic-typed responses, streaming, tool calling, structured outputs, and built-in retries and
timeouts. It matters beyond OpenAI because its Chat Completions request shape became the de facto
interface that many other servers (vLLM, Ollama, gateways) imitate, so the same client often talks to
them by changing `base_url`.

## Why it exists
Calling an LLM over raw HTTP looks easy: POST JSON, read JSON. The details are where production code
breaks. Rate-limit responses (429) and transient 5xx errors are routine at volume and need backoff;
requests can hang for minutes unless a timeout is set; streaming responses arrive as server-sent events
that must be parsed incrementally; tool calls arrive as JSON strings inside nested structures whose
shape changes when new features land; and every field you read from a dict is an unchecked guess about
the schema. Teams that hand-roll this tend to reinvent retries badly (retrying non-idempotent work, or
retrying 400s forever) and discover schema changes as `KeyError`s in production.

The SDK encodes the API's contract in code generated from its OpenAPI specification, so request
parameters and response objects are typed and autocompletable, errors are an exception hierarchy you
can catch precisely, and the transport behaviour (connection pooling, retry with backoff, timeouts,
streaming) is implemented once. The reason to learn it well, rather than hiding it behind a framework
immediately, is that most debugging of LLM features comes down to what this client sent and received:
the exact messages, the tool schema, the `usage` counts and the request ID you give to support.

## How it works
### Client and transport
`OpenAI()` reads `OPENAI_API_KEY` (and optionally organisation and project) from the environment and
builds an `httpx` client with connection pooling. `AsyncOpenAI` exposes the same methods as coroutines
for servers built on asyncio. Create one client per process and reuse it, because each client owns a
connection pool; constructing one per request defeats keep-alive. `base_url` points the same client at
any compatible server; Azure has a dedicated `AzureOpenAI` client because its classic deployment-based
URLs, `api-version` parameter and Entra ID auth differ (Azure's newer v1-style endpoints can also be
reached with the plain client and a `base_url`; check which one your deployment exposes).

### Two generation APIs
- **Chat Completions** (`client.chat.completions.create`) takes a list of role-tagged `messages` and
  returns `choices[0].message`. It is stateless: you resend the whole history each call. This is the
  shape other providers copied.
- **Responses** (`client.responses.create`) is OpenAI's newer API, taking `input` plus `instructions`,
  returning a list of typed output items, with optional server-side conversation state and built-in
  tools. The convenience property `response.output_text` concatenates text output.
New OpenAI-specific features tend to arrive in Responses first; portability favours Chat Completions.

Server-side state (`previous_response_id`, or a conversation object) saves you from resending history
over the wire, but it does not make history free: the model still reads the earlier turns, and they are
billed as input tokens on each call. It also moves the transcript into the provider's storage, which
matters for retention policies; `store=False` opts out, at the cost of managing history yourself.

### Typed responses and structured output
Responses are Pydantic models, so `resp.usage.prompt_tokens` is checked attribute access, not a dict
lookup. For structured output, the `parse` helpers accept a Pydantic class, convert it to a strict JSON
Schema, and return the validated object (`output_parsed` on Responses, `message.parsed` on Chat
Completions). Strict schema mode constrains decoding to the schema, which is stronger than asking for
JSON in the prompt; you still handle refusals and truncated outputs.

### Tools
You pass JSON Schema function definitions; the model may return tool calls whose `arguments` field is a
JSON string. Your code parses and validates the arguments, runs the function, and sends the result back
in a follow-up message tied to the call's ID. The SDK does not execute tools for you; that loop is your
agent code.

### Streaming
`stream=True` returns an iterator of chunks (Chat Completions) or typed events (Responses) parsed from
server-sent events, so you can forward tokens to a user as they arrive. Usage totals arrive only at the
end: Chat Completions includes them in a final chunk only if you pass
`stream_options={"include_usage": True}`, while Responses carries them on the closing
`response.completed` event, so log them from the final event.

### Errors, retries, timeouts
Exceptions subclass `openai.APIError`: `APIConnectionError` and `APITimeoutError` for transport,
`APIStatusError` subclasses such as `RateLimitError` (429), `BadRequestError` (400) and
`AuthenticationError` (401). By default the client retries connection errors, 408, 409, 429 and 5xx
responses twice (`max_retries=2`) with exponential backoff and jitter, honouring a `retry-after` header
when the server sends one, because those failures are plausibly transient; 4xx errors like 400 are not
retried, because resending the same bad request cannot succeed. The default timeout is 600 seconds, far
too long for an interactive path, so set your own per client or per call with `with_options`.

Two consequences follow. Worst-case wall-clock time is roughly `timeout × (max_retries + 1)` plus
backoff. And a generation request is not idempotent: if a timeout fires after the server started work,
the retry is a second, separately billed generation, so aggressive timeouts on long outputs can raise
cost as well as latency.

### Raw responses and headers
Typed objects hide the HTTP layer, which is usually what you want, except for the headers.
`client.with_raw_response.chat.completions.create(...)` returns the raw response, whose headers include
the request ID and rate-limit state (`x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`),
and `.parse()` on it gives you the usual typed object. Reading remaining-token headers lets a batch
worker slow down before it hits 429s instead of after.

## Example
A support classifier with structured output, a tight timeout and explicit error handling. Model names
change; read them from configuration.

```python
import os
from typing import Literal
from pydantic import BaseModel
import openai
from openai import OpenAI

MODEL = os.environ.get("CLASSIFIER_MODEL", "gpt-4o-mini")
client = OpenAI(timeout=20.0, max_retries=3)        # one client per process; worst case ~4 × 20 s + backoff

class Triage(BaseModel):
    category: Literal["billing", "bug", "how_to", "other"]
    urgent: bool
    summary: str

def triage(ticket: str) -> Triage | None:
    try:
        resp = client.responses.parse(
            model=MODEL,
            instructions="Classify the support ticket.",
            input=ticket,
            text_format=Triage,                     # Pydantic class -> strict JSON Schema
        )
    except openai.RateLimitError:
        raise                                       # already retried; let the queue back off
    except openai.BadRequestError as e:
        print("bad request, not retrying:", e.status_code, e.message)
        return None
    print("request id:", resp._request_id, "tokens:", resp.usage.input_tokens, resp.usage.output_tokens)
    return resp.output_parsed                       # a validated Triage, or None on refusal

print(triage("I was charged twice this month and need it fixed today."))
```

What happens on the wire: `parse` converts `Triage` into a JSON Schema (an enum for `category`, a
boolean, a string, every field required, no extra properties), sends it as a strict text format, and the
server constrains decoding so the reply can only be JSON matching that schema; the SDK then validates it
back into a `Triage`. A 429 is retried up to three times before `RateLimitError` reaches the `except`, so
re-raising there hands the failure to an outer queue rather than retrying a fourth time in-process.

Pointing the same code at a local vLLM server is `OpenAI(base_url="http://localhost:8000/v1",
api_key="unused")`, provided that server implements the endpoint you call.

## Trade-offs
- The official SDK tracks new features immediately, but couples your code to one provider's shapes;
  a thin internal interface or a router like LiteLLM is worth it once you genuinely use several providers.
- Built-in retries handle transient failures with no code, but they multiply latency and cost on
  overload; in queued batch jobs you may prefer `max_retries=0` and let the queue own backoff.
- The Responses API offers server-side state and built-in tools, but other providers do not mirror it,
  so Chat Completions remains the better choice when portability matters more than new features.
- Strict structured outputs guarantee schema conformance, but restrict the JSON Schema features you can
  use; complex unions or open-ended dicts may need a looser schema plus Pydantic validation.

## Pitfalls
- Creating a new client per request: connection setup on every call raises latency, and under load you
  see socket exhaustion or many TLS handshakes in traces.
- Leaving the default long timeout in a user-facing endpoint: one stuck upstream call ties up a worker
  for minutes, and the symptom is request pile-ups during provider incidents.
- Calling the sync client inside an async web server: the event loop blocks, and every concurrent
  request stalls while one LLM call is in flight; use `AsyncOpenAI`.
- Trusting tool-call `arguments` as valid: they are model-generated JSON strings, and the symptom is
  occasional `JSONDecodeError` or wrong-typed fields crashing the tool layer.
- Not logging request IDs and usage: when a customer reports a bad answer or a billing spike, you
  cannot match it to a specific call or explain the cost.

## When to use
Use the OpenAI SDK directly when OpenAI (or Azure OpenAI) is your primary provider and you want new
features as they ship; use it with `base_url` against OpenAI-compatible servers for local or self-hosted
models. Wrap it behind your own small interface (one function per capability: classify, generate, embed)
so tests can fake it and a second provider can be added without touching callers. Move to LiteLLM or a
gateway when you need routing, fallbacks or central spend tracking across providers. Measure latency
percentiles, retry counts, error rates by type and tokens per call. Interviewers probe retry semantics,
streaming, and how you validate structured output.

## Role lens
- **Engineer:** Writes the client wrapper with timeouts, retries, structured output and streaming,
  and logs request IDs and usage for every call.
- **Architect:** Decides whether product code calls the SDK directly or through an internal interface
  or gateway, and which API surface (Chat Completions or Responses) the platform standardises on.
- **FDE:** Points the client at a customer's Azure deployment or private compatible endpoint, often
  behind proxies and custom certificates configured through the underlying HTTP client.

## Check yourself
**Q:** During a provider incident your API's p99 latency jumps to several minutes and workers are
exhausted, even though most calls eventually fail. What is happening in the SDK and what do you change?
**A:** Each failing call may wait for the client's long default timeout and then be retried several
times with backoff, so one user request can hold a worker for the timeout multiplied by the attempts.
I would set an explicit per-call timeout sized to the endpoint's latency budget, lower `max_retries` for
interactive paths (keeping more retries for background jobs), use the async client so waiting does not
block workers, and add a circuit breaker or fallback model so that after repeated failures requests fail
fast or route elsewhere. I would also alert on `APITimeoutError` and `RateLimitError` rates separately,
because they need different fixes.

## Quiz
### Q: Why does the SDK retry 429 and 5xx responses by default but not 400?
- [ ] Because 400 errors are always caused by network problems
- [x] Because 429 and 5xx are plausibly transient, while resending an invalid request will fail the same way
- [ ] Because the API forbids retrying 400 responses
- [ ] Because 400 responses are retried by the server automatically
> Rate limits and server errors often clear after a short wait, so backoff retries help. A 400 means the
> request itself is wrong; retrying wastes time and quota without any chance of success.

### Q: What does changing `base_url` on the OpenAI client accomplish?
- [ ] It enables the Responses API on older models
- [ ] It switches the client to a different OpenAI region for lower latency
- [x] It lets the same client call any server implementing the compatible endpoints, such as a local vLLM instance
- [ ] It disables authentication for local testing
> Many servers implement OpenAI's request and response shapes. Pointing `base_url` at them reuses the
> client, though only the endpoints and features that server actually implements will work.

### Q: A model returns a tool call. What is the SDK's responsibility versus yours?
- [ ] The SDK executes the function and returns the final answer automatically
- [x] The SDK returns the call with its JSON-string arguments; your code validates, executes and returns the result
- [ ] The SDK validates the arguments against your Python function signature
- [ ] The SDK sends the tool result back to the model on the next request automatically
> The client only transports the model's request to call a tool. Parsing arguments, running the tool and
> sending results back with the call ID is the agent loop you write and must guard.
