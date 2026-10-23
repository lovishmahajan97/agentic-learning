---
id: code-execution-sandboxes-for-agents
module: agent-frameworks
name: code-execution sandboxes for agents (E2B, Modal, Docker)
kind: tool
difficulty: core
est_minutes: 18
status: typed-verified
lesson_type: lab
agent_relevance: core
related:
  - agents/sandboxing
  - agents/computer-use
  - safety-security/excessive-agency
  - safety-security/tool-privilege-escalation
  - agent-reliability/tool-permissions-and-least-privilege
  - backend-fundamentals/kubernetes
  - infra-capacity-economics/serverless-gpus
sources:
  - E2B documentation
  - Modal documentation (Sandboxes)
  - Docker documentation and Docker SDK for Python
  - gVisor documentation
  - Firecracker documentation
---

## TL;DR
Agents that write and run code need an isolated place to run it. E2B gives hosted microVM sandboxes,
Modal gives programmable cloud sandboxes, and Docker gives self-managed containers that need hardening
before they are safe.

## For agent builders
- **Why it matters for agents.** A code-interpreter tool turns model output into executed programs.
  Without isolation, one prompt injection can read secrets, call internal services or delete data on the
  host running your agent.
- **Where it shows up in your stack.** A `run_code` or `execute_python` tool whose implementation creates
  a sandbox, writes files, runs code with limits, and returns stdout, stderr and artifacts to the model.
- **What you control.** Isolation level (container, gVisor, microVM), network egress, CPU, memory and
  time limits, mounted files, installed packages, sandbox lifetime and what output flows back.
- **What you can skip.** Writing your own hypervisor integration. Pick a product or a hardened container
  runtime; the threat model is covered in the sandboxing lesson.

## Lab
### Task
Build the agent side of a `run_python` tool: the part you own whichever sandbox product sits behind
it. You add one sandbox per session, an environment allowlist so no host secret enters, a kill after
timeouts, tail-preserving output truncation, and an idle reaper. Together they implement steps 2 to 5
of the agent-side contract in How it works.
- **Time box:** 45-75 minutes.
- **Prerequisites:** this lesson's contract and hardening checklist; the sandboxing lesson's threat model.
- **Runs anywhere:** `FakeBackend` stands in for E2B, Modal or the Docker tool in Examples. It is
  **not** an isolation boundary; swap in a real backend before running anything a model wrote.
- **Needs:** Python 3.10+ and `pytest`; checked with Python 3.12 and pytest 9.1.1.

### Starter code
```python
# code_tool.py: the agent-side run_python tool (session reuse, no secrets, bounded output, reaping)
import contextlib, io, itertools, os, time
from dataclasses import dataclass

MAX_CHARS = 200        # what may flow back into the model's context per call
IDLE_S = 300           # kill a session's sandbox after this many idle seconds
SAFE_ENV = {"PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"}

@dataclass(frozen=True)
class Limits:
    network: bool = False
    mem_mb: int = 256
    timeout_s: int = 10

class FakeBackend:
    """Stand-in for E2B, Modal or the Docker tool in Examples, so the lab runs anywhere.
    NOT an isolation boundary: it exec()s in-process and is only safe for these test inputs.
    Each sandbox keeps its own globals (like a live interpreter) and sees only the env it was given.
    A run containing 'while True' simulates the backend killing it at the timeout."""
    def __init__(self):
        self.live: dict[str, dict] = {}
        self.created = 0
        self._ids = itertools.count(1)
    def create(self, limits: Limits, env: dict) -> str:
        sid = f"sbx-{next(self._ids)}"
        self.live[sid] = {"limits": limits, "env": dict(env), "ns": {"ENV": dict(env)}}
        self.created += 1
        return sid
    def exec(self, sid: str, code: str) -> dict:
        box = self.live[sid]
        if "while True" in code:
            raise TimeoutError(f"killed after {box['limits'].timeout_s}s")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(code, box["ns"])
            return {"exit_code": 0, "output": buf.getvalue()}
        except Exception as e:
            return {"exit_code": 1, "output": buf.getvalue() + f"{type(e).__name__}: {e}"}
    def kill(self, sid: str) -> None:
        self.live.pop(sid, None)

class CodeTool:
    def __init__(self, backend, limits: Limits = Limits(), clock=time.monotonic):
        self.backend, self.limits, self.clock = backend, limits, clock
        self.sessions: dict[str, tuple[str, float]] = {}    # session_id -> (sandbox_id, last_used)

    def run_python(self, session_id: str, code: str) -> dict:
        """Tool the model calls. Returns a bounded {"exit_code", "output"} dict."""
        # YOUR CODE (1): reuse this session's sandbox if it has one, else create it and remember it
        # YOUR CODE (2): create sandboxes with SAFE_ENV only, never the agent's own environment
        sid = self.backend.create(self.limits, dict(os.environ))
        try:
            res = self.backend.exec(sid, code)
        except TimeoutError as e:
            res = {"exit_code": None, "output": f"timeout: {e}"}
            # YOUR CODE (3): a timed-out interpreter may still be spinning: kill it and forget it
        # YOUR CODE (4): keep at most MAX_CHARS, the TAIL of the output (errors print last), and
        # prefix "[truncated N chars]\n" when you cut N characters
        return res

    def reap(self) -> list[str]:
        """Called by a periodic job: kill sandboxes idle longer than IDLE_S, return session ids."""
        # YOUR CODE (5)
        return []

if __name__ == "__main__":
    os.environ["DB_PASSWORD"] = "s3cret"                 # the agent host holds a credential
    now = [0.0]
    backend = FakeBackend()
    tool = CodeTool(backend, clock=lambda: now[0])
    print(tool.run_python("s1", "import math\nrows = [math.sqrt(i) for i in range(5)]"))
    print(tool.run_python("s1", "print(round(sum(rows), 3))"))
    print(tool.run_python("s1", "print(ENV.get('DB_PASSWORD'))"))
    print(tool.run_python("s1", "print('x' * 1000)")["output"][:40])
    print(tool.run_python("s2", "while True: pass"))
    now[0] = 400.0
    print("reaped:", tool.reap(), "| live:", len(backend.live), "| created:", backend.created)
```

As given, the second call fails with `NameError` (state lost), the third prints `s3cret`, 1,000
characters flow back, and the run ends with 5 sandboxes created and 5 still live.

### Expected output
```
{'exit_code': 0, 'output': ''}
{'exit_code': 0, 'output': '6.146\n'}
{'exit_code': 0, 'output': 'None\n'}
[truncated 801 chars]
xxxxxxxxxxxxxxxxxx
{'exit_code': None, 'output': 'timeout: killed after 10s'}
reaped: ['s1'] | live: 0 | created: 2
```

### Tests
```python
# test_code_tool.py: run with `pytest -q`
import pytest
from code_tool import MAX_CHARS, SAFE_ENV, CodeTool, FakeBackend

@pytest.fixture
def env():
    now = [0.0]
    backend = FakeBackend()
    return backend, CodeTool(backend, clock=lambda: now[0]), now

def test_session_keeps_interpreter_state(env):
    backend, tool, _ = env
    tool.run_python("s1", "df = [3, 4]")
    assert tool.run_python("s1", "print(sum(df))")["output"] == "7\n"
    tool.run_python("s2", "print(1)")
    assert backend.created == 2                       # one sandbox per session, not per call

def test_no_agent_secrets_inside(env, monkeypatch):
    backend, tool, _ = env
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    assert tool.run_python("s1", "print(ENV.get('DB_PASSWORD'))")["output"] == "None\n"
    for box in backend.live.values():
        assert box["env"] == SAFE_ENV                 # an allowlist, not a copy of os.environ
        assert box["limits"].network is False

def test_output_is_bounded_and_keeps_the_tail(env):
    _, tool, _ = env
    out = tool.run_python("s1", "print('a' * 500)\nraise ValueError('the real error')")["output"]
    assert out.startswith("[truncated ") and out.endswith("ValueError: the real error")
    assert len(out.split("\n", 1)[1]) == MAX_CHARS

def test_timeout_kills_the_sandbox(env):
    backend, tool, _ = env
    res = tool.run_python("s1", "while True: pass")
    assert res["exit_code"] is None and "timeout" in res["output"]
    assert backend.live == {}
    tool.run_python("s1", "print(1)")
    assert backend.created == 2                       # a fresh sandbox after the kill

def test_idle_sandboxes_are_reaped(env):
    backend, tool, now = env
    tool.run_python("old", "x = 1")
    tool.run_python("busy", "x = 1")
    now[0] = 250.0
    tool.run_python("busy", "print(x)")               # use refreshes idle time
    now[0] = 400.0
    assert tool.reap() == ["old"]
    assert list(tool.sessions) == ["busy"] and len(backend.live) == 1
```

All five fail on the starter. The reaper test uses an injected clock, so idle time is exact and the
test never sleeps.

### Failure scenarios
- **Truncate from the head.** The model sees 200 characters of data and never the traceback, then
  retries the same broken code. Debug by logging the last line of every truncated result.
- **Copy `os.environ` and delete known keys.** The next secret someone adds leaks. Debug by diffing
  the sandbox env against `SAFE_ENV`; only an allowlist survives new variables.
- **Refresh `last_used` only on create.** Busy sessions get reaped mid-analysis and lose their data
  frames; here the reaper test returns `['old', 'busy']`. Debug by logging sandbox age and idle time
  at each reap.
- **Reuse a sandbox after a timeout.** On a real backend the next call queues behind the spinning loop
  and also times out; here the timeout test fails because the sandbox is still live. Debug by counting
  consecutive timeouts per sandbox id.

### Submission
- **Code:** `code_tool.py` with the five `YOUR CODE` parts done; `FakeBackend` unchanged.
- **Test run:** `pytest -q` showing 5 passed.
- **Program output:** the seven stdout lines from `python code_tool.py`, matching Expected output.
- **Real backend:** a short `DockerBackend` (built from the Examples tool) or E2B adapter with the same
  `create`, `exec` and `kill` methods, plus the run showing no containers left afterwards.
- **Short note (5 lines):** which backend you would ship for your data residency rules, and why.

## Why it exists
- **Generated code is untrusted code.** It comes from a model that read user input, web pages and tool
  results, any of which can contain injected instructions.
- **Plain containers share the host kernel.** A kernel exploit or misconfiguration (privileged mode,
  mounted Docker socket) escapes to the host; microVMs (Firecracker) and user-space kernels (gVisor) add a
  stronger boundary.
- **Agents need state across steps.** Data analysis agents load a file, explore, plot and iterate, so a
  sandbox must persist a session (files and a live interpreter) across tool calls.
- **Cost and latency matter.** A sandbox per call must start fast; products keep warm pools and snapshot
  templates so start-up is closer to a second than to a minute (illustrative order of magnitude).

## How it works
### The agent-side contract
1. **The model emits code** as a tool call, for example `run_python(code="...")`.
2. **The tool gets or creates a sandbox** for this session, with limits and no secrets inside.
3. **It runs the code** with a timeout and captures stdout, stderr, errors and files.
4. **It returns a bounded result** (truncated output, error text, artifact references), because huge
   outputs flood the context window.
5. **It destroys or expires the sandbox** when the session ends or after an idle timeout.

### The three options
| Option | Isolation | Model | Strengths | Costs |
|---|---|---|---|---|
| E2B | Firecracker microVMs, hosted (self-host option exists) | SDK creates sandboxes; code-interpreter SDK runs Jupyter-style cells | fast start, stateful sessions, charts and files returned, built for agents | per-second vendor pricing, data leaves your cloud unless self-hosted |
| Modal | gVisor-based containers on Modal's cloud | `modal.Sandbox.create(...)` from Python with an image, timeout, network controls | any image, GPUs, same platform as your other Modal jobs | vendor platform, sandboxes run in Modal's cloud |
| Docker (self-managed) | Linux namespaces and cgroups on your host; add gVisor (`runsc`) or Kata for more | Docker SDK or a job runner starts a container per task | full control, runs on your infra, no vendor | you own hardening, pooling, cleanup and escape risk |

### Hardening checklist for any option
| Control | Why |
|---|---|
| No network, or an egress allowlist | blocks data exfiltration and internal service calls |
| No secrets in the sandbox | injected code can read every environment variable and file |
| CPU, memory, process and time limits | stops fork bombs, infinite loops and cost runaway |
| Non-root user, read-only root filesystem, dropped capabilities | shrinks what an escape attempt can use |
| Never mount the Docker socket or host paths | either one is effectively root on the host |
| Fresh sandbox per user or tenant | no cross-user files or state |

## Diagram
Follow the tool call: the agent never runs code itself; a sandbox with limits does, and only a bounded
result returns to the model.

```mermaid
flowchart TD
    M["Model emits run_python call"] --> T["Code tool in agent"]
    T --> P{"Session sandbox exists?"}
    P -->|"no"| C["Create sandbox: limits, no secrets"]
    P -->|"yes"| R["Reuse sandbox"]
    C --> X["Execute with timeout"]
    R --> X
    X --> O["stdout, stderr, files"]
    O --> B["Truncate and summarize"]
    B --> M
    X -.->|"egress blocked"| N["Internal network"]
    T -->|"session end or idle"| K["Kill sandbox"]
```

## Examples
### A hardened Docker code tool you can run locally
Requires Docker and `pip install docker`. Each call starts a fresh, offline, resource-capped container.

```python
import docker

client = docker.from_env()

def run_python(code: str, timeout_s: int = 10) -> dict:
    """Tool: run untrusted Python in an isolated container and return bounded output."""
    c = client.containers.run(
        "python:3.12-slim", ["python", "-c", code],
        detach=True, network_disabled=True, mem_limit="256m", nano_cpus=500_000_000,
        pids_limit=64, read_only=True, user="65534:65534", cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
    )
    try:
        status = c.wait(timeout=timeout_s)          # raises on timeout
        out = c.logs(stdout=True, stderr=True).decode(errors="replace")
        return {"exit_code": status["StatusCode"], "output": out[-4000:]}  # keep the tail only
    except Exception as e:
        return {"exit_code": None, "output": f"timeout or error: {type(e).__name__}"}
    finally:
        c.remove(force=True)                        # force kills a still-running container first

print(run_python("print(sum(range(10)))"))
print(run_python("import urllib.request; urllib.request.urlopen('http://example.com')"))  # fails: no network
print(run_python("while True: pass", timeout_s=3))                                         # killed
```

### Hosted sandboxes: E2B and Modal for a data-analysis agent
Both snippets show the core calls; method names have changed between major SDK versions, so check the
current docs.

```python
# E2B: pip install e2b-code-interpreter, E2B_API_KEY set
from e2b_code_interpreter import Sandbox

sbx = Sandbox.create(timeout=300)              # older SDKs used Sandbox(); stateful for this session
sbx.files.write("/home/user/sales.csv", open("sales.csv", "rb").read())
ex = sbx.run_code("import pandas as pd; df = pd.read_csv('/home/user/sales.csv'); df.describe()")
print(ex.logs.stdout, ex.error, ex.results)    # results can include rich outputs such as charts
ex2 = sbx.run_code("df['revenue'].sum()")      # same interpreter: df still exists
sbx.kill()

# Modal: pip install modal, modal token set
import modal
app = modal.App.lookup("agent-sandboxes", create_if_missing=True)
sb = modal.Sandbox.create(app=app, image=modal.Image.debian_slim().pip_install("pandas"),
                          timeout=120, block_network=True)
p = sb.exec("python", "-c", "print(2 + 2)")
print(p.stdout.read())
sb.terminate()
```

| Need | Best fit |
|---|---|
| Stateful notebook-style analysis with charts | E2B code interpreter |
| Custom images, GPUs, or jobs already on Modal | Modal Sandboxes |
| Data must stay on your infrastructure | Docker with gVisor or Kata, or a self-hosted product |

## Production patterns
- **One sandbox per session, killed on idle.** Map agent session id to sandbox id, reuse it across tool
  calls, and set an idle timeout so abandoned sessions stop billing. Monitor live sandbox count and
  average lifetime against active sessions.
- **Block egress by default, allowlist when needed.** Start with no network; if the agent must install
  packages, pre-bake them into the image or template instead of opening the internet. Monitor blocked
  connection attempts as a prompt-injection signal.
- **Bound everything that returns to the model.** Truncate stdout to a few thousand characters, return
  file references instead of contents, and summarize big tables. Monitor tokens added per code-tool call.
- **Pre-build images or templates.** Bake common libraries into a custom image or E2B template so start-up
  stays fast and runs are reproducible. Monitor sandbox start latency p95.
- **Add a stronger runtime for self-managed containers.** Run untrusted code under gVisor (`--runtime=runsc`)
  or Kata Containers on dedicated nodes, never on the host that holds agent secrets. Monitor node-level
  CPU and memory saturation.
- **Log code and outcomes for audit and evals.** Store the executed code, exit code and truncated output
  with the trace, so you can replay failures and score code-tool success rate.

## Trade-offs
- **Hosted vs self-managed.** E2B and Modal remove isolation and pooling work but send code and data to a
  vendor; Docker keeps data in-house but makes you responsible for escapes and cleanup.
- **Stronger isolation vs speed and cost.** MicroVMs and gVisor add a boundary at some start-up and
  syscall overhead; plain containers are faster but rely on the shared kernel.
- **Stateful sessions vs hygiene.** Reusing a sandbox keeps variables and files for iterative analysis,
  but long-lived sandboxes accumulate state and cost; cap lifetime.
- **Network access vs safety.** Internet access lets agents fetch data and install packages but opens
  exfiltration paths; the allowlist is the compromise.

## Pitfalls
- **Running generated code in the agent process.** `exec()` or a subprocess on the agent host exposes every
  secret and network route. Fix: always cross a sandbox boundary.
- **Mounting the Docker socket for convenience.** Symptom: a container can start privileged containers.
  Fix: never mount it; use a separate job runner with its own credentials.
- **Passing API keys into the sandbox.** Injected code prints or sends them. Fix: keep credentials in the
  agent's tools outside the sandbox, and hand the sandbox only data.
- **Leaking sandboxes.** Symptom: bills climb with abandoned sessions. Fix: timeouts, `finally` cleanup and
  a reaper job that kills sandboxes past their lifetime.

## When to use
- **Use when:** agents analyse data, generate charts, transform files, test code or run user-provided
  scripts.
- **Avoid when:** a fixed function tool can do the job; giving the model a general interpreter is far more
  power than calculating a total needs.
- **Measure:** code-tool success rate, sandbox start latency, sandbox minutes per session, blocked egress
  attempts and tokens returned per call.
- **Interviews:** explain why containers alone are a weak boundary, and walk through the hardening list.

## Role lens
- **Engineer:** Implements the code tool with limits, timeouts, output truncation and cleanup, and tests it
  with hostile inputs such as infinite loops and network calls.
- **Architect:** Chooses hosted microVMs, Modal or self-managed gVisor based on data residency, cost and
  isolation needs, and keeps secrets outside every sandbox.
- **FDE:** Explains to a customer's security team where generated code runs, what it can reach, and how
  sandboxes are destroyed, with the hardening checklist as evidence.

## Check yourself
**Q:** Your data-analysis agent runs model-written Python with `subprocess` on the same server as the
agent, which holds a database password in an environment variable. Security asks you to fix it this
sprint. What do you change?
**A:** Move execution across an isolation boundary: an E2B or Modal sandbox, or a self-managed container
under gVisor on separate nodes. Inside it, no secrets and no network by default, CPU, memory, process and
time limits, a non-root user and a read-only filesystem. Keep the database credential in a separate,
narrow query tool outside the sandbox and pass only result data in. Reuse one sandbox per session with an
idle timeout, truncate output returned to the model, and log executed code for audit. Test with
injection cases that try to read env vars or call the network.

## Interview questions
### Why is a plain Docker container not enough isolation for agent-generated code?
- **It shares the host kernel.** A kernel vulnerability or misconfiguration (privileged mode, mounted
  socket, broad capabilities) lets code escape to the host.
- **Stronger options exist.** gVisor intercepts syscalls in user space and Firecracker or Kata microVMs
  give each sandbox its own kernel, so escapes need a much harder exploit.

### How would you design the run_code tool for a data-analysis agent?
- **Session-scoped sandbox.** Create a sandbox per agent session, upload the user's files, and reuse the
  interpreter across calls so variables persist.
- **Limits and bounded results.** Apply timeouts and resource caps, return truncated stdout, errors and
  file references, and kill the sandbox on idle or session end.

### Your sandbox bill tripled last month. What do you look at?
- **Lifetime versus activity.** Compare sandbox minutes with active session minutes; a gap means missing
  idle timeouts or cleanup failures.
- **Start-up and retries.** Check whether failed runs are retried in fresh sandboxes, and whether images
  install packages at start-up instead of being pre-built.

### When would you choose self-managed sandboxes over E2B or Modal?
- **Data residency and control.** When code and data must stay in your VPC or on-premises, or a customer
  forbids third-party execution.
- **Accepting the cost.** You then own gVisor or Kata setup, pooling, cleanup and patching, so it is worth
  it only when the requirement is real.

### How do you stop a prompt-injected agent from exfiltrating data through the sandbox?
- **No secrets and no egress.** Keep credentials outside, disable network or allowlist specific hosts, and
  give the sandbox only the data the task needs.
- **Detect and audit.** Log executed code and blocked connections, alert on egress attempts, and include
  injection cases in your eval suite.

## Quiz
### Q: Which change most reduces risk when an agent runs generated code?
- [ ] Using a larger model that writes better code
- [x] Running the code in an isolated sandbox with no secrets and no network by default
- [ ] Asking the model to promise not to call the network
- [ ] Running the code as root so it never fails on permissions
> Generated code can be steered by injected instructions, so the boundary must be enforced by isolation,
> no secrets and blocked egress, not by the model's own judgment.

### Q: Why do agent sandboxes often persist across several tool calls in one session?
- [ ] Persistent sandboxes are always cheaper than fresh ones
- [ ] Sandboxes cannot be created more than once per user
- [x] Iterative analysis needs files and interpreter state such as loaded data frames to survive between calls
- [ ] It removes the need for timeouts
> A data-analysis agent loads data in one call and explores it in the next. A session-scoped sandbox keeps
> that state, with an idle timeout so it does not live forever.

### Q: What is the main reason to truncate sandbox output before returning it to the model?
- [ ] Models cannot read program output
- [ ] Truncation encrypts the output
- [ ] Sandboxes cannot return more than one line
- [x] Large outputs consume the context window and cost tokens without helping the next step
> Printing a whole data frame can add thousands of tokens to every later step. Returning a tail, an error
> summary or a file reference keeps the loop cheap and focused.

## Use cases & projects
### Use cases
- **Data-analysis agents.** Users upload CSVs; the agent writes pandas code, runs it in a sandbox and
  returns charts and tables.
- **Coding agents that run tests.** The agent edits a repository clone inside a sandbox and runs the test
  suite before proposing a patch.
- **Spreadsheet and file conversion assistants.** Generated scripts transform documents without touching
  the host.
- **Evaluation harnesses.** Execute model-generated code against unit tests safely at scale.

### Mini project
**Build a sandboxed code tool and attack it (2-3 hours).**
- **Goal:** a `run_python` tool for a small tool-calling agent, backed by Docker (or E2B), that survives
  hostile code.
- **Steps:** implement the hardened Docker tool from this lesson; connect it to a tool-calling loop that
  answers questions about a CSV; write 6 hostile tests (infinite loop, fork bomb, network call, reading env
  vars, writing to `/`, a 10 MB print); add session reuse with an idle timeout.
- **Done when:** the agent answers 3 CSV questions correctly, all 6 hostile tests are contained with a clear
  error, no containers remain after the run, and returned output never exceeds your size cap.
