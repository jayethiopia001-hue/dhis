from sqlalchemy.orm import Session

from app.db.models import PlanningDataIngest
from app.schemas.ingest import IngestPayload


def write_data_values(db: Session, payload: IngestPayload) -> int:
    """Write each DataValue in the payload as a row in the landing table."""
    rows = [
        PlanningDataIngest(
            data_element=dv.dataElement,
            period=dv.period,
            org_unit=dv.orgUnit,
            category_option_combo=dv.categoryOptionCombo,
            attribute_option_combo=dv.attributeOptionCombo,
            value=dv.value,
            source=payload.source,
            raw_payload=dv.model_dump(),
        )
        for dv in payload.dataValues
    ]
    db.add_all(rows)
    try:
        db.commit()
    except Exception as e:
        # Rollback and raise a clearer error including a small sample of payloads
        db.rollback()
        sample = [getattr(r, "raw_payload", None) for r in rows[:5]]
        raise Exception(f"DB write failed: {e}; sample_raw_payloads={sample}; total_rows={len(rows)}")
    return len(rows)
