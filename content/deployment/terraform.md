---
id: terraform
module: deployment
name: Terraform (infrastructure as code)
kind: tool
difficulty: core
est_minutes: 19
status: reviewed
related:
  - deployment/customer-vpc-on-prem-air-gapped
  - deployment/private-endpoints
  - deployment/security-and-architecture-reviews
  - backend-fundamentals/ci-cd
  - backend-fundamentals/secrets-management
  - backend-fundamentals/kubernetes
  - enterprise-integration/sandbox-vs-production-environments
  - infra-capacity-economics/cloud-ai-architecture
sources:
  - Official docs: Terraform
  - Official docs: OpenTofu
  - Kief Morris, 2020 — Infrastructure as Code (2nd edition)
---

## TL;DR
Infrastructure as code (IaC) describes networks, clusters, GPU node pools, buckets and permissions in
versioned files that a tool reconciles against reality, instead of clicking through consoles. Terraform
is the most widely used multi-cloud IaC tool: you declare desired state in HCL, it diffs that against a recorded
state file and the provider APIs, shows a plan, and applies only the difference. That makes environments
reproducible, reviewable and repeatable in every customer account you deploy to.

## Why it exists
An AI platform is a lot of infrastructure: a VPC with private subnets, private endpoints to the model
API and storage, a Kubernetes cluster with a GPU node pool, a vector database, KMS keys, IAM roles,
log sinks. Built by hand in a console, it works once. Then you need a staging copy, then the customer
wants it in their own account in another region, then an auditor asks who opened port 443 to the world
and when.

Console work fails because it leaves no reviewable record, cannot be reproduced exactly, and drifts:
the third environment is never quite like the first. Imperative scripts (a shell file of CLI calls) are
better but still brittle, because they encode steps rather than outcomes, so running them twice either
fails ("already exists") or duplicates resources, and they cannot tell you what would change before
they change it.

Declarative IaC fixes this by making the files the source of truth. You state what should exist; the
tool works out the create, update and delete calls, and can show them to a reviewer first. Changes go
through pull requests like application code, so a security reviewer can approve the diff rather than
trust a screenshot. For forward-deployed work this is decisive: the same reviewed module can be run by
the customer's own platform team, in their account, under their change control.

## How it works
### Configuration, providers and the resource graph
You write `.tf` files in HCL declaring resources (`aws_vpc_endpoint`, `google_container_node_pool`,
`azurerm_private_endpoint`) with arguments. Providers are plugins that translate each resource type into
API calls for a cloud or service; that plugin model is why one tool covers AWS, Azure, Google Cloud,
Kubernetes, DNS providers and many SaaS products. Terraform parses references between resources
(a security group ID used by an endpoint) into a dependency graph, then creates independent resources in
parallel and dependent ones in order. That is why you rarely write ordering by hand.

### State
Terraform records what it created, and the real IDs, in a state file. State exists because the tool
must map `aws_s3_bucket.documents` in your code to one specific bucket in the account, and must know
which resources it owns so it can delete one you removed from the code. Teams store state remotely
(object storage, Terraform Cloud/HCP, or similar) with locking so two applies cannot run at once and
corrupt it. State contains every attribute the provider returns, including generated passwords and
keys, in plain text, so it must be encrypted and access-controlled like a secret.

### Plan and apply
`terraform plan` refreshes state from the real APIs, compares with your configuration and prints the
create/update/replace/destroy actions. "Replace" is the line to read carefully, because some argument
changes cannot be updated in place and force destroy-then-create, which for a database or bucket means
data loss. `terraform apply` executes a plan; in CI you save the plan file and apply exactly that plan
after approval, so what was reviewed is what runs.

### Modules and variables
Modules package resources behind input variables and outputs, like functions. A "customer deployment"
module might take a VPC ID, subnet IDs, region and KMS key, and output the service URL. Each environment
or customer is then a small root configuration calling that module with different inputs.

### How the diff decides update versus replace
For each resource the plan compares three things: the configuration, the last-recorded state, and what
the refresh read from the API. If an argument differs, the provider's schema says whether the API can
change it in place (a tag, an instance count) or whether it is immutable once created (a bucket name,
a cluster's subnet), in which case the action becomes replace. Replacement defaults to
destroy-then-create *because* many names must be unique; `lifecycle { create_before_destroy = true }`
reverses the order for stateless things like launch templates or certificates, so there is no gap.

### Resource addresses are identity
State keys each object by its address in code (`module.rag.aws_s3_bucket.documents`), not by its
cloud ID. Renaming a block, moving it into a module or switching `count` to `for_each` therefore looks
to Terraform like "delete the old address, create a new one", even though nothing real changed. A
`moved { from = ..., to = ... }` block (or `terraform state mv`) tells it the object merely changed
address, and the plan becomes a no-op. This is the mechanism behind most "harmless refactor recreated
the database" stories.

### Drift and import
If someone changes a resource in the console, the next plan shows drift and proposes reverting it.
Existing hand-built resources can be adopted with an `import` block or `terraform import` so they come
under management without being recreated.

### One state per environment
Each environment or customer gets its own state, so a plan for staging cannot touch production.
Two layouts are common: separate root directories per environment (explicit, each with its own
backend key and credentials) or CLI workspaces sharing one configuration (less duplication, but the
same credentials and an easy-to-forget `terraform workspace select`). For customer accounts with
separate change boards, separate roots with separate backends is the usual choice *because* the
isolation boundary should match the approval boundary.

### Alternatives
CloudFormation (AWS) and Bicep/ARM (Azure) are native single-cloud equivalents with no state file to
manage, since the provider tracks it. Pulumi and AWS CDK let you write infrastructure in general-purpose
languages, which helps loops and abstraction but makes reviews depend on reading code rather than a
declarative file. OpenTofu is a community fork of Terraform created after HashiCorp moved Terraform to
the Business Source License in 2023; it keeps HCL and the provider ecosystem and is open source.

## Example
A minimal module for the private model-access slice of a customer deployment: a locked remote state, a
security group that only admits the app subnet, an interface endpoint for the model runtime with private
DNS, and a document bucket protected from accidental destruction. Argument names follow the AWS provider;
check them against the provider version you pin.

```hcl
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket         = "acme-tfstate"
    key            = "ai-platform/prod.tfstate"
    region         = "eu-west-1"
    use_lockfile   = true         # S3-native lock file (added in 1.10, GA in 1.11); supersedes dynamodb_table
    encrypt        = true
  }
}

variable "vpc_id"             { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "app_cidr"           { type = string }

resource "aws_security_group" "model_ep" {
  name   = "model-runtime-endpoint"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.app_cidr]
  }
}

resource "aws_vpc_endpoint" "model_runtime" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.eu-west-1.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids     # one subnet per availability zone
  security_group_ids  = [aws_security_group.model_ep.id]
  private_dns_enabled = true
}

resource "aws_s3_bucket" "documents" {
  bucket = "acme-rag-documents-prod"
  lifecycle { prevent_destroy = true }   # plan fails instead of deleting customer data
}
```

`terraform plan` on a fresh account shows three resources to add; changing `bucket` later would show a
replace, and `prevent_destroy` turns that into an error rather than a deleted archive. The guard lives in
the resource block, so it does not protect against someone deleting the block itself; pair it with
bucket versioning or a deletion-protection setting on the cloud side.

## Trade-offs
- Declarative plans make every change reviewable before it happens, but the state file becomes critical
  shared infrastructure; losing or corrupting it is worse than losing the code.
- One tool across clouds and SaaS providers helps teams deploying into varied customer accounts, but a
  single-cloud team may find native CloudFormation or Bicep simpler since there is no state to operate.
- HCL's restricted language keeps configurations readable for security reviewers, but complex logic
  (dynamic topologies, heavy loops) is awkward; general-purpose IaC tools win when logic dominates.
- Large single state files give one view of everything but make plans slow and blast radius big;
  splitting by layer (network, cluster, app) limits damage at the cost of cross-state references.

## Pitfalls
- Committing state or secrets to git, or storing state unencrypted; the symptom is a secret scanner
  alert, or an auditor finding database passwords readable by everyone with repository access.
- Approving a plan without reading "must be replaced" lines; the symptom is a vector database or
  bucket recreated empty after an apparently harmless argument change.
- Refactoring resource names or module structure without `moved` blocks; the symptom is a plan that
  shows an equal number of destroys and creates for a pull request that "only renamed things".
- Unpinned provider versions or an uncommitted `.terraform.lock.hcl`; the symptom is a plan that
  suddenly proposes changes nobody made, after a new provider release changed a default.
- Making emergency fixes in the console and never back-porting them; the symptom is the next routine
  apply silently reverting the fix and re-causing the incident.
- Assuming the customer's air-gapped environment can download providers; the symptom is
  `terraform init` failing, fixed only by shipping a provider mirror with the release bundle.

## When to use
Use IaC for anything that must exist in more than one environment or survive an audit: which is to say
every production AI deployment, and every customer-VPC deployment. Terraform or OpenTofu is the default
when you deploy across clouds or into many customer accounts, or when the customer's platform team
already uses it. Use native templates when an organization is single-cloud and mandates them. Keep
application rollout (container versions, model weights) in a deploy tool such as Helm or Argo rather than
Terraform, since those change far more often. Measure drift (plans that are not empty on main) and
time to stand up a new environment. Interviewers probe state, plan review and the replace hazard.

## Role lens
- **Engineer:** Writes modules for GPU node pools, endpoints and buckets, runs plan in CI on every pull
  request, and reads each replace or destroy action before approving.
- **Architect:** Sets the state layout, module boundaries and versioning, decides which layer IaC owns
  versus the deploy tool, and makes plan output part of the change-approval evidence.
- **FDE:** Hands the customer a versioned module and variables file their platform team can run in their
  own account, plus a provider mirror when their environment cannot reach the registry.

## Check yourself
**Q:** A customer's platform team must run your deployment in their own AWS account under their change
board, and they forbid your engineers from having write access. How do you structure the Terraform
delivery so it is reviewable, repeatable and safe for them to run?
**A:** Ship a versioned module with a narrow, documented variable interface (VPC and subnet IDs, KMS key,
region, sizing), no hard-coded account details, and pinned provider versions. Their root configuration
calls it, with state in their backend under their access controls and locking. Their CI runs plan and
attaches the saved plan to the change ticket; the board reviews it, and apply executes exactly that
plan. Protect data resources with `prevent_destroy`, document every replace-triggering argument, and
publish an IAM policy listing the permissions the apply role needs. For upgrades, release a new module
version with a changelog and an expected-plan summary so they can compare. If the account is isolated,
include a provider mirror.

## Quiz
### Q: Why does Terraform need a state file at all, when it could just query the cloud APIs?
- [ ] To cache API responses so plans run faster
- [x] To map each resource in the code to the specific real object it manages, including ones removed from code that must be deleted
- [ ] Because cloud APIs do not report resource attributes
- [ ] To store the HCL source in a binary form
> Without state, Terraform cannot know which real bucket corresponds to a resource block, or which objects
> it owns when a block is deleted. Refreshing does query the APIs, but only for objects state identifies.

### Q: A reviewer sees a plan line saying the vector database cluster "must be replaced" after a minor-looking argument change. What is the right response?
- [ ] Approve it, since Terraform preserves data during replacement
- [ ] Run apply twice so the second run cancels the replacement
- [x] Treat it as destroy-and-recreate with data loss, and find an in-place path or a planned migration before applying
- [ ] Delete the state file so Terraform forgets the cluster exists
> Replacement destroys the existing resource and creates a new one, so stored data is lost unless
> migrated. Deleting state would make Terraform try to create a duplicate and abandon the original unmanaged.

### Q: Why might an AWS-only team reasonably prefer CloudFormation over Terraform?
- [ ] CloudFormation supports more third-party SaaS providers
- [ ] Terraform cannot manage AWS networking resources
- [ ] CloudFormation lets you write infrastructure in Python
- [x] AWS tracks the stack state itself, so there is no separate state file and locking setup to operate
> The native service manages state and rollback for you, which removes an operational burden. Terraform's
> advantage is breadth across clouds and SaaS, and Python-based infrastructure belongs to CDK or Pulumi.
