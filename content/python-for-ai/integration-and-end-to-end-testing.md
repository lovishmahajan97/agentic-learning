---
id: integration-and-end-to-end-testing
module: python-for-ai
name: Integration and end-to-end testing
kind: practice
difficulty: core
est_minutes: 16
status: fact-checked
related:
  - python-for-ai/pytest
  - python-for-ai/test-doubles-mocks-fixtures
  - python-for-ai/contract-testing
  - fastapi-for-ai/integration-tests-with-real-dependencies
  - backend-fundamentals/ci-cd
  - evals/regression-tests
  - agent-reliability/deterministic-replay-and-reproducibility
  - enterprise-integration/sandbox-vs-production-environments
sources:
  - Cohn, 2009 — Succeeding with Agile
  - Official docs: Testcontainers
  - Official docs: pytest
---

## TL;DR
Unit tests with doubles prove your logic is right given your assumptions about the outside world.
Integration tests run your code against real dependencies (a real Postgres with pgvector, a real Redis,
a real HTTP stack) to check those assumptions; end-to-end tests drive the whole deployed system through
its public interface the way a user would. You need fewer of each as you go up, because each level is
slower and flakier, but some failures only exist at those levels.

## Why it exists
A RAG service can have perfect unit tests and still fail on its first real query. The fake vector store
returned exactly *k* results, but the real approximate index with a tenant filter returns fewer. The fake
database accepted 1,536-dimensional vectors into a column declared for 768. The SQL that the ORM
generates differs from what the mocked repository implied. The app works, but the migration that
creates the index was never run in tests. The streaming endpoint works in isolation, but the reverse
proxy buffers the stream. None of these are logic bugs; they are disagreements between your code and
the real systems it touches, and a double cannot reveal them because the double *is* your assumption.

The naive answer, "test everything end to end against a staging environment", fails in the opposite
direction. End-to-end runs are slow (minutes, not milliseconds), they depend on shared state that other
people change, they fail for reasons unrelated to your change (a network blip, a model rate limit, a
redeploy), and when they fail they rarely say *where*. A suite built mainly from them becomes slow,
flaky and ignored. The test pyramid is the compromise: many fast unit tests for logic, fewer integration
tests at each real boundary, and a thin layer of end-to-end tests for the critical user journeys.

## How it works
### Where the lines are
- **Unit:** one piece of your code, collaborators replaced by doubles, milliseconds per test.
- **Integration:** your code plus one or more *real* collaborators you control in the test: a database
  in a container, a real HTTP client against a local server, the real framework routing and
  serialisation. Usually seconds per test, mostly spent on startup.
- **End-to-end:** the assembled system, deployed roughly as in production, exercised only through its
  public interface (HTTP API, UI, queue), checking a full journey: upload a document, ask a question,
  get a cited answer.

### Real dependencies, disposable
Testcontainers (with a Python library) starts a throwaway Docker container from a pinned image for the
duration of a fixture, waits until it is ready, and gives you its connection URL. You test against the
same engine and extensions as production rather than SQLite or an in-memory imitation, because dialect,
indexing and extension behaviour are exactly what you are trying to verify. Docker Compose does the same
for a multi-service stack. Container startup is the expensive part, so scope the container per session
and make each test isolated within it.

### Isolation inside a shared dependency
Options, each with its own failure mode: wrap each test in a transaction and roll back (fast, but breaks if
the code commits or uses several connections); give each test a unique namespace, such as a tenant ID,
schema or collection name (works across connections and services); or truncate tables between tests
(simple, slower). Never rely on test order.

### The model in the loop
The LLM is the dependency that breaks the usual playbook, because it is non-deterministic, paid and
rate-limited. Choices for integration and e2e tests:
1. **A fake model server** that speaks the provider's HTTP API and returns scripted replies. The full HTTP
   path, SDK, retries and parsing run for real; only the model is fixed.
2. **Record and replay**: capture real responses once (cassette libraries such as vcrpy do this for HTTP)
   and replay them, refreshing deliberately.
3. **Live calls**, kept few, run on a schedule or before release, asserting structure and invariants
   (valid JSON, cited document IDs exist) rather than exact text. Judging answer *quality* belongs in the
   eval suite, not the test suite.

### Running them
Mark slow tests (`@pytest.mark.integration`, `@pytest.mark.e2e`), register the markers, and select them
with `-m` so the default local run stays fast while CI runs integration tests on every change and e2e
on merge or deploy.

## Example
An integration test for a pgvector retriever, against the real extension in a container. The embedder is
a deterministic fake so results are reproducible; everything else (SQL, index, migrations, tenant
filter) is real. `PgRetriever` and `HashEmbedder` stand for the application's own code.

```python
import uuid
import pytest
from testcontainers.postgres import PostgresContainer
from app.retriever import PgRetriever     # code under test: migrate(), add(), search()
from app.embed import HashEmbedder        # deterministic fake: hashes text into an 8-dim vector

pytestmark = pytest.mark.integration      # run with: pytest -m integration

@pytest.fixture(scope="session")
def retriever():
    with PostgresContainer("pgvector/pgvector:0.8.0-pg16", driver=None) as pg:  # pin both versions
        r = PgRetriever(pg.get_connection_url(), embedder=HashEmbedder(dim=8))
        r.migrate()                       # the real migrations: extension, table, HNSW index
        yield r

@pytest.fixture
def tenant():
    return f"t-{uuid.uuid4().hex[:8]}"    # unique namespace per test: no cleanup, no cross-talk

def test_search_never_crosses_tenants(retriever, tenant):
    retriever.add(tenant, "d1", "refunds are issued within 14 days")
    retriever.add(tenant + "-other", "d2", "refunds are issued within 14 days")
    hits = retriever.search(tenant, "refund window", k=5)
    assert [h.doc_id for h in hits] == ["d1"]

def test_wrong_dimension_is_rejected_by_the_database(retriever, tenant):
    with pytest.raises(ValueError):       # adapter maps the DB error to a domain error
        retriever.add_vector(tenant, "d3", [0.1] * 7)

def test_filtered_search_returns_all_matches(retriever, tenant):
    for i in range(50):
        retriever.add(tenant, f"doc{i}", f"policy section {i}")
    assert len(retriever.search(tenant, "policy", k=20)) == 20
```

The last test is the one doubles cannot write: with a shared approximate index and a selective tenant
filter, a naive query can return fewer than *k* rows, which is a property of the real engine and its
settings, not of your Python. pgvector's HNSW scan gathers a bounded candidate list (`hnsw.ef_search`,
40 by default) and the `WHERE` filter is applied afterwards; version 0.8.0 added iterative index scans
to keep searching when too few rows survive. To make the test meaningful, seed enough rows for other
tenants that the planner actually uses the index; on a near-empty table Postgres may choose an exact
sequential scan and the test passes for the wrong reason. Note the image tag pins pgvector as well as
Postgres: a bare `pg16` tag moves when pgvector releases.

## Trade-offs
- Real dependencies catch dialect, index and migration bugs that fakes hide, but add container startup
  time and a Docker requirement in CI; session-scoped containers keep that cost roughly constant.
- End-to-end tests are the only check that the deployed whole works, but are slow, flaky and poor at
  locating faults; keep them to a few critical journeys and push everything else down a level.
- Fake model servers make AI e2e tests deterministic and free, but test only your handling of replies
  you anticipated; scheduled live checks flip that, at the cost of money, rate limits and noise.
- Transaction rollback isolation is very fast, but breaks once code commits or spans connections;
  unique namespaces per test are slower to set up and work everywhere.

## Pitfalls
- Using SQLite or an in-memory imitation in place of Postgres with pgvector; the symptom is green tests
  and production errors on vector operators, JSON functions or index behaviour.
- Tests sharing rows or collections; the symptom is a suite that passes serially and fails under
  parallel runs or in a different order, which teams misdiagnose as random flakiness.
- Asserting exact LLM output text in end-to-end tests; the symptom is failures after any model or
  prompt change even though the system still works, until the tests are disabled.
- Unpinned container image tags such as `latest`; the symptom is a suite that breaks overnight with no
  code change because the database or extension version moved.
- Retrying flaky e2e tests until green; the symptom is a real intermittent bug (a race, a timeout too
  close to p99 latency) that the retry hides and production users then hit.

## When to use
Write an integration test at every boundary where your code meets something you did not write and
cannot fully fake: databases and vector stores, queues, caches, the web framework's request handling,
and the HTTP layer to model providers via a fake server. Keep end-to-end tests to the handful of journeys
that would be a severity-one incident if broken, and run them on merge or deploy rather than on every
save. Measure suite time and flake rate, and fix or delete flaky tests quickly. In interviews, "how would
you test a RAG service?" expects this layering, plus a clear separation from evals that measure quality.

## Role lens
- **Engineer:** Writes container-backed integration tests with unique per-test namespaces, marks them,
  and keeps the default local test run fast.
- **Architect:** Defines the test layering, what runs on every commit versus before deploy, and where the
  model is faked, replayed or called live, with an explicit budget for each.
- **FDE:** Builds an end-to-end smoke test against the customer's environment (their SSO, network, data
  store) that proves the deployment works before handing it over.

## Check yourself
**Q:** Your RAG service's unit tests all pass, but after deploying, answers for some tenants have no
citations. How would you change the test suite so this class of failure is caught before deploy?
**A:** Missing citations for some tenants smells like a real-engine behaviour: a tenant filter combined
with an approximate index returning fewer rows, a migration not applied, or a dimension mismatch. I would
add container-backed integration tests against the same Postgres and pgvector version, running the real
migrations, with per-test tenant namespaces and cases for selective filters and large tenants. Then one
end-to-end test that uploads a document and asks a question through the public API against a fake model
server, asserting that the response cites an existing document ID. Model answer quality stays in evals;
these tests check the plumbing that the model depends on.

## Quiz
### Q: What distinguishes an integration test from a unit test in this layering?
- [ ] Integration tests are written in a different framework
- [x] Integration tests run your code against at least one real collaborator instead of a double
- [ ] Integration tests always go through the public UI
- [ ] Integration tests cannot be run with pytest
> The defining feature is the real dependency, such as a containerised database. Driving the whole
> deployed system through its public interface describes end-to-end tests, not integration tests.

### Q: Why is SQLite a poor stand-in for Postgres with pgvector in integration tests?
- [ ] SQLite is too slow for tests
- [ ] SQLite cannot be started from pytest fixtures
- [x] The test exists to verify engine-specific behaviour, which a different engine does not have
- [ ] SQLite does not support transactions
> Vector operators, index behaviour and SQL dialect are exactly what integration tests check, so a
> different engine silently skips the assumptions you meant to verify. SQLite is fast, not slow.

### Q: Which approach keeps an AI end-to-end test deterministic while still exercising the HTTP path?
- [ ] Calling the live model with temperature zero and asserting exact text
- [x] Pointing the system at a fake model server that speaks the provider's API with scripted replies
- [ ] Mocking the service's handler function inside the test process
- [ ] Retrying the test until the model produces the expected answer
> A fake server keeps SDK, retries and parsing real while fixing the model's output. Temperature zero
> does not guarantee identical text, and mocking the handler is no longer end to end.
