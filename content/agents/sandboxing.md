---
id: sandboxing
module: agents
name: Sandboxing
kind: pattern
difficulty: core
est_minutes: 14
status: fact-checked
related:
  - agents/computer-use
  - agents/approval-gates-for-consequential-actions
  - agent-reliability/tool-permissions-and-least-privilege
  - agent-runtime-architecture/secrets-and-credential-isolation
  - safety-security/prompt-injection
  - safety-security/tool-privilege-escalation
  - safety-security/data-leakage
  - backend-fundamentals/secrets-management
sources:
  - Official docs: gVisor
  - Official docs: Firecracker
  - OWASP — Top 10 for Large Language Model Applications
---

## TL;DR
A sandbox is an execution boundary around an agent's actions, limiting what the code it runs and the
tools it calls can reach: filesystem, network, credentials, processes, CPU, memory and time. It exists
because an agent's actions are untrusted input: the model is probabilistic and prompt-injectable, and it
runs with whatever access you gave it. The goal is bounded blast radius: a wrong or hijacked action can
waste a container, not leak a database. Isolation strength, startup latency and compatibility trade
against each other.

## Why it exists
Useful agents run code and shell commands, because that is the most general tool there is: a coding
agent runs tests, a data agent runs pandas, a research agent scrapes pages. Arbitrary code can do
anything the process it runs in can do. Two facts make this different from running your own scripts.
First, the code is chosen by a model, which is wrong some fraction of the time: `rm -rf` on the wrong
path, a loop that fills the disk, a script that posts to the production endpoint instead of staging.
Second, the model's choices are influenced by whatever it reads, so an attacker who can place text where
the agent will see it (a web page, a README in a dependency, a ticket comment) can choose the code.
Prompt injection turns "the model makes mistakes" into "the attacker runs code with the agent's access".

What is that access? Typically the developer's own: a home directory with `~/.ssh` and `~/.aws`,
environment variables holding API keys, a network route to internal services, a Docker socket. An agent
running unsandboxed on that machine is a remote shell for anyone who can get a sentence in front of it.

The naive defences do not hold. "The model is aligned" is a statistical property, not a boundary, and
alignment does not survive injection. "We review the code before running it" does not scale past a few
runs an hour and is itself a rubber-stamp risk. "Disable code execution" removes most of the value.
Sandboxing keeps the capability and bounds the damage: the agent can do anything *inside* the box, and
the box can touch nothing that matters.

## How it works
### Threat model first
Decide what you are protecting: the host machine, your data stores, your credentials, other tenants on
the same infrastructure, the outside world (sending spam, attacking third parties), and the integrity
of the agent's own task (an injected instruction that quietly changes the output). Each maps to a
different control; a sandbox that stops host compromise but allows free egress still leaks every secret
the agent can read.

### Isolation tiers
From weakest to strongest, with cost rising with strength:
- **Same process with restrictions** (a restricted interpreter, dropped privileges): cheap, easily
  escaped, fine for nothing adversarial.
- **Container** (Linux namespaces and cgroups): isolates the filesystem view, process tree and
  resources, but shares the host kernel, so a kernel exploit escapes it. Starts in well under a second.
- **User-space kernel** (gVisor): intercepts syscalls in a sandboxed kernel implementation so most of
  the host kernel's attack surface is hidden; some syscall compatibility and performance cost.
- **MicroVM** (Firecracker and similar): a separate guest kernel with a minimal device model, boot
  times in the low hundreds of milliseconds (illustrative), near-VM isolation. The common choice for
  multi-tenant code execution services.
- **WebAssembly runtimes**: in-process isolation for code compiled to Wasm with a capability-based host
  interface; strong for the languages it supports, not a general shell.
Choose by who the adversary is: for a single developer's coding agent on their own laptop, a container
is a reasonable floor; for untrusted multi-tenant execution, a microVM or gVisor is the floor.

### Filesystem
Ephemeral root that is discarded after the run; mount only the working directory read-write; everything
else read-only or absent. No home directory, no `.env`, no cloud credential files, no Docker socket.
Outputs that should persist are copied out explicitly, which is also the point at which they can be
reviewed.

### Network
Default-deny egress. Allowlist the hosts the task needs (a package registry, the API the agent is
integrating against) through a proxy that logs. This is the control that matters most after injection,
because an injected instruction's usual payload is "send what you can read to this address". Be honest
about residual channels: an allowed registry or code host can still carry data out (an issue comment, a
package upload), and DNS is a covert channel; allowlists shrink the surface, they do not close it.

### Credentials
Secrets never enter the sandbox. If the agent must call an authenticated API, it calls a broker outside
the box that holds a short-lived, narrowly scoped token and applies policy per call; the sandbox sees
only the broker's endpoint. Where a token must be inside, make it per-run, least-privilege and expiring,
so exfiltration buys minutes of access to one project rather than years of access to everything.

### Resources and time
CPU and memory limits, a process-count limit (runaway process creation, such as an unbounded
multiprocessing pool or a recursive shell script, happens by mistake as well as by attack), disk quotas, output-size caps so a `cat` of a huge file cannot flood the transcript, and a
wall-clock timeout per command and per run. These protect availability and cost, and they bound the
damage of ordinary bugs.

### Lifecycle
One fresh environment per task or session; snapshot a warmed image for fast start; destroy on
completion. Nothing persists except reviewed outputs. Persistence across sessions, when needed, goes
through an explicit, inspectable channel (a git push to a branch, an artifact store), not a long-lived
writable volume.

### Sandboxing non-code tools
The same thinking applies to API tools: give the agent a scoped key, point it at a staging environment
or a dry-run mode, and treat "production" as something reached only through an approval gate. A sandbox
for a browser-using agent is a disposable VM with a logged-in session scoped to one account and no
other.

## Example
A minimal container sandbox for running model-written Python: ephemeral, read-only root, no network,
resource-limited, non-root, with a hard timeout that also kills the container. The working directory
must be writable by the unprivileged uid, and the bind-mount source must be an absolute path (a bare
name after `-v` is interpreted as a named volume, not a host directory). Stronger isolation swaps the `docker` command for a microVM
launcher; the policy stays the same.

```python
import subprocess, uuid, pathlib

def run_in_sandbox(code: str, workdir: pathlib.Path, timeout_s: int = 60) -> dict:
    (workdir / "main.py").write_text(code)
    name = f"sbx-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",                       # default deny; add a logging proxy if egress is required
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        "--read-only", "--tmpfs", "/tmp:size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "65534:65534",                   # unprivileged uid inside the container
        "-v", f"{workdir.resolve()}:/work:rw", "-w", "/work",   # absolute path, or docker treats it as a volume name
        "python:3.12-slim", "python", "-I", "main.py",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)   # the CLI dying does not stop the container
        return {"ok": False, "stdout": "", "stderr": f"killed after {timeout_s}s"}
    return {"ok": p.returncode == 0,
            "stdout": p.stdout[-20_000:], "stderr": p.stderr[-20_000:]}   # cap what enters the transcript
```

## Trade-offs
- Stronger isolation (microVM, gVisor) buys a real security boundary at the cost of startup latency,
  memory overhead per sandbox and some syscall or device incompatibility; it flips toward plain
  containers when the only user is the developer running it.
- Default-deny networking blocks exfiltration but also blocks package installs and API calls; a logged
  allowlist proxy restores the needed ones at the price of maintaining the list and accepting residual
  channels through allowed hosts.
- Ephemeral environments guarantee a clean slate and remove persistence attacks, but every run pays
  setup time; warmed snapshots recover most of it at the cost of image management.
- Keeping credentials outside the sandbox behind a broker is the safest design, but it means writing a
  broker and a policy per API; a scoped, short-lived token inside the box is the pragmatic fallback.
- Tight resource limits stop runaway cost and fork bombs but produce false failures on legitimately
  heavy tasks; the limits must be set from measured task profiles, not guesses.

## Pitfalls
- Running the agent on the developer's machine with their shell and credentials; the symptom is an
  incident report that begins "the agent read a web page and then…".
- Mounting the home directory or passing environment variables wholesale into the container; the
  symptom is API keys visible in the agent's transcript or exfiltrated after an injection.
- Allowing full egress "just for pip"; the symptom is a sandbox that cannot be escaped but freely posts
  your data to an attacker's endpoint.
- Treating a container as a hard security boundary for untrusted multi-tenant code; the symptom is a
  kernel CVE becoming a cross-tenant breach.
- No process, disk or output limits; the symptom is a model-written loop that fills the disk, forks
  until the host stalls, or dumps 50 MB into the context window.
- Persisting a writable volume across sessions for convenience; the symptom is a planted file from one
  run altering the next run's behaviour.

## When to use
Sandbox any agent that executes model-written code or shell commands, browses the web, or installs
packages, which is nearly every coding, data or research agent; the only question is the tier. Pick the
tier by adversary: personal tooling can use containers, anything that processes third-party content or
serves multiple tenants should assume injection and use microVM-class isolation plus egress control.
Measure sandbox start time, task failures caused by limits, egress denials (each is either a missing
allowlist entry or an attempted exfiltration, and you want to know which), and secrets found in
transcripts. In security reviews the sandbox and the egress policy are the first two questions; in
interviews, expect to explain why a container alone is not enough for untrusted code and why the
network policy matters more than the filesystem policy after injection.

## Role lens
- **Engineer:** Builds and tunes the sandbox launcher (image, mounts, limits, timeouts, egress proxy),
  caps tool output sizes, and tests that a planted injection cannot reach a secret or an external host.
- **Architect:** Chooses the isolation tier per workload from the threat model, owns the
  credential-broker design and egress policy, and defines what may persist across runs and through
  which reviewed channel.
- **FDE:** Fits the sandbox into the customer's infrastructure and security policy, demonstrates egress
  denial and credential isolation during security review, and explains why their staging environment is
  part of the design.

## Check yourself
**Q:** A customer wants a coding agent that can run their test suite, install packages and open pull
requests, deployed as a shared service for 200 engineers. Design the sandbox and explain each boundary
with the attack it stops.
**A:** Per-task microVM or gVisor sandbox rather than a plain container, because 200 engineers'
repositories and whatever dependencies they pull are untrusted multi-tenant code and a shared kernel is
a cross-tenant risk. Ephemeral root with only the checked-out repository mounted read-write, no home
directory, no cloud credential files, so a wrong `rm` or an injected script finds nothing to steal.
Default-deny egress with a logging proxy allowlisting the package registry and the git host, which
blocks the standard injection payload of exfiltrating what the agent can read, while accepting that the
git host is a residual channel. No long-lived tokens inside: pull-request creation goes through a broker
outside the box holding a per-run, repository-scoped, expiring token. CPU, memory, pids, disk and
timeout limits sized from measured test-suite profiles, with output caps so logs cannot flood the
context. Destroy on completion; the pull request is the only persistent output, and a human reviews it.

## Quiz
### Q: After a prompt injection, which sandbox control most directly prevents the agent from leaking data it can legitimately read?
- [ ] A read-only root filesystem
- [ ] A CPU and memory limit
- [x] Default-deny network egress with a logged allowlist
- [ ] Running as a non-root user
> Injection payloads typically instruct the agent to send what it sees to an external address.
> Filesystem and resource controls do not stop an outbound request; egress policy does, within the
> limits of allowed hosts.

### Q: Why is a plain container usually not considered sufficient for executing untrusted, multi-tenant code?
- [ ] Containers cannot limit memory or CPU
- [x] Containers share the host kernel, so a kernel vulnerability can become an escape into other tenants
- [ ] Containers cannot be destroyed after use
- [ ] Containers always have network access
> Namespaces and cgroups isolate views and resources but not the kernel attack surface. MicroVMs or
> user-space kernels add a separate kernel boundary, which is why they are the floor for multi-tenant
> execution.

### Q: What is the safest way to let a sandboxed agent call an authenticated external API?
- [ ] Mount the developer's credentials file read-only
- [ ] Pass the long-lived API key as an environment variable
- [x] Route calls through a broker outside the sandbox that holds a short-lived, narrowly scoped token and applies per-call policy
- [ ] Give the agent the key but ask it in the prompt not to print it
> Secrets that enter the sandbox can be read and exfiltrated by injected code. A broker keeps the
> credential outside, scopes and expires it, and provides a policy and logging point per call.
