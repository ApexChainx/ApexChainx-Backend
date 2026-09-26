from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
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
