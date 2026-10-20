---
id: jupyter
module: python-for-ai
name: Jupyter
kind: tool
difficulty: intro
est_minutes: 12
status: reviewed
related:
  - python-for-ai/pandas
  - python-for-ai/async-await
  - python-for-ai/package-structure-and-import-design
  - evals/error-analysis
  - llmops-platform/experiment-tracking
  - llmops-platform/reproducible-evaluations
  - scoping/rapid-prototyping-poc
sources:
  - Official docs: Project Jupyter
---

## TL;DR
Jupyter runs code interactively in cells against a long-lived kernel, keeping variables in memory
between cells and saving code, outputs and charts together in an `.ipynb` file. That makes it the fastest
way to explore data, iterate on prompts and read model outputs side by side. The same persistent state
that makes it fast also makes notebooks unreliable as production code or as evidence, unless you
restart and run everything top to bottom.

## Why it exists
Much AI work is exploratory: look at 50 failing eval cases, try three prompt variants, plot score
distributions, inspect what the retriever returned. In a script, each question means re-running
everything from the start: reloading a model, re-reading a dataset, re-calling an API you pay for. Seconds
or minutes of setup dominate a two-second question, and output scrolls away in a terminal. A plain REPL
keeps state but loses history and cannot show tables, images or charts well.

Jupyter separates a **kernel** that holds state from a **front end** that sends code to it, so you load
the model or dataframe once and then ask questions in small cells, each with its output rendered inline
and saved next to the code. The result doubles as a record others can read. The naive alternative,
scripts with print statements, is reproducible but slow to iterate; the notebook trades some
reproducibility for iteration speed, and knowing exactly what you gave up is the skill.

## How it works
### Architecture
- **Kernel.** A separate process (for Python, `ipykernel`, built on IPython) that executes code and
  holds all variables. It is separate *because* the front end can then crash, reconnect or be a different
  program entirely without losing state, and kernels for other languages plug in the same way.
- **Messaging protocol.** Front ends talk to kernels over ZeroMQ sockets using a documented message
  protocol: execute requests, streamed output, rich display data, interrupts. That protocol is why
  JupyterLab, the classic Notebook interface, VS Code and hosted notebook services can all drive the same
  kernel.
- **Server.** Jupyter Server manages kernels, files and the browser-facing API.
- **Document.** An `.ipynb` is JSON: a list of cells, each with source, an execution count and outputs
  (text, HTML, base64 images). Outputs are stored *in* the file, which is why a notebook can be read
  without re-running it, and also why it can carry data and secrets you did not intend to share.

### State is the execution history, not the file
The kernel's state is whatever cells you ran, in the order you ran them, including cells you have since
edited or deleted. The file on disk shows the current text of each cell, which may not be the code that
produced the variables in memory. Execution counts (`[7]`, `[3]`) out of order are the visible clue.
The only proof that a notebook works is **Restart Kernel and Run All** producing the same results.

### Kernels, environments and memory
Which Python a notebook uses is set by its **kernelspec**, a small JSON file naming the interpreter to
launch, not by the shell you started Jupyter from. Registering a virtual environment
(`python -m ipykernel install --user --name myenv`) is how a project's environment becomes a selectable
kernel. This explains the install trap: `!pip` runs whatever `pip` is first on the server's `PATH`, which
may belong to another environment, while `%pip` installs with the kernel's own interpreter.

Because the kernel lives until you restart it, so does everything it references. IPython also keeps
every displayed result in its output history (`Out`, `_`, `__`), so a large DataFrame or tensor you
displayed stays referenced even after you `del` the variable. Re-running a "load model" cell creates a
second copy before the first can be freed. On a GPU that is the classic notebook out-of-memory error:
the first load fits, the re-run does not, and only a kernel restart reliably gives the memory back.

### Useful machinery
- Magics: `%timeit` for micro-benchmarks, `%autoreload` to pick up edits to imported modules, `!pip`
  and `%pip` (the latter installs into the kernel's environment, which is what you usually want).
- Top-level `await` works in IPython kernels *because* the kernel already runs an event loop; the same
  fact is why `asyncio.run()` fails inside a notebook with "cannot be called from a running event loop".
- Headless execution: `jupyter nbconvert --execute` runs a notebook top to bottom; papermill injects
  parameters and runs notebooks as batch jobs, which some teams use for scheduled reports.
- Version control helpers: tools such as nbstripout remove outputs before commit, and Jupytext pairs a
  notebook with a plain `.py` file so diffs and reviews are readable.

## Example
A worked scenario: error analysis on an eval run of 2,000 RAG answers.

Cell 1 loads the eval results JSONL into a pandas DataFrame and, once, the embedding model (about 30
seconds). Cell 2 filters the 140 rows the judge scored as failures (illustrative numbers). Cell 3 shows
ten of them with question, retrieved chunks and answer side by side. You notice many failures involve
tables, so cell 4 adds a `has_table` flag by regex over the retrieved chunks and stores a cross-tab of
document type against `has_table` for the failing rows in a variable, `by_type`. Cell 5 plots `by_type`.
Each question takes seconds because the model and data stay in memory.

Then the trap. You edit cell 2's filter to include "partial" scores and re-run cells 2 and 5 but not
cell 4, so `by_type` still holds counts from the old subset and the chart is silently wrong; nothing
errors. The notebook file shows the new filter, so a reviewer cannot tell. Before sharing, you Restart and Run All: cell 4 now runs on the new
subset and the chart changes. That is the habit that makes the notebook trustworthy. Finally, the
`has_table` heuristic moves into `analysis/features.py` with a test, and the notebook imports it, so the
logic outlives the exploration.

```python
%load_ext autoreload
%autoreload 2
import pandas as pd
from analysis.features import has_table      # logic lives in a module, not a cell

df = pd.read_json("runs/eval-2025-03.jsonl", lines=True)
fails = df[df.score < 3].assign(has_table=lambda d: d.chunks.map(has_table))
pd.crosstab(fails.doc_type, fails.has_table)
```

## Trade-offs
- Persistent kernel state makes iteration fast when setup is expensive, but hidden state means results
  may not reproduce; the balance flips the moment the output informs a decision others rely on.
- Outputs saved in the file make notebooks readable without re-running, yet they bloat diffs and can leak
  customer data or keys; strip outputs for code review, keep them only for curated reports.
- Notebooks are excellent for exploration and teaching, but poor for modular, tested code; once logic is
  reused or scheduled, it belongs in a package that the notebook imports.
- Parameterized notebook execution turns analyses into repeatable jobs cheaply, but a scheduled notebook
  is harder to test, type-check and monitor than a script with the same logic.

## Pitfalls
- Trusting results from out-of-order execution; the symptom is a colleague running the notebook top to
  bottom and getting different numbers, or a `NameError` for a variable you deleted.
- Committing outputs with API keys, tokens or customer records; the symptom is secrets or personal data
  appearing in the repository history and in rendered previews.
- Calling `asyncio.run()` in a cell; it raises because the kernel's loop is already running, and the
  usual hack, patching nested loops, hides the problem instead of using top-level `await`.
- Re-running a model-loading cell, or displaying large tensors, in a long session; the symptom is CUDA
  out-of-memory on a cell that worked an hour ago, cleared only by restarting the kernel.
- Installing with `!pip` into a different interpreter than the kernel's; the symptom is a successful
  install followed by `ModuleNotFoundError` in the next cell.
- Shipping a notebook as the production service; the symptom is no tests, no error handling, and an
  outage when someone runs cells in a new order.

## When to use
Use Jupyter for exploration and communication: data inspection, error analysis on eval results, prompt
iteration, quick model comparisons, and demos for stakeholders. Move code out as soon as it is reused,
scheduled or needs tests. Do not treat a notebook's numbers as final until Restart and Run All reproduces
them, and pin the environment so others can. For tracking runs and making evaluations reproducible, see
the LLMOps lessons; notebooks complement experiment tracking rather than replace it. In interviews,
expect to explain hidden state and how you keep notebook analysis honest.

## Role lens
- **Engineer:** Explores data and failure cases in notebooks, then promotes stable logic into tested
  modules the notebook imports, with outputs stripped before commit.
- **Architect:** Sets the boundary: notebooks for exploration and reports, packages and pipelines for
  anything scheduled or served, plus rules on data allowed in saved outputs.
- **FDE:** Uses a notebook to walk a customer through their own data and model results live, then hands
  over a cleaned, re-run version as the record of findings.

## Check yourself
**Q:** A colleague's notebook shows a 12-point accuracy improvement from a new prompt. What would you
check before believing it, and why is a notebook specifically prone to this kind of error?
**A:** I would restart the kernel and run all cells in order, because the kernel's state reflects the
execution history, not the file: an edited cell, a variable left over from an earlier experiment or a
filter applied to only one arm can produce a difference that the visible code does not explain. I would
also check that both prompts ran on the same rows, with the same model and settings, that the evaluation
function is identical for both, and that the sample is large enough for a 12-point gap to be meaningful.
Then I would move the comparison into a script or eval harness so it can be re-run.

## Quiz
### Q: What determines the values of variables in a running Jupyter kernel?
- [ ] The current text of the cells, read top to bottom
- [x] The cells actually executed, in the order they were executed, including since-edited ones
- [ ] The last saved version of the .ipynb file
- [ ] Only the most recently executed cell
> The kernel is a live process that accumulates state from every execution. The file shows current cell
> text, which may differ from what produced the state, hence Restart and Run All.

### Q: Why does asyncio.run() fail inside a standard IPython notebook cell?
- [ ] Notebooks do not support asynchronous code at all
- [ ] The kernel uses threads instead of an event loop
- [x] The kernel already runs an event loop, and asyncio.run() refuses to start one inside a running loop
- [ ] asyncio is not installed in the default kernel
> IPython kernels run their own event loop, which is also why top-level await works directly in a cell.
> asyncio.run() wants to create and own a new loop, so it raises.

### Q: A team wants readable code reviews of notebooks without leaking data. Which practice best addresses both?
- [ ] Converting every notebook to PDF before committing
- [ ] Committing notebooks with full outputs so reviewers can see results
- [x] Stripping outputs before commit and pairing notebooks with plain-text .py files
- [ ] Storing notebooks only on a shared drive outside version control
> Outputs cause noisy diffs and can contain sensitive data. Stripping them, and pairing with a text
> representation such as Jupytext, gives clean diffs while keeping the notebook usable.
