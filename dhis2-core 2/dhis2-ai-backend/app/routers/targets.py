"""
Indicative Target Setting router — ML/Statistical layer (SPEC.md §5.2).

Implements the spec in docs/INDICATIVE_TARGET_SETTING_SPEC.md.

Key design principles (from spec):
  - No flat %-increase allocation. Uses population-weighted, trajectory-adjusted
    disaggregation — every unit gets a different target reflecting its actual situation.
  - No GenAI. Pure arithmetic + statistical rules, fully traceable.
  - Consumes baselines and trajectories from planning_data_ingest (never recomputes FASTER).
  - Comparable-cohort method: performance-similarity clustering (quartile-based) across
    same-level units for the same indicator — surfaced explicitly, not hidden.
  - Reconciliation gap (top-down vs bottom-up sum) is shown, never silently forced.
  - Every recommendation carries a human-readable rationale string.
  - Recommends only; never finalises. Human sign-off required.

Endpoints:
  GET  /targets/kpis                              → list of available KPI identifiers
  GET  /targets/disaggregate/{kpi}/{period}       → national target split to all levels
  POST /targets/woreda-recommendation             → single-woreda detailed recommendation
  GET  /targets/{unit_id}/comparable-cohort       → cohort used for that unit's sanity check
"""
from __future__ import annotations

import statistics
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.database import get_db
from app.config import settings

router = APIRouter(prefix="/targets", tags=["targets"])

# ── Shared KPI + OU registries (mirror of situational_analysis.py) ────────────

KPIS: list[tuple[str, str, str]] = [
    ("penta1",   "gUTDQGm7wQ4", "eBLFFQCHD6R"),
    ("penta3",   "UbbrqQ7B697", "z2kPnR9zAwf"),
    ("mcv1",     "xegDuD9La4v", "xbIBbuMFd9W"),
    ("mcv2",     "PfO4WUDlWuX", "gfq4lUY0P6z"),
    ("opv3",     "WG81RqwkVHW", "q53mBzytPAn"),
    ("pcv3",     "Uyy6Skhj4Yj", "I4JRfdQAe6N"),
    ("ipv",      "vNMWVmoyXmE", "tW3jSOi27n7"),
    ("full_imm", "M8OvIia8OG5", "ZqLnqha9z4M"),
    ("pab",      "d4vCK4A4FGG", "GPqX9cbF8Ph"),
    ("rota2",    "B9m2bnFVuHZ", "cGogkdmtsVr"),
]
KPI_MAP: dict[str, tuple[str, str]] = {name: (num, den) for name, num, den in KPIS}
KPI_DISPLAY: dict[str, str] = {
    "penta1":   "Penta-1 Coverage",
    "penta3":   "Penta-3 Coverage",
    "mcv1":     "MCV1 Coverage",
    "mcv2":     "MCV2 Coverage",
    "opv3":     "OPV3 Coverage",
    "pcv3":     "PCV-3 Coverage",
    "ipv":      "IPV Coverage",
    "full_imm": "Full Immunization Coverage",
    "pab":      "PAB Coverage",
    "rota2":    "Rotavirus-2 Coverage",
}

OU_LOOKUP: dict[str, dict] = {
    "yjpvwh09CDf": {"name": "Addis Ababa",       "level": 2, "parent": None},
    "kUjEcPVJ2tR": {"name": "B/Gumuz",           "level": 2, "parent": None},
    "RYrxPbCkhdV": {"name": "Central E",          "level": 2, "parent": None},
    "cIizHJIQgJl": {"name": "Amhara",             "level": 2, "parent": None},
    "Tzw27jkooEK": {"name": "South West",         "level": 2, "parent": None},
    "nBgLgJH6Mem": {"name": "Dire Dawa",          "level": 2, "parent": None},
    "MEXefqReTtJ": {"name": "Southern E",         "level": 2, "parent": None},
    "qrjHLth16OT": {"name": "Gambela",            "level": 2, "parent": None},
    "D3W2XyygNde": {"name": "Harari",             "level": 2, "parent": None},
    "iTy4EkyriAI": {"name": "Sidama",             "level": 2, "parent": None},
    "gHddmQAtKCs": {"name": "Oromia",             "level": 2, "parent": None},
    "YlWfsEvBLeF": {"name": "Somali",             "level": 2, "parent": None},
    "pfAZhnM6Jy8": {"name": "Afar",               "level": 2, "parent": None},
    "bCIvElc1oWm": {"name": "Amhara Zone 2",      "level": 3, "parent": "cIizHJIQgJl"},
    "kzzr09D7lGW": {"name": "Amhara Zone 1",      "level": 3, "parent": "cIizHJIQgJl"},
    "FaeLGJt8gPM": {"name": "Oromia Zone 1",      "level": 3, "parent": "gHddmQAtKCs"},
    "kIBzTdr0Ocp": {"name": "Oromia Zone 2",      "level": 3, "parent": "gHddmQAtKCs"},
    "axKeXooc9T2": {"name": "Amhara Z1 Woreda 1", "level": 4, "parent": "kzzr09D7lGW"},
    "NB7HRO6bTfY": {"name": "Amhara Z1 Woreda 2", "level": 4, "parent": "kzzr09D7lGW"},
    "Z3UVKThDZm5": {"name": "Amhara Z2 Woreda 1", "level": 4, "parent": "bCIvElc1oWm"},
    "IzwBx9xbfS9": {"name": "Amhara Z2 Woreda 2", "level": 4, "parent": "bCIvElc1oWm"},
    "vvLtgeYWxQP": {"name": "Oromia Z1 Woreda 1", "level": 4, "parent": "FaeLGJt8gPM"},
    "Q7z97K2Exp2": {"name": "Oromia Z1 Woreda 2", "level": 4, "parent": "FaeLGJt8gPM"},
    "zeu38UDp7GT": {"name": "Oromia Z2 Woreda 1", "level": 4, "parent": "kIBzTdr0Ocp"},
    "b73wjwVE3Di": {"name": "Oromia Z2 Woreda 2", "level": 4, "parent": "kIBzTdr0Ocp"},
}

PERIODS = ["2011July", "2012July", "2013July", "2014July", "2015July", "2016July", "2017July"]
NATIONAL_TARGET = 90.0
DIVERGENCE_THRESHOLD_PP = 5.0  # flag if recommended target deviates > 5pp from indicative


# ── Core data fetching ────────────────────────────────────────────────────────

def _fetch_coverage_history(db: Session, num_id: str, den_id: str) -> dict[str, dict[str, float]]:
    """
    Returns {ou_id: {period: coverage%}} for all org units across all periods.
    Uses individual named params for IN clause (pg8000 requirement).
    """
    placeholders = ", ".join(f":p{i}" for i in range(len(PERIODS)))
    sql = text(f"""
        SELECT org_unit, period,
               SUM(CASE WHEN data_element=:num THEN value::numeric ELSE 0 END) /
               NULLIF(SUM(CASE WHEN data_element=:den THEN value::numeric ELSE 0 END), 0) * 100 AS cov
        FROM planning_data_ingest
        WHERE data_element IN (:num, :den)
          AND source='dhis2-nextgen-integration'
          AND period IN ({placeholders})
        GROUP BY org_unit, period
    """)
    params: dict = {"num": num_id, "den": den_id}
    params.update({f"p{i}": p for i, p in enumerate(PERIODS)})
    rows = db.execute(sql, params).fetchall()

    result: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.cov is not None:
            result.setdefault(r.org_unit, {})[r.period] = round(float(r.cov), 2)
    return result


def _compute_trajectory(history: dict[str, float]) -> Optional[float]:
    """
    Average year-on-year change in pp across all consecutive periods with data.
    Returns None if fewer than 2 data points.
    """
    values = [history[p] for p in PERIODS if p in history]
    if len(values) < 2:
        return None
    diffs = [values[i+1] - values[i] for i in range(len(values) - 1)]
    return round(statistics.mean(diffs), 3)


def _estimate_population_weight(ou_id: str, all_ou_ids: list[str]) -> float:
    """
    Proxy population weight from data volume in the ingest table.
    In production this would come from DHIS2 population estimates.
    Uniform fallback if data unavailable.
    """
    # Equal weight within level — replace with real population data when available
    return 1.0 / len(all_ou_ids) if all_ou_ids else 1.0


def _comparable_cohort(
    ou_id: str,
    baseline: float,
    all_baselines: dict[str, float],
    level: int,
) -> dict:
    """
    Cohort = same-level units within the same performance quartile as ou_id.
    Method: split all units at this level into 4 quartiles by EFY2017 baseline.
    Returns cohort member IDs + names + their baselines, and cohort median trajectory.
    """
    same_level = {oid: b for oid, b in all_baselines.items()
                  if oid != ou_id and OU_LOOKUP.get(oid, {}).get("level") == level}
    if not same_level:
        return {"method": "performance-quartile", "members": [], "cohort_median_baseline": None}

    sorted_vals = sorted(same_level.values())
    n = len(sorted_vals)
    q1 = sorted_vals[n // 4]
    q2 = sorted_vals[n // 2]
    q3 = sorted_vals[3 * n // 4]

    def quartile(v: float) -> int:
        if v <= q1: return 1
        if v <= q2: return 2
        if v <= q3: return 3
        return 4

    target_q = quartile(baseline)
    members = [
        {"id": oid, "name": OU_LOOKUP[oid]["name"], "baseline": b, "quartile": target_q}
        for oid, b in same_level.items()
        if quartile(b) == target_q
    ]
    cohort_baselines = [m["baseline"] for m in members]
    cohort_median = round(statistics.median(cohort_baselines), 2) if cohort_baselines else None

    return {
        "method": "performance-quartile",
        "quartile": target_q,
        "quartile_bounds": {"q1": round(q1, 1), "q2": round(q2, 1), "q3": round(q3, 1)},
        "members": members,
        "cohort_median_baseline": cohort_median,
    }


def _recommend_target(
    ou_name: str,
    baseline: float,
    baseline_period_label: str,
    trajectory_pp_per_year: Optional[float],
    national_target: float,
    indicative_range: tuple[float, float],
    cohort_median_baseline: Optional[float],
    years_to_target: int = 5,
) -> dict:
    """
    Recommend a specific target within [indicative_low, indicative_high].

    Method (transparent, step-by-step):
    1. Trajectory-based projection: baseline + (trajectory * years)
    2. Anchor: halfway between trajectory projection and national target
    3. Clamp to national target ceiling and current baseline floor
    4. Clamp to indicative range
    5. Flag divergence if result deviates from indicative midpoint > threshold

    Returns target, rationale string, divergence flag.
    """
    steps: list[str] = []

    # Step 1: trajectory projection
    if trajectory_pp_per_year is not None:
        projected = baseline + (trajectory_pp_per_year * years_to_target)
        projected = min(max(projected, baseline), 100.0)
        steps.append(
            f"Historical trajectory: {trajectory_pp_per_year:+.1f}pp/year → "
            f"projects to {projected:.1f}% in {years_to_target} years"
        )
    else:
        # No trajectory: assume modest uniform growth
        projected = min(baseline + 5.0, national_target)
        steps.append("Trajectory: insufficient history — applied conservative +5pp assumption")

    # Step 2: anchor between projection and national target
    anchor = (projected + national_target) / 2
    anchor = min(anchor, national_target)
    steps.append(
        f"Anchor: midpoint of trajectory projection ({projected:.1f}%) "
        f"and national target ({national_target}%) = {anchor:.1f}%"
    )

    # Step 3: cohort adjustment (nudge toward cohort median achievable growth)
    if cohort_median_baseline is not None:
        cohort_gap_vs_national = national_target - cohort_median_baseline
        cohort_achievable = cohort_median_baseline + cohort_gap_vs_national * 0.5
        # Weighted blend: 70% anchor, 30% cohort achievable
        blended = 0.70 * anchor + 0.30 * cohort_achievable
        blended = min(blended, national_target)
        steps.append(
            f"Cohort adjustment: comparable units median baseline {cohort_median_baseline:.1f}% → "
            f"cohort-achievable {cohort_achievable:.1f}%; "
            f"70/30 blend → {blended:.1f}%"
        )
        anchor = blended

    # Step 4: clamp to indicative range
    low, high = indicative_range
    clamped = max(low, min(high, anchor))
    if clamped != anchor:
        steps.append(
            f"Clamped to indicative range [{low:.1f}%, {high:.1f}%]: {anchor:.1f}% → {clamped:.1f}%"
        )
    else:
        steps.append(f"Within indicative range [{low:.1f}%, {high:.1f}%]: {clamped:.1f}%")

    recommended = round(clamped, 1)
    indicative_mid = (low + high) / 2
    divergence = abs(recommended - indicative_mid) > DIVERGENCE_THRESHOLD_PP

    rationale = (
        f"{ou_name} — baseline ({baseline_period_label}): {baseline:.1f}%, HSDIP national target: {national_target}%. "
        + " → ".join(steps)
        + (f" ⚠ DIVERGENCE FLAG: recommended target deviates {abs(recommended - indicative_mid):.1f}pp "
           f"from indicative midpoint ({indicative_mid:.1f}%) — review required." if divergence else "")
    )

    return {
        "recommended_target": recommended,
        "rationale": rationale,
        "divergence_flagged": divergence,
        "divergence_pp": round(abs(recommended - indicative_mid), 1),
        "steps": steps,
    }


def _disaggregate(
    db: Session,
    num_id: str,
    den_id: str,
    kpi_name: str,
    national_target: float,
) -> dict:
    """
    Core disaggregation logic used by both the API endpoint and woreda-recommendation.
    Returns per-unit allocations for all levels with rationales + reconciliation check.
    """
    history = _fetch_coverage_history(db, num_id, den_id)

    # Build baselines using the MOST RECENT period with data per unit.
    # Regions may only have data through 2016July while zones/woredas have 2017July.
    baselines: dict[str, float] = {}
    baseline_periods: dict[str, str] = {}
    trajectories: dict[str, Optional[float]] = {}
    for ou_id, h in history.items():
        for period in reversed(PERIODS):   # most recent first
            if period in h:
                baselines[ou_id] = h[period]
                baseline_periods[ou_id] = period
                trajectories[ou_id] = _compute_trajectory(h)
                break

    # Group by level
    by_level: dict[int, list[str]] = {2: [], 3: [], 4: []}
    for ou_id in baselines:
        lvl = OU_LOOKUP.get(ou_id, {}).get("level")
        if lvl in by_level:
            by_level[lvl].append(ou_id)

    results_by_level: dict[str, list[dict]] = {}
    bottom_up_sums: dict[int, float] = {}

    for level in (2, 3, 4):
        units = by_level[level]
        if not units:
            continue

        # Population weights (uniform proxy — replace with real pop data in production)
        weights = {ou_id: _estimate_population_weight(ou_id, units) for ou_id in units}

        level_results: list[dict] = []
        total_weighted_target = 0.0

        for ou_id in sorted(units):
            b = baselines[ou_id]
            bp = baseline_periods[ou_id]
            traj = trajectories.get(ou_id)
            cohort = _comparable_cohort(ou_id, b, baselines, level)
            cohort_median = cohort.get("cohort_median_baseline")

            gap_to_target = national_target - b

            if gap_to_target <= 0:
                # Unit already at or above target — recommend maintaining at target
                indicative_low = round(national_target - 2.0, 1)
                indicative_high = round(national_target, 1)
                indicative_point = national_target
            else:
                # Trajectory adjustment on the indicative range width
                traj_factor = 1.0
                if traj is not None:
                    traj_factor = max(0.5, min(1.5, 1.0 - (traj / 10.0)))
                range_half = max(2.0, gap_to_target * 0.15 * traj_factor)
                indicative_point = b + gap_to_target * 0.5
                indicative_low = round(max(b, indicative_point - range_half), 1)
                indicative_high = round(min(national_target, indicative_point + range_half), 1)
                # Safety: ensure low ≤ high
                if indicative_low > indicative_high:
                    indicative_low, indicative_high = indicative_high, indicative_low

            rec = _recommend_target(
                ou_name=OU_LOOKUP.get(ou_id, {}).get("name", ou_id),
                baseline=b,
                baseline_period_label=bp,
                trajectory_pp_per_year=traj,
                national_target=national_target,
                indicative_range=(indicative_low, indicative_high),
                cohort_median_baseline=cohort_median,
            )

            total_weighted_target += rec["recommended_target"] * weights[ou_id]

            level_results.append({
                "unit_id": ou_id,
                "unit_name": OU_LOOKUP.get(ou_id, {}).get("name", ou_id),
                "level": level,
                "parent_id": OU_LOOKUP.get(ou_id, {}).get("parent"),
                "parent_name": OU_LOOKUP.get(
                    OU_LOOKUP.get(ou_id, {}).get("parent", ""), {}
                ).get("name"),
                "baseline_efy2017": b,
                "baseline_period": bp,
                "trajectory_pp_per_year": traj,
                "indicative_range": {"low": indicative_low, "high": indicative_high},
                "recommended_target": rec["recommended_target"],
                "rationale": rec["rationale"],
                "divergence_flagged": rec["divergence_flagged"],
                "divergence_pp": rec["divergence_pp"],
                "cohort": {
                    "method": cohort["method"],
                    "quartile": cohort.get("quartile"),
                    "member_count": len(cohort.get("members", [])),
                    "cohort_median_baseline": cohort_median,
                },
            })

        bottom_up_sums[level] = round(total_weighted_target, 2)
        results_by_level[f"level_{level}"] = level_results

    # Reconciliation check
    reconciliation: dict[str, object] = {}
    for level, bottom_up in bottom_up_sums.items():
        gap = round(bottom_up - national_target, 2)
        reconciliation[f"level_{level}"] = {
            "national_target": national_target,
            "bottom_up_weighted_sum": bottom_up,
            "gap_pp": gap,
            "reconciled": abs(gap) <= 2.0,
            "note": (
                "Within reconciliation tolerance (±2pp)"
                if abs(gap) <= 2.0
                else f"⚠ Gap of {gap:+.1f}pp between national target and bottom-up weighted sum — "
                     f"review flagged units or adjust national target."
            ),
        }

    return {
        "kpi": kpi_name,
        "national_target": national_target,
        "periods_used": PERIODS,
        "baseline_period": "2017July (EFY2017)",
        "disaggregation": results_by_level,
        "reconciliation": reconciliation,
        "method_notes": [
            "Population weights: uniform proxy (1/N within level) — replace with DHIS2 population estimates in production.",
            "Trajectories: average annual pp change across EFY2011–2017 from planning_data_ingest.",
            "Comparable cohort: performance-quartile clustering of same-level units by EFY2017 baseline.",
            "Open question per spec §9.1: 'comparable district' definition not yet confirmed with TWG — "
            "current method uses performance similarity only, not geography or population size.",
            "Indicative ranges: units close half the gap to national target; width adjusted by trajectory.",
            "Divergence threshold: ±5pp from indicative midpoint triggers human-review flag.",
        ],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/kpis")
def list_kpis():
    return [{"id": name, "display_name": KPI_DISPLAY[name]} for name, _, _ in KPIS]


@router.get("/disaggregate/{kpi}/{period}")
def disaggregate(kpi: str, period: str, db: Session = Depends(get_db)):
    if kpi not in KPI_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown KPI '{kpi}'. Valid values: {list(KPI_MAP.keys())}",
        )
    if period not in PERIODS and period != "2017July":
        raise HTTPException(
            status_code=400,
            detail=f"Period '{period}' not in dataset. Valid: {PERIODS}",
        )
    num_id, den_id = KPI_MAP[kpi]
    return _disaggregate(db, num_id, den_id, KPI_DISPLAY[kpi], NATIONAL_TARGET)


class WoRedaRecRequest(BaseModel):
    unit_id: str
    kpi: str
    national_target: float = NATIONAL_TARGET
    years_to_target: int = 5


@router.post("/woreda-recommendation")
def woreda_recommendation(req: WoRedaRecRequest, db: Session = Depends(get_db)):
    if req.kpi not in KPI_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown KPI '{req.kpi}'")
    if req.unit_id not in OU_LOOKUP:
        raise HTTPException(status_code=400, detail=f"Unknown unit_id '{req.unit_id}'")

    num_id, den_id = KPI_MAP[req.kpi]
    history = _fetch_coverage_history(db, num_id, den_id)

    # Use most recent available period
    baseline = None
    baseline_period = None
    for period in reversed(PERIODS):
        if period in history.get(req.unit_id, {}):
            baseline = history[req.unit_id][period]
            baseline_period = period
            break

    if baseline is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for unit '{req.unit_id}' and KPI '{req.kpi}'",
        )

    baselines = {oid: next((h[p] for p in reversed(PERIODS) if p in h), None)
                 for oid, h in history.items()}
    baselines = {oid: b for oid, b in baselines.items() if b is not None}

    traj = _compute_trajectory(history.get(req.unit_id, {}))
    level = OU_LOOKUP[req.unit_id]["level"]
    cohort = _comparable_cohort(req.unit_id, baseline, baselines, level)

    gap = req.national_target - baseline
    if gap <= 0:
        indicative_range = (round(req.national_target - 2.0, 1), round(req.national_target, 1))
    else:
        range_half = max(2.0, gap * 0.15)
        indicative_point = baseline + gap * 0.5
        indicative_range = (
            round(max(baseline, indicative_point - range_half), 1),
            round(min(req.national_target, indicative_point + range_half), 1),
        )

    rec = _recommend_target(
        ou_name=OU_LOOKUP[req.unit_id]["name"],
        baseline=baseline,
        baseline_period_label=baseline_period,
        trajectory_pp_per_year=traj,
        national_target=req.national_target,
        indicative_range=indicative_range,
        cohort_median_baseline=cohort.get("cohort_median_baseline"),
        years_to_target=req.years_to_target,
    )

    return {
        "unit_id": req.unit_id,
        "unit_name": OU_LOOKUP[req.unit_id]["name"],
        "level": level,
        "kpi": req.kpi,
        "kpi_display": KPI_DISPLAY[req.kpi],
        "baseline": baseline,
        "baseline_period": baseline_period,
        "trajectory_pp_per_year": traj,
        "indicative_range": {"low": indicative_range[0], "high": indicative_range[1]},
        **rec,
        "cohort": cohort,
        "method_notes": [
            "Recommendation is indicative only — requires human review and political negotiation before finalisation.",
            "Trajectory derived from available annual averages; not recalculated from raw data.",
            "Cohort method: performance-quartile (open question per spec §9.1 — confirm with TWG).",
        ],
    }


@router.get("/{unit_id}/comparable-cohort")
def comparable_cohort(unit_id: str, kpi: str, db: Session = Depends(get_db)):
    if unit_id not in OU_LOOKUP:
        raise HTTPException(status_code=400, detail=f"Unknown unit_id '{unit_id}'")
    if kpi not in KPI_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown KPI '{kpi}'")

    num_id, den_id = KPI_MAP[kpi]
    history = _fetch_coverage_history(db, num_id, den_id)
    baselines = {oid: h["2017July"] for oid, h in history.items() if "2017July" in h}

    if unit_id not in baselines:
        raise HTTPException(
            status_code=404,
            detail=f"No EFY2017 data for unit '{unit_id}' and KPI '{kpi}'",
        )

    baseline = baselines[unit_id]
    level = OU_LOOKUP[unit_id]["level"]
    cohort = _comparable_cohort(unit_id, baseline, baselines, level)

    # Enrich cohort members with trajectories
    for m in cohort.get("members", []):
        m["trajectory_pp_per_year"] = _compute_trajectory(history.get(m["id"], {}))

    return {
        "unit_id": unit_id,
        "unit_name": OU_LOOKUP[unit_id]["name"],
        "unit_baseline_efy2017": baseline,
        "kpi": kpi,
        "kpi_display": KPI_DISPLAY[kpi],
        **cohort,
        "open_question": (
            "The 'comparable district' definition (spec §9.1) has not yet been confirmed with "
            "Yordanos/TWG. Current method uses performance-similarity (quartile) only — "
            "geography and population size are not yet factored in."
        ),
    }


# ── LLM review endpoint ────────────────────────────────────────────────────────

TARGET_LLM_REVIEW_PROMPT = """You are a public health analyst providing narrative rationale for indicative immunisation targets recommended for Ethiopia's HSDIP planning cycle.

You have been given the statistical target recommendations produced by the system — these numbers are FINAL and correct. Your job is to:
1. Explain in plain, professional language WHY each unit has been assigned its target range
2. Contextualise the statistical rationale with the supplied policy/resource/strategic context
3. Flag any units where the recommended target warrants special human scrutiny
4. If endorsed situational analysis text is supplied, link the target narrative to the SA findings — this ensures consistency between the SA and the target plan

## Non-negotiable rules
- Do NOT change any numeric figures. The numbers were computed by the statistical engine.
- Never invent programme context not supplied to you. Use [CONTEXT NEEDED] where relevant context is absent.
- Frame as a briefing to a planning officer — concise, evidence-grounded, action-oriented.
- If a unit has a divergence flag, explicitly recommend what the planner should investigate.
- Structure: one paragraph per unit (or grouped if many similar units), then a summary paragraph.

## KPI
{kpi_display}

## Statistical recommendations
{recommendations}

## Additional context provided by planner
{additional_context}

## Endorsed situational analysis (if available)
{endorsed_sa}

Produce the narrative rationale now.
"""


class LLMReviewRequest(BaseModel):
    kpi: str
    period: str = "2017July"
    additional_context: Optional[str] = None
    endorsed_sa_id: Optional[int] = None


@router.post("/llm-review")
def llm_review(req: LLMReviewRequest, db: Session = Depends(get_db)):
    from google import genai as google_genai

    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="Gemini API key not configured.")

    kpi = req.kpi.lower()
    if kpi not in KPI_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown KPI '{kpi}'. Use GET /targets/kpis.")

    # Fetch statistical disaggregation output by calling the internal function
    num_id, den_id = KPI_MAP[kpi]
    history = _fetch_coverage_history(db, num_id, den_id)
    baselines = {uid: _latest_coverage(h) for uid, h in history.items() if _latest_coverage(h) is not None}

    if not baselines:
        raise HTTPException(status_code=404, detail=f"No coverage data found for KPI '{kpi}'.")

    national_target = 90.0
    pop_weight: dict[str, float] = {
        uid: float(OU_LOOKUP.get(uid, {}).get("population", 100000))
        for uid in baselines
    }
    total_pop = sum(pop_weight.values()) or 1

    recs_lines = []
    for uid, baseline in sorted(baselines.items(), key=lambda x: OU_LOOKUP.get(x[0], {}).get("level", 9)):
        info = OU_LOOKUP.get(uid, {})
        traj = _compute_trajectory(history.get(uid, {}))
        cohort = _comparable_cohort(uid, baseline, baselines, info.get("level", 4))
        rec = _recommend_target(
            baseline, traj, national_target,
            cohort.get("cohort_achievable_rate", traj),
            pop_weight[uid] / total_pop
        )
        level_label = {2: "Region", 3: "Zone", 4: "Woreda"}.get(info.get("level", 4), "Unit")
        diverge = " ⚠️ DIVERGENCE FLAG" if rec["divergence_flag"] else ""
        recs_lines.append(
            f"- **{info.get('name', uid)}** ({level_label}): Baseline {baseline:.1f}% | "
            f"Trend {traj:+.1f}pp/yr | Indicative range {rec['indicative_low']:.1f}–{rec['indicative_high']:.1f}% | "
            f"Recommended {rec['recommended_target']:.1f}%{diverge}\n  "
            f"  Statistical rationale: {rec['rationale']}"
        )

    endorsed_sa_text = "[No endorsed SA supplied]"
    if req.endorsed_sa_id:
        sa_row = db.execute(
            text("SELECT report_md FROM sa_reports WHERE id=:sid AND state='endorsed'"),
            {"sid": req.endorsed_sa_id},
        ).fetchone()
        if sa_row:
            endorsed_sa_text = sa_row.report_md[:4000]

    try:
        client = google_genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=TARGET_LLM_REVIEW_PROMPT.format(
                kpi_display=KPI_DISPLAY.get(kpi, kpi),
                recommendations="\n".join(recs_lines),
                additional_context=req.additional_context or "[None provided]",
                endorsed_sa=endorsed_sa_text,
            ),
        )
        narrative = response.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    return {
        "kpi": kpi,
        "kpi_display": KPI_DISPLAY.get(kpi, kpi),
        "llm_narrative": narrative,
        "unit_count": len(recs_lines),
    }

