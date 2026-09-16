"""
Run once to create tables against the configured Postgres instance:

    python -m app.db.init_db
"""
from sqlalchemy import text

from app.db.database import Base, engine
from app.db import models  # noqa: F401  (ensures models are registered on Base)

_RAW_DDL = [
    # Forecasting results (used by /forecasting/run via raw SQL)
    """
    CREATE TABLE IF NOT EXISTS forecast_results (
        id          SERIAL PRIMARY KEY,
        kpi         TEXT NOT NULL,
        period      TEXT NOT NULL,
        value       DOUBLE PRECISION,
        lower_ci    DOUBLE PRECISION,
        upper_ci    DOUBLE PRECISION,
        is_forecast BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    # Situational analysis reports
    """
    CREATE TABLE IF NOT EXISTS sa_reports (
        id          SERIAL PRIMARY KEY,
        state       TEXT NOT NULL DEFAULT 'draft',
        report_md   TEXT,
        overrides   TEXT,
        endorsed_at TIMESTAMP,
        created_at  TIMESTAMP DEFAULT NOW()
    )
    """,
    # Situational analysis chat messages
    """
    CREATE TABLE IF NOT EXISTS sa_chat (
        id         SERIAL PRIMARY KEY,
        report_id  INTEGER NOT NULL,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
]


def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        for ddl in _RAW_DDL:
            conn.execute(text(ddl))
        conn.commit()
    print("Tables created.")


if __name__ == "__main__":
    init_db()
