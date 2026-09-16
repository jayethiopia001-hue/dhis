from sqlalchemy import Column, Integer, String, DateTime, JSON, func

from app.db.database import Base


class PlanningDataIngest(Base):
    """
    Landing table for raw planning data pushed from the DHIS2 prototype app.

    Intentionally a loose landing zone for step 1 — takes whatever
    DHIS2-dataValue-shaped payload the fake app sends, raw_payload included
    for full fidelity. See docs/SPEC.md §5.1 for the deterministic layer
    that will eventually read from / validate against this table (baseline
    derivation, target formula linkage, aggregation roll-up).
    """
    __tablename__ = "planning_data_ingest"

    id = Column(Integer, primary_key=True, index=True)
    data_element = Column(String, nullable=False, index=True)
    period = Column(String, nullable=False, index=True)
    org_unit = Column(String, nullable=False, index=True)
    category_option_combo = Column(String, nullable=True)
    attribute_option_combo = Column(String, nullable=True)
    value = Column(String, nullable=True)
    source = Column(String, nullable=False, default="dhis2-fake-app")
    raw_payload = Column(JSON, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
