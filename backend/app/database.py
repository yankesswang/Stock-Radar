import sqlite3

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import settings

_database_url = make_url(settings.DATABASE_URL)
_is_sqlite = _database_url.get_backend_name() == "sqlite"

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 5.0} if _is_sqlite else {},
    echo=False,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _set_sqlite_connection_pragmas(
    dbapi_connection: sqlite3.Connection,
    _connection_record=None,
) -> None:
    """Apply correctness and lock-wait settings to every pooled connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


if _is_sqlite:
    event.listen(engine, "connect", _set_sqlite_connection_pragmas)


class Base(DeclarativeBase):
    pass


def initialize_database() -> None:
    """Initialize SQLite runtime settings and upgrade the managed schema."""
    if _is_sqlite:
        # WAL lets API readers proceed while scheduled jobs are writing.  This is
        # persistent database metadata, so it only needs to be requested once at
        # startup rather than on every pooled connection.
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
    from alembic import command
    from alembic.config import Config

    config = Config(str(settings.PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    command.upgrade(config, "head")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
