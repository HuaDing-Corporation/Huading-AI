from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args_for(url: str, connect_timeout: int) -> dict[str, object]:
    # connect_timeout keeps the readiness probe from hanging when Postgres is
    # unreachable (#003-FIX P2). Only psycopg/libpq accept it; skip for sqlite etc.
    if url.startswith(("postgresql", "postgres")):
        return {"connect_timeout": connect_timeout}
    return {}


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=_connect_args_for(settings.database_url, settings.db_connect_timeout),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
