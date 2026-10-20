---
id: integration-tests-with-real-dependencies
module: fastapi-for-ai
name: Integration tests with real dependencies
kind: practice
difficulty: core
est_minutes: 15
related:
  - fastapi-for-ai/testclient-plus-pytest-asyncio
  - python-for-ai/integration-and-end-to-end-testing
  - python-for-ai/contract-testing
  - fastapi-for-ai/async-database-access-and-connection-pooling
  - fastapi-for-ai/alembic-migrations
  - fastapi-for-ai/openapi-contract-testing
  - backend-fundamentals/docker
  - backend-fundamentals/ci-cd
sources:
  - Official docs: Testcontainers for Python
  - Official docs: FastAPI (Testing)
  - Official docs: SQLAlchemy (Joining a Session into an External Transaction)
---

## TL;DR
Fakes prove your logic; they cannot prove that your SQL runs on Postgres, that pgvector accepts your
vector dimension, that a Redis TTL behaves, or that a migration applies. Integration tests run the
app against real instances of those dependencies, started disposably in containers, with each test
isolated by transaction rollback or cleanup. The paid, non-deterministic dependency, the LLM, is the
exception: test it against recorded responses or in a small, separate contract suite.

## Why it exists
AI services lean on infrastructure with real semantics: Postgres with JSONB queries and row locks,
pgvector with its distance operators and index types, Redis with expiries and atomic `SET NX`, an
object store for uploads, a queue for background jobs. Unit tests with fakes for these are fast but
they test your model of the dependency, not the dependency. The bugs that reach production live in
the gap: a query that works on SQLite and fails on Postgres, an ORM model that disagrees with the
migration, an `ON CONFLICT` clause that never fires, a vector column declared with 1536 dimensions
while the new embedding model returns 1024, a Redis key that never expires because the TTL was set
on the wrong call.

The naive fixes both fail. Substituting SQLite "because it is close enough" hides dialect
differences and has no pgvector. Pointing tests at a shared staging database makes tests depend on
each other's data and on whoever else is using it, so they flake and cannot run in parallel. What you
want is the real engine, private to the test run, in a known state for every test, and gone afterward.
Containers make that cheap enough to do on every pull request.

## How it works
### Provision real dependencies per test run
Start each dependency as a container from the same image and major version you run in production
(for example a pgvector-enabled Postgres image and the same Redis major version), *because* version
drift is one of the bugs you are trying to catch. Two common ways:
- **Testcontainers** starts containers from inside pytest fixtures and tears them down at the end of
  the session, so `pytest` alone is enough locally and in CI, given a Docker daemon.
- **Compose or CI service containers** start dependencies before pytest and pass connection URLs in
  environment variables, which suits CI systems that already manage services.

Scope the container to the test session, *because* starting Postgres takes seconds and doing it per
test makes the suite unusably slow.

### Bring the schema up the real way
Run your migrations (for example `alembic upgrade head`) against the fresh database rather than
`metadata.create_all()`, *because* the migration chain is what production runs, and a test suite that
never applies it will not notice a broken or missing migration.

### Isolate each test
Every test must start from a known state. Options, in order of speed:
1. **Transaction rollback.** Open a connection, begin a transaction, bind the app's session to it,
   run the test, roll back. Nothing persists. It does not work for code that commits on separate
   connections or relies on committed data being visible to other connections.
2. **Truncate after each test.** Slower, but works when the app commits or uses several connections.
3. **Per-test database or schema.** Strongest isolation; use when tests run in parallel workers.
For Redis, use a dedicated logical database or a per-test key prefix and `FLUSHDB` between tests.

### Wire the app to the real dependencies
Override the settings or dependencies that provide connection URLs so the app builds its engine,
Redis client and httpx client against the containers, then drive it through `TestClient` or
`ASGITransport` as usual. The app code under test is unchanged, *because* the seam is configuration,
not mocks.

### The LLM is a different kind of dependency
Real provider calls are slow, cost money, are rate-limited and are not deterministic. Keep them out
of the main suite. Record real responses once and replay them (cassette-style recording at the httpx
layer), or run a self-hosted small model in a container when you need a live endpoint. Keep a small,
separately scheduled contract suite that hits the real provider to catch API and schema drift.

## Example
Session-scoped Postgres and Redis containers with Testcontainers, migrations applied once, and a
rollback per test. Assumes the app reads its URLs from settings exposed through a `get_settings`
dependency.

```python
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer
from alembic import command
from alembic.config import Config

@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:   # match prod image and major version
        url = make_url(pg.get_connection_url()).set(drivername="postgresql+psycopg")
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
        command.upgrade(cfg, "head")                             # schema via real migrations
        yield url

@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7") as rc:
        yield f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0"

@pytest.fixture
def db_conn(pg_url):
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        tx = conn.begin()
        yield conn            # bind the app's session to this connection
        tx.rollback()         # every test starts from the migrated, empty schema

def test_vector_dimension_matches_model(db_conn):
    dim = db_conn.execute(text(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass "
        "AND attname = 'embedding'")).scalar_one()
    assert dim == 1024        # fails the build if the migration and embedding model disagree
```

The vector test is the kind of assertion a fake can never make: it reads the schema that the real
migration produced. Endpoint tests then use `TestClient` with settings overridden to `pg_url` and
`redis_url`.

## Trade-offs
- Real dependencies catch dialect, migration and semantic bugs that fakes cannot, at the cost of
  seconds of startup and a Docker requirement; keep unit tests for fast inner-loop feedback.
- Transaction rollback is the fastest isolation, but breaks when code under test commits on its own
  connections; switch to truncation or per-test databases when that happens.
- Session-scoped containers make suites fast, but a test that mutates global state (extensions,
  roles, server settings) can poison every later test; isolate those in their own session.
- Recorded LLM responses make tests deterministic and free, but go stale when the provider changes its
  format; a scheduled live contract suite is what catches that drift.

## Pitfalls
- Testing against SQLite while production runs Postgres; the suite passes and production fails on
  JSONB operators, `ON CONFLICT`, locking or pgvector, which SQLite lacks.
- Creating tables with `create_all()` instead of running migrations; a missing or broken migration
  ships, and the symptom is the first deploy failing on `alembic upgrade`.
- No per-test isolation; tests pass alone and fail together, or depend on execution order.
- Using a different image or major version from production, such as the latest Postgres image; the
  symptom is a bug that reproduces only in production or only in CI.
- Calling the real LLM in every integration test; the suite becomes slow, flaky on rate limits and
  expensive, and failures are non-deterministic.

## When to use
Write integration tests for every code path whose correctness depends on the semantics of a real
dependency: SQL and migrations, vector search queries, Redis-based caching, locks and rate limits,
upload storage and queue handoffs. Keep them fewer than unit tests, run them on every pull request,
and keep the live-LLM contract suite separate and scheduled. Measure suite runtime, flake rate and
which production incidents the suite would have caught. In interviews, explaining why SQLite is not a
Postgres test double and how you isolate tests is the expected depth.

## Role lens
- **Engineer:** Builds session-scoped container fixtures, migration-based schema setup, per-test
  rollback, and overrides that point the app at the containers.
- **Architect:** Defines the test pyramid for the service: what runs against fakes, what against real
  dependencies, and how live-provider contract tests gate releases.
- **FDE:** Reproduces a customer's database version and extensions in a container to debug a failure
  that only happens on their infrastructure, then keeps it as a regression test.

## Check yourself
**Q:** Your integration tests pass with per-test transaction rollback, but a new endpoint that
enqueues a background job and lets a worker read the row fails in the test: the worker sees nothing.
Why, and what do you change?
**A:** With rollback isolation the endpoint's write lives inside an uncommitted transaction on the
test's connection. The worker uses its own connection, and under Postgres's read-committed isolation
it cannot see uncommitted data from another transaction, so the row looks absent. This is exactly the
case rollback cannot cover. For this test, let the code commit and isolate by truncating tables
afterward or using a per-test database, and run the worker against the same real database. Keep
rollback for the many tests that stay on one connection, because it is much faster.

## Quiz
### Q: Why are database migrations, rather than `metadata.create_all()`, the better way to build the test schema?
- [ ] `create_all()` does not work against containerised Postgres
- [x] Migrations are what production runs, so the tests catch broken or missing migrations
- [ ] Migrations are faster than `create_all()`
- [ ] `create_all()` cannot create indexes
> The schema in production comes from the migration chain. Building tests from ORM metadata can hide a
> migration that is missing, out of order or inconsistent with the models until the first deploy.

### Q: Which dependency is usually best kept out of the main integration suite and handled with recordings or a separate scheduled suite?
- [ ] Postgres with pgvector
- [ ] Redis
- [x] The hosted LLM provider
- [ ] The object store used for uploads
> Real provider calls are slow, billed, rate-limited and non-deterministic. Databases and caches are
> cheap to run in containers and deterministic, so they belong in the main suite.

### Q: Per-test transaction rollback fails to isolate which kind of test?
- [ ] Tests that only read data
- [ ] Tests that insert rows through the ORM
- [ ] Tests that run vector similarity queries
- [x] Tests where the code under test commits or reads on separate connections
> Rollback works because everything happens in one uncommitted transaction on one connection. Commits
> or other connections escape that boundary, so truncation or per-test databases are needed instead.
