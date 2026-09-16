"""
Aggregation roll-up endpoints (deterministic layer) — see docs/SPEC.md §5.1.

Covers:
- Woreda -> zone -> region -> national roll-up
- Reconciliation checks (which woredas are driving a gap)
- Resolving template-vs-analysis-mode discrepancies

NOT YET IMPLEMENTED.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/aggregation", tags=["aggregation"])

# TODO: POST /aggregation/rollup
# TODO: GET /aggregation/reconcile/{indicator}/{period}
