from typing import List, Optional
from pydantic import BaseModel


class DataValue(BaseModel):
    dataElement: str
    period: str
    orgUnit: str
    categoryOptionCombo: Optional[str] = None
    attributeOptionCombo: Optional[str] = None
    value: Optional[str] = None


class IngestPayload(BaseModel):
    dataValues: List[DataValue]
    source: Optional[str] = "dhis2-fake-app"


class IngestResult(BaseModel):
    received: int
    written: int
    source: str
