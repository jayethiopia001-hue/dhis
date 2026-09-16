"""
Diagnostics endpoints for the Integration panel —
quick checks to verify connectivity and schema state.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/db-ping")
def db_ping(db: Session = Depends(get_db)):
    """Check Postgres connectivity."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "message": "Postgres reachable"}


@router.get("/tables")
def list_tables(db: Session = Depends(get_db)):
    """List all tables in the public schema."""
    rows = db.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    ).fetchall()
    return {"tables": [r[0] for r in rows], "count": len(rows)}


@router.get("/row-counts")
def row_counts(db: Session = Depends(get_db)):
    """Row counts for all tables in the public schema."""
    tables = db.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    ).fetchall()
    counts = {}
    for (table,) in tables:
        result = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
        counts[table] = result
    return {"counts": counts}
