---
id: private-endpoints
module: deployment
name: Private endpoints
kind: pattern
difficulty: core
est_minutes: 15
status: crisp
related:
  - deployment/customer-vpc-on-prem-air-gapped
  - deployment/vpn
  - deployment/terraform
  - deployment/compliance-constraints
  - security-governance-compliance/zero-trust
  - security-governance-compliance/data-residency
  - system-design/ai-gateway
  - infra-capacity-economics/cloud-ai-architecture
sources:
  - Official docs: AWS PrivateLink
  - Official docs: Azure Private Link
  - Official docs: Google Cloud Private Service Connect
---

## TL;DR
A private endpoint gives a managed service a private IP inside the consumer's network, so traffic never
crosses the internet and public access can be switched off. It pairs a network interface with a DNS
override; the DNS half is where most failures live.

## Why it exists
- **The subnet has no internet route on purpose.** A bank's application subnet still needs a hosted
  model API, object storage and a SaaS observability tool, and every one has a public hostname that
  resolves to a public IP.
- **A NAT gateway opens everything.** The same path that reaches the model API also reaches a paste
  site, which is exactly what the data-loss team was preventing.
- **IP allowlists are brittle.** Cloud services use large, shared and changing address pools, so the
  allowlist ends up permitting a huge slice of the cloud.
- **Peering is too broad.** It is usually not offered for multi-tenant services; where it is, it connects
  entire networks, requires non-overlapping CIDR ranges and exposes far more than one API.
- **A private endpoint says exactly what security wants.** "This subnet may reach this one service, over
  a path we control, and that service refuses calls that did not come through that path." Once every
  consumer arrives privately, the provider can disable public network access, so a leaked API key is
  useless from the internet.

## How it works
The three large clouds implement the same idea under different names:

| Cloud | Name | Consumer object | Publishing your own SaaS |
|---|---|---|---|
| AWS | PrivateLink | interface VPC endpoint | endpoint service fronted by a Network Load Balancer |
| Azure | Private Link | private endpoint | Private Link service behind a Standard Load Balancer |
| Google Cloud | Private Service Connect | endpoint | service attachment |

The mechanism has four parts.

### 1. A network interface in the consumer's subnet
- **What is created.** One or more network interfaces, each with a private IP from the consumer's
  subnet; packets to that IP travel over the provider's internal network to the service.
- **Overlapping CIDRs are fine.** The consumer only ever sees an IP in its own range, so address spaces
  never have to be compatible. This is the main reason it beats peering for a SaaS whose thousands of
  customers all use 10.0.0.0/8.
- **One interface per availability zone**, because the interface lives in a zone; a single-zone endpoint
  is a single point of failure and pushes cross-zone traffic.

### 2. A service published on the provider side
- **First-party services** (model APIs, storage, key management): the cloud publishes them for you.
- **Your own SaaS:** you publish it yourself (see the table above).
- **Decide who may connect** by allowlisting customer accounts or requiring manual acceptance of each
  connection request, because otherwise anyone who learns the service name could attach to it.

### 3. DNS that points the normal hostname at the private IP
- **Why DNS matters.** Applications and SDKs use the service's ordinary hostname; if it still resolves to
  a public IP, the endpoint is never used.
- **The override.** The endpoint comes with a private DNS zone that overrides the public record inside
  the consumer network, so the same hostname resolves to the interface's private IP.
- **Hostname unchanged on purpose**, because TLS certificates still validate and no SDK needs
  reconfiguring.
- **Callers outside the VPC** (on-prem over a VPN, or another network) need their DNS queries forwarded
  into the cloud resolver, otherwise they resolve publicly.

### 4. Policy on both ends
- **Connectivity is not authorization.** Calls still need IAM credentials or API keys.
- **Endpoint policy:** which principals and actions may use this endpoint.
- **Service-side rule:** a resource policy or network rule saying "only accept requests arriving via
  endpoint X". The combination is what makes stolen credentials useless off-network.

### Following one request
Put the four parts together for a call from a pod to a model API:
1. **Resolve.** The SDK asks the VPC resolver for `bedrock-runtime.<region>.amazonaws.com`; the private
   zone answers with the endpoint interfaces' private IPs (say 10.20.1.15 and 10.20.2.15, one per zone).
2. **Connect.** The pod opens TCP 443 to 10.20.1.15. The **security group on the endpoint interface**
   must allow 443 from the pod's subnet. It is a separate control from the pod's own egress rules, and
   forgetting it is the classic "DNS is right but it still times out" failure.
3. **Carry.** The provider network takes the flow to the service's load balancer. The service never sees
   a route into your VPC; it sees a connection tagged with the endpoint it came through, which is what
   resource policies such as "only via endpoint X" match on.
4. **Authenticate.** TLS is negotiated against the ordinary hostname, so the provider's public
   certificate validates; then the API key or IAM signature is checked as usual.

- **Customer identity for your own SaaS.** On AWS, enabling Proxy Protocol v2 on the NLB is how your
  application learns which customer endpoint a connection came from, *because* the source IP alone is
  not a reliable customer identifier.
- **One-way by design.** Connections are initiated by the consumer toward the service; the provider
  cannot open connections back into the consumer network through the endpoint, which is why security
  teams approve it more easily than a VPN or peering link.

### Gateway endpoints (AWS only)
- **Older, different mechanism** available only for S3 and DynamoDB: a route-table entry (a prefix list)
  instead of an interface.
- **Cost and reach.** Cost nothing extra, but only work from inside that VPC, not from on-prem or peered
  networks.
- **Elsewhere.** Azure and Google Cloud have no direct equivalent; their closest analogues are service
  endpoints and Private Google Access respectively.

## Diagram
One call from a pod to a model API: the private DNS answer and the endpoint's security group are the
two steps that most often break.

```mermaid
sequenceDiagram
    participant P as Pod in private subnet
    participant R as VPC resolver
    participant E as Endpoint interface
    participant L as Provider load balancer
    participant S as Model API
    P->>R: resolve the ordinary API hostname
    R-->>P: private IPs, one per zone
    P->>E: TCP 443 to private IP
    Note over E: security group must admit 443
    E->>L: carried on provider network, tagged with endpoint id
    L->>S: forward request
    Note over S: TLS on normal hostname, then IAM or API key, then only-via-endpoint policy
    S-->>P: response on the same path
```

## Examples
### Deploy-time check that SDK hostnames resolve privately
- **Setting.** A team runs a RAG service in a private subnet with no NAT. They created an interface
  endpoint for the model runtime API and one for object storage.
- **Why check.** A failed check is the earliest signal that private DNS is off, which otherwise shows up
  only as timeouts (no egress) or, worse, silent internet traffic (if egress exists).

```python
import ipaddress, socket, sys

# Hostnames the application's SDKs actually call (region-specific in real deployments).
REQUIRED_PRIVATE = [
    "bedrock-runtime.eu-west-1.amazonaws.com",
    "s3.eu-west-1.amazonaws.com",
    "vectors.internal.example.com",   # our own SaaS exposed through a private endpoint
]

def resolved_ips(host):
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({ipaddress.ip_address(info[4][0]) for info in infos})

failures = []
for host in REQUIRED_PRIVATE:
    try:
        ips = resolved_ips(host)
    except socket.gaierror as e:
        failures.append(f"{host}: does not resolve ({e})"); continue
    public = [str(ip) for ip in ips if not ip.is_private]
    if public:
        failures.append(f"{host}: resolves to public {public}; private DNS not active here")
    else:
        print(f"ok   {host} -> {[str(ip) for ip in ips]}")

if failures:
    print("\n".join("FAIL " + f for f in failures)); sys.exit(1)
```

- **Run it from inside the workload** (a pod, a task), not a laptop, because resolution depends on which
  resolver the caller uses.
- **Storage may use a gateway route** rather than a private IP; check the route table for those.

### The endpoint in Terraform, with the security group that usually gets forgotten
One interface per zone, private DNS on, and an ingress rule for 443 from the workload subnets.

```hcl
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.eu-west-1.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.app_a.id, aws_subnet.app_b.id]   # one per AZ
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true                                          # the DNS half
}

resource "aws_security_group" "endpoint" {
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_subnet.app_a.cidr_block, aws_subnet.app_b.cidr_block]
  }
}
```

### Worked scenario: three symptoms, three different causes
| Symptom seen | What the lookup from the pod shows | Cause | Fix |
|---|---|---|---|
| Flow logs show bytes via NAT to the API | public IP | private DNS off or zone not linked to this VPC | enable private DNS, link the zone |
| Timeouts, hostname resolves to 10.20.1.15 | private IP | endpoint security group blocks 443 | admit 443 from the workload subnet |
| Works in cluster, fails from data centre | public IP on-prem only | no DNS forwarding from on-prem | forward the domain to the cloud resolver |

The second row is the one teams misdiagnose as a provider outage, because DNS already looks correct.

## Production patterns
- **Fail closed at the network layer.** Remove the NAT route from subnets that only need endpointed
  services, so a call that resolves publicly fails loudly instead of leaking. Monitor VPC flow logs for
  any bytes to the service via NAT; the target is zero.
- **Restrict the service to the endpoint.** For S3, a bucket policy that denies requests unless the
  `aws:SourceVpce` condition key matches your endpoint id; for Azure services, set public network access
  to disabled. Monitor denied requests: a spike means a caller is still on the public path.
- **Shared endpoint hub.** With dozens of services across many VPCs, host endpoints once in a central VPC
  and share the private zones, because per-endpoint, per-zone charges add up. Monitor endpoint count and
  cost per VPC.
- **Hybrid DNS forwarding.** Give on-prem resolvers a conditional forward for the service domains to a
  cloud inbound resolver (Route 53 Resolver inbound endpoints on AWS, Azure DNS Private Resolver on Azure).
  Monitor the DNS check above run from an on-prem host.
- **Manual acceptance for your SaaS endpoint service.** Set `acceptance_required = true` and an explicit
  principal allowlist on the Terraform `aws_vpc_endpoint_service`, so unknown accounts never attach.
  Review the connection list on a schedule.
- **DNS check as a deploy gate.** Run the resolution script as a pre-start job in each environment and
  block rollout on failure; alert if it ever flips from private to public.

## Trade-offs
- **Narrow path vs cost.** You gain a narrow, one-directional path to exactly one service, but pay
  per-endpoint, per-zone hourly and per-GB charges. With dozens of services across many VPCs, a central
  shared-endpoint VPC becomes cheaper.
- **No public access vs convenience.** Disabling public access blocks leaked credentials from the
  internet, but also blocks your own engineers' laptops and CI runners unless they reach it over VPN or a
  private runner.
- **No CIDR coordination vs flexibility.** Endpoints avoid the CIDR coordination peering or VPNs need,
  but only carry traffic to the published service. If workloads need many services or bidirectional
  flows, peering or a transit network is simpler.
- **Path assurance vs data assurance.** Private networking reassures a security team about the path, but
  does not change who processes the data or where. If the concern is residency or training use, you
  need contractual and regional controls instead.

## Pitfalls
- **Endpoint without private DNS.** Creating the endpoint but not enabling or linking the private DNS
  zone. Symptom: the SDK times out in a no-egress subnet, or flow logs show traffic still leaving
  through the NAT gateway.
- **Forgetting hybrid callers.** On-prem and peered-VPC callers need DNS forwarding to the cloud
  resolver. Symptom: "works from the cluster, fails from the data center", with the hostname resolving to
  a public IP there.
- **Default security group on the interface.** It does not admit 443 from the workload subnet. Symptom:
  the hostname correctly resolves to a private IP but every connection times out, which teams misdiagnose
  as a DNS or provider outage.
- **Endpointing the API but not its dependencies.** Such as the identity provider, token service or a
  separate file-upload host. Symptom: authentication or uploads hang while plain calls succeed.
- **Single-zone interface.** Symptom: a full outage of model calls during a zone incident and unexpected
  cross-zone data charges in normal operation.
- **Auto-accept for any principal on your SaaS endpoint service.** Symptom: unknown accounts appear in
  the connection list, which an audit will flag immediately.

## When to use
- **Use when** a customer's workload in a cloud network must call a managed service and policy says "no
  internet egress" or "no public endpoints for data services". That covers most regulated buyers calling
  hosted model APIs, storage and key management in their own account.
- **Offer your SaaS this way**: it is the standard way to serve enterprise customers without a VPN.
- **Prefer it over VPN or peering** when the flow is one-directional to a single service.
- **Do not use it** as a substitute for authorization, encryption or data-processing terms.
- **Measure:** flow logs (zero bytes to the service via NAT) and the service rejecting non-endpoint traffic.
- **Interviews:** explaining the DNS override and the one-way property is what separates real experience
  from slideware.

## Role lens
- **Engineer:** Writes the endpoint, security group and private DNS resources in Terraform, and adds a
  deploy check that every SDK hostname resolves to a private address from inside the workload.
- **Architect:** Decides where endpoints live (per VPC or a shared hub), which services must disable
  public access, and how on-prem DNS forwarding is designed so hybrid callers stay private.
- **FDE:** Walks the customer's network team through publishing or consuming the endpoint, requests
  account allowlisting, and debugs the inevitable DNS resolution difference between clusters and laptops.

## Check yourself
**Q:** A customer created a private endpoint for a hosted LLM API, yet their security team's flow logs
still show traffic from the RAG service going out through the NAT gateway. What is the likely cause, and
how do you make the setup fail closed?
**A:** The endpoint exists but the application's hostname still resolves to the public IP, usually
because private DNS was not enabled on the endpoint, the private zone is not associated with that VPC,
or the workload uses a custom resolver that does not forward to the cloud resolver. Fix the resolution,
verify with a lookup from inside the pod, then make it fail closed: remove the NAT route from that
subnet so a call that resolves publicly fails loudly, set the service's resource policy to accept only requests via
this endpoint, and turn off public network access where the service supports it. Finally add the DNS
check to deployment so a regression is caught before traffic flows.

## Interview questions
### What is a private endpoint, and why is it preferred over a NAT gateway or IP allowlist?
- **Definition:** a network interface with a private IP in the consumer's subnet, plus a private DNS
  override, that carries traffic to one managed service over the provider's network.
- **Versus NAT:** NAT opens the whole internet to the subnet; the endpoint reaches exactly one service.
- **Versus allowlists:** provider IP pools are shared and change, so allowlists end up admitting a large
  slice of the cloud.

### DNS resolves to a private IP but every call times out. Walk me through the debugging.
- **Confirm the path:** from inside the pod, check the resolved IP is the endpoint interface and try a
  TCP connect to 443.
- **Check the endpoint's security group first:** it must admit 443 from the workload subnet, and it is
  separate from the pod's egress rules.
- **Then check** pod egress policy, network ACLs on the subnet, and that the endpoint has an interface in
  the zone the pod is calling.

### When would you choose a VPN or peering instead of private endpoints?
- **Many services or both directions:** endpoints carry traffic only to one published service, so broad
  or bidirectional flows are simpler over peering or a transit network.
- **Non-cloud sources needing routes into the VPC:** a VPN joins networks, which endpoints deliberately
  do not.
- **Cost:** endpoints bill per endpoint, per zone and per GB, so dozens of services across many VPCs
  favour a shared hub or a different topology.

### How do you make a stolen API key useless from outside the customer network?
- **The endpoint alone does not:** it adds a private path but leaves the public one open.
- **Restrict at the service:** disable public network access, or use a resource policy that only accepts
  requests via this endpoint (for S3, the `aws:SourceVpce` condition).
- **Prove it:** call from outside with a valid key and expect a denial; alert on denied-request spikes.

### You are offering your SaaS over PrivateLink to 300 enterprise customers. What do you design for?
- **Publishing:** an endpoint service behind a Network Load Balancer, with manual acceptance or an
  account allowlist so only contracted customers attach.
- **Tenant identity:** Proxy Protocol v2 tells your app which endpoint a connection came from, because
  source IPs overlap across customers using the same private ranges.
- **Operations:** availability in every zone customers use, and a runbook for their DNS and security-group
  setup, since most onboarding failures are on their side.

## Quiz
### Q: Why can a SaaS vendor offer private endpoints to hundreds of customers who all use overlapping 10.0.0.0/16 ranges?
- [ ] The vendor runs a separate VPC per customer with a unique CIDR
- [x] Each customer reaches the service through an interface with an IP from their own subnet, so the two address spaces are never joined
- [ ] Private endpoints use IPv6 internally, so IPv4 overlap is irrelevant
- [ ] The cloud's NAT gateway rewrites addresses in both directions
> The consumer only routes to a private IP in its own range and the provider network carries the traffic
> behind a load balancer, so no routes are exchanged. Peering and VPNs join address spaces and therefore
> require non-overlapping ranges.

### Q: An endpoint for a model API is created and healthy, but calls from on-prem servers over the VPN still go to the public internet. What is missing?
- [ ] A second endpoint in another availability zone
- [ ] An endpoint policy allowing the on-prem IP range
- [x] DNS forwarding so on-prem resolvers send the service's hostname to the cloud resolver that holds the private zone
- [ ] A BGP route advertising the service's public IP range over the VPN
> The private zone only answers inside the cloud network. On-prem resolvers still return the public
> record unless queries for that domain are forwarded inward; advertising public ranges over the VPN
> would be a fragile hack, not the design.

### Q: A security reviewer says "we have a private endpoint, so stolen API keys are harmless." What makes that statement true or false?
- [ ] It is true by default, because private endpoints encrypt the API key
- [ ] It is false, because private endpoints only work for storage services
- [ ] It is true, because the endpoint policy replaces the need for credentials
- [x] It is only true if the service also rejects requests that did not arrive through the endpoint, for example by disabling public access
> The endpoint adds a private path but does not remove the public one. Only a resource-side restriction,
> such as disabling public access or a policy requiring this endpoint, makes the key useless from outside.
