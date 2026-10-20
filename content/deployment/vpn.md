---
id: vpn
module: deployment
name: VPN
kind: concept
difficulty: core
est_minutes: 15
status: reviewed
related:
  - deployment/private-endpoints
  - deployment/customer-vpc-on-prem-air-gapped
  - deployment/security-and-architecture-reviews
  - security-governance-compliance/zero-trust
  - infra-capacity-economics/hybrid-on-prem
  - enterprise-integration/legacy-system-integration
  - enterprise-integration/data-ingestion-from-client-systems
sources:
  - RFC 7296 — Internet Key Exchange Protocol Version 2 (IKEv2)
  - RFC 4303 — IP Encapsulating Security Payload (ESP)
  - Official docs: WireGuard
---

## TL;DR
A VPN joins two private networks (site-to-site) or a device to a network (remote access) through an
encrypted tunnel over the public internet, so systems can talk using private addresses as if they were
on one network. AI deployments need it because the data that matters often sits in a customer's data
center and cannot be exposed publicly. It is cheap and quick to set up, but it joins address spaces and
carries everything routed into it, so scope, routing and MTU need deliberate design.

## Why it exists
Your RAG product runs in the cloud. The customer's contracts live in an on-prem document store, their
ticket history in a database in the same data center, and neither has, nor will ever have, a public IP.
Your ingestion workers need to reach them on private addresses.

The naive alternatives fail in predictable ways. Exposing the database or file share on the internet
behind a firewall rule gets rejected in the first security meeting, and rightly: credentials become the
only barrier. Shipping data on disks or nightly SFTP exports works once, but gives you stale data, a
manual process and no way to run live queries or tool calls. A dedicated private circuit (AWS Direct
Connect, Azure ExpressRoute, Google Cloud Interconnect) is the right answer for heavy, permanent traffic,
but takes weeks to months to provision through a carrier and costs accordingly.

A VPN is the middle path: it reuses the internet connection both sides already have, adds encryption and
mutual authentication, and lets routers forward private-address traffic through the tunnel. It can be up
in days, which matters during a pilot. Its weakness is the flip side of its strength: once two networks
are joined, anything routable can talk to anything else unless you narrow it, so a VPN is a network
integration, not just an encryption feature.

## How it works
### Site-to-site IPsec
Most enterprise site-to-site VPNs, including the managed VPN gateways of the major clouds, use IPsec
negotiated by IKEv2. Two phases:

1. **IKE SA (authentication and key agreement).** The two gateways exchange Diffie-Hellman values to
   derive shared keys, then authenticate each other with a pre-shared key or certificates. Certificates
   are preferable at scale because a leaked PSK compromises every tunnel that shares it. The result is an
   encrypted control channel, usually over UDP 500, moving to UDP 4500 when NAT is detected (NAT
   traversal), which is why firewalls on both sides must open those ports. Without NAT, the data
   packets travel as raw ESP (IP protocol 50, not TCP or UDP), which firewalls must permit as well.
2. **Child SAs (the data plane).** Inside that channel the peers agree keys and traffic selectors for ESP,
   the protocol that encrypts and authenticates each packet. Keys are rekeyed on a timer so a single
   compromised key exposes only a bounded window.

### Routing: policy-based vs route-based
A policy-based VPN encrypts traffic matching configured source/destination prefixes; adding a subnet
means editing both ends. A route-based VPN presents the tunnel as a virtual interface and uses ordinary
routing, either static routes or BGP. BGP is preferred for production because each side advertises its
prefixes and withdraws them when a tunnel fails, so failover between redundant tunnels happens
automatically. Cloud VPN gateways typically give you two tunnels per connection, terminating on
different endpoints, precisely so that the provider can maintain one without an outage; configure both.

### What the packet pays
Each packet gains ESP headers, padding and an authentication tag plus an outer IP header, tens of bytes
in total. A full-size 1,500-byte packet no longer fits, so either it is fragmented or, if the
"don't fragment" bit is set and ICMP is blocked, silently dropped. Gateways therefore clamp the TCP
maximum segment size (MSS) in the SYN handshake so endpoints send smaller segments; cloud VPN docs
typically recommend a clamp somewhere in the 1,350–1,400-byte range, but use your provider's figure.
Clamping happens at the gateway *because* you cannot rely on every host's path-MTU discovery when
firewalls drop the ICMP messages it depends on. It fixes TCP only; UDP traffic must be sized by the
application. Throughput per tunnel is also bounded by the
gateway's crypto capacity and the internet path, typically gigabit-class rather than the tens of gigabits
a dedicated circuit offers (check your provider's documented limits).

### Remote-access VPN and WireGuard
Client VPNs connect a single device, for example an FDE's laptop, into a customer network. WireGuard is
a newer, deliberately small protocol: fixed modern cryptography, peers identified by public keys, and a
configuration of a few lines. Its simplicity makes it easy to audit and fast, but many enterprises
standardize on IPsec appliances, so you meet WireGuard more in your own infrastructure than in a
bank's.

### When address spaces overlap
Routing only works if each side can tell local addresses from remote ones, so overlapping CIDRs are
the first thing to check, not the last. Three ways out, in rough order of preference: terminate in a
fresh network whose range neither side uses (cheap when you control one end, as a vendor usually
does); NAT on the gateway, so the customer sees your traffic arriving from an agreed non-overlapping
range and you reach their hosts through translated addresses (works, but every log and firewall rule
now speaks two address vocabularies); or re-address one side, which is correct and almost never
quick inside an enterprise.

### Narrowing the blast radius
Because a tunnel joins networks, scope it: advertise only the specific subnets needed, put firewall rules
or security groups on both ends that allow only the required ports to the required hosts, and terminate
the tunnel in a dedicated integration VPC rather than your core production network.

## Example
A legal-tech vendor pilots contract search for a firm whose 2 TB document archive sits on an on-prem
file server, with case metadata in a SQL Server instance. The plan is a site-to-site VPN from the firm's
data center to a dedicated "customer-integration" VPC in the vendor's cloud account.

Addressing first: the firm uses 10.0.0.0/8 broadly and the vendor's VPC is 10.20.0.0/16, which overlaps.
Rather than re-IP anything, the vendor confirms the firm uses nothing in 172.16.0.0/12, creates the
integration VPC as 172.31.240.0/22, and the firm
advertises only two /32 host routes (the file server and the database) over BGP. Firewall rules on the
firm's side allow only SMB and the SQL Server port, from the ingestion workers' subnet, to those two hosts.

Capacity next. The firm's internet uplink and the tunnel yield an effective 400 Mbit/s for bulk transfer
(an assumption to measure, not a promise). The initial sync is 2 TB × 8 = 16 Tbit; at 0.4 Gbit/s that is
40,000 seconds, about 11 hours, so it is scheduled over a weekend with rate limiting to protect the firm's
business traffic. Daily deltas of around 5 GB take under two minutes.

Then the classic failure: small metadata queries work, but large file reads stall after a few kilobytes.
Packet captures show oversized packets being dropped with ICMP blocked. Enabling MSS clamping on the
gateway fixes it. Finally both tunnels are configured, and the vendor tests failover by disabling one
during a sync and confirming BGP shifts traffic without dropping the job.

## Trade-offs
- A VPN is quick and cheap to stand up over existing internet links, but throughput and latency vary
  with the internet path; once traffic is large and permanent, a dedicated circuit pays for itself.
- Joining networks enables any protocol in either direction, which helps legacy integrations, but it
  widens the attack surface compared with a one-way private endpoint to a single service.
- A dedicated circuit gives predictable bandwidth and latency, but it is private rather than
  encrypted by default, so regulated customers often still run IPsec (or MACsec where offered) over
  it; the choice flips back to VPN alone when volumes are modest and lead time matters.
- Static routes are simple to reason about for one or two subnets, but they carry no liveness signal of
  their own, so failover depends on vendor-specific tunnel monitoring (dead-peer detection, route
  tracking) or manual edits; BGP adds configuration and debugging skill in return for standard,
  automatic failover and fewer manual edits.
- Remote-access VPN gives engineers broad network access easily, but identity-aware zero-trust access
  per application is safer when many people need narrow access to a few internal tools.

## Pitfalls
- Overlapping CIDR ranges discovered after the tunnel is up; the symptom is a healthy tunnel status with
  traffic that never arrives, because each side routes the other's addresses locally.
- No MSS clamping or path MTU discovery when ICMP is filtered; the symptom is small requests succeeding
  while large uploads, file reads or long model responses hang mid-transfer.
- Configuring only one of the two provided tunnels; the symptom is an outage during the provider's
  routine maintenance window, often in the middle of the night.
- Advertising whole corporate supernets "to keep it simple"; the symptom shows up in the security review
  as the vendor network being able to reach thousands of customer hosts it never needed.
- Leaving DNS out of the design; the symptom is IP addresses hard-coded in config because internal
  hostnames do not resolve across the tunnel, which then break when the customer re-IPs a server.

## When to use
Use a site-to-site VPN when your cloud workloads need private, bidirectional or multi-protocol access to
a customer's on-prem systems: databases, file shares, legacy APIs, internal identity providers. It is the
default for pilots and moderate steady-state traffic. Prefer a private endpoint when the flow is one-way
to a single cloud service, and a dedicated circuit when sustained volume or latency guarantees matter.
For people rather than systems, prefer per-application zero-trust access over a full-network client VPN.
Measure tunnel uptime, BGP state changes, throughput against the plan, and dropped or fragmented
packets. In interviews and design reviews, mentioning address planning, scope and MTU unprompted signals
that you have actually operated one.

## Role lens
- **Engineer:** Configures ingestion workers to use internal DNS names over the tunnel, sets timeouts and
  retries that survive tunnel failover, and recognises an MTU stall from its distinctive symptom.
- **Architect:** Chooses VPN versus private endpoint versus dedicated circuit, plans non-overlapping
  address space, and decides where the tunnel terminates so its blast radius stays small.
- **FDE:** Fills in the customer's VPN request form with exact peer IPs, prefixes and ports, schedules the
  bulk sync around their business hours, and joins the call when the tunnel will not come up.

## Check yourself
**Q:** Your ingestion job reads from a customer's on-prem database over a new site-to-site VPN. Short
queries return instantly, but any query returning more than a few kilobytes hangs until timeout. The
tunnel shows "up". What is happening and how do you confirm and fix it?
**A:** That size-dependent pattern points to MTU. IPsec encapsulation adds tens of bytes, so full-size
packets exceed the tunnel MTU; if "don't fragment" is set and ICMP "fragmentation needed" messages are
blocked by a firewall, those packets are silently dropped while small ones pass. Confirm by sending pings
with the don't-fragment flag (on Linux, `ping -M do -s 1400 <host>`) at decreasing sizes to find the largest packet that passes, or by capturing
traffic and seeing retransmissions of large segments. Fix it by enabling TCP MSS clamping on the VPN
gateway to a value that fits the tunnel overhead, or by allowing the relevant ICMP messages so path MTU
discovery works. Then retest with a large result set.

## Quiz
### Q: Why do production site-to-site VPNs usually use BGP rather than static routes?
- [ ] BGP encrypts traffic more strongly than static routing
- [x] BGP advertises and withdraws prefixes dynamically, so traffic fails over to the redundant tunnel automatically
- [ ] Static routes cannot be used with IPsec
- [ ] BGP removes the need for non-overlapping address ranges
> A static route says nothing about whether its tunnel is alive, so failover relies on vendor-specific
> tunnel monitoring or manual action. BGP withdraws routes from a failed tunnel as part of the protocol. Encryption is ESP's job, and overlapping ranges remain a problem
> with either routing method.

### Q: A customer asks for a VPN so your SaaS can call their single internal REST API. Their network team worries about exposure. What is the strongest scoping you can offer?
- [ ] Use a pre-shared key instead of certificates to keep configuration simple
- [ ] Advertise the customer's full 10.0.0.0/8 so routing never needs updating
- [x] Terminate in a dedicated integration network, advertise only the API host's address, and allow only its port from your worker subnet
- [ ] Enable split tunneling on every engineer's laptop
> Scoping routes to a single host and filtering ports on both sides limits what the joined networks can
> reach. Broad advertisements create the exposure the network team fears, and PSK choice affects
> authentication rather than reach.

### Q: When is a dedicated circuit such as Direct Connect or ExpressRoute a better choice than a VPN?
- [ ] When the integration is a two-week pilot that needs to start tomorrow
- [ ] Whenever the data is confidential, because VPNs are not encrypted
- [ ] When the customer has overlapping CIDR ranges
- [x] When traffic is large and permanent or needs predictable latency and bandwidth that the internet path cannot guarantee
> Dedicated circuits trade long lead times and higher fixed cost for consistent bandwidth and latency.
> VPNs are encrypted, and a circuit does nothing to fix overlapping address ranges, which still require
> re-addressing or NAT.
