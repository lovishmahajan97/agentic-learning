---
id: security-scanning-and-dependency-auditing
module: python-for-ai
name: Security scanning & dependency auditing (pip-audit, Bandit)
kind: practice
difficulty: core
est_minutes: 14
related:
  - python-for-ai/lockfiles-and-dependency-pinning
  - python-for-ai/uv-poetry-pip
  - python-for-ai/ruff
  - python-for-ai/safetensors
  - safety-security/supply-chain-security-for-models-and-datasets
  - safety-security/owasp-top-10-for-llms
  - backend-fundamentals/secrets-management
  - backend-fundamentals/ci-cd
sources:
  - Official docs: pip-audit (PyPA)
  - Official docs: Bandit (PyCQA)
  - Official docs: OSV (Open Source Vulnerabilities) database
---

## TL;DR
Two complementary automated checks: **pip-audit** compares the exact versions you install against public
vulnerability databases (known CVEs in *other people's code*), and **Bandit** statically scans *your own
code* for risky patterns such as `pickle.load`, `eval`, `shell=True` and hard-coded secrets. Both run in CI
in seconds. Neither proves code is safe: they catch known and pattern-shaped problems cheaply, so humans can
spend review time on design-level risks.

## Why it exists
An AI service is mostly third-party code: an LLM SDK, an HTTP stack, a web framework, tokenizers, parsers for
PDFs and HTML, often torch and transformers. Vulnerabilities are disclosed in packages like these regularly,
and an exact lockfile means you keep running the vulnerable version until someone notices. Nobody reads
advisories for a hundred transitive packages by hand, so without automation the gap between "fix published"
and "fix deployed" is unbounded.

Your own code adds a second class of risk that AI work makes more common. Model artifacts are loaded with
pickle-based formats that execute code on load; LLM output is tempting to pass to `eval`, a shell or SQL;
prototype code hard-codes API keys; `yaml.load` and `trust_remote_code=True` slip in from tutorials. Code
review catches some of this, inconsistently. The naive approach, "we will review carefully and update
dependencies when we remember", fails because both problems scale with code volume and dependency count,
while attention does not. Cheap, deterministic scanners on every pull request close the obvious holes.

## How it works
### pip-audit: known vulnerabilities in dependencies
pip-audit, maintained under PyPA, takes a set of installed or declared packages (the current environment,
`-r requirements.txt`, or a project directory), resolves exact versions, and queries a vulnerability service
(PyPI's advisory data by default, or OSV with `-s osv`) for advisories affecting each version. It reports the
package, version, advisory ID and the fixed versions, exits non-zero when anything is found (so CI fails), and
can emit JSON or CycloneDX SBOM output. `--fix` upgrades to fixed versions where possible; `--ignore-vuln ID`
records a deliberate, reviewed exception.

The design depends on exact versions, *because* advisories are expressed as affected version ranges. That is
why auditing a lockfile or exported pinned requirements is meaningful, while auditing loose ranges in
`pyproject.toml` is not: the answer depends on what would be installed. Auditing a lockfile is also how you
find vulnerable transitive packages you never chose directly.

### Bandit: risky patterns in your code
Bandit parses each Python file into an abstract syntax tree and runs plugins that match known-dangerous
constructs, each with a test ID, severity and confidence. Examples: `B101` assert used (asserts vanish under
`python -O`, so they must not enforce security), `B301` pickle deserialization, `B307` `eval`, `B506`
unsafe `yaml.load`, `B602` subprocess with `shell=True`, `B105` hard-coded password strings, `B608` SQL built
by string formatting. Run `bandit -r src -ll` to report medium severity and above; configure skips per
project (passing the config file explicitly), and suppress a reviewed line with `# nosec B602` plus a
reason. Ruff implements many of the same checks as its `S` rules (from flake8-bandit), which lets teams run
them inside the linter they already have.

Bandit is fast and predictable *because* it matches syntax rather than tracking data flow. The cost is that
it cannot tell whether the string reaching `subprocess` came from a constant or from a user, so it produces
false positives, and it misses vulnerabilities that do not have a recognizable syntactic shape.

### What neither tool covers
- **Malicious or typosquatted packages** that have no CVE: mitigated by locks with hashes, approved
  indexes or mirrors, and reviewing new dependencies.
- **Logic and authorization bugs**, prompt injection, data leakage between tenants: design and review issues.
- **Secrets already committed**: dedicated secret scanners (gitleaks, trufflehog) search history, which
  Bandit's string heuristics do not.
- **Model artifacts**: prefer safetensors over pickle-based checkpoints, pin model revisions, and avoid
  `trust_remote_code=True` for unreviewed repositories.
- **Reachability**: a vulnerable function may be present but never called; tools such as OSV-Scanner,
  Dependabot alerts, Safety or Snyk differ in databases and in triage features, so choose by workflow.

## Example
Code a Bandit run would flag, the safer equivalent, and the CI commands. The scenario is an agent that loads
a classifier, runs a shell tool and stores results.

```python
import pickle, sqlite3, subprocess, shlex
import yaml

API_KEY = "sk-live-123"                                  # B105: hard-coded secret -> load from env/secrets
clf = pickle.load(open("router.pkl", "rb"))              # B301: executes code from an untrusted file
cfg = yaml.load(open("agent.yaml"), Loader=yaml.Loader)  # B506: full loader can build arbitrary objects

def run_tool(cmd_from_llm: str) -> str:                  # B602: model output reaches a shell
    return subprocess.run(cmd_from_llm, shell=True, capture_output=True, text=True).stdout

def save(db: sqlite3.Connection, user: str, answer: str) -> None:
    db.execute(f"INSERT INTO answers VALUES ('{user}', '{answer}')")   # B608: SQL injection

# Safer versions
ALLOWED = {"ls", "wc"}
def run_tool_safe(cmd_from_llm: str) -> str:
    argv = shlex.split(cmd_from_llm)
    if not argv or argv[0] not in ALLOWED:
        raise PermissionError(f"tool not allowed: {argv[:1]}")
    return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout

def save_safe(db: sqlite3.Connection, user: str, answer: str) -> None:
    db.execute("INSERT INTO answers VALUES (?, ?)", (user, answer))

cfg_safe = yaml.safe_load(open("agent.yaml"))
# Weights: store as safetensors; for any pickle-based checkpoint, load only from trusted, pinned sources.

# CI (fails the build on findings):
#   uv export --format requirements-txt --no-hashes --no-emit-project > req.txt && pip-audit -r req.txt
#   bandit -r src -ll
```

## Trade-offs
- Failing CI on any pip-audit finding keeps dependencies current, but blocks unrelated work when an advisory
  has no fix yet; allow time-boxed, reviewed `--ignore-vuln` entries rather than disabling the check.
- Bandit catches common dangerous calls with near-zero setup, but its syntax-only view yields false
  positives; tune with severity thresholds and justified `nosec` comments, or move to a data-flow tool such
  as Semgrep or CodeQL when signal-to-noise becomes the bottleneck.
- Running scanners on every pull request catches problems early, but new advisories appear for code that has
  not changed; also schedule a daily or weekly audit of the deployed lockfile.
- Using Ruff's `S` rules avoids another tool, but may not match Bandit's full plugin set and configuration;
  pick one and keep the rule list explicit.

## Pitfalls
- Auditing `pyproject.toml` ranges or the developer's environment instead of the lock that ships; the symptom
  is a clean report while the production image contains a flagged version.
- Blanket `# nosec` or skipping whole test IDs to make the build green; the symptom is a security review
  finding `shell=True` on model output that the scanner had reported months earlier.
- Treating a clean pip-audit report as "no supply-chain risk"; the symptom is surprise when a malicious
  lookalike package with no CVE is found in the dependency tree.
- Assuming Bandit understands context; the symptom is the team learning to ignore its output because findings
  in tests and constant strings drown real ones, which a config excluding tests and a severity floor prevents.
- Loading pickle-based model files from a public hub because "it is just weights"; the symptom is arbitrary
  code execution on the inference host, which no dependency scanner would report.

## When to use
Add both checks to CI as soon as a project has a lockfile and code that will run with real data or
credentials; for AI services that is usually the first deployment. Run pip-audit against the exact lock on
every PR and on a schedule, and Bandit (or Ruff `S` rules) on source directories with tests excluded or
treated leniently. Track open findings, time from advisory to fix deployed, and the count of suppressions.
In regulated customer environments, the JSON or CycloneDX output often becomes audit evidence. In interviews,
the strong answer explains what each tool can and cannot see rather than listing tools.

## Role lens
- **Engineer:** Fixes or justifies each finding, replaces pickle, `eval` and `shell=True` patterns, and keeps
  suppressions narrow with a reason next to them.
- **Architect:** Sets the policy: which scanners gate merges, severity thresholds, exception expiry, approved
  indexes and model-artifact formats.
- **FDE:** Produces SBOMs and audit reports in the format the customer's security team requires, and adapts
  scanning to their private mirrors and air-gapped build systems.

## Check yourself
**Q:** Your service passes pip-audit and Bandit with zero findings. A security reviewer still blocks the
launch, citing the agent's ability to download and load community fine-tuned models. Is the reviewer right?
**A:** Yes. pip-audit only knows published advisories for Python packages, and Bandit only matches syntax in
our source; neither inspects model artifacts. Pickle-based checkpoints, and repositories loaded with
`trust_remote_code=True`, execute code chosen by whoever published the model, so loading community models is
remote code execution by design unless constrained. Mitigations: accept only safetensors, pin model revisions
or checksums, allowlist sources, scan or review artifacts, and run loading in a sandbox with minimal
credentials. Clean scanner output narrows risk; it does not certify the system.

## Quiz
### Q: Why should pip-audit run against the lockfile rather than the version ranges in pyproject.toml?
- [ ] pip-audit cannot parse pyproject.toml files
- [x] Advisories apply to specific versions, so only the exact resolved set says what is actually vulnerable
- [ ] Ranges are always newer than the lockfile and therefore safe
- [ ] The lockfile contains the vulnerability database
> Vulnerability data is expressed as affected version ranges. A range like httpx>=0.27 could install
> many versions; the lock records exactly what ships, including transitive packages, so the audit is meaningful.

### Q: What kind of problem is Bandit structurally unable to detect well?
- [ ] subprocess calls with shell=True
- [ ] use of pickle.load
- [x] whether a value reaching a dangerous call came from untrusted input across functions
- [ ] hard-coded password-like string literals
> Bandit matches patterns in each file's syntax tree; it does not track data flow. It flags the dangerous
> call itself, but cannot tell a constant from user-controlled input reaching it, hence false positives.

### Q: A team wants to defend against a typosquatted package with no CVE. Which measure helps most?
- [ ] Running pip-audit more frequently
- [ ] Lowering Bandit's severity threshold
- [x] Reviewing new dependencies and installing only from a lockfile with hashes from an approved index
- [ ] Adding nosec comments to import statements
> pip-audit only reports known advisories, and Bandit scans your code, not packages. Controlling what
> enters the lock, and verifying hashes from trusted sources, is what stops an unknown malicious package.
