---
id: git-and-code-review-workflow
module: engineering-fundamentals
name: Git and code review workflow
kind: practice
difficulty: core
est_minutes: 18
status: typed-verified
lesson_type: lab
agent_relevance: core
mastery: implement
code_tested: yes
prerequisites:
  - engineering-fundamentals/command-line-and-environment-basics
related:
  - backend-fundamentals/ci-cd
  - llmops-platform/ci-cd-for-prompts-and-models
  - engineering-fundamentals/testing-fundamentals
  - engineering-fundamentals/debugging-method
  - python-for-ai/lockfiles-and-dependency-pinning
  - safety-security/supply-chain-security-for-models-and-datasets
sources:
  - Chacon and Straub — Pro Git (2nd ed.)
  - git documentation — git-rebase, git-bisect, git-reflog (git 2.56)
  - Google Engineering Practices — Code Review Developer Guide
---

## TL;DR
Git stores snapshots linked by parent pointers; branches are movable labels on them. Small branches,
clear commits and review before merge keep a codebase safe to change, which matters more once an AI
assistant writes much of the diff.

## For agent builders
- **Why it matters for agents.** Agent behaviour lives in code, prompts, tool schemas and eval sets.
  Versioning all four in git lets you bisect a quality drop to the exact prompt edit that caused it.
- **Where it shows up in your stack.** Pull requests that run tests and evals in CI, prompts stored as
  files next to code, and AI coding assistants (including coding agents) proposing diffs you must
  review.
- **What you control.** Branch and commit size, what goes into each commit, when you rebase, and how
  hard you review generated code before it merges.
- **What you can skip.** Git's plumbing commands and exotic workflows. Master snapshots, branches,
  rebase versus merge, `revert`, `bisect` and `reflog`.

## Lab
### Task
A coding assistant opened `feat/invoice` against your agent repo.

Write the pre-merge gate that reads the branch with real git and reports the AI-code checklist from
this lesson as findings: unlisted
imports, `shell=True`, broad `except`, a prompt change with no eval change, a secret anywhere in the
branch history, and unsquashed `fixup!` commits. Time box: 45-75 min.
- **Prerequisites:** Command line and environment basics (exit codes, subprocess); the review checklist
  and Pitfalls in this lesson; the bisect example below.
- **Setup:** Python 3.9+, git 2.28+ (for `init -b`) and `pip install pytest` (checked with Python
  3.9.6, git 2.56 and pytest 8.4.2). Save the starter as `lab.py` and the tests as `test_lab.py`. Each
  run builds a throwaway repo in a temp directory with your personal git config ignored.
- **The trap:** the assistant committed a key and then "removed" it in a later commit, so the final
  diff is clean. Only the history shows it.

### Starter code
```python
"""Lab: a pre-merge review gate for an AI-written branch, driven by real git. Run: python lab.py"""
import os, re, subprocess, tempfile

ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="demo", GIT_AUTHOR_EMAIL="demo@example.com",
           GIT_COMMITTER_NAME="demo", GIT_COMMITTER_EMAIL="demo@example.com")
STDLIB = {"json", "os", "re", "subprocess", "pathlib", "typing", "dataclasses"}

def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], env=ENV, check=True,
                          capture_output=True, text=True).stdout

def commit(repo, files, msg):
    for path, text in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)

def make_demo_repo(repo):
    git(repo, "init", "-q", "-b", "main")
    commit(repo, {"requirements.txt": "httpx==0.28.1\n", "prompts/router.md": "Pick one tool.\n",
                  "evals/router.jsonl": '{"q": "refund?", "tool": "refunds"}\n'}, "Initial agent")
    git(repo, "switch", "-qc", "feat/invoice")           # the coding assistant's branch
    commit(repo, {"tools/invoice.py": (
        "import json\nimport subprocess\nimport billing_sdk\n\n"
        "def fetch_invoice(path):\n    try:\n"
        "        out = subprocess.run(f\"pdftotext {path}\", shell=True, capture_output=True)\n"
        "        return json.loads(out.stdout)\n    except Exception:\n        return {}\n")},
        "Add fetch_invoice tool")
    commit(repo, {"prompts/router.md": "Pick one tool. Prefer fetch_invoice for billing.\n"}, "Tune router prompt")
    commit(repo, {"config.py": 'API_KEY = "sk-test-FAKE123"\n'}, "fixup! Add fetch_invoice tool")
    commit(repo, {"config.py": 'import os\nAPI_KEY = os.environ["API_KEY"]\n'}, "Read key from env")
    git(repo, "switch", "-qc", "feat/clean", "main")      # a well-formed change for comparison
    commit(repo, {"prompts/router.md": "Pick exactly one tool.\n",
                  "evals/router.jsonl": '{"q": "refund?", "tool": "refunds"}\n{"q": "two tools?", "tool": "refunds"}\n'},
           "Tighten router prompt, add eval case")

def changed_files(repo, base, head):
    return git(repo, "diff", "--name-only", f"{base}...{head}").split()

def subjects(repo, base, head):
    return git(repo, "log", "--format=%s", f"{base}..{head}").splitlines()

def added_lines(repo, base, head):
    """[(path, line text)] for every line the branch adds, parsed from `git diff -U0 base...head`."""
    # YOUR CODE HERE: '+++ b/<path>' starts a file; lines starting with '+' (but not '+++') are added
    raise NotImplementedError

def review(repo, base, head):
    """Sorted list of (where, rule) findings. Rules:
    - (path, "unlisted-import"): a .py file adds an import whose top-level name is neither in STDLIB
      nor a package pinned in requirements.txt at head (compare names lower-cased, '-' as '_')
    - (path, "shell-true"): an added line contains shell=True
    - (path, "broad-except"): an added line is `except Exception:` or a bare `except:`
    - ("prompts/<f>", "eval-missing"): a prompt changed but nothing under evals/ changed
    - ("history", "secret"): any commit in base..head adds or removes the text 'sk-'
      (hint: `git log -S`), even if the final diff no longer contains it
    - ("branch", "fixup"): a commit subject in base..head starts with 'fixup!'"""
    # YOUR CODE HERE
    raise NotImplementedError

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as repo:
        make_demo_repo(repo)
        print("changed:", changed_files(repo, "main", "feat/invoice"))
        print("added in tools/invoice.py:", sum(p == "tools/invoice.py" for p, _ in added_lines(repo, "main", "feat/invoice")))
        for where, rule in review(repo, "main", "feat/invoice"):
            print(f"  {rule:16} {where}")
        print("clean branch findings:", review(repo, "main", "feat/clean"))
```

### Expected output
`python lab.py` with a correct solution prints exactly:

```
changed: ['config.py', 'prompts/router.md', 'tools/invoice.py']
added in tools/invoice.py: 10
  fixup            branch
  secret           history
  eval-missing     prompts/router.md
  broad-except     tools/invoice.py
  shell-true       tools/invoice.py
  unlisted-import  tools/invoice.py
clean branch findings: []
```

- **Read the lines:** `config.py` shows up as changed, but its final content reads the key from the
  environment; the `secret` finding comes from the history, which is where a leaked key still lives
  after "removing" it. The clean branch changes a prompt and an eval together, so the gate passes it.

### Tests
Run `pytest -q test_lab.py`. On the starter all 5 fail with `NotImplementedError`; a correct solution
passes all 5 (Python 3.9.6, git 2.56, pytest 8.4.2).

```python
import pytest
from lab import added_lines, commit, git, make_demo_repo, review

@pytest.fixture
def repo(tmp_path):
    make_demo_repo(str(tmp_path))
    return str(tmp_path)

def test_added_lines_come_from_the_diff_not_the_history(repo):
    lines = added_lines(repo, "main", "feat/invoice")
    assert ("tools/invoice.py", "import billing_sdk") in lines
    assert ("config.py", 'API_KEY = os.environ["API_KEY"]') in lines
    assert not any("sk-" in text for _, text in lines)          # the final diff hides the key

def test_ai_branch_findings(repo):
    assert review(repo, "main", "feat/invoice") == [
        ("branch", "fixup"),
        ("history", "secret"),
        ("prompts/router.md", "eval-missing"),
        ("tools/invoice.py", "broad-except"),
        ("tools/invoice.py", "shell-true"),
        ("tools/invoice.py", "unlisted-import"),
    ]

def test_clean_branch_passes(repo):
    assert review(repo, "main", "feat/clean") == []

def test_pinned_and_stdlib_imports_are_not_flagged(repo):
    git(repo, "switch", "-q", "feat/clean")
    commit(repo, {"tools/web.py": "import json\nimport httpx\nfrom pathlib import Path\n"}, "Add web tool")
    assert review(repo, "main", "feat/clean") == []

def test_squashing_removes_fixup_but_not_the_leaked_key(repo):
    git(repo, "switch", "-q", "feat/invoice")
    git(repo, "-c", "sequence.editor=:", "rebase", "-q", "-i", "--autosquash", "main")
    found = review(repo, "main", "feat/invoice")
    assert ("branch", "fixup") not in found and ("history", "secret") in found
```

### Failure scenarios
- **Scan only the final diff for secrets.** `review` misses `("history", "secret")` and the branch looks
  clean. Run `git log -p -S'sk-' main..feat/invoice` and you see the commit that added the key and the
  one that removed it; the fix is rotating the key, then rewriting history.
- **Diff with two dots** (`main..head`) after a commit lands on `main`. The branch "deletes" files it
  never touched, because two dots compare the two tips; three dots compare against the merge base,
  which is what a review needs. Check with `git diff --stat` both ways.
- **Treat autosquash as a cleanup of the leak.** The last test shows it: the fixup commit is folded
  away, yet the key is still in the rewritten commit. Squashing changes commit boundaries, not content.
- **Clear `unlisted-import` by pinning the name.** The gate goes green, but `billing_sdk` was invented by
  the assistant and could be a squatted package. The fix is the existing internal client, so a reviewer
  reads the lockfile diff, not just the gate result.

### Submission
- **`lab.py`** with `added_lines` and `review` implemented; the helpers and demo repo unchanged.
- **A green run** of `pytest -q test_lab.py` (5 passed) and the output of `python lab.py`.
- **A written review** of `feat/invoice` in the table format of the worked scenario: finding, why it
  matters for the agent, requested change.
- **A note** (3-5 sentences): which rules you would make blocking in CI and which only comment, and why.
- **Evaluated on:** every rule fires on the AI branch and none on the clean one; the secret is found
  from history; diffs use the merge base.

## Why it exists
- **Change needs history.** Without versioned snapshots you cannot answer "what changed between the
  agent that worked on Monday and the one failing today?", which is most of debugging.
- **Parallel work needs isolation.** Branches let several people (or several coding agents) change the
  same codebase without stepping on each other until they integrate.
- **One author misses things.** A second reader catches wrong assumptions, missing tests and security
  holes; it also spreads knowledge of the code.
- **Generated code raises the stakes.** An assistant can produce 300 plausible lines in a minute.
  Review is where you find the invented API, the swallowed exception or the test that tests nothing.

## How it works
### The model
| Concept | What it really is |
|---|---|
| Commit | a snapshot of the whole tree, plus author, message and parent commit id(s) |
| Branch | a file holding one commit id; committing moves it forward |
| `HEAD` | the branch (or commit) you currently have checked out |
| Staging area | the next commit being assembled: `git add` puts changes there |
| Remote | another copy of the repository; `fetch` copies its commits, `push` sends yours |

- **Commits are immutable.** "Editing" history (amend, rebase) creates new commits with new ids, which
  is why rewriting pushed history disrupts everyone who has the old ids.

### Everyday workflow
1. `git switch -c feat/rerank-tool` from an up-to-date `main`.
2. Small commits, each building and passing tests, with a message saying *why*.
3. `git fetch` then `git rebase origin/main` to replay your commits on the latest `main`.
4. Push and open a pull request; CI runs tests (and evals for prompt changes).
5. Address review with new commits or `fixup!` commits, squash them before merge.
6. Merge, delete the branch.

### Merge vs rebase
- **Merge** joins histories with a merge commit. It never rewrites anything, so it is always safe on
  shared branches, at the cost of a branchier history.
- **Rebase** replays your commits on a new base, producing a linear history. Use it on your own
  unpublished or personal branch; after rebasing a pushed branch, push with `git push --force-with-lease`,
  which refuses if someone else pushed in the meantime.
- **The rule:** never rebase a branch other people build on, such as `main`.

### Undoing safely
| Situation | Command | Rewrites history? |
|---|---|---|
| Undo a pushed commit | `git revert <id>` (new commit that inverts it) | no |
| Fold a fix into an earlier local commit | `git commit --fixup <id>` then `git rebase -i --autosquash` | yes |
| Find the commit that broke a check | `git bisect run <script>` | no |
| Recover a commit "lost" after reset or rebase | `git reflog`, then `git branch rescue <id>` | no |

### Reviewing code, including AI-generated code
1. **Does it do the stated thing?** Read the PR description, then the tests, then the code.
2. **Does it run?** Pull it and run the tests yourself for non-trivial changes; CI green is necessary,
   not sufficient.
3. **AI-specific checks:** every imported package and API actually exists at the pinned version (assistants
   invent plausible names, and attackers register such names on package indexes); tests assert behaviour
   rather than echo the implementation; errors are not silently swallowed; no unrelated "cleanups" ride
   along.
4. **Security:** no secrets, no `shell=True` with model or user input, SQL is parameterised, tool
   permissions are not widened.
5. **Size:** a diff too big to review well is sent back to be split.

## Diagram
A change flows from a branch through review and CI to `main`; a failure at any gate sends it back to
the branch, never around the gate.

```mermaid
flowchart TD
    M["main"] --> B["feature branch"]
    B --> C["small commits, tests pass locally"]
    C --> R["rebase on latest main"]
    R --> P["pull request"]
    P --> CI["CI: tests, lint, evals"]
    CI -->|"fails"| C
    CI --> V["human review, AI-code checks"]
    V -->|"changes requested"| C
    V -->|"approved"| G["squash or merge into main"]
```

## Examples
### Branch, bisect a bad AI "cleanup", revert it, and autosquash a fixup
Runs end to end in a throwaway repository. An assistant's "Simplify sort" commit silently inverts a
ranking; `git bisect run` finds it among five commits, `git revert` undoes it without rewriting
history, and a `fixup!` commit is folded into its target non-interactively (tested with git 2.56).

```bash
set -e
export GIT_CONFIG_GLOBAL=/dev/null                   # ignore personal config for a clean demo
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com
rm -rf /tmp/efund/repo
mkdir -p /tmp/efund/repo                             # separate lines: set -e ignores failures inside &&
cd /tmp/efund/repo                                   # chains, so a failed mkdir would leave you in the cwd
git init -q -b main
printf 'def top_k(scores, k):\n    return sorted(scores, reverse=True)[:k]\n' > rank.py
printf 'from rank import top_k\nassert top_k([0.2, 0.9, 0.5], 2) == [0.9, 0.5]\n' > check.py
git add . && git commit -qm "Add top_k ranking with check"

git switch -qc feat/rerank                            # one branch per change
for i in 1 2 3; do echo "note $i" > note$i.md; git add .; git commit -qm "Add note $i"; done
sed -i.bak 's/reverse=True/reverse=False/' rank.py && rm rank.py.bak   # the AI-suggested "cleanup"
git commit -qam "Simplify sort"
echo "note 4" > note4.md; git add .; git commit -qm "Add note 4"

git bisect start HEAD main >/dev/null                        # bad, then good
git bisect run python3 check.py >/dev/null 2>&1
git log -1 --format="first bad: %h %s" refs/bisect/bad
git bisect reset >/dev/null 2>&1

git revert --no-edit HEAD~1 >/dev/null                 # undo the bad commit with a new commit
python3 check.py && echo "check passes after revert"
echo "typo fixed" >> note1.md
git commit -qam "fixup! Add note 1"                    # fold a fix into an earlier commit
GIT_SEQUENCE_EDITOR=: git rebase -qi --autosquash main # non-interactive autosquash
git log --oneline main..HEAD
# Output (ids differ per run):
# first bad: 46c3be3 Simplify sort
# check passes after revert
# ebf5444 Revert "Simplify sort"
# 9bde7f2 Add note 4
# be20f34 Simplify sort
# c17626e Add note 3
# 1585f5c Add note 2
# 1f0ce8f Add note 1          (now contains the fixup; no separate fixup commit remains)
```

- **Why the check script matters:** bisect is only as good as the command it runs. Exit 0 means good,
  125 means "cannot test this commit, skip it", any other code from 1 to 127 means bad, and a code
  above 127 aborts the bisect.
- **On a real branch** you would drop the bad commit with an interactive rebase before review instead
  of reverting; revert is the tool once a commit is on a shared branch.

### Worked scenario: reviewing an assistant-written tool
An assistant produced a 180-line `fetch_invoice` tool with tests, all green in CI. Review found:

| Finding | Why it matters | Requested change |
|---|---|---|
| `from billing_sdk import InvoiceClient` | package not in the lockfile, name not on the internal index | use the existing internal client |
| `except Exception: return {}` | model sees "no invoice" instead of an error and invents one | raise a typed error, return an error message to the model |
| tests mock `InvoiceClient.get` and assert it was called | tests check the implementation, not behaviour | test parsing on recorded responses |
| `subprocess.run(f"pdftotext {path}", shell=True)` | `path` comes from tool args, so this is shell injection | pass an argument list, validate the path |
| reformatted 3 unrelated files | hides the real change, risks conflicts | split into a separate PR |

## Production patterns
- **Prompts, tool schemas and eval sets in git.** Store them as files reviewed like code, so `git log
  -p prompts/` and `git bisect` work on behaviour changes. Monitor eval scores per commit in CI.
- **Small pull requests with a required check.** Protect `main`: require passing CI and at least one
  approval, and aim for PRs a reviewer can read in one sitting. Track PR size and time to review.
- **Evals as a PR gate for prompt and model changes.** CI runs the eval set on any change under
  `prompts/` or model config and posts the score delta to the PR; block merges that regress past a
  threshold.
- **An AI-code review checklist.** Add the checks (packages exist and are pinned, no swallowed errors,
  behaviour-level tests, no widened permissions, no unrelated edits) to the PR template, because
  generated diffs fail in recognisable ways.
- **Squash or fixup before merge.** Keep `main` history one meaningful commit per change, so `revert`
  and `bisect` stay precise.

## Trade-offs
- **Rebase vs merge.** Rebase gives a linear history that bisects cleanly, but rewrites commits and
  needs care on pushed branches; merge is always safe but noisier.
- **Small PRs vs overhead.** Small diffs get better reviews and simpler reverts, but each costs a CI run
  and a reviewer context switch; stack related PRs when a change is large.
- **Strict review vs speed.** Required approvals catch defects and spread knowledge, but slow urgent
  fixes; allow an expedited path with review after merge for incidents.
- **Trusting AI-generated code vs verifying it.** Assistants speed up writing, but review time per line
  rises because plausible-looking errors are harder to spot.

## Pitfalls
- **Force-pushing over a teammate's work.** Symptom: commits vanish from a shared branch. Diagnose:
  `git reflog` on any machine that had them, and the remote's push log if the host provides one. Fix:
  restore from reflog; use `--force-with-lease`, never plain `--force`, and do not rebase shared
  branches.
- **Giant mixed commits.** Symptom: `git bisect` lands on a 2,000-line commit and you are no closer.
  Diagnose: `git show --stat <id>` shows unrelated files changed together. Fix: one logical change per
  commit, formatting changes separate.
- **Rubber-stamping green AI diffs.** Symptom: an invented package or swallowed exception reaches
  production. Diagnose: check the lockfile diff and grep the diff for broad `except` clauses and
  `shell=True` before reading the rest. Fix: the checklist, and run the code yourself.
- **Committing secrets.** Symptom: an API key appears in history, or a secret scanner alerts. Diagnose:
  `git log -p --all -S'sk-'` (or the key's prefix) to find every commit on every branch that adds or
  removes it. Fix: rotate the key first, then purge history; deleting the file in a new commit does
  not remove it from history.

## When to use
- **Always:** a branch and pull request for any change to code, prompts or tool schemas that others
  depend on.
- **Rebase** your own branch onto `main` before review; **merge** when integrating shared branches.
- **Bisect** whenever a check that used to pass now fails and the cause is not obvious.
- **Measure:** PR size, time to first review, reverts per month and escaped defects in generated code.

## Exercise
**Goal:** practise the full branch, review and recovery workflow in a throwaway repository. No solution
is given.
- **Setup:** create a repo with a small agent tool, its tests, and a `prompts/system.md` file; add 8-10
  commits on a feature branch, one of which (written by an assistant or you) silently breaks a test and
  one of which commits a fake key such as `FAKE_KEY_123`.
- **Constraints:** use only command-line git; no GUI.
- **Deliver:** a short log of the commands you ran and why, plus a written review of one assistant-made
  diff using the checklist from this lesson.
- **Acceptance:**
  - `git bisect run` identifies the breaking commit automatically;
  - the final branch history has no fixup commits and passes tests at every commit (check with
    `git rebase --exec "pytest -q" main`);
  - `git log -p -S'FAKE_KEY_123'` returns nothing on the final branch;
  - you recover a deliberately reset-away commit using `git reflog`.

## Role lens
- **Engineer:** Keeps commits small and bisectable, rebases personal branches, and reviews AI-generated
  diffs against a checklist before approving, running the code for anything non-trivial.
- **Architect:** Sets branch protection, required checks and eval gates for prompt changes, and decides
  how prompts, schemas and eval sets are versioned alongside code.
- **FDE:** Works in customer repositories with their conventions, delivers reviewable PRs, and uses
  history (`log`, `blame`, `bisect`) to explain when and why a customer's agent behaviour changed.

## Check yourself
**Q:** After rebasing your feature branch onto `main`, the push is rejected. A teammate suggests
`git push --force`. What is happening, what is the safer command, and when should you not rebase at all?
**A:** Rebasing rewrote your commits with new ids, so the remote branch is no longer an ancestor of your
local one and a normal push would lose the old commits. If only you use the branch, push with
`git push --force-with-lease`, which overwrites only if the remote still points where you last saw it,
so a teammate's new commits are not destroyed. Do not rebase a branch other people have built on (such
as `main` or a shared integration branch); merge instead, because rewriting it breaks everyone's
history. If commits were lost anyway, `git reflog` can recover them.

## Interview questions
### Explain the difference between merge and rebase, and your rule for choosing.
- **Merge** creates a commit with two parents and preserves history exactly; safe anywhere.
- **Rebase** replays commits onto a new base with new ids, giving linear history.
- **Rule:** rebase your own unshared branch to stay current; merge into shared branches; never rewrite
  history others have pulled.

### What do you look for when reviewing a pull request written mostly by an AI assistant?
- **Existence and pinning:** every package and API exists at the version in the lockfile.
- **Error handling:** no broad `except` returning empty results that the agent will treat as truth.
- **Tests:** assert behaviour on realistic inputs rather than mocking the code under test.
- **Security and scope:** no injection, secrets or widened permissions, and no unrelated edits.

### The agent's eval score dropped 10 points sometime in the last 40 commits. How do you find out why?
- **Script the check:** a command that runs a fixed, seeded subset of the eval and exits non-zero below a
  threshold.
- **Bisect:** `git bisect start HEAD <good>` then `git bisect run ./eval_check.sh`; about 6 steps for
  40 commits, using 125 to skip commits that cannot run.
- **Read the culprit diff**, then confirm by reverting it alone.

### When would you allow merging without a full review?
- **During an incident** for a minimal, revertible fix, with review immediately after merge.
- **Never** for changes that touch auth, payments, tool permissions or secrets, because those are where
  a missed defect is most expensive.

## Quiz
### Q: A bad commit is already on the shared `main` branch. What is the safest way to undo it?
- [ ] `git reset --hard HEAD~1` and force-push
- [x] `git revert <id>`, which adds a new commit that inverts it
- [ ] Delete the branch and recreate it
- [ ] Interactive rebase to drop the commit, then force-push
> Revert undoes the change without rewriting history, so everyone else's copy of `main` stays valid.
> Reset and rebase rewrite shared history and break other clones.

### Q: What does `git bisect run ./check.sh` need from `check.sh`?
- [ ] It must print the word "bad" on failure
- [x] Exit 0 for a good commit, non-zero (except 125) for a bad one, and 125 to skip
- [ ] It must be written in Python
- [ ] It must compare two commits itself
> Bisect reads only the exit code. 125 tells it the commit cannot be tested, and any other non-zero
> code up to 127 marks the commit bad.

### Q: Which is the most AI-specific risk to check in a generated diff?
- [ ] Inconsistent indentation
- [ ] Commit messages in the wrong tense
- [x] Imports of packages or APIs that do not exist or are not pinned
- [ ] Long function names
> Assistants generate plausible but non-existent package and API names, and attackers can register such
> names on public indexes. Checking imports against the lockfile and docs catches this.

## Mastery test
**Problem:** a coding agent opens 12 pull requests a day against your agent's repository. Two incidents
last month came from its PRs: one added a dependency with a typo-squatted name, one changed
`prompts/router.md` and dropped tool-choice accuracy by 8 points. Design the workflow changes.

**A strong answer:**
- requires CI on every PR, including a dependency check that fails on packages not in an allow-list or
  lockfile, and a secret scan;
- runs the eval set whenever `prompts/` or model config changes, posts the score delta, and blocks
  regressions past a threshold;
- limits the agent's PR size and scope, and keeps a human approval requirement on `main`;
- gives the coding agent a token that can open PRs but not approve or merge, and adds CODEOWNERS
  review for `prompts/` and dependency files so those changes reach the people who own them;
- adds the two incidents as regression checks (the dependency rule and the router eval cases);
- tracks escaped defects per PR source to see whether the gates work.

## Use cases & projects
### Use cases
- **Prompt regressions.** Prompts in git plus `bisect` over an eval check find which edit dropped quality.
- **Reviewing coding-agent output.** A checklist for generated diffs catches invented packages,
  swallowed errors and hollow tests.
- **Safe rollback.** Small squashed commits make `git revert` a one-command rollback of a bad agent
  change.
- **Customer handover.** Clean history and PR descriptions let a customer's team own the agent code
  after an engagement.

### Mini project
**An eval-gated PR workflow for an agent repo (2-3 hours).**
- **Goal:** make prompt changes as safe to merge as code changes.
- **Steps:** move prompts and tool schemas into files; write `eval_check.sh` that runs 20 seeded eval
  cases and exits non-zero below a threshold; add a CI job that runs it on changes under `prompts/`;
  add a PR template with the AI-code checklist; break a prompt on purpose and open a PR.
- **Done when:** the broken prompt PR is blocked by CI with the score shown, and `git bisect run
  ./eval_check.sh` finds the bad commit locally.
