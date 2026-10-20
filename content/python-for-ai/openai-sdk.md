---
id: openai-sdk
module: python-for-ai
name: OpenAI SDK
kind: tool
difficulty: core
est_minutes: 16
status: crisp
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
The `openai` package is OpenAI's official typed client: sync and async, streaming, tools, structured
outputs, retries and timeouts. Its Chat Completions shape is the de facto standard, so the same client
reaches vLLM, Ollama or gateways by changing `base_url`.

## Why it exists
- **Raw HTTP breaks on the details.** 429 rate limits and transient 5xx errors are routine at volume and
  need backoff; requests can hang for minutes without a timeout.
- **Payloads are awkward to parse safely.** Streaming arrives as server-sent events parsed incrementally;
  tool calls arrive as JSON strings inside nested structures whose shape changes as features land; every
  dict field you read is an unchecked guess about the schema.
- **Hand-rolled clients fail predictably.** Teams reinvent retries badly (retrying non-idempotent work, or
  retrying 400s forever) and discover schema changes as `KeyError`s in production.
- **The SDK encodes the contract once.** Generated from the OpenAPI specification, it gives typed,
  autocompletable parameters and responses, a precise exception hierarchy, and one implementation of
  pooling, retry with backoff, timeouts and streaming.
- **Most LLM debugging happens here.** What this client sent and received, meaning the exact messages,
  the tool schema, the `usage` counts and the request ID you give to support, is why to learn it before
  hiding it behind a framework.

## How it works
### Client and transport
- **Env-configured.** `OpenAI()` reads `OPENAI_API_KEY` (and optionally organisation and project) and
  builds an `httpx` client with connection pooling.
- **Async twin.** `AsyncOpenAI` exposes the same methods as coroutines for asyncio servers.
- **One client per process**, reused, because each client owns a connection pool; one per request defeats
  keep-alive.
- **`base_url`** points the same client at any compatible server.
- **Azure.** A dedicated `AzureOpenAI` client exists because classic deployment-based URLs, the
  `api-version` parameter and Entra ID auth differ. Azure's newer v1-style endpoints can also be reached
  with the plain client and a `base_url`; check which one your deployment exposes.

### Two generation APIs
| | Chat Completions | Responses |
|---|---|---|
| Call | `client.chat.completions.create` | `client.responses.create` |
| Input | role-tagged `messages` | `input` plus `instructions` |
| Output | `choices[0].message` | list of typed output items; `response.output_text` concatenates text |
| State | stateless: resend the whole history each call | optional server-side conversation state, built-in tools |
| Best for | portability; the shape other providers copied | new OpenAI-specific features, which tend to arrive here first |

Server-side state (`previous_response_id`, or a conversation object):
- **Saves resending history over the wire, not paying for it.** The model still reads earlier turns, and
  they are billed as input tokens on each call.
- **Moves the transcript into provider storage**, which matters for retention policies. `store=False` opts
  out, at the cost of managing history yourself.

### Typed responses and structured output
- **Pydantic responses.** `resp.usage.prompt_tokens` is checked attribute access, not a dict lookup.
- **`parse` helpers** take a Pydantic class, convert it to a strict JSON Schema, and return the validated
  object (`output_parsed` on Responses, `message.parsed` on Chat Completions).
- **Strict mode constrains decoding** to the schema, stronger than asking for JSON in the prompt. You still
  handle refusals and truncated outputs.

### Tools
1. You pass JSON Schema function definitions.
2. The model may return tool calls whose `arguments` field is a JSON string.
3. Your code parses and validates the arguments and runs the function.
4. You send the result back in a follow-up message tied to the call's ID.

- **The SDK does not execute tools.** That loop is your agent code.

### Streaming
- **`stream=True`** returns an iterator of chunks (Chat Completions) or typed events (Responses) parsed from
  server-sent events, so you can forward tokens as they arrive.
- **Usage arrives only at the end.** Chat Completions includes it in a final chunk only with
  `stream_options={"include_usage": True}`; Responses carries it on the closing `response.completed`
  event. Log usage from the final event.

### Errors, retries, timeouts
| Exception (all subclass `openai.APIError`) | Meaning |
|---|---|
| `APIConnectionError`, `APITimeoutError` | transport |
| `RateLimitError` | 429 (an `APIStatusError` subclass) |
| `BadRequestError` | 400 |
| `AuthenticationError` | 401 |

- **Default retries:** connection errors, 408, 409, 429 and 5xx are retried twice (`max_retries=2`) with
  exponential backoff and jitter, honouring a `retry-after` header when sent, because those failures are
  plausibly transient.
- **No retry on 4xx like 400**, because resending the same bad request cannot succeed.
- **Default timeout is 600 seconds**, far too long for an interactive path; set your own per client or per
  call with `with_options`.
- **Worst-case wall clock** is roughly `timeout × (max_retries + 1)` plus backoff.
- **Generation is not idempotent.** If a timeout fires after the server started work, the retry is a
  second, separately billed generation, so aggressive timeouts on long outputs raise cost as well as
  latency.

### Raw responses and headers
- **Typed objects hide the HTTP layer**, usually what you want, except for the headers.
- **`client.with_raw_response.chat.completions.create(...)`** returns the raw response; its headers carry
  the request ID and rate-limit state (`x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`),
  and `.parse()` gives the usual typed object.
- **Throttle before the 429.** Reading remaining-token headers lets a batch worker slow down before it hits
  429s instead of after.

## Examples
### Support classifier with structured output and explicit errors
Model names change; read them from configuration.

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

- **On the wire:** `parse` turns `Triage` into a JSON Schema (an enum for `category`, a boolean, a string,
  every field required, no extra properties) sent as a strict text format. The server constrains decoding
  to matching JSON; the SDK validates it back into a `Triage`.
- **Retry accounting:** a 429 is retried up to three times before `RateLimitError` reaches the `except`, so
  re-raising hands the failure to an outer queue instead of retrying a fourth time in-process.
- **Local server:** the same code runs against vLLM with `OpenAI(base_url="http://localhost:8000/v1",
  api_key="unused")`, provided that server implements the endpoint you call.

### One round of the tool loop (Chat Completions)
Reuses `client` and `MODEL` from above. The SDK only transports the call; parsing, validation, execution
and the reply are yours.

```python
import json

tools = [{"type": "function", "function": {
    "name": "get_order", "description": "Look up an order by id",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                   "required": ["order_id"], "additionalProperties": False}}}]

def get_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}

messages = [{"role": "user", "content": "Where is order A123?"}]
resp = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
msg = resp.choices[0].message
if msg.tool_calls:
    messages.append(msg)                            # the assistant turn that asked for tools
    for call in msg.tool_calls:
        try:
            args = json.loads(call.function.arguments)          # model-generated, may be invalid
            result = get_order(**args) if call.function.name == "get_order" else {"error": "unknown tool"}
        except (json.JSONDecodeError, TypeError) as e:
            result = {"error": f"bad arguments: {e}"}
        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
    resp = client.chat.completions.create(model=MODEL, messages=messages, tools=tools)
print(resp.choices[0].message.content)
```

### Worked scenario: worst-case time a worker is held
Using `timeout × (max_retries + 1)`, before backoff:

| Settings | Worst case |
|---|---|
| Defaults: 600 s, `max_retries=2` | 1,800 s (30 minutes) |
| Classifier above: 20 s, `max_retries=3` | 80 s |
| Interactive path: 10 s, `max_retries=1` | 20 s |
| Queued batch job: 60 s, `max_retries=0`, queue owns backoff | 60 s per attempt |

During a provider incident, the first row is how a few slow calls exhaust a worker pool.

## Production patterns
- **Budget timeouts per path.** Create one client, then derive variants with
  `client.with_options(timeout=10.0, max_retries=1)` for user-facing calls and longer budgets for batch.
  Monitor `APITimeoutError` and `RateLimitError` rates separately, because they need different fixes.
- **Log the request ID and usage on every call.** Record `resp._request_id`, model, input and output
  tokens and latency in structured logs. Use them to answer "why this answer" and "why this bill".
- **Throttle batch workers from headers.** Use `with_raw_response` and read
  `x-ratelimit-remaining-tokens`, slowing the worker as it approaches zero. Monitor 429 count per hour; it
  should trend to zero.
- **Stream with usage on.** Pass `stream=True` with `stream_options={"include_usage": True}` on Chat
  Completions, forward deltas to the user, and record usage from the final chunk. Monitor time to first
  token.
- **Async client in async servers.** Use `AsyncOpenAI` in FastAPI or other asyncio apps, created once at
  startup. Monitor event-loop lag and concurrent in-flight calls.
- **Configurable endpoint behind a thin interface.** Read `base_url` and model from config, and wrap calls
  in one function per capability (classify, generate, embed) so tests fake it and a compatible server can
  be swapped in.

## Trade-offs
- **Immediate features vs provider coupling.** The official SDK tracks new features immediately but
  couples code to one provider's shapes; a thin internal interface or a router like LiteLLM pays off once
  you genuinely use several providers.
- **Free retries vs multiplied latency.** Built-in retries handle transient failures with no code, but
  multiply latency and cost on overload; in queued batch jobs prefer `max_retries=0` and let the queue own
  backoff.
- **Responses features vs portability.** Server-side state and built-in tools are not mirrored by other
  providers, so Chat Completions is better when portability matters more than new features.
- **Schema guarantee vs schema expressiveness.** Strict structured outputs guarantee conformance but
  restrict JSON Schema features; complex unions or open-ended dicts may need a looser schema plus Pydantic
  validation.

## Pitfalls
- **A new client per request.** Connection setup on every call raises latency; under load you see socket
  exhaustion or many TLS handshakes in traces.
- **The default long timeout in a user-facing endpoint.** One stuck upstream call ties up a worker for
  minutes. Symptom: request pile-ups during provider incidents.
- **The sync client inside an async web server.** The event loop blocks and every concurrent request
  stalls while one LLM call is in flight; use `AsyncOpenAI`.
- **Trusting tool-call `arguments` as valid.** They are model-generated JSON strings. Symptom: occasional
  `JSONDecodeError` or wrong-typed fields crashing the tool layer.
- **Not logging request IDs and usage.** When a customer reports a bad answer or a billing spike, you
  cannot match it to a call or explain the cost.

## When to use
- **Use directly when** OpenAI (or Azure OpenAI) is your primary provider and you want new features as they
  ship; use it with `base_url` against OpenAI-compatible servers for local or self-hosted models.
- **Wrap it** behind a small interface (one function per capability: classify, generate, embed) so tests
  can fake it and a second provider can be added without touching callers.
- **Move to LiteLLM or a gateway when** you need routing, fallbacks or central spend tracking across
  providers.
- **Measure** latency percentiles, retry counts, error rates by type and tokens per call.
- **Interviews:** retry semantics, streaming, and how you validate structured output.

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
