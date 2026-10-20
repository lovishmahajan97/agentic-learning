---
id: cloud-ai-architecture
module: infra-capacity-economics
name: Cloud AI architecture (AWS Bedrock, Azure OpenAI, Vertex AI)
kind: concept
difficulty: core
est_minutes: 17
status: reviewed
related:
  - infra-capacity-economics/api-provider-quotas-and-regional-limits
  - infra-capacity-economics/hybrid-on-prem
  - system-design/self-host-vs-api
  - system-design/ai-gateway
  - system-design/open-weight-vs-proprietary
  - deployment/private-endpoints
  - security-governance-compliance/data-residency
  - security-governance-compliance/vendor-exit-strategy
sources:
  - Official docs: Amazon Bedrock
  - Official docs: Azure OpenAI Service
  - Official docs: Google Cloud Vertex AI
---

## TL;DR
Bedrock, Azure OpenAI and Vertex AI are one idea on three clouds: frontier and open-weight models
served as a managed API *inside* your cloud account's identity, network, logging and billing
perimeter. Enterprises pick them over a vendor's public API because procurement, compliance and
networking are already solved there, and pay with model lag, per-region quotas and a second layer
of lock-in. The architecture work is placing the endpoint inside that perimeter and planning
capacity per region.

## Why it exists
Calling a model vendor's public API from an enterprise application raises questions that have
nothing to do with the model: who approved this vendor, where does the data go, which network path
does the request take, how is it authenticated, where are the logs, who pays and under which
contract. Each of those is a review, and each review is slow. Meanwhile the company already has an
answer to every one of them for its cloud provider: IAM or Entra identities, private networking,
audit logging, consolidated billing against a committed spend, and a signed compliance package.

The cloud AI platforms exist to let the model inherit those answers. A request to Bedrock is a
SigV4-signed AWS API call that can travel over a VPC endpoint, with opt-in model invocation logging
to CloudWatch Logs or S3, billed on the AWS invoice. An
Azure OpenAI deployment is a resource in a resource group with Entra RBAC, a private endpoint and
diagnostic settings. A Vertex AI call is a Google Cloud API call under a project's IAM, VPC Service
Controls and Cloud Audit Logs. From the security reviewer's point of view, the model becomes "just
another cloud service", which is the point.

The naive alternative, "it is the same model, use the vendor API and save the complexity", fails on
the review calendar rather than on technology, and later on data terms (training opt-outs, retention,
residency) that the cloud contracts fix in writing. It also fails on capacity: committed cloud
customers can buy provisioned throughput in their own region, which a public pay-as-you-go API does
not offer in the same form. The opposite naive alternative, "self-host everything on our GPUs", fails
for most teams on model quality and operations cost, which is why the managed route is the default
in large organisations.

## How it works
### The common shape
All three platforms present the same architectural layers, and your design decisions live in each:

1. **Identity.** No API keys from the model vendor. Bedrock uses IAM principals and policies per
   model; Azure OpenAI uses Entra ID (keys exist but are the weaker option); Vertex uses Google
   Cloud IAM and service accounts. Decision: which workload identities may call which models.
2. **Network.** Private connectivity so traffic never crosses the public internet: VPC endpoints
   (PrivateLink) for Bedrock, Private Endpoints for Azure OpenAI, Private Service Connect and VPC
   Service Controls for Vertex. Decision: where the endpoint sits and which networks can reach it.
3. **Model catalog and endpoint.** Bedrock offers models from several vendors (Amazon's own,
   Anthropic, Meta, Mistral and others) behind one API, with the Converse API as the model-agnostic
   interface. Azure OpenAI hosts OpenAI models, and you create a named *deployment* of a model
   version with a capacity setting; the wider Azure AI Foundry catalog (since rebranded Microsoft
   Foundry; these product names change often) adds other vendors' models.
   Vertex serves Gemini natively and partner models (Anthropic, Meta, Mistral and others) through
   Model Garden. Decision: which models, which versions, pinned how.
4. **Capacity model.** Two modes everywhere, with different names. Pay-as-you-go shares a regional
   pool under quotas (requests and tokens per minute per model per region per account or
   subscription). Reserved capacity buys guaranteed throughput: Bedrock Provisioned Throughput in
   model units, Azure Provisioned Throughput Units (PTU), Vertex Provisioned Throughput. Reserved
   capacity exists *because* the shared pool cannot promise throughput at peak; you pay for
   dedicated model capacity in exchange for predictability. Batch modes exist for offline work at a
   discount. Decision: which workloads run on shared quota and which need reserved capacity.
5. **Region.** Models are available per region, and quotas are per region. Each platform has added
   ways to route across regions when one is full: Bedrock cross-region inference profiles, Azure
   "Global" and "Data Zone" deployment types that process within a wider geography, and a global
   endpoint option on Vertex for some models. These trade residency precision for availability.
   Decision: which regions satisfy the data-residency rule, and whether cross-region routing is
   permitted.
6. **Governance and logging.** Invocation logging to the cloud's log services, content filters
   (Bedrock Guardrails, Azure's built-in content filtering, Vertex safety settings), and the data
   terms: all three state that customer prompts and outputs are not used to train the base models.
   Azure's abuse-monitoring may retain prompts for a limited period unless an exemption is granted,
   which matters for regulated data and must be checked against current docs.
7. **Adjacent building blocks.** Managed retrieval (Bedrock Knowledge Bases, Vertex AI Search and
   RAG tooling, Azure AI Search), agent frameworks, evaluation and fine-tuning services. They are
   convenient and deepen lock-in; treat each as a separate build-versus-buy decision.

### Where the differences bite
- **Model lag.** New models and features usually reach the vendor's own API first and the cloud
  platforms days to months later, sometimes with missing features. If the roadmap depends on the
  newest model, that lag is a real cost.
- **Capacity semantics.** Azure's quota is a TPM allocation per model per region per subscription
  that you split across deployments; Bedrock's on-demand quotas are per model per region per
  account; Vertex has moved Gemini pay-as-you-go toward a dynamically shared quota rather than
  fixed per-project limits. The practical consequence is the same: plan capacity per model per
  region and request increases early, since increases are tickets, not API calls.
- **SDK shape.** Bedrock has its own SDK (boto3) with two call styles: `InvokeModel`, whose
  request body is each vendor's native format, and the Converse API, one message format across
  models; vendor SDKs can also use Bedrock as a backend; Azure OpenAI uses the OpenAI SDK with an Azure client where
  `model` means the deployment name (Azure has also added a versionless "v1" endpoint usable from
  the standard OpenAI client, so check which style the current docs recommend); Vertex uses the Google Gen AI SDK or vendor SDKs with a Vertex
  backend. An internal gateway or an abstraction such as LiteLLM usually hides this so that
  application code is not rewritten when a model moves.
- **Version pinning and retirement.** Each platform retires model versions on its own schedule;
  Azure deployments can be set to auto-upgrade or pinned, Bedrock and Vertex pin by model id. A pin
  is not forever: when a pinned version reaches its retirement date you must migrate (or, on Azure,
  you may be upgraded), so the retirement calendar is part of the plan.
  Unplanned upgrades change behaviour under your evals, so pinning plus a scheduled re-evaluation
  is the safe pattern.

### Choosing the capacity mode
Reserved capacity is a fixed hourly or monthly price for a throughput ceiling; pay-as-you-go is a
per-token price with no ceiling guarantee. The comparison is the same arithmetic as owning GPUs:

```
on-demand cost  = tokens actually served × price per token
reserved cost   = units × price per unit-hour × hours (paid whether used or not)
break-even utilization u* = reserved cost ÷ (tokens the reservation could serve at full use × on-demand price)
```

Above u*, reserved is cheaper per token; below it, you are paying for a guarantee. The number that
is hard to get is "tokens the reservation could serve", because each platform's unit (model unit,
PTU) delivers a throughput that depends on the model and on the input/output mix: prompt-heavy
traffic and generation-heavy traffic consume a unit differently. Each platform publishes sizing
guidance or a calculator, but the reliable figure comes from a load test of your own traffic mix
against a small reservation. Two further facts usually decide it: a latency SLO at peak can justify
reserved capacity below u*, since the alternative is 429s at the worst moment; and commitment
terms (hourly, monthly, longer) mean a forecast error is paid for the length of the term.

### The reference architecture
Application → internal AI gateway (auth, routing, quotas, logging, prompt and response capture) →
private endpoint → cloud AI platform in the approved region → optional cross-region or second
provider fallback. The gateway is where you keep the option to move between platforms, which is the
answer to the second-layer lock-in that these services create.

## Example
The same one-turn request through all three platforms. Notice what differs: Bedrock has no key and
signs with the IAM identity in the environment; Azure needs an endpoint, an API version and the
*deployment* name; Vertex authenticates with Application Default Credentials and a project/location.
Model identifiers are placeholders to look up in each catalog.

```python
import os, boto3
from openai import AzureOpenAI
from google import genai

QUESTION = "Summarise our refund policy in one sentence."

def via_bedrock(model_id: str) -> str:
    rt = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])  # IAM creds, SigV4
    r = rt.converse(
        modelId=model_id,                                   # or a cross-region inference profile id
        messages=[{"role": "user", "content": [{"text": QUESTION}]}],
        inferenceConfig={"maxTokens": 200},
    )
    return r["output"]["message"]["content"][0]["text"]

def via_azure(deployment_name: str) -> str:
    client = AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],   # https://<resource>.openai.azure.com
        api_key=os.environ["AZURE_OPENAI_API_KEY"],            # prefer Entra ID token auth in prod
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    )
    r = client.chat.completions.create(
        model=deployment_name,                               # deployment, not the model family name
        messages=[{"role": "user", "content": QUESTION}], max_tokens=200,
    )
    return r.choices[0].message.content

def via_vertex(model_name: str) -> str:
    client = genai.Client(vertexai=True, project=os.environ["GCP_PROJECT"],
                          location=os.environ["GCP_LOCATION"])  # ADC supplies the identity
    r = client.models.generate_content(model=model_name, contents=QUESTION)
    return r.text
```

In production none of these calls live in application code; they sit behind the gateway, which
tags each request with tenant, feature and region so that quota use and cost can be attributed and
a fallback can be configured without touching the application.

The decision the gateway's telemetry feeds is the capacity mode. A worked version with
illustrative prices (not any platform's list price): a reservation costs $30,000 a month and your
load test shows it sustains about 1.5 billion tokens a month of your mix at full use; the same
tokens on pay-as-you-go cost $40 per million, so full use would cost $60,000 on demand. Break-even
utilization is 30,000 ÷ 60,000 = 50%. If the workload keeps the reservation 70% utilised on
average, reserved costs $30,000 against $42,000 on demand; at 30%, reserved costs $30,000
against $18,000, and only a peak-latency SLO justifies it. The usual design reserves for the base
and lets the burst above it spill to a pay-as-you-go deployment, which the gateway does by trying
the reserved endpoint first and falling over on a 429.

## Trade-offs
- You inherit the cloud's identity, network, logging and compliance perimeter, but you accept model
  and feature lag behind the vendor's own API; the balance flips when the product depends on the
  newest model capabilities.
- Reserved capacity (PTU, provisioned throughput, model units) buys predictable throughput for
  peak, but it is paid whether used or not; it is worth it only when steady load fills most of it
  or an SLO makes shared-pool 429s unacceptable.
- Consolidated billing against committed spend simplifies procurement and can discount the model
  bill, but it ties model choice to one cloud's catalog and makes a multi-cloud model strategy
  harder.
- Managed adjacent services (knowledge bases, agents, guardrails) accelerate delivery, but every one
  adopted deepens lock-in and narrows the exit path; the trade flips when the component is
  commodity and replaceable behind an interface.
- Cross-region and global routing options raise availability, but they loosen the residency
  guarantee; where regulation names a region, they must be disabled.

## Pitfalls
- Passing the model family name instead of the deployment name to Azure OpenAI; the symptom is a
  404 "deployment not found" even though the model is clearly available in the region.
- Sizing capacity in one region and discovering the model is not offered there or the quota is
  lower; the symptom is a launch delayed by a quota-increase ticket.
- Leaving auto-upgrade or default model versions in place; the symptom is a silent behaviour change
  that breaks evals or output parsing with no deploy on your side.
- Enabling cross-region inference or global deployment types on a workload with a residency
  requirement; the symptom is an audit finding months later rather than an error at request time.
- Calling the platform from application code directly; the symptom is a multi-week rewrite when a
  model moves clouds or a second provider is added for fallback.
- Assuming the cloud's data terms cover abuse-monitoring retention and human review; the symptom is
  a compliance review rejecting a workload that handles regulated data until the exemption is
  arranged.

## When to use
Reach for a cloud AI platform when the organisation already runs on that cloud, when identity,
private networking and compliance scope are review gates, when spend commitments can absorb the
model bill, or when you need reserved capacity in a chosen region. Prefer the vendor's direct API
when you need the newest models or features first and your data terms are satisfied by the vendor's
contract. Prefer self-hosting when open-weight models meet the bar and volume makes it cheaper, or
when the data cannot leave your own hardware. Measure quota headroom per model per region, 429 and
throttle rates, and time-to-new-model when a release matters. In interviews, the expected depth is
explaining the identity, network, capacity and region layers and how a gateway keeps the exit open.

## Role lens
- **Engineer:** Wires the gateway to each platform's SDK, pins model versions, handles each
  platform's throttling and error shapes, and tags requests for cost and quota attribution.
- **Architect:** Chooses platform versus direct API versus self-host per workload, places endpoints
  inside the network and identity perimeter, and decides reserved versus shared capacity per region.
- **FDE:** Deploys the solution inside the customer's existing cloud account, works through their
  quota, region and private-endpoint constraints, and documents which data terms apply.

## Check yourself
**Q:** A bank on Azure wants a customer-facing assistant on a frontier model, with EU data residency,
a p95 latency target and a launch in six weeks. Walk through the architecture decisions and the
risks you would raise.
**A:** Use Azure OpenAI deployed in an EU region, reached through a Private Endpoint from the
application's VNet, with Entra ID identities and diagnostic logging to the bank's workspace. Check
model availability in that region first, then request quota early; for the latency target, size
provisioned throughput for peak and keep a pay-as-you-go deployment for overflow, or accept 429s
with queueing if the SLO allows. Use regional deployment types (regional Standard and regional
Provisioned) and avoid Global types; an EU Data Zone deployment keeps processing inside the EU and
may be acceptable, but only if legal confirms that the wider geography satisfies the residency rule. Pin the model version and schedule
re-evaluation before any upgrade. Confirm the abuse-monitoring retention terms and apply for the
exemption if required. Put all calls behind a gateway so a second region or provider can be added
as fallback. The biggest risks are quota lead time, model availability in the region, and
behaviour drift from version upgrades.

## Quiz
### Q: What is the main reason enterprises route model calls through Bedrock, Azure OpenAI or Vertex AI rather than the model vendor's public API?
- [ ] The cloud platforms serve the models with lower latency
- [x] The model inherits the cloud account's identity, private networking, logging, billing and compliance perimeter
- [ ] The cloud platforms always offer newer model versions
- [ ] The cloud platforms do not enforce rate limits
> The technical model is the same; what changes is that security, procurement and compliance are
> already solved for the cloud provider. Model versions usually lag, and quotas still apply.

### Q: Why do all three platforms sell a reserved or provisioned capacity mode alongside pay-as-you-go?
- [ ] Reserved capacity uses a different, faster model build
- [ ] Pay-as-you-go cannot be used from a private endpoint
- [x] The shared regional pool is governed by quotas and cannot guarantee throughput at peak, so predictability has to be bought as dedicated capacity
- [ ] Reserved capacity is required for logging and audit features
> Shared pools give best-effort throughput under per-region quotas; a workload with a latency SLO at
> peak needs capacity that is reserved for it, which is what PTU, provisioned throughput and model
> units provide.

### Q: A workload with a strict single-country data-residency rule is deployed with a cross-region or global routing option enabled. What is the consequence?
- [ ] Requests fail immediately with a residency error
- [ ] Latency improves and nothing else changes
- [x] Requests may be processed in another region, violating the rule without any runtime error
- [ ] The quota is halved because two regions share it
> Cross-region and global modes are designed to route to wherever capacity exists; they do not know
> your legal constraints. The violation surfaces in an audit, not in an error, so it must be
> prevented by configuration.
