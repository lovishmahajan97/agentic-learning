---
id: python-for-ai
title: Python for AI
---

## Overview
This module covers the Python you need to build AI systems that hold up once they leave the notebook.
It starts with the language itself. The object model and mutability explain the aliasing bugs that
leak state between requests. Type hints, `TypedDict`, `Literal`, Protocols and generics give names to
the shapes moving through your code. Dataclasses handle trusted internal records and Pydantic
validates untrusted JSON. Exceptions, generators, decorators and context managers are the idioms
every later library depends on. Next comes the project around the code: uv, lockfiles, a `src/`
layout, configuration from `.env` files and pydantic-settings, and Ruff and mypy as gates. Concurrency
follows, because AI services mostly wait: async/await and asyncio, cancellation, locks and semaphores,
the GIL, and how to choose between asyncio, threads and processes. The integration layer is httpx,
tenacity, structured logging, dependency injection, the OpenAI and Anthropic SDKs, tiktoken, LiteLLM,
Instructor and the MCP SDK. Testing goes from pytest and test doubles up to property-based, contract
and integration tests. The data and model stack is Jupyter, NumPy, pandas, pyarrow, Polars,
scikit-learn, PyTorch, Hugging Face, safetensors, sentence-transformers and Outlines. Profiling and
security scanning close the module. Afterwards you can write a typed, tested, reproducible Python
service or pipeline that calls models concurrently and safely, handles their data efficiently and
fails in ways you can diagnose. The module matters most for Engineers, who write this code every
day. FDEs rely on it too, because they have to make the same code install, configure and run inside a
customer's environment.

## Why it matters
- **Engineer:** You write the agent loops, SDK wrappers, async fan-outs, Pydantic schemas, data pipelines and test suites. A mutable default shared across tenants, a blocking call on the event loop, a swallowed `CancelledError` or retries on a non-retryable error are all bugs that end up in your code.
- **Architect:** You decide where the trust boundaries are and which ones get Pydantic validation, which vendor SDKs sit behind small Protocols, how the import graph is layered and what concurrency model each workload uses. Those choices decide whether the provider can be swapped, whether the core runs in tests without credentials, and where the system's throughput limit sits.
- **FDE:** Customer environments have proxies, pinned Python versions, air-gapped package mirrors, their own secrets stores and their own models. You are the one who gets a locked, audited environment to install, injects settings without code changes, points the SDK at their gateway through httpx, and profiles the job that runs out of memory on their hardware.

## Mental model
Think of an AI program as a typed core surrounded by a membrane of boundaries, running on an event
loop, with a data plane next to it. The core is plain Python: frozen dataclasses, Protocols and pure
functions, which you can test without a network, a key or a GPU. The membrane is where untrusted or
slow things come in. Pydantic validates every JSON payload, model output and setting that crosses
it. Small adapters wrap the OpenAI, Anthropic, MCP and httpx clients, and the adapters translate
vendor failures into your own exception hierarchy, with tenacity retrying only what is safe to
repeat. The event loop runs the I/O: it may wait as often as it likes but must never block, and
cancellation, semaphores and context managers keep its resources bounded and released. CPU-heavy
work leaves the loop for threads, processes or vectorised libraries. The data plane moves tables and
tensors through NumPy, Arrow, Parquet, Polars and PyTorch without row-by-row Python. Tooling holds it
all in place: lockfiles fix the environment, type checkers and Ruff fix the shapes, tests pin the
behaviour at each layer, and logs and profiles show what actually happened.

## Suggested order
1. object-model-and-mutability — names, aliasing and mutation underlie every later bug about shared state, defaults and caches.
2. type-hints — the annotation syntax that checkers, dataclasses, Pydantic and FastAPI all read.
3. typeddict-literal-enum — types the wire-format dicts and closed vocabularies (roles, finish reasons) that SDKs pass around.
4. protocols-generics-structural-typing — lets the consumer define small interfaces for LLM clients, embedders and stores without inheritance.
5. dataclasses — the default record type for trusted internal values, built on the hints just learned.
6. pydantic — adds runtime validation and JSON Schema for the untrusted data that dataclasses deliberately do not check.
7. exceptions-and-custom-exception-hierarchies — organises failures by handling policy before any remote call is written.
8. generators — lazy pipelines and token streams, which context managers and streaming SDKs build on.
9. decorators — the mechanism behind retries, tracing, tool registration and routes in later lessons.
10. context-managers-resource-cleanup — guaranteed release of clients, streams, sessions and locks, the base for `async with`.
11. json-jsonl — the formats of API payloads, eval sets and fine-tuning data, validated with Pydantic line by line.
12. uv-poetry-pip — sets up projects and environments before the code grows dependencies.
13. lockfiles-and-dependency-pinning — makes that environment reproducible across laptops, CI and containers.
14. package-structure-and-import-design — a src layout and one-way imports turn the code into a testable, layered package.
15. python-dotenv — local configuration through environment variables, and where its limits are.
16. pydantic-settings — typed, validated configuration that fails at startup instead of mid-request.
17. ruff — a fast lint and format gate from the first commit, including async and security rules.
18. mypy — makes the type hints enforceable in CI, now that the code has real types to check.
19. async-await — coroutines and the never-block rule, the base for every async lesson that follows.
20. asyncio — the event loop, tasks, gather and queues that run concurrent model and tool calls.
21. async-cancellation-and-exception-propagation — how timeouts, TaskGroups and disconnects actually stop work and release resources.
22. race-conditions-and-synchronization-primitives — locks for check-then-act and semaphores to cap concurrent provider calls.
23. gil — explains why threads do not speed up pure-Python CPU work, which the next lesson relies on.
24. asyncio-vs-threads-vs-multiprocessing — picks the concurrency model by what the work waits on.
25. httpx — the async HTTP client under the provider SDKs, whose timeouts and pools become your SDK's behaviour.
26. tenacity — declarative retries with backoff and error classification for those remote calls.
27. logging-structlog — structured events with request, model and token fields, so retries and calls can be queried.
28. dependency-injection-and-interface-boundaries — wraps slow, paid dependencies behind Protocols before the SDK lessons start.
29. openai-sdk — the de facto request shape, used directly and through `base_url` for compatible servers.
30. anthropic-sdk — content blocks, tool-result turns and prompt caching, which differ from OpenAI's shape.
31. tiktoken — local token counting for context budgets, chunking and cost estimates on those calls.
32. litellm — one interface across providers, once you know what it translates and what it hides.
33. instructor — validated Pydantic objects from hosted models with re-prompting when validation fails.
34. mcp-python-sdk — exposes typed Python functions as tools to any MCP host, combining decorators, Pydantic and asyncio.
35. pytest — fixtures, parametrization and assertions as the base of every test lesson.
36. test-doubles-mocks-fixtures — fakes and stubs for model APIs and clocks, enabled by the interface boundaries.
37. pytest-asyncio — tests concurrency caps, timeouts and cancellation on a managed event loop.
38. property-based-testing — Hypothesis finds the edge cases in chunkers, parsers and serialisers.
39. contract-testing — keeps doubles and services in agreement on interfaces without full environments.
40. integration-and-end-to-end-testing — checks the assumptions behind the doubles against real databases and HTTP stacks.
41. jupyter — the exploration environment for the data lessons, and its reproducibility pitfalls.
42. numpy — arrays, shapes, dtypes and broadcasting, which every later data and model library builds on.
43. pandas — labelled tables for eval results and logs that fit in memory.
44. pyarrow — the columnar format and Parquet I/O shared by pandas, Polars, DuckDB and datasets.
45. polars — lazy, multi-core DataFrames on Arrow for gigabyte-scale or scheduled pipelines.
46. scikit-learn-basics — baselines, pipelines and honest metrics that any LLM solution must beat.
47. pytorch — tensors, autograd and devices, the base of the open-model stack.
48. hugging-face — loading, tokenising, fine-tuning and evaluating open models on top of PyTorch.
49. safetensors — the safe weight format those models should be stored and exchanged in.
50. sentence-transformers — embeddings and cross-encoder reranking from open encoders for retrieval.
51. outlines — constrained decoding for self-hosted models, contrasted with Instructor's retry approach.
52. memory-and-performance-profiling — measures CPU, wall time and native memory across everything built so far.
53. security-scanning-and-dependency-auditing — pip-audit and Bandit on the locked environment and code as the final CI gate.

## Capstone
### Brief
A customer wants to triage tens of thousands of support tickets exported as JSONL into categories,
urgency levels and extracted fields, and wants proof that it beats a cheap baseline. Build it as an
installable Python package with a `src/` layout, managed and locked with uv. Validate every ticket
line with Pydantic and report rejected lines by line number. Configuration (provider, model, concurrency,
budget) comes from pydantic-settings, with a committed `.env.example`. The core classifier depends on a
small `LLMClient` Protocol. Implement it twice, with the OpenAI SDK and the Anthropic SDK, each built on
a shared httpx client with explicit timeouts. Get typed outputs through Instructor, or through native
structured output. Run calls concurrently on asyncio with a semaphore cap, tenacity retries that
apply only to transient errors, a custom exception hierarchy, and a per-run token budget estimated
with tiktoken. Cancelling the run must release every connection. Log one structlog event per call
with model, tokens, latency and retry count. As a baseline, embed tickets with sentence-transformers
and train a scikit-learn classifier. Write results to Parquet and compare both approaches with Polars.
Ship it with tests and CI that a reviewer can run.

### Deliverables
- A repository with `pyproject.toml`, `uv.lock`, a `src/` package and a CLI entry point that runs the full pipeline from a JSONL file to a Parquet results file.
- Two provider adapters behind one Protocol, a Pydantic output schema, an exception hierarchy separating retryable, input-too-large and fatal errors, and a settings class that rejects invalid configuration at startup.
- A test suite with a fake LLM client, pytest-asyncio tests for the concurrency cap, timeout and cancellation, Hypothesis properties for the JSONL parser and token-based chunker, and one integration test against a local fake HTTP server.
- A CI workflow running `uv sync --locked`, Ruff, mypy in strict mode on the package, pytest, pip-audit and Bandit.
- A short report, as a notebook that passes Restart and Run All plus a Markdown summary, comparing LLM and embedding-baseline accuracy, macro F1, cost and wall time on a held-out labelled set, with a profile of the slowest stage.

### Rubric
- Running the test suite with network access disabled and no API keys passes, and switching providers is a one-line settings change with no edits to the core.
- With concurrency set to 8, logs never show more than 8 calls in flight, a 429 is retried with backoff, and an invalid-key error fails once without any retry.
- Pressing Ctrl+C mid-run exits within two seconds, leaves no open connections or half-written output file, and logs how many tickets were completed.
- A malformed JSONL line is reported with its line number and skipped, and mypy strict and Ruff report zero errors without blanket `type: ignore` or `noqa` comments.
- A fresh clone on another machine reproduces the same environment from the lockfile, and pip-audit reports no unaddressed known vulnerabilities.
- The report states whether the LLM beats the scikit-learn baseline on macro F1 and cost per thousand tickets, using the same held-out split for both.
