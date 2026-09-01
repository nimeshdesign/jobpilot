from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import DB_PATH

log = logging.getLogger("jobpilot.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Tiny forward-only migration.

    `create_all` creates missing *tables* but never adds a column to a table
    that already exists, so a database made by an earlier version would break
    on the first query after a new field is added. SQLite can append nullable
    columns cheaply, which is all this app has ever needed.
    """
    from sqlalchemy import inspect, text

    from . import models

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in models.Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or column.primary_key:
                    continue
                col_type = column.type.compile(engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )
                log.info("added column %s.%s", table.name, column.name)


def init_db() -> None:
    from . import models  # noqa: F401  (registers mappers)

    models.Base.metadata.create_all(engine)
    _add_missing_columns()
    with SessionLocal() as db:
        if db.get(models.Profile, 1) is None:
            db.add(models.Profile(id=1))
            db.commit()
