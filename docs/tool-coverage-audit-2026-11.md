# Third-party tool coverage in the course: final recommendation (checked 2026-11-05)

This audit answers the question "do we have tools like Tinker, RAGAS and other third-party tools", plus Temporal (temporal.io). It was read-only: no lesson was edited.

**Method.** A regex inventory of ~170 tools across all 673 lessons, then one research agent per category (10 categories) that read the lessons and checked current facts on the web and on PyPI/npm, then an independent verifier per category that tried to refute every claim, then this synthesis. Registry versions quoted here were re-checked on 2026-11-05. Facts about acquisitions, shutdowns and renames come from the agents' web research with cited evidence; items they could not confirm are listed under "Unverified items" at the end.

## 1. Direct answer: Tinker, RAGAS, Temporal

**Temporal**
- **What the course has.** About 26 lessons mention Temporal, and the concepts are taught deeply and correctly: event history, replay, the determinism rule, activities, signals with timeouts, continue-as-new, `workflow.patched` and idempotency keys.
  - Real `temporalio` code appears in only two lessons: `content/system-design/workflow-orchestration.md` and `content/reliability/durable-workflow-execution.md`. Both snippets are untested and need a server plus stubbed helpers. The two agent loops overlap in concept, not in code.
  - The only tested lab, `content/agent-reliability/durable-execution-and-checkpointing.md`, is a stdlib sqlite toy replay engine with no Temporal in it.
  - A Temporal port appears only as a stretch goal in `content/mini-project-ideas/background-job-runner-for-long-agent-tasks.md:561`.
- **What it lacks.**
  - A hands-on lab: run a worker, kill it mid-run, test with time skipping (0 uses of `WorkflowEnvironment`), and replay-test a saved history.
  - Version pins. Current versions are temporalio 1.35.0 and @temporalio/workflow 1.25.0.
  - Worker Versioning, GA since 2026-03-30 and mentioned nowhere.
  - Temporal's first-party framework integrations, none of which are named:
    - OpenAI Agents SDK: package `temporalio-openai-agents` 1.1.0, imported as `temporalio.openai_agents` (not the old `temporalio.contrib` path). GA per Temporal's blog on 2026-03-23; only sandbox, streaming and OTel carry pre-release labels.
    - Pydantic AI `TemporalDurability`.
    - LangGraph, Claude Agent SDK, Google ADK, Strands, Deep Agents and MCP plugins, plus the Vercel AI SDK for TypeScript.

**RAGAS**
- **What the course has.** A section in `content/evals/tooling.md` with a metric table (faithfulness, answer relevancy, context precision and recall) and sharp edges. Its faithfulness definition is reused in `content/evals/faithfulness-groundedness.md`. It also appears in `content/llmops-platform/evaluation-frameworks.md` and cumulative project 1. Depth is concept-level: no RAGAS code anywhere and no version.
- **What it lacks.**
  - A version pin. Current is ragas 0.4.3 (2026-01-13), with no release in 10 months.
  - The new import path: `ragas.metrics.collections`. Imports from `ragas.metrics` are deprecated and will be removed in 1.0.
  - The agent metrics that ship today: `tool_call_accuracy`, `tool_call_f1`, `agent_goal_accuracy`, `topic_adherence`.
  - The repo move to `vibrantlabsai/ragas`. This is cosmetic, since the lessons cite only the paper.
  - No lab is needed.

**Tinker (Thinking Machines)**
- **What the course has.** Zero mentions in any lesson (confirmed by grep).
- **What it lacks.** A short, version-stamped "hosted training primitives" subsection in `content/training-adaptation/tooling.md` next to TRL, Axolotl and Unsloth, plus a row in a hosted-options note.
  - Facts to use: GA 2025-12-12; SDK `tinker` 0.37.0 and `tinker-cookbook` 0.5.9.
  - Primitives: `create_lora_training_client`, `forward_backward`, `optim_step`, `sample`. LoRA weights can be downloaded.
  - Pricing meters on the official page: prefill, cached prefill, sample, train, and storage at $0.10/GB-month. Quote the meters, not the per-token rates, which change.
- **Why now.** OpenAI's fine-tuning platform no longer accepts new users, so the course's "hosted fine-tune" rung needs live options. Priority is medium, not high. No lab, because TRL stays the one tool to know in this category.

## 2. New hands-on labs (3 required, 1 optional)

The course already has more framework labs than its "know one tool well per category" rule calls for, so I'm recommending only these.

1. **Temporal (durable execution)**
   - **Lesson:** a runnable Lab section in `content/reliability/durable-workflow-execution.md`, or one new lesson `agent-reliability/temporal-durable-agent-lab` that reuses its scenario.
   - **Why:** this is the gap you asked about. Temporal leads durable execution for long, approval-heavy agent runs, and the hard lessons (non-determinism errors, activity idempotency, keeping big payloads out of history) only show up when you run it.
   - **What it builds:**
     - The model call and each tool call as activities; approval as a signal with a timeout; a 3-day wait as a durable timer.
     - Tests with `WorkflowEnvironment.start_time_skipping()`. It downloads a test-server binary on first use, so state that.
     - A deliberate determinism break caught by a `Replayer` test.
     - `workflow.patched` compared with Worker Versioning.
     - A final step running the same agent through `temporalio-openai-agents` or Pydantic AI `TemporalDurability`, with a list of the other framework plugins.
     - Pins: temporalio 1.35.0 and temporalio-openai-agents 1.1.0.
   - **Afterwards:** shrink the duplicate agent loop in the other lesson to a link.

2. **A web search API for agents**
   - **Lesson:** new `agents/web-search-and-fetch-tools`, or upgrade `content/practice-use-cases/deep-research-agent.md` (line 161 currently says only "a real agent uses a search API").
   - **Why:** no lesson names any search API, crawler or provider search tool.
   - **What it builds:**
     - One vendor (Exa or Tavily) behind a `SearchProvider` protocol, with a recorded-fixture fake for offline tests.
     - Per-call cost and step caps, domain allow lists and freshness filters.
     - Page text treated as untrusted input (prompt injection) and citation provenance.
     - trafilatura as the free fetch/extract step.
   - **Vendor churn to teach:**
     - Bing Search APIs were retired 2025-08-11.
     - Nebius agreed to acquire Tavily on 2026-02-10.
     - Exa raised a $250M Series C in May 2026.
   - **Versions:** tavily-python 0.8.5, exa-py 2.27.0.

3. **Pipecat voice agent**
   - **Lesson:** new `practice-use-cases/voice-agent-pipecat-lab`.
   - **Why:** `content/practice-use-cases/voice-agent.md` is strong on concepts but names Pipecat and LiveKit only, with no code. Pipecat is vendor-neutral, runs locally, and maps one-to-one onto the turn loop already taught.
   - **What it builds:**
     - The existing booking mini project as a real agent: local or SmallWebRTC transport, Silero VAD, one streaming STT, an LLM that calls tools, and streaming TTS.
     - Per-stage latency timestamps checked against the lesson's EU clinic budget, plus a barge-in test.
     - Smart Turn v3 is already Pipecat's default stop strategy, so the lab tunes it (and Silero's `stop_secs`) rather than adding it.
     - One line on Pipecat Cloud (pipecatcloud 1.4.0) as the hosted deploy path.
     - Pin pipecat-ai 1.15.x.

4. **Optional, medium priority: Inspect AI**
   - **Lesson:** `evals/inspect-agent-evals-lab`.
   - **Why it's only optional:** the verifier narrowed the gap to sandboxed agent evals that grade final environment state. The course already has real eval-library labs: agentevals and openevals in `content/agent-frameworks/langsmith-for-agents.md`, and pydantic_evals in `content/agent-frameworks/pydantic-ai.md`. Inspect is mainly a safety and frontier-lab tool.
   - **What it builds:** a Task/Solver/Scorer eval of a tool-using agent in a Docker sandbox, cross-linked to the existing eval labs. Pin inspect-ai 0.3.287.
   - **Either way:** fix the "capability harness" framing at `content/evals/eval-harness.md:399` and `content/evals/benchmarking.md:297-298`.

## 3. Sections to add to existing lessons

**Training**
- **Tinker**, in `content/training-adaptation/tooling.md`: the hosted-primitives subsection described in section 1.
- **Hosted fine-tuning landscape**, in `content/training-adaptation/fine-tune-vs-rag-vs-prompt-decision.md`: a note dated 2026-11.
  - OpenAI fine-tuning is closed to new users. Existing customers can create jobs until 2027-01-06. gpt-4.1 and gpt-4.1-mini fine-tunes shut down 2027-01-14.
  - Azure Foundry GPT fine-tuning is limited to existing customers (2026-03-31 cutoff), so don't suggest it as the fallback.
  - Live options: Together, Fireworks, Bedrock RFT (qwen3-32b and gpt-oss-20b, since 2026-02-17), and Tinker.
  - Re-anchor the OpenAI parameter examples in `content/training-adaptation/learning-rate.md:36-37` and `content/training-adaptation/epochs.md:35-36`.
  - Grep the 25 lessons that mention a managed or provider fine-tuning API for any that name OpenAI.
- **TRL multi-turn agent RL**, in `content/training-adaptation/grpo.md`:
  - Replace the hedge at line 36 with a version-stamped trl 1.17.1 example using `tools=`, `rollout_func=` and `environment_factory=` with an optional `get_reward()`.
  - Flag that `rollout_func` and `environment_factory` are experimental and that `environment_factory` needs transformers 5.2.0 or later.
  - Add an agent-RL comparison line: TRL, OpenPipe ART, verifiers, verl and OpenRLHF.

**Evals**
- **DeepEval**, in `content/evals/tooling.md`: switch `LLMTestCaseParams` (lines 454 and 461) to `SingleTurnParams`. Add a `ToolCorrectnessMetric` or `TaskCompletionMetric` snippet pinned to deepeval 4.3.x.
- **RAGAS**, in `content/evals/tooling.md`: the version pin, import path and agent metrics described in section 1.
- **Promptfoo**, in `content/evals/tooling.md` and `content/evals/red-teaming.md`:
  - A status line: OpenAI announced its acquisition of Promptfoo on 2026-03-09; closing is unverified, and the CLI stays MIT. Cite promptfoo.dev/blog/promptfoo-joining-openai.
  - In `red-teaming.md`, a small pinned red-team config against a tool-using agent.
- **SWE-bench**, in `content/evals/benchmarking.md`: a contamination case study.
  - OpenAI stopped reporting SWE-bench Verified on 2026-02-23 and recommends SWE-bench Pro.
  - SWE-bench Pro is Scale AI's benchmark. V2 has 642 tasks in Harbor format.
  - Replace Verified as the default comparison benchmark at lines 179, 228, 300 and 401.
- **τ³-bench**, in `content/practice-use-cases/customer-support-agent.md:220` and `content/agent-reliability/agent-trajectory-evaluation.md`: point to sierra-research/tau2-bench, not the frozen tau-bench repo. Never `pip install tau2`; that PyPI name is an unrelated package.

**Observability**
- **Langfuse**, in `content/observability/langfuse.md`:
  - Rewrite both examples with `from langfuse import propagate_attributes` and the `with propagate_attributes(user_id=..., session_id=..., tags=[...])` context manager.
  - Add a version banner: Python SDK 5.x needs Python 3.11+ and server v4.59+; self-hosted v3 servers must stay on SDK v4.
  - Note the observations-first data model, the v4 default span-export filter, and that the JS SDK is now `@langfuse/*` 6.x.
  - Note that ClickHouse acquired Langfuse (2026-01-16) and says there are "no planned changes to licensing".
- **Arize Phoenix**, in `content/observability/arize-phoenix.md`: rewrite the eval write-back path around `phoenix.evals.LLM`, `phoenix.evals.metrics.HallucinationEvaluator(llm=...)`, `evaluate_dataframe`, and `phoenix.client.Client().spans.get_spans_dataframe` / `log_span_annotations_dataframe`. The current code is at lines 361-369, the bullets at 273-284, the diagram at 309-311, the table at 430-434 and the quiz at 607-609. Pin arize-phoenix 21 / evals 3.9.5.
- **OpenTelemetry GenAI conventions**, in `content/observability/opentelemetry.md:291-300`:
  - Point to the new `open-telemetry/semantic-conventions-genai` repo, with no date or version, since the move date is unverified.
  - Describe `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` as an instrumentor-side switch for libraries that still emit older names.

**Frameworks**
- **AutoGen, AG2 and Microsoft Agent Framework**, in `content/agent-frameworks/autogen-ag2.md`:
  - Status as of today:
    - AutoGen is archived and no longer maintained.
    - Microsoft Agent Framework is GA at 1.x (agent-framework 1.24.0) and succeeds both AutoGen and Semantic Kernel.
    - AG2 v1 (2026-07-27) is `import ag2` with Agent/Network. Classic AG2 is published as the PyPI package `autogen` 0.14.1.
  - Move the lab off the frozen autogen-agentchat 0.7.5, to MAF or to pinned AG2 Classic.
  - Fix line 183, the AG2 example at line 274, line 309 and the quiz at line 403.
  - Add MAF to `content/agents/frameworks.md` and `content/agent-frameworks/choosing-an-agent-framework.md`.
- **Vercel AI SDK v7**, in `content/node-ts-for-ai/vercel-ai-sdk.md`:
  - Re-pin from `ai@5` (lines 54-57) to `ai@7`. `MockLanguageModelV2` no longer exists; use V3 or V4.
  - Re-check the step-budget numbers, because v7 usage totals cover all steps.
  - Note: ESM-only, Node 22+, and OTel via @ai-sdk/otel with `registerTelemetry()`. `experimental_telemetry` is a deprecated alias (lines 288 and 427).
  - One line on WorkflowAgent and the Workflow SDK (npm `workflow` 5.4.0).
- **OpenAI Agents SDK**, in `content/agent-frameworks/openai-agents-sdk.md`:
  - Correct lines 207, 389 and 431: the SDK now has `tool_input_guardrail` and `tool_output_guardrail`.
  - Correct line 371 ("no checkpointing or interrupt primitive"): the SDK has `needs_approval`, `RunState` and interruptions. Name the Temporal integration.
  - Add one line on the Agent Builder and hosted Evals wind-down (shutdown 2026-11-30; ChatKit is not part of it).

**Durable execution and queues**
- **Airflow 3**, in `content/system-design/workflow-orchestration.md`: switch the example to `from airflow.sdk import dag, task` (lines 364-369). Replace "SLAs" at line 137 with Deadline Alerts, which were experimental as of 3.1. Mark the version as 3.x.
- **ARQ**, in `content/fastapi-for-ai/job-queues-and-durable-workers.md:296-301,521-522`: note that ARQ is maintenance-only. Name taskiq or Procrastinate as maintained async alternatives.
- **BullMQ 6**, in `content/node-ts-for-ai/bullmq-and-durable-jobs.md`: update the "BullMQ 5" markers at lines 312, 339, 359 and 655. Add `npm i ioredis`, since it is now an optional peer dependency and the lesson imports it at line 414. Note the new Postgres backend.

**RAG data**
- **Provider-native web tools**, in `content/agents/tool-calling.md` (line 121 area): Anthropic `web_search` / `web_fetch_20260318`, OpenAI `web_search` (not the legacy `web_search_preview`), and Gemini Grounding with Google Search. Date-stamp every tool type string and teach the domain and URL restrictions as exfiltration controls.
- **sentence-transformers**, in `content/python-for-ai/sentence-transformers.md`: add MultiVectorEncoder (new in v6) for ColBERT. SparseEncoder already existed in v5.
- **Docling**, in `content/data-architecture/document-processing-pipelines.md` (tier 3) or `content/rag-retrieval/document-parsing.md`: one runnable docling 2.147.0 call to fill the layout-aware tier.

**Serving and gateways**
- **TensorRT-LLM**, in `content/serving-inference/engines.md`:
  - PyTorch is the default backend throughout 1.x. The TensorRT engine path is legacy in stable 1.2.1 and removed in 1.3+ (pre-release).
  - Fix lines 293-301, 502, 510, 540, 557 and the quiz at 632.
  - Triton is now Dynamo-Triton.
- **TGI**, in the same lesson: downgrade to a legacy note (maintenance mode, last release v3.3.7). Remove the decision-tree leaf at line 357, the rule at line 551, and the table and tradeoff rows at 325 and 522.
- **Cloud platforms**, in `content/infra-capacity-economics/cloud-ai-architecture.md`:
  - Bedrock: fix lines 126 and 257. Bedrock issues API keys; short-term keys are recommended for production, and long-term keys can be denied with `bedrock:bearerTokenType`.
  - Add the OpenAI-compatible `/openai/v1` path on bedrock-runtime.
  - Azure: make v1 `OpenAI()` with `base_url .../openai/v1/` the default snippet.
  - Vertex AI is now "Gemini Enterprise Agent Platform, formerly Vertex AI" (2026-04-22). The sub-product renames are unverified, so check them before editing `content/agent-frameworks/google-agent-development-kit.md`.
- **LiteLLM**, in `content/python-for-ai/litellm.md`, cross-linked from `content/system-design/ai-gateway.md`: a supply-chain section on the 2026-03-24 compromise.
  - 1.82.7 carried a payload in `proxy_server.py`; 1.82.8 added a `.pth` file that runs on every Python start.
  - Practices: hash-pinned installs, the pinned official image, and key rotation.
- **Portkey and OpenRouter**, in `content/system-design/ai-gateway.md`:
  - Palo Alto Networks completed its acquisition of Portkey on 2026-05-29.
  - Use "AI Providers / Model Catalog (formerly virtual keys)" and `@provider/model` strings.
  - The OSS gateway has had no release since 2026-01.
  - Add a short "hosted aggregator vs self-hosted gateway" section (OpenRouter).
- **Hosted open-weight providers**, in `content/system-design/self-host-vs-api.md`: expand the existing "Managed open-weight hosting" bullet (lines 174-175) with one table covering Together, Fireworks, Groq, Cerebras and HF Inference Providers. Columns: residency, data retention, quantization disclosure, tool-call parser.
- **Ollama Cloud**, in `content/slm-for-agents/running-slms-locally.md`: add a failure mode, since `:cloud` tags send prompts to Ollama's servers, and the practice of disabling cloud features for private or air-gapped work. Add a qualified MLX note: preview only, supported models, Macs with more than 32GB.
- **Cluster routing**, in `content/infra-capacity-economics/inference-clusters.md`: a section on KV-cache-aware routing naming llm-d 1.0, NVIDIA Dynamo (`ai-dynamo` 1.6.1, not `nvidia-dynamo`) and the Gateway API Inference Extension.

**Guardrails and PII**
- **Presidio**, in `content/safety-security/pii-redaction.md`:
  - A runnable section with `AnalyzerEngine`, a custom `PatternRecognizer` and `AnonymizerEngine`, pinned to 2.2.366, scoring per-entity recall on the existing fixtures. Optionally use GLiNER as the name recognizer.
  - Re-attribute "Microsoft Presidio" as the community project `data-privacy-stack/presidio` at `pii-redaction.md:25,79`, `content/security-governance-compliance/pii-protection-dlp.md:20` and `content/data-architecture/data-retention-and-deletion-architecture.md:372`.
- **Prompt Guard 2 and LlamaFirewall**, in `content/safety-security/prompt-injection.md`: screen tool outputs with a classifier as a detection layer, not a boundary. Mention LlamaFirewall's AlignmentCheck; its last release was 2025-05.

**Memory, tools, computer use**
- **Mem0 2.x**, in `content/agent-frameworks/agent-memory-libraries.md`:
  - Entity IDs go in `filters=` for `search` and `get_all` (lines 209-211, 265, 278, 315). `add()` still accepts a top-level `user_id`.
  - `limit` is now `top_k` (default 20; threshold 0.1).
  - `add()` is ADD-only, so fix lines 205-207, 366-372, 377-378 and the quiz at 407-413.
  - Graph memory is Platform-only (lines 213-214).
  - Stamp "mem0ai 2.2.x Python / 3.x npm".
- **Letta**, in the same lesson (lines 35-36, 217-224, 281-302, 322):
  - The V1 server is retired and `letta-client` is deprecated. `pip install letta` now installs the Letta Code CLI.
  - Keep the MemGPT concepts. Replace the code with a pinned sketch of `@letta-ai/letta-agent-sdk` 0.8.x (pre-1.0) or drop it.
- **Anthropic toolsets**, in `content/agents/computer-use.md` (lines 98-103) and a row in `content/practice-use-cases/browser-and-computer-use-agent.md:135`: `computer_toolset_20260801` and `browser_toolset_20260801`, with `read_page` refs. `computer_20251124` returns 400 for Claude 5.5+ on the Claude API, but Bedrock still accepts it.
- **Managed integration platforms**, in `content/mcp/mcp-ecosystem.md`: a build-vs-buy section on Composio (1,500+ apps; sessions are the recommended entry point), with rows for Arcade, Zapier MCP and Pipedream (Workday acquisition announced, not confirmed closed).

**Voice**
- **Speech-to-speech in practice**, in `content/practice-use-cases/voice-agent.md`:
  - A RealtimeAgent with one tool, marked "openai-agents 0.24, gpt-realtime-2.1, checked 2026-11". The Python SDK uses WebSocket or SIP; browser WebRTC is not in it.
  - Fix the "Two architectures" rows at lines 63 and 127-128: tool calls and transcripts are now standard, and residency and DPA terms are the real differentiator.
  - Name Smart Turn v3 and the LiveKit turn-detector at lines 94, 136, 150 and 270.
- **Speech-to-text**, in `content/multimodal/speech-to-text.md`:
  - Pin faster-whisper 1.2.x.
  - Add rows to the model table for large-v3-turbo (MIT) and Parakeet-TDT-0.6B-v3 (CC-BY-4.0).
  - In "Streaming is not native", name dated examples: GPT-Realtime-Whisper, Deepgram Flux and Parakeet.

## 4. Mention-only additions (one line each)

**Training**
- OpenPipe ART (agent RL; CoreWeave acquired OpenPipe) in `grpo.md`.
- verifiers 0.4.1 (RL environments) in `grpo.md`.
- verl and OpenRLHF only, in `grpo.md` and `rlhf.md`. Leave out SkyRL and NeMo RL, whose activity couldn't be confirmed.
- Together and Fireworks as the hosted fine-tuning row.
- Bedrock, Vertex and Azure Foundry tuning, dated, in the client-data options table.
- Trackio in `experiment-tracking.md`.
- distilabel or Data Designer in `synthetic-data-generation.md`.
- ms-swift as the replacement for torchtune in the "similar frameworks" line.
- W&B is owned by CoreWeave (closed 2025-05-05).

**Evals**
- OpenAI Evals API and trace grading, clarified as distinct from the legacy openai/evals repo.
- Terminal-Bench 2.0 / Harbor.
- Name agentevals and openevals at `tooling.md:697` and cross-link the existing lab.
- Opik.
- MLflow GenAI evaluation (API names unverified).
- BFCL.
- Azure AI Evaluation SDK.
- Pydantic Evals: inventory it and check its version.

**Observability**
- Datadog LLM Observability ingests OTel GenAI semantic conventions v1.37+ (`opentelemetry.md:227`).
- MLflow 3 tracing (`@mlflow.trace`, autolog) at `experiment-tracking.md:35,183`.
- OpenLLMetry: Traceloop joined ServiceNow; OpenLLMetry stays open source.
- Opik in the platform table.
- Sentry agent monitoring at `openai-agents-sdk.md:353`. Drop the "2.41.0+" claim and verify the PII default before teaching it.
- Bedrock AgentCore Observability.

**Frameworks**
- A low-code row in `choosing-an-agent-framework.md`: n8n ($5.2B valuation with SAP's investment), Dify, Flowise (Workday) and Langflow (DataStax/IBM).
- Strands Agents and Bedrock AgentCore in the vendor-SDK row.
- Haystack next to LlamaIndex.
- smolagents as the code-agent reference implementation.
- BAML in `output-validation.md`.
- Guidance / llguidance next to XGrammar.
- LangChain Deep Agents in `langchain.md`.
- Vercel Workflow SDK.

**Durable execution**
- One engine comparison table in `reliability/durable-workflow-execution.md` covering:
  - Restate.
  - DBOS, the "just Postgres" option closest to the toy lab.
  - AWS Lambda durable functions (fix option D at lines 63 and 198).
  - Inngest.
  - Vercel Workflows (GA April 2026).
  - Cloudflare Workflows (GA 2025-04-07).
  - Dapr Agents (GA 2026-03-23).
  - MAF Durable Task extension (beta).
- Hatchet in the background-jobs queue table.
- Trigger.dev in the BullMQ "what it is not" paragraph.
- A Prefect version marker.

**RAG**
- Firecrawl and Crawl4AI (trafilatura is the default).
- Cohere rerank-v5.0-pro and rerank-v5.0-fast.
- Voyage AI (MongoDB, 2025-02-17) and Jina AI (Elastic, 2025-10-09), dated.
- Turbopuffer.
- Amazon S3 Vectors (GA 2025-12-02).
- ParadeDB pg_search v0.26.5 in `bm25-internals.md:38` and hybrid-search.
- Mistral OCR 4.1, Reducto, and LlamaParse via the `llama-cloud` package.
- Marker, MinerU and MarkItDown.
- Search providers: Perplexity, Brave (no verified official SDK), Parallel and Linkup.
- RedisVL.
- neo4j-graphrag.
- FastEmbed.
- pgvectorscale 0.9.1.
- ColPali.
- MongoDB Atlas Vector Search.
- bm25s as the maintained replacement for rank_bm25.

**Serving**
- Agent Router (formerly Envoy AI Gateway) at `ai-gateway.md:192`.
- Vercel AI Gateway.
- LM Studio and MLX in `running-slms-locally.md`.
- Baseten in `serverless-gpus.md`.
- vLLM production-stack and semantic-router.
- NVIDIA NIM (current status unverified).
- Gemini Developer API: check whether its absence is a provider-coverage gap.

**Guardrails**
- Guardrails AI: correct the row at `nemo-guardrails.md:307` (Input/Output Guards, callable from NeMo).
- gpt-oss-safeguard.
- Qwen3Guard (including the Stream variant) and Granite Guardian 4.1.
- Bedrock Guardrails, Azure Prompt Shields (GA) and Google Model Armor.
- Version stamps for garak 0.17.1 and PyRIT 1.1.0 (microsoft/PyRIT).
- Lakera, acquired by Check Point (closed).
- Snyk Agent Scan, formerly mcp-scan.
- any-guardrail.

**Tools and memory**
- Arcade, Zapier MCP and Pipedream.
- Provider-hosted code execution (Anthropic `code_execution_20260521` / `20260120`) plus server-side web tools.
- Daytona: hosted and closed-source since June 2026; the OSS repo is archived at v0.190.0 (AGPL). The package is now `daytona`, not `daytona-sdk`.
- AWS AgentCore in the sandbox and memory tables.
- Browser Use.
- Stagehand / Browserbase.
- The Anthropic memory tool `memory_20250818`.
- Zep Cloud or Graphiti: cite the getzep/zep README; the deprecation URL returns 404.
- Docker MCP Gateway at `mcp-security-in-practice.md:477`.
- Cloudflare Sandbox (GA) and Vercel Sandbox (GA).

**Voice**
- LiveKit Agents 1.8.10 in a framework comparison table.
- Deepgram Nova-3 and Flux. Don't claim Flux is multilingual.
- Option D, managed platforms: Vapi, Retell and ElevenAgents, with residency and DPA as the deciding column.
- Gemini Live: `gemini-3.1-flash-live-preview`, and the Vertex 2.5 native-audio model retiring 2026-12-13.
- Azure Voice Live and Amazon Nova Sonic for in-region option B.
- A dated TTS engines table in `tts.md`: ElevenLabs, Cartesia, and Kokoro (open weights; packaging last released 2025-04). Exclude OpenAI tts-1, tts-1-hd and gpt-4o-mini-tts (shutdown 2027-01-06).
- Voice eval platforms as a category only, with no vendor names, since their status is unverified.

## 5. Accuracy fixes confirmed by the verifiers

Fixes already listed with line numbers in section 3 are not repeated here. Notes on which fixes were dropped are at the end.

**Training**
- `content/training-adaptation/tooling.md:307` and `rlhf.md:315`: PPOTrainer was deleted in trl 1.13.0. Keep RewardTrainer and point PPO to OpenRLHF or verl, or tell learners to pin trl<1.13.
- `tooling.md:300`: replace the hedge with the specifics. trl 1.17.1 exports `supports_tool_calling` and reads a per-row `tools` field.
- `tooling.md:371`: drop torchtune or mark it "no longer actively developed".
- Course-wide `torch_dtype=`, which only causes a deprecation warning:
  - `training-adaptation/tooling.md:441`, `distributed-training.md:275`, `supervised-fine-tuning.md:185`
  - `model-internals/kv-cache.md:157`, `perplexity.md:231`
  - `serving-inference/gpu-memory-optimization.md:254`, `cpu-offloading.md:246`, `structured-constrained-generation.md:312`, `quantization-quality-evaluation.md:229`
  - `system-design/open-weight-vs-proprietary.md:267`, `math-for-ai/numerical-precision.md:202`
  - Change to `dtype=`.
- Add "checked against" stamps to `tooling.md` (trl 1.17.1, peft 0.21.2, transformers 5.22.1, axolotl 0.21.1, unsloth 2026.11.2). The TRL code in `mini-project-ideas/small-fine-tune-for-tool-call-formatting.md:311` and `portfolio-projects/self-hosted-slm-agent-with-fine-tuning-and-routing.md:220` needs the same pass.

**Evals**
- `content/evals/model-evaluation.md:40` and the mini-project at lines 21, 182 and 293: say which OpenAI Evals product is meant.
- `content/evals/tooling.md:497`: change the `create_react_agent` comment to `create_agent`.

**Observability**
- `content/observability/langfuse.md:313`: the "v3 SDK" note is stale (current is 5.1.0).

**Serving**
- `content/serving-inference/engines.md:373`, `prefix-caching.md:36,166`: prefix caching is on by default; `--no-enable-prefix-caching` turns it off.
- `engines.md:375`: start `ollama serve` before `ollama pull`.
- `content/slm-for-agents/running-slms-locally.md:233`: `--jinja` is the default in current llama.cpp builds.
- `engines.md:56`: add the vLLM version (0.33.x).

**RAG**
- `content/rag-retrieval/vector-databases.md:297`: Pinecone pod indexes are legacy; serverless is the only option for new customers.
- `vector-databases.md:374`: update to pgvector 0.8.7. Line 395: update to Qdrant server 1.20.0.
- `content/rag-retrieval/inverted-indexes.md:237`: the Elasticsearch client is 9.x.
- `content/rag-retrieval/hybrid-search.md:38,303`, `dense-vs-sparse-retrieval.md:40`, `bm25-internals.md:38`: EnsembleRetriever is in `langchain-classic`; BM25Retriever is in `langchain-community`.
- `content/rag-retrieval/reranking.md:221`: the stamp says 5.1; re-run the snippet on sentence-transformers 6.2.0.
- `content/data-architecture/document-processing-pipelines.md:221,237`, `content/multimodal/document-understanding.md:210,224`: change `import fitz` to `import pymupdf`. PyMuPDF 2.x requires Python 3.11+.

**Guardrails**
- `content/safety-security/nemo-guardrails.md:398,400`: the call-count section is stamped "0.17.0" but the lab pins 0.24.1. Re-check it or label it as an older-version result.
- `content/safety-security/llama-guard.md:385`: re-verify on transformers 5.22.1.
- `llama-guard.md:317`: add a version to Granite Guardian.
- `llama-guard.md:318`: Prompt Guard 2 is a binary benign/malicious classifier with no injection label.

**Tools**
- Add version stamps to the E2B snippet (e2b-code-interpreter 2.x) and the Modal snippet (modal 1.x) in `content/agent-frameworks/code-execution-sandboxes-for-agents.md`.

**Dropped because a verifier refuted them**
- The Unsloth multi-GPU wording is accurate.
- The Pydantic AI durable-execution gap is minor: the lesson already mentions durable integrations, it just doesn't name the engines.
- The NeMo Guardrails repo move needs no edit, because no lesson links the old path.
- The MCP spec "2026-10-30" is page metadata, not a spec version.
- The hosted open-weight option is not missing from `self-host-vs-api.md`; only the names are.

## 6. Tools deliberately skipped

- **Predibase:** acquired by Rubrik; no longer an independent developer platform.
- **HF AutoTrain:** last release 2025-01, and it hides the decisions the course teaches.
- **Argilla and Label Studio (beyond the existing mentions):** labelling is rightly taught tool-agnostically; prefer Label Studio as the named example.
- **Braintrust:** already covered at the right depth.
- **TruLens (Snowflake), Giskard, Patronus, DeepTeam:** they duplicate RAGAS, DeepEval or Promptfoo.
- **Galileo:** Cisco completed the acquisition 2026-05-22; it is now Splunk Agent Observability.
- **HELM and lm-eval:** current coverage is correct and enough.
- **Logfire, W&B Weave, Braintrust tracing:** already mentioned correctly.
- **Helicone:** in maintenance mode after the Mintlify acquisition and now announced as separated from Mintlify; its future is unclear.
- **AgentOps:** no release since 2025-08.
- **Laminar, OpenLIT:** not category leaders; same OTel concepts.
- **Agno, Marvin, BeeAI, VoltAgent, Mirascope, CAMEL:** niche; would only grow the course.
- **Prefect (beyond a version marker), Dagster, Absurd, pgtask, Azure Durable Functions, Temporal Nexus:** data-team or niche tools.
- **Tika, RAGatouille, Jina Reader:** RAGatouille is stale, Tika is JVM-based, and Jina Reader's status is unverified.
- **Bifrost, any-llm, agentgateway:** same concept as LiteLLM.
- **openai-guardrails:** young and tied to one vendor.
- **LLM Guard, Rebuff:** both archived.
- **Playwright MCP extras, Vercel agent-browser, Steel:** overlap with tools already listed.
- **Cognee, Supermemory, LangMem:** not category leaders. LangMem is pre-1.0 with no release since 2025-10.
- **AssemblyAI, Speechmatics, Soniox, Moshi, Ultravox, Hume EVI:** interchangeable with Deepgram, or research-stage.

## Unverified items

- Whether the Promptfoo acquisition and the Pipedream acquisition have closed.
- The Vertex sub-product name mapping.
- The exact date the OpenTelemetry GenAI conventions moved repos.
- Model Armor's core GA status.
- Current NVIDIA NIM packaging.
- MLflow GenAI eval API names.
- GitHub archive and push dates for many repos (the GitHub API was rate-limited; registry release dates were used instead).