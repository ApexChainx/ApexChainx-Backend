from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# #578: Postgres ships with random_page_cost=4, a spinning-disk assumption. On
# SSD-backed storage that makes the planner systematically undervalue random
# index access, so moderately selective outage searches (a few % of rows) get a
# full Seq Scan instead of the pg_trgm GIN indexes from migration 0028. Setting
# it per connection keeps the behaviour in the codebase (no superuser or
# ALTER DATABASE needed) and applies identically in tests and production.
SEARCH_PLANNER_OPTIONS = "-c random_page_cost=1.1"


def _connect_args(url: str) -> dict:
    return {"options": SEARCH_PLANNER_OPTIONS} if url.startswith("postgresql") else {}


engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args(settings.DATABASE_URL),
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

audit_engine = create_engine(
    settings.DATABASE_AUDIT_URL or settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AuditSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=audit_engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_audit_db():
    db = AuditSessionLocal()
    try:
        yield db
    finally:
        db.close()
