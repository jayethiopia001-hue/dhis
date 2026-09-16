"""
Step 1 endpoint: receive DHIS2-shaped data value payloads (from the fake
DHIS2 planning app prototype, for now) and land them in Postgres.

See docs/SPEC.md §5.1 for what comes next — this landing table is the
input the deterministic layer (baseline derivation, target formula
linkage, aggregation roll-up) will eventually read from.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.ingest import IngestPayload, IngestResult
from app.services.postgres_writer import write_data_values

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/push", response_model=IngestResult)
def push_data(payload: IngestPayload, db: Session = Depends(get_db)):
    written = write_data_values(db, payload)
    return IngestResult(received=len(payload.dataValues), written=written, source=payload.source)
