#!/usr/bin/env python3
"""Generate taxonomy.json (modules -> topics with stable ids) and chunks.json
(work units for content generation) from the AI Roles Skills Taxonomy."""
import json, re, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"\(.*?\)", "", s)                 # drop parenthetical lists
    s = s.replace("&", " and ").replace("+", " plus ")
    s = s.replace("→", " to ").replace("–", "-").replace("—", "-").replace("’", "")
    s = s.replace("/", " ").replace(".", " ")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    s = re.sub(r"-+", "-", s)
    return s

# (module id, tier, title, [topics])  — topic may be str or (str, hint)
TAXONOMY = [
 # ---------------- TIER 1 ----------------
 ("ml-fundamentals", 1, "ML fundamentals", [
  "supervised vs unsupervised vs self-supervised learning","classification","regression","clustering",
  "loss functions (cross-entropy, contrastive)","gradient descent","backpropagation","optimizers (Adam, AdamW)",
  "learning-rate schedulers","warmup","gradient clipping","batch size","gradient accumulation","mixed precision",
  "normalization (LayerNorm, RMSNorm)","residual connections","dropout / regularization","train/test leakage",
  "class imbalance","calibration","precision / recall / F1","ROC-AUC","confusion matrix"]),
 ("model-internals", 1, "Model internals", [
  "transformer","attention mechanism","self-attention","multi-head attention","grouped-query attention (GQA)",
  "FlashAttention","positional encoding (RoPE)","tokenization (BPE, SentencePiece)","embeddings",
  "LLM architecture (encoder–decoder vs decoder-only)","context window","KV cache","logits","softmax",
  "sampling strategies (temperature, top-k / top-p, greedy vs beam)","speculative decoding","mixture of experts (MoE)",
  "small language models (SLMs)","scaling laws","perplexity","hallucination","reasoning models / test-time compute"]),
 ("training-adaptation", 1, "Training & adaptation", [
  "pre-training","post-training","supervised fine-tuning (SFT)","instruction tuning","RLHF","DPO","GRPO",
  "PEFT (LoRA, QLoRA)","knowledge distillation","quantization (INT8/INT4, GPTQ, AWQ, GGUF)","pruning","model merging",
  "data curation","synthetic data generation","data labeling","train/val/test split","epochs","learning rate",
  "overfitting","catastrophic forgetting","checkpointing","gradient checkpointing","fine-tune vs RAG vs prompt decision",
  ("tooling (Unsloth, Axolotl, TRL)", "tool"),
  ("distributed training (data / tensor / pipeline parallelism, FSDP, DeepSpeed)", "caveat: only if the JD is training-heavy; drop for LLM-app roles")]),
 ("prompting", 1, "Prompting", [
  "prompt engineering","system prompt","zero-shot","few-shot","chain-of-thought","ReAct","prompt templates",
  "prompt versioning","prompt caching","structured outputs / JSON mode","function / tool calling","context engineering",
  "prompt injection","jailbreaks"]),
 ("rag-retrieval", 1, "RAG & retrieval", [
  "chunking strategies (fixed, recursive, semantic)","chunk overlap","retrieval granularity","embedding models",
  "embedding dimension & normalization","embedding pipelines",
  ("vector databases (pgvector, Qdrant, Pinecone, Weaviate, Milvus, Chroma)", "tool"),
  "similarity search (cosine, dot product)","ANN indexes (HNSW, IVF)","dense vs sparse retrieval","BM25 internals",
  "inverted indexes","hybrid search (BM25 + dense)","reranking (cross-encoder)","recall vs precision trade-off",
  "query rewriting","query decomposition","multi-hop retrieval","HyDE","parent-document retrieval","contextual retrieval",
  "metadata filtering","ACL-aware retrieval","lost-in-the-middle","citations / grounding","citation correctness",
  "GraphRAG","knowledge graphs","freshness / document deletion / index consistency","retrieval failure diagnosis",
  "long-context vs RAG","agentic RAG","document parsing (OCR, layout, tables)"]),
 ("agents", 1, "Agents", [
  "agent loop","tool calling","tool schemas & argument validation","typed tool contracts","tool selection accuracy",
  "tool-result interpretation","parallel tool calls","Model Context Protocol (MCP)",
  "MCP authentication / authorization / transport security","A2A protocol","planning","task decomposition",
  "reflection / self-critique","multi-agent patterns (orchestrator-worker, handoffs)",
  "agent memory (short-term, long-term, episodic)","memory retrieval / update / deletion policies",
  "context compaction / summarization","state machines","human-in-the-loop","approval gates for consequential actions",
  "sandboxing","computer use","workflows vs agents",
  ("frameworks (LangGraph, LangChain, LlamaIndex, Pydantic AI, DSPy)", "tool")]),
 ("agent-reliability", 1, "Agent reliability & runtime", [
  "stopping conditions / termination","max-step budgets","loop detection","durable execution & checkpointing",
  "state persistence & conversation recovery","state & checkpoint stores","conversation / execution identifiers",
  "idempotent tool execution","tool permissions & least privilege","tool adapters / service interfaces",
  "structured event streams","audit logs / execution traces","deterministic replay & reproducibility",
  "agent trajectory evaluation","task completion rate","tool-call success rate"]),
 ("evals", 1, "Evals", [
  "model evaluation","golden dataset (eval dataset)","eval harness","task-specific acceptance criteria",
  "benchmarking (MMLU, HumanEval, SWE-bench, GPQA)","dataset contamination","LLM-as-a-judge","judge calibration & bias",
  "rubrics","pairwise comparison","inter-annotator agreement","pass@k","retrieval evaluation (recall@k, MRR, NDCG)",
  "faithfulness / groundedness","hallucination evaluation","regression tests","metamorphic testing",
  "property-based testing","adversarial evaluation","offline vs online evaluation","A/B testing","error analysis",
  "red teaming","eval-driven development",
  ("tooling (Promptfoo, RAGAS, DeepEval, Braintrust, LangSmith, Langfuse)", "tool")]),
 ("serving-inference", 1, "Serving & inference", [
  "model serving","inference optimization","prefill vs decode","KV-cache memory estimation","KV-cache paging & eviction",
  "prefix caching","continuous batching","PagedAttention","batching & concurrency limits",
  "tensor parallelism at inference","CPU offloading","quantization quality evaluation",
  "structured / constrained generation","GPU memory optimization (VRAM math)","streaming (SSE)","TTFT","TPOT",
  "tokens/sec","throughput vs latency","timeouts","cancellation","backpressure","fallbacks","retry strategies",
  "rate limiting","idempotency","caching (semantic, prompt)",
  ("engines (vLLM, SGLang, TGI, TensorRT-LLM, Ollama, llama.cpp)", "tool")]),
 ("observability", 1, "Observability", [
  "tracing","spans","distributed tracing across services","logging","monitoring","token / cost tracking",
  "latency percentiles (p50/p95/p99)",("OpenTelemetry","tool"),("Langfuse","tool"),("LangSmith","tool"),
  ("Arize Phoenix","tool")]),
 ("safety-security", 1, "Safety & security", [
  "input / output guardrails","output validation (Pydantic, Instructor, Outlines)","PII redaction","content moderation",
  "prompt injection (direct, indirect)","RAG poisoning","excessive agency","tool privilege escalation",
  "cross-tenant data leakage","data leakage","supply-chain security for models & datasets","OWASP Top 10 for LLMs",
  ("Llama Guard","tool"),("NeMo Guardrails","tool")]),
 ("multimodal", 1, "Multimodal", [
  "vision-language models","CLIP","image embeddings","OCR","speech-to-text (Whisper)","TTS","document understanding"]),
 ("backend-fundamentals", 1, "Backend fundamentals", [
  ("Python","orientation topic: Python as the AI backend language; deep dive lives in the python-for-ai module"),
  ("FastAPI","orientation topic: why FastAPI for AI services; deep dive lives in the fastapi-for-ai module"),
  ("Node.js / TypeScript","orientation topic: where Node/TS fits in AI systems; deep dive lives in node-ts-for-ai"),
  "REST / GraphQL API design","API & SDK integration","API versioning & backward compatibility",
  "pagination / rate limits / API quotas","webhook signature verification & replay protection","database design","SQL",
  "NoSQL","Redis","PostgreSQL transactions & isolation levels","message queues (Kafka, SQS, RabbitMQ)",
  "background jobs / durable workers","authentication vs authorization","OAuth 2.0 / OIDC","RBAC","secrets management",
  "Docker","Kubernetes","CI/CD"]),
 ("python-for-ai", 1, "Python for AI", [
  "type hints","Protocols / generics / structural typing","TypedDict / Literal / Enum",("Pydantic","tool"),"dataclasses",
  "object model & mutability","exceptions & custom exception hierarchies","async / await","asyncio",
  "async cancellation & exception propagation","race conditions & synchronization primitives (asyncio.Lock, Semaphore)",
  "asyncio vs threads vs multiprocessing","GIL","generators","decorators","context managers / resource cleanup",
  "dependency injection & interface boundaries","package structure & import design",("tenacity","tool"),
  "logging / structlog","memory & performance profiling",("uv / poetry / pip","tool"),"lockfiles & dependency pinning",
  "security scanning & dependency auditing (pip-audit, Bandit)",("pytest","tool"),("pytest-asyncio","tool"),
  "test doubles / mocks / fixtures","property-based testing (Hypothesis)","contract testing",
  "integration & end-to-end testing",("mypy","tool"),("ruff","tool"),("python-dotenv","tool"),("pydantic-settings","tool"),
  "JSON / JSONL",("numpy","tool"),("pandas","tool"),("polars","tool"),("pyarrow","tool"),"scikit-learn basics",
  "PyTorch (tensors, autograd, nn.Module, DataLoader, device)",
  "Hugging Face (transformers, datasets, tokenizers, accelerate, PEFT, TRL)",("sentence-transformers","tool"),
  ("tiktoken","tool"),("safetensors","tool"),("OpenAI SDK","tool"),("Anthropic SDK","tool"),("LiteLLM","tool"),
  ("Instructor","tool"),("Outlines","tool"),("MCP Python SDK","tool"),("httpx","tool"),("Jupyter","tool")]),
 ("fastapi-for-ai", 1, "FastAPI for AI", [
  "ASGI lifecycle","middleware ordering","async endpoints","Pydantic request / response models","response_model",
  "dependency injection (Depends)","request-scoped vs application-scoped resources","lifespan (load model / client once)",
  "StreamingResponse","Server-Sent Events","WebSockets","BackgroundTasks",
  "job queues & durable workers (Celery / ARQ + job-status endpoint)","async database access & connection pooling",
  "SQLAlchemy / SQLModel","Alembic migrations","timeouts","idempotency keys","CORS","API keys / OAuth2 / JWT",
  "rate limiting (slowapi)","request-size limits & upload security","UploadFile","HTTPException handling","routers",
  "OpenAPI docs","OpenAPI contract testing","uvicorn / gunicorn workers","graceful shutdown & worker draining",
  "health / readiness endpoints","structured logging","OpenTelemetry instrumentation","Redis caching",
  "shared httpx AsyncClient","TestClient + pytest-asyncio","integration tests with real dependencies",
  "concurrency limits (semaphores)","backpressure","cancellation on client disconnect"]),
 ("node-ts-for-ai", 1, "Node.js / TypeScript for AI", [
  "TypeScript strict mode & advanced types","event loop & microtask queue","promises & async error propagation",
  "streams & backpressure","worker threads for CPU-bound tasks","AbortController & cancellation",
  "runtime schema validation (Zod)","dependency injection","PostgreSQL transactions & migrations","Redis",
  "BullMQ & durable jobs","API clients with retries & circuit breakers","OpenTelemetry instrumentation",
  "graceful shutdown","Vitest / Jest & integration testing","Docker / CI/CD deployment",("Vercel AI SDK","tool"),
  ("OpenAI / Anthropic Node SDKs","tool"),("MCP TypeScript SDK","tool")]),
 # ---------------- TIER 2 ----------------
 ("system-design", 2, "System design", [
  "AI / solution architecture","reference architectures","model selection","model portfolio","SLM vs LLM trade-offs",
  "build vs buy","open-weight vs proprietary","self-host vs API","model routing / cascading","multi-model architecture",
  "AI gateway (LiteLLM proxy, Portkey)","model fallback strategies","graceful degradation",
  "caching strategy & cache invalidation","RAG architecture","agentic architecture","multi-agent systems",
  "agent orchestration","workflow orchestration (Temporal, Prefect, Airflow)","memory architecture",
  "domain-driven design & bounded contexts","event-driven architecture","synchronous vs asynchronous execution",
  "saga pattern & distributed transactions","outbox / inbox patterns","exactly-once processing limitations",
  "distributed systems","data consistency","modular monolith vs microservices","multi-tenancy","tenant isolation",
  "C4 diagrams","ADRs","NFRs","trade-off analysis"]),
 ("reliability", 2, "Reliability", [
  "high availability","fault tolerance","multi-region architecture","disaster recovery (RPO / RTO)","business continuity",
  "durable workflow execution","backpressure & load shedding","tenant-level quotas & noisy-neighbor isolation",
  "circuit breakers","bulkheads","FMEA","chaos engineering & resilience testing","SLAs / SLOs"]),
 ("agent-runtime-architecture", 2, "Agent runtime architecture", [
  "agent runtime & orchestration layer","approval & authorization boundaries","policy enforcement across agents & tools",
  "multi-tenant state isolation","secrets & credential isolation","cross-language API contracts"]),
 ("data-architecture", 2, "Data architecture", [
  "ingestion pipelines","ETL / ELT for RAG","training data pipelines","lakehouse","document processing pipelines",
  "vector database architecture","vector store sizing","embedding versioning / re-indexing strategy",
  "knowledge management","data lineage","data contracts","model & dataset provenance",
  "data retention & deletion architecture",
  ("feature stores (hybrid ML only)","caveat: hybrid ML only; drop for LLM-app roles")]),
 ("infra-capacity-economics", 2, "Infra, capacity & economics", [
  "GPU infrastructure","GPU sizing (VRAM math)","GPU utilization & memory bandwidth","A100 / H100 / L4 selection",
  "serverless GPUs","KEDA autoscaling","Ray / KubeRay","inference clusters",
  "shared vs isolated model-serving infrastructure","cloud AI architecture (AWS Bedrock, Azure OpenAI, Vertex AI)",
  "API provider quotas & regional limits","hybrid / on-prem","edge AI deployment",
  "capacity planning (tokens/sec, requests/sec, concurrent sessions)","queueing theory","latency budgets","scalability",
  "cost optimization","cost modeling (cost per request, token economics)","unit economics per successful task",
  "cost allocation by tenant & feature","quality–latency–cost optimization",
  "break-even analysis (hosted API vs self-hosting)","FinOps","TCO"]),
 ("llmops-platform", 2, "LLMOps & AI platform", [
  "CI/CD for prompts and models","prompt registry","model registry","experiment tracking (MLflow, W&B)",
  "evaluation frameworks","golden dataset strategy","reproducible evaluations",
  "evaluation gates for production releases","canary / shadow deployments","rollback","model monitoring","drift",
  "model lifecycle & deprecation policy","observability stack","AI control plane vs data plane",
  "centralized vs federated AI platform"]),
 ("security-governance-compliance", 2, "Security, governance & compliance", [
  "AI security","threat modeling","prompt injection defense","PII protection / DLP","data classification","zero trust",
  "data residency","model governance","model cards","audit trails","AI inventory & use-case risk classification",
  "responsible AI","AI risk management","AI incident response","vendor exit strategy",
  "compliance architecture (GDPR, SOC 2, HIPAA, ISO 42001, EU AI Act, DPDP Act)"]),
 ("strategy", 2, "Strategy", [
  "AI roadmap","platform strategy","vendor evaluation","technical due diligence","stakeholder alignment"]),
 # ---------------- TIER 3 ----------------
 ("scoping", 3, "Scoping", [
  "customer discovery","AI use-case discovery","problem decomposition","workflow analysis","domain modeling",
  "data access & permissions discovery","success metrics / KPIs","baseline measurement before deployment","ROI model",
  "statement of work","requirements traceability","dependency & risk management","timeboxing","demos",
  "rapid prototyping / PoC","pilot → production plan","productionization"]),
 ("enterprise-integration", 3, "Enterprise integration", [
  "enterprise connectors (Salesforce, SAP, ServiceNow, SharePoint, Confluence, Jira, Slack, Teams)",
  "legacy system integration","SSO / IAM integration (Okta, Entra ID)","SAML SSO","SCIM provisioning",
  "RBAC / ABAC mapping to customer identity","webhooks","incremental synchronization & CDC",
  "retry / deduplication / reconciliation","sandbox vs production environments",
  "tenant-specific configuration & feature flags","data onboarding","data ingestion from client systems",
  "data mapping & canonical schemas (schema mapping)","data profiling & quality assessment",
  "client document parsing (PDF, scans, OCR)"]),
 ("customer-data-evaluation", 3, "Customer data & evaluation", [
  "domain prompt engineering","golden dataset creation with client SMEs","representative test-case sampling",
  "SME review & annotation guidelines","labeling sessions","eval review loops",
  "acceptance testing against customer KPIs","customer-specific regression suites","shadow-mode validation",
  "per-customer guardrails","fine-tuning pipelines on client data","model integration",
  "production feedback classification","feedback loops to product"]),
 ("deployment", 3, "Deployment", [
  "customer VPC / on-prem / air-gapped","private endpoints","VPN","Terraform (IaC)","security questionnaires",
  "security & architecture reviews","compliance constraints","deployment readiness reviews","UAT",
  "rollout criteria & rollback triggers","runbooks","health checks","hypercare"]),
 ("delivery-ownership", 3, "Delivery & ownership", [
  "production debugging","failure analysis","incident severity & escalation","root-cause analysis & postmortems",
  "operational ownership & support boundaries","SLAs","system reliability","technical documentation",
  "productizing repeated customer solutions"]),
 ("adoption", 3, "Adoption", [
  "change management","user training","workshops","stakeholder management","exec communication","usage analytics",
  "adoption & value-realization measurement","continuous improvement","handoff to customer teams",
  "feature-request triage"]),
 ("agent-frameworks", 1, "Agent frameworks & tooling", [
  ("choosing an agent framework (build on SDKs vs frameworks)", "decision: how to pick between raw SDKs, LangGraph, LlamaIndex, OpenAI/Claude agent SDKs, CrewAI, Pydantic AI"),
  ("LangChain (LCEL, chat models, tools, retrievers)", "tool"),
  ("LangGraph fundamentals (StateGraph, nodes, edges, conditional routing)", "tool"),
  ("LangGraph persistence, streaming and human-in-the-loop (checkpointers, interrupts)", "tool"),
  ("LangGraph multi-agent patterns (supervisor, handoffs, subgraphs)", "tool"),
  ("LangSmith for agents (tracing, datasets, evaluators, prompt hub)", "tool"),
  ("LlamaIndex (indexes, query engines, agents, workflows)", "tool"),
  ("OpenAI Agents SDK (agents, tools, handoffs, guardrails, tracing)", "tool"),
  ("Claude Agent SDK (agent loop, tools, MCP, subagents)", "tool"),
  ("Pydantic AI (typed agents, dependencies, structured results)", "tool"),
  ("CrewAI (crews, roles, tasks, flows)", "tool"),
  ("AutoGen / AG2 (conversational multi-agent)", "tool"),
  ("Google Agent Development Kit (ADK)", "tool"),
  ("DSPy (signatures, modules, optimizers)", "tool"),
  ("Mastra (TypeScript agents and workflows)", "tool"),
  ("agent memory libraries (Mem0, Letta)", "tool"),
  ("code-execution sandboxes for agents (E2B, Modal, Docker)", "tool"),
  ("end-to-end agent stack (framework, LLM gateway, vector store, tracing, evals, deployment)", "decision: a reference stack and how the pieces connect"),
 ]),
 ("mcp", 1, "Model Context Protocol (MCP) in depth", [
  ("MCP architecture (hosts, clients, servers, JSON-RPC lifecycle, capability negotiation)", ""),
  ("MCP server primitives (tools, resources, prompts)", ""),
  ("MCP client primitives (sampling, roots, elicitation)", ""),
  ("MCP transports (stdio, Streamable HTTP, legacy SSE)", ""),
  ("building an MCP server end to end (FastMCP in Python, TypeScript SDK)", "tool"),
  ("designing MCP tools for agents (naming, schemas, errors, pagination, token-efficient results)", ""),
  ("using MCP servers inside agents (Claude, OpenAI Agents SDK, LangGraph adapters)", "tool"),
  ("testing and debugging MCP servers (MCP Inspector, contract tests)", "tool"),
  ("deploying remote MCP servers (OAuth, sessions, hosting, scaling)", ""),
  ("MCP security in practice (tool poisoning, injection through tool results, consent, least privilege)", ""),
  ("MCP ecosystem (registries, popular servers, when not to use MCP)", "decision: when MCP beats direct tool calling and when it does not"),
 ]),
 ("practice-use-cases", 1, "Agent use cases to practice", [
  ("customer support agent (triage, retrieval, refunds with approval)", "usecase"),
  ("coding agent (repo navigation, edits, tests in a sandbox)", "usecase"),
  ("deep research agent (search, read, cite, synthesise)", "usecase"),
  ("data analyst agent (text-to-SQL, charts, guarded queries)", "usecase"),
  ("document processing agent (extraction, validation, human review)", "usecase"),
  ("sales and CRM agent (lead research, enrichment, email drafts)", "usecase"),
  ("IT and SRE ops agent (alerts, runbooks, safe remediation)", "usecase"),
  ("voice agent (speech in, tools, speech out, latency budget)", "usecase"),
  ("browser and computer-use agent (forms, web tasks, safety)", "usecase"),
  ("internal knowledge assistant (enterprise RAG with ACLs)", "usecase"),
  ("contract and compliance review agent (clauses, risk flags, citations)", "usecase"),
  ("multi-agent workflow (planner, specialists, reviewer)", "usecase"),
 ]),
 ("mini-project-ideas", 1, "Mini project ideas", [
  ("tool-calling CLI assistant with three real tools", "project"),
  ("RAG over your own notes with citations and an eval set", "project"),
  ("MCP server for a personal API (calendar, GitHub or files)", "project"),
  ("LangGraph agent with checkpoints and human approval", "project"),
  ("structured extraction pipeline with Pydantic validation and retries", "project"),
  ("LLM-as-judge eval harness for a small agent", "project"),
  ("agent tracing dashboard (OpenTelemetry or Langfuse)", "project"),
  ("prompt-injection red-team suite for a tool-using agent", "project"),
  ("semantic cache and model router to cut cost", "project"),
  ("streaming chat backend with FastAPI and SSE", "project"),
  ("memory layer for a chat agent (summaries, facts, deletion)", "project"),
  ("text-to-SQL agent over a sample database", "project"),
  ("multimodal receipt or invoice reader", "project"),
  ("background job runner for long agent tasks", "project"),
  ("small fine-tune for tool-call formatting (LoRA)", "project"),
 ]),
 ("slm-for-agents", 1, "Small language models (SLMs) for agents", [
  ("when an SLM is enough (task fit, cost, latency, privacy)", ""),
  ("choosing an SLM (Llama, Qwen, Phi, Gemma, Mistral small models)", "decision: a selection checklist rather than a leaderboard"),
  ("running SLMs locally (Ollama, llama.cpp, vLLM)", "tool"),
  ("quantized SLMs (GGUF, AWQ, quality vs memory)", ""),
  ("tool calling and structured output with SLMs", ""),
  ("fine-tuning an SLM for an agent task (LoRA, QLoRA, data)", ""),
  ("distilling a large-model agent into an SLM", ""),
  ("SLM-LLM cascades and routing", ""),
  ("on-device and edge agents with SLMs", ""),
  ("evaluating SLMs on your agent tasks", ""),
 ]),
 ("portfolio-projects", 1, "Portfolio projects", [
  ("production support agent end to end", "project"),
  ("agentic RAG platform with evals and observability", "project"),
  ("multi-agent research and report system", "project"),
  ("self-hosted SLM agent with fine-tuning and routing", "project"),
  ("enterprise agent with SSO, RBAC tools and audit logs", "project"),
  ("MCP tool hub for a team", "project"),
 ]),
 ("engineering-fundamentals", 1, "Engineering fundamentals", [
  ("HTTP from first principles (requests, status codes, headers, connections, TLS)", ""),
  ("debugging method (reproduce, isolate, hypothesise, verify)", ""),
  ("testing fundamentals (what to test, unit vs integration, test doubles, flaky tests)", ""),
  ("git and code review workflow (branches, commits, rebasing, reviewing AI code)", ""),
  ("reading errors, logs and stack traces", ""),
  ("command line and environment basics (shell, env vars, processes, ports)", ""),
 ]),
 ("math-for-ai", 1, "Math and statistics for AI", [
  ("probability for AI engineers (distributions, conditional probability, Bayes)", ""),
  ("statistics for evaluation (sampling, confidence intervals, significance, sample size)", ""),
  ("linear algebra intuition (vectors, dot products, matrices, norms)", ""),
  ("calculus intuition for gradients (derivatives, chain rule)", ""),
  ("information theory basics (entropy, cross-entropy, KL divergence)", ""),
  ("numerical precision (floating point, fp16, bf16, overflow)", ""),
 ]),
 ("cumulative-projects", 1, "Cumulative projects", [
  ("Project 1: reliable RAG application", "project"),
  ("Project 2: tool-using agent with guardrails", "project"),
  ("Project 3: production AI service", "project"),
  ("Project 4: SLM feasibility study", "project"),
  ("Project 5: end-to-end business solution", "project"),
 ]),
]

TIER_INFO = {
 1: {"title": "Tier 1 — Shared core (AI Engineer base)",
     "blurb": "Everything in this tier is assumed by Tiers 2 and 3. It is the AI Engineer baseline."},
 2: {"title": "Tier 2 — AI Architect (adds)",
     "blurb": "The Architect owns the decisions and boundaries; Tier 1 is implemented by engineers under them."},
 3: {"title": "Tier 3 — Forward Deployed Engineer (adds)",
     "blurb": "The FDE takes Tier 1 into a specific customer's systems, data, security posture and people."},
}

# Role paths, from the taxonomy's "Trimming per JD" note.  status: core | background | skip
ROLE_PATHS = {
 "all":       {"title": "Everything", "desc": "All three tiers, no trimming.", "rules": {}},
 "llm-app":   {"title": "LLM-app AI Engineer",
               "desc": "Tier 1 minus ML-fundamentals depth; distributed training and feature stores dropped.",
               "rules": {"tier2": "skip", "tier3": "skip", "ml-fundamentals": "background",
                         "caveat": "skip"}},
 "ml-heavy":  {"title": "ML-heavy AI Engineer", "desc": "Tier 1 in full.",
               "rules": {"tier2": "skip", "tier3": "skip"}},
 "architect": {"title": "AI Architect", "desc": "Tier 1 breadth + Tier 2.",
               "rules": {"tier3": "skip"}},
 "fde":       {"title": "Forward Deployed Engineer",
               "desc": "Tier 1 + Tier 3, with Tier 2 Reliability and Security as background reading.",
               "rules": {"tier2": "skip", "reliability": "background",
                         "security-governance-compliance": "background"}},
}

def main():
    modules, chunks, all_ids = [], [], []
    for order, (mid, tier, title, items) in enumerate(TAXONOMY):
        topics, seen = [], set()
        for it in items:
            name, hint = (it, "") if isinstance(it, str) else it
            s = slug(name)
            if s in seen:
                raise SystemExit(f"duplicate slug {s} in {mid}")
            seen.add(s)
            t = {"id": s, "key": f"{mid}/{s}", "name": name}
            if hint.startswith("tool"):
                t["kind_hint"] = "tool"
            if hint in ("usecase", "project"):
                t["kind_hint"] = hint
            if hint.startswith("caveat"):
                t["caveat"] = hint.split(":", 1)[1].strip()
            if hint.startswith(("orientation", "decision")):
                t["note"] = hint.split(":", 1)[1].strip()
            topics.append(t); all_ids.append(t["key"])
        modules.append({"id": mid, "tier": tier, "order": order, "title": title, "topics": topics})
        # chunk into work units of <= 8 topics, balanced
        n = len(topics); k = -(-n // 8); size = -(-n // k)
        for ci in range(k):
            part = topics[ci*size:(ci+1)*size]
            if not part: continue
            chunks.append({"module": mid, "module_title": title, "tier": tier, "index": ci,
                           "topics": [{"id": t["id"], "name": t["name"],
                                       **({"kind_hint": t["kind_hint"]} if "kind_hint" in t else {}),
                                       **({"caveat": t["caveat"]} if "caveat" in t else {}),
                                       **({"note": t["note"]} if "note" in t else {})} for t in part]})
    tax = {"generated_from": "AI Roles Skills Taxonomy, Oct 20 2026, Nugen IT Services",
           "tiers": TIER_INFO, "role_paths": ROLE_PATHS, "modules": modules}
    (ROOT / "taxonomy.json").write_text(json.dumps(tax, indent=1, ensure_ascii=False))
    (ROOT / "chunks.json").write_text(json.dumps(chunks, indent=1, ensure_ascii=False))
    print(f"modules={len(modules)} topics={len(all_ids)} chunks={len(chunks)}")
    for m in modules:
        print(f"  tier{m['tier']} {m['id']:34s} {len(m['topics']):3d}")

if __name__ == "__main__":
    main()
