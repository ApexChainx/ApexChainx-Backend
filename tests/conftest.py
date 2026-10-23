"""Shared fixtures plus a guard that turns infra outages into informative skips.

DB/Redis-backed tests declare module markers (`pytest.mark.postgres`,
`pytest.mark.redis`, registered in pyproject.toml). When such a test fails
because the service is simply not reachable, the failure is converted into a
skip whose reason says how to start the service — instead of a wall of
"Connection refused" tracebacks that hides real defects. Non-connection
failures (assertions, app bugs) are never skipped: a marker only vouches for
the service being *optional to attempt*, not for the test to pass.

Start the backing services with:

    docker compose up -d postgres redis
"""

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --------------------------------------------------------------------------- #
# Infra outage -> skip                                                        #
# --------------------------------------------------------------------------- #

_START_HINT = (
    "the {service} service is not reachable — start it with "
    "`docker compose up -d {service}` (or drop the '{service}' marker from this test)"
)


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _classify_infra_error(exc: BaseException) -> str | None:
    """Return 'postgres' or 'redis' when the failure is a service-connection error."""
    import psycopg2
    from redis.exceptions import ConnectionError as RedisConnectionError

    for link in _iter_exception_chain(exc):
        if isinstance(link, psycopg2.OperationalError) or "connection to server at" in str(link):
            return "postgres"
        if isinstance(link, RedisConnectionError):
            return "redis"
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    report = yield
    if report.when in ("setup", "call") and report.failed and call.excinfo is not None:
        service = _classify_infra_error(call.excinfo.value)
        if service is not None and item.get_closest_marker(service) is not None:
            report.outcome = "skipped"
            # Skipped reports must carry the (path, lineno, reason) tuple form,
            # or the terminal reporter's folded-skips view crashes on -rs.
            report.longrepr = (
                str(item.path),
                item.location[1],
                _START_HINT.format(service=service),
            )
    return report
