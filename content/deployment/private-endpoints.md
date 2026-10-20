---
id: private-endpoints
module: deployment
name: Private endpoints
kind: pattern
difficulty: core
est_minutes: 15
status: reviewed
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
A private endpoint gives a managed service (an LLM API, object storage, a vector database, your SaaS)
a private IP address inside the consumer's own network, so traffic to it never crosses the public
internet and the service can have its public access switched off. It exists because enterprises want
to call cloud services without opening internet egress, and it works by pairing a network interface
with a DNS override. The DNS half is where most failures live.

## Why it exists
A bank's application subnet has no route to the internet: that is the point of the subnet. Now the
team wants to call a hosted model API, read documents from object storage and push traces to a SaaS
observability tool. Every one of those has a public hostname that resolves to a public IP.

The naive options all weaken the perimeter. Adding a NAT gateway gives the subnet outbound internet,
so the same path that reaches the model API also reaches a paste site, which is exactly what the
data-loss team was preventing. Allowlisting the provider's IP ranges on a firewall is brittle, because
cloud services use large, shared and changing address pools, so the allowlist ends up permitting a
huge slice of the cloud. VPC peering with the provider is usually not offered for multi-tenant
services, and where it is, it connects entire networks, requires non-overlapping CIDR ranges and
exposes far more than one API.

What the security team actually wants is narrower: "this subnet may reach this one service, over a
path we control, and that service refuses calls that did not come through that path." A private
endpoint expresses exactly that. It also gives the service side a lever: once every legitimate
consumer arrives privately, the provider-side resource can disable public network access entirely,
so a leaked API key is useless from the internet.

## How it works
The three large clouds implement the same idea under different names: AWS PrivateLink (interface VPC
endpoints), Azure Private Link (private endpoints) and Google Cloud Private Service Connect. The
mechanism has four parts.

### 1. A network interface in the consumer's subnet
Creating the endpoint places one or more network interfaces, each with a private IP from the
consumer's subnet, into the consumer's network. Packets sent to that IP are carried over the cloud
provider's internal network to the service. Because the consumer only ever sees an IP in its own
range, the two sides' address spaces never have to be compatible: overlapping CIDRs are fine,
which is the main reason this beats peering for a SaaS that has thousands of customers who all use
10.0.0.0/8. You place one interface per availability zone because the interface lives in a zone; a
single-zone endpoint is a single point of failure and pushes cross-zone traffic.

### 2. A service published on the provider side
For first-party services (model APIs, storage, key management) the cloud publishes the service for
you. For your own SaaS you publish it yourself: on AWS, an endpoint service fronted by a Network Load
Balancer; on Azure, a Private Link service behind a Standard Load Balancer; on Google Cloud, a service
attachment. You then decide who may connect, by allowlisting customer accounts or requiring manual
acceptance of each connection request, because otherwise anyone who learns the service name could
attach to it.

### 3. DNS that points the normal hostname at the private IP
Applications and SDKs use the service's ordinary hostname. If that name still resolves to a public IP,
the endpoint is never used. So the endpoint comes with a private DNS zone that overrides the public
record inside the consumer network: the same hostname now resolves to the interface's private IP.
Keeping the hostname unchanged is a deliberate design choice, because it means TLS certificates still
validate and no SDK needs reconfiguring. For callers outside the VPC (on-prem over a VPN, or another
network) you must forward their DNS queries into the cloud resolver, otherwise they resolve publicly.

### 4. Policy on both ends
Connectivity is not authorization. Calls still need IAM credentials or API keys. On top, you can attach
an endpoint policy (which principals and actions may use this endpoint) and, on the service, a
resource policy or network rule saying "only accept requests arriving via endpoint X". The combination
is what makes stolen credentials useless off-network.

### Following one request
Put the four parts together for a call from a pod to a model API:
1. The SDK asks the VPC resolver for `bedrock-runtime.<region>.amazonaws.com`; the private zone
   answers with the endpoint interfaces' private IPs (say 10.20.1.15 and 10.20.2.15, one per zone).
2. The pod opens TCP 443 to 10.20.1.15. The **security group on the endpoint interface** must allow
   443 from the pod's subnet; it is a separate control from the pod's own egress rules, and forgetting
   it is the classic "DNS is right but it still times out" failure.
3. The provider network carries the flow to the service's load balancer. The service never sees a
   route into your VPC; it sees a connection tagged with the endpoint it came through, which is what
   resource policies such as "only via endpoint X" match on. (For your own SaaS on AWS, enabling
   Proxy Protocol v2 on the NLB is how your application learns which customer endpoint a connection
   came from, *because* the source IP alone is not a reliable customer identifier.)
4. TLS is negotiated against the ordinary hostname, so the provider's public certificate validates,
   then the API key or IAM signature is checked as usual.

Note the direction: connections are initiated by the consumer toward the service. The provider cannot
open connections back into the consumer network through the endpoint, which is why security teams
find it easier to approve than a VPN or peering link.

On AWS, gateway endpoints are a different, older mechanism available only for S3 and DynamoDB: they
add a route-table entry (a prefix list) instead of an interface, cost nothing extra, but only work from
inside that VPC and not from on-prem or peered networks. Azure and Google Cloud have no direct equivalent;
their closest analogues are service endpoints and Private Google Access respectively.

## Example
A team runs a RAG service in a private subnet with no NAT. They created an interface endpoint for the
model runtime API and one for object storage, then want a deploy-time check that the SDK's hostnames
really resolve privately. A failed check is the earliest signal that private DNS is off, which otherwise
shows up only as timeouts (no egress) or, worse, silent internet traffic (if egress exists).

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

Run it from inside the workload (a pod, a task), not from a laptop, because resolution depends on which
resolver the caller uses. Storage services may answer with a gateway route rather than a private IP;
check the route table for those.

## Trade-offs
- You gain a narrow, one-directional path to exactly one service, but pay per-endpoint, per-zone hourly
  and per-GB charges; with dozens of services across many VPCs, a central shared-endpoint VPC becomes cheaper.
- Disabling public access on the service blocks leaked credentials from the internet, but also blocks your
  own engineers' laptops and CI runners unless they reach it over VPN or a private runner.
- Endpoints avoid CIDR coordination that peering or VPNs need, but they only carry traffic to the
  published service; if workloads need many services or bidirectional flows, peering or a transit network is simpler.
- Private networking reassures a security team about the path, but it does not change who processes
  the data or where; if the concern is residency or training use, you need contractual and regional controls instead.

## Pitfalls
- Creating the endpoint but not enabling or linking the private DNS zone; the symptom is the SDK timing
  out in a no-egress subnet, or flow logs showing traffic still leaving through the NAT gateway.
- Forgetting on-prem and peered-VPC callers need DNS forwarding to the cloud resolver; the symptom is
  "works from the cluster, fails from the data center" with the hostname resolving to a public IP there.
- Leaving the endpoint interface's security group at a default that does not admit 443 from the
  workload subnet; the symptom is that the hostname correctly resolves to a private IP but every
  connection times out, which teams misdiagnose as a DNS or provider outage.
- Endpointing the main API but not its dependencies, such as the identity provider, token service or a
  separate file-upload host; the symptom is authentication or uploads hanging while plain calls succeed.
- Deploying the interface in one availability zone; the symptom is a full outage of model calls during a
  zone incident and unexpected cross-zone data charges in normal operation.
- Publishing your SaaS endpoint service with auto-accept for any principal; the symptom is unknown
  accounts appearing in the connection list, which an audit will flag immediately.

## When to use
Reach for private endpoints whenever a customer's workload in a cloud network must call a managed
service and their policy says "no internet egress" or "no public endpoints for data services". That
covers most regulated buyers calling hosted model APIs, storage and key management in their own
account, and it is the standard way to offer your SaaS to enterprise customers without a VPN. Prefer
it over VPN or peering when the flow is one-directional to a single service. Do not use it as a
substitute for authorization, encryption or data-processing terms. Measure success by flow logs (zero
bytes to the service via NAT) and by the service rejecting non-endpoint traffic. In interviews,
explaining the DNS override and the one-way property is what separates real experience from slideware.

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
