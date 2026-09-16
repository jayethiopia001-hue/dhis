"""
Situational Analysis router.

Flow:
  POST /situational-analysis/generate
    1. Pull coverage data + forecasts from GCP (national + sub-national)
    2. Build structured data context with regional/zone/woreda breakdown
    3. Call Gemini with HSDIP-aligned instructions to draft full SA report
    4. Persist as sa_reports row (state=draft)
    5. Return report markdown + id

  GET  /situational-analysis/latest
    Returns most recent report + chat history

  POST /situational-analysis/chat
    Appends a user message, sends full conversation + data context to Gemini.
    Amendments are report-level overrides only — source data unchanged.

  POST /situational-analysis/endorse
    Sets state=endorsed on the latest report.
"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.database import get_db
from app.config import settings

router = APIRouter(prefix="/situational-analysis", tags=["situational-analysis"])

# ── KPI registry ──────────────────────────────────────────────────────────────
KPIS = [
    ("Penta-1 Coverage",           "gUTDQGm7wQ4", "eBLFFQCHD6R"),
    ("Penta-3 Coverage",           "UbbrqQ7B697", "z2kPnR9zAwf"),
    ("MCV1 Coverage",              "xegDuD9La4v", "xbIBbuMFd9W"),
    ("MCV2 Coverage",              "PfO4WUDlWuX", "gfq4lUY0P6z"),
    ("OPV3 Coverage",              "WG81RqwkVHW", "q53mBzytPAn"),
    ("PCV-3 Coverage",             "Uyy6Skhj4Yj", "I4JRfdQAe6N"),
    ("IPV Coverage",               "vNMWVmoyXmE", "tW3jSOi27n7"),
    ("Full Immunization Coverage", "M8OvIia8OG5", "ZqLnqha9z4M"),
    ("PAB Coverage",               "d4vCK4A4FGG", "GPqX9cbF8Ph"),
    ("Rotavirus-2 Coverage",       "B9m2bnFVuHZ", "cGogkdmtsVr"),
]

PERIODS = ["2011July","2012July","2013July","2014July","2015July","2016July","2017July"]
PERIOD_LABEL = {
    "2011July":"EFY2011","2012July":"EFY2012","2013July":"EFY2013",
    "2014July":"EFY2014","2015July":"EFY2015","2016July":"EFY2016",
    "2017July":"EFY2017",
}
TARGET = 90.0

# Org unit lookup: id → {name, level}
OU_LOOKUP: dict[str, dict] = {
    "yjpvwh09CDf": {"name": "Addis Ababa",  "level": 2},
    "kUjEcPVJ2tR": {"name": "B/Gumuz",      "level": 2},
    "RYrxPbCkhdV": {"name": "Central E",    "level": 2},
    "cIizHJIQgJl": {"name": "Amhara",       "level": 2},
    "Tzw27jkooEK": {"name": "Sout West",    "level": 2},
    "nBgLgJH6Mem": {"name": "Dir Dawa",     "level": 2},
    "MEXefqReTtJ": {"name": "Souther E",    "level": 2},
    "qrjHLth16OT": {"name": "Gambela",      "level": 2},
    "D3W2XyygNde": {"name": "Harrari",      "level": 2},
    "iTy4EkyriAI": {"name": "Sidama",       "level": 2},
    "gHddmQAtKCs": {"name": "Oromia",       "level": 2},
    "YlWfsEvBLeF": {"name": "Somali",       "level": 2},
    "pfAZhnM6Jy8": {"name": "Afar",         "level": 2},
    "bCIvElc1oWm": {"name": "Amhara Zone 2","level": 3},
    "FaeLGJt8gPM": {"name": "Oromia Zone 1","level": 3},
    "kzzr09D7lGW": {"name": "Amhara Zone 1","level": 3},
    "kIBzTdr0Ocp": {"name": "Oromia Zone 2","level": 3},
    "zeu38UDp7GT": {"name": "Oromia Z2 Woreda 1", "level": 4},
    "Q7z97K2Exp2": {"name": "Oromia Z1 Woreda 2", "level": 4},
    "NB7HRO6bTfY": {"name": "Amhara Z1 Woreda 2", "level": 4},
    "vvLtgeYWxQP": {"name": "Oromia Z1 Woreda 1", "level": 4},
    "Z3UVKThDZm5": {"name": "Amhara Z2 Woreda 1", "level": 4},
    "axKeXooc9T2": {"name": "Amhara Z1 Woreda 1", "level": 4},
    "IzwBx9xbfS9": {"name": "Amhara Z2 Woreda 2", "level": 4},
    "b73wjwVE3Di": {"name": "Oromia Z2 Woreda 2", "level": 4},
}

TARGET = 90.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _coverage_by_orgunits(db: Session, num_id: str, den_id: str, level: int, period: str) -> dict[str, float]:
    """Return {ou_id: coverage%} for a given level, KPI, and period."""
    ou_ids = [oid for oid, meta in OU_LOOKUP.items() if meta["level"] == level]
    if not ou_ids:
        return {}
    # pg8000 requires individual named placeholders — no tuple binding for IN clauses
    ou_placeholders = ", ".join(f":ou{i}" for i in range(len(ou_ids)))
    sql = text(f"""
        SELECT org_unit,
               SUM(CASE WHEN data_element=:num THEN value::numeric ELSE 0 END) /
               NULLIF(SUM(CASE WHEN data_element=:den THEN value::numeric ELSE 0 END),0)*100 AS cov
        FROM planning_data_ingest
        WHERE data_element IN (:num,:den)
          AND source='dhis2-nextgen-integration'
          AND period=:period
          AND org_unit IN ({ou_placeholders})
        GROUP BY org_unit
    """)
    params = {"num": num_id, "den": den_id, "period": period}
    params.update({f"ou{i}": oid for i, oid in enumerate(ou_ids)})
    rows = db.execute(sql, params).fetchall()
    return {r.org_unit: round(float(r.cov), 1) for r in rows if r.cov is not None}


def _build_data_context(db: Session) -> str:
    """Build structured data context: national trends + sub-national breakdown."""
    lines = []

    # ── 1. National historical coverage ──────────────────────────────────────
    lines.append("## A. National Coverage — EFY2011–2017 (Source: DHIS2 Immunisation Services dataset)")
    lines.append("HSDIP target for all antigens: 90%")
    lines.append("")
    lines.append("| KPI | EFY2011 | EFY2012 | EFY2013 | EFY2014 | EFY2015 | EFY2016 | EFY2017 | Target | Gap (EFY2017 vs Target) |")
    lines.append("|-----|---------|---------|---------|---------|---------|---------|---------|--------|------------------------|")

    national_latest: dict[str, float] = {}
    for kpi_name, num_id, den_id in KPIS:
        sql = text("""
            SELECT period,
                   SUM(CASE WHEN data_element=:num THEN value::numeric ELSE 0 END) /
                   NULLIF(SUM(CASE WHEN data_element=:den THEN value::numeric ELSE 0 END),0)*100 AS cov
            FROM planning_data_ingest
            WHERE data_element IN (:num,:den) AND source='dhis2-nextgen-integration'
            GROUP BY period ORDER BY period
        """)
        rows = {r.period: round(float(r.cov), 1) for r in db.execute(sql, {"num": num_id, "den": den_id}).fetchall() if r.cov}
        vals = [f"{rows.get(p, '??')}%" for p in PERIODS]
        latest = rows.get("2017July")
        national_latest[kpi_name] = latest or 0.0
        gap = f"{round(latest - TARGET, 1):+.1f}pp" if latest is not None else "??"
        lines.append(f"| {kpi_name} | " + " | ".join(vals) + f" | {TARGET}% | {gap} |")

    # ── 2. Regional breakdown (L2) — EFY2017 ────────────────────────────────
    lines.append("\n## B. Regional Coverage — EFY2017 (Source: DHIS2, org unit level 2)")
    lines.append("Only regions present in the dataset are shown.")
    lines.append("")

    # Build per-region coverage for key KPIs (Penta-1, Penta-3, MCV1, Full Imm)
    key_kpis = [k for k in KPIS if k[0] in ("Penta-1 Coverage", "Penta-3 Coverage", "MCV1 Coverage", "Full Immunization Coverage")]
    region_ids = [oid for oid, m in OU_LOOKUP.items() if m["level"] == 2]
    region_names = {oid: OU_LOOKUP[oid]["name"] for oid in region_ids}

    header = "| Region | " + " | ".join(k[0] for k in key_kpis) + " |"
    sep    = "|--------|" + "---------|" * len(key_kpis)
    lines.append(header)
    lines.append(sep)

    region_data: dict[str, dict[str, float]] = {}
    for kpi_name, num_id, den_id in key_kpis:
        cov = _coverage_by_orgunits(db, num_id, den_id, 2, "2017July")
        for oid, val in cov.items():
            region_data.setdefault(oid, {})[kpi_name] = val

    for oid in region_ids:
        if oid not in region_data:
            continue
        row_vals = [f"{region_data[oid].get(k[0], '??')}%" for k in key_kpis]
        lines.append(f"| {region_names[oid]} | " + " | ".join(row_vals) + " |")

    # ── 3. Equity analysis — best vs worst region per KPI ───────────────────
    lines.append("\n## C. Equity Gap Analysis — EFY2017 (Source: DHIS2)")
    lines.append("Best vs. worst performing region for each key indicator:")
    lines.append("")
    for kpi_name, num_id, den_id in key_kpis:
        cov = _coverage_by_orgunits(db, num_id, den_id, 2, "2017July")
        if len(cov) < 2:
            continue
        best_id  = max(cov, key=cov.__getitem__)
        worst_id = min(cov, key=cov.__getitem__)
        gap = round(cov[best_id] - cov[worst_id], 1)
        lines.append(f"- **{kpi_name}**: Best = {OU_LOOKUP[best_id]['name']} ({cov[best_id]}%), "
                     f"Worst = {OU_LOOKUP[worst_id]['name']} ({cov[worst_id]}%), equity gap = {gap}pp")

    # ── 4. Zone-level breakdown (L3) ─────────────────────────────────────────
    lines.append("\n## D. Zone-Level Coverage — EFY2017 (Source: DHIS2, org unit level 3)")
    zone_ids = [oid for oid, m in OU_LOOKUP.items() if m["level"] == 3]
    if zone_ids:
        lines.append("| Zone | " + " | ".join(k[0] for k in key_kpis) + " |")
        lines.append("|------|" + "---------|" * len(key_kpis))
        zone_data: dict[str, dict] = {}
        for kpi_name, num_id, den_id in key_kpis:
            cov = _coverage_by_orgunits(db, num_id, den_id, 3, "2017July")
            for oid, val in cov.items():
                zone_data.setdefault(oid, {})[kpi_name] = val
        for oid in zone_ids:
            if oid not in zone_data:
                continue
            row_vals = [f"{zone_data[oid].get(k[0], '??')}%" for k in key_kpis]
            lines.append(f"| {OU_LOOKUP[oid]['name']} | " + " | ".join(row_vals) + " |")

    # ── 5. Woreda outliers (L4) ───────────────────────────────────────────────
    lines.append("\n## E. Woreda-Level Coverage — EFY2017 (Source: DHIS2, org unit level 4)")
    woreda_ids = [oid for oid, m in OU_LOOKUP.items() if m["level"] == 4]
    if woreda_ids:
        lines.append("Full immunization coverage by woreda (lowest performers flagged):")
        cov = _coverage_by_orgunits(db, "M8OvIia8OG5", "ZqLnqha9z4M", 4, "2017July")
        sorted_woredas = sorted(cov.items(), key=lambda x: x[1])
        for oid, val in sorted_woredas:
            flag = " ⚠ BELOW TARGET" if val < TARGET else ""
            lines.append(f"- {OU_LOOKUP.get(oid, {}).get('name', oid)}: {val}%{flag}")

    # ── 6. ARIMA Forecasts ────────────────────────────────────────────────────
    fc_rows = db.execute(text("""
        SELECT kpi, period, value, lower_ci, upper_ci, is_forecast
        FROM forecast_results ORDER BY kpi, period
    """)).fetchall()

    if fc_rows:
        lines.append("\n## F. ARIMA Forecasts — EFY2018–2020 (Source: DHIS2historical + ARIMA model)")
        lines.append("| KPI | EFY2018 | EFY2019 | EFY2020 |")
        lines.append("|-----|---------|---------|---------|")
        kpi_fc: dict[str, list] = {}
        for r in fc_rows:
            if r.is_forecast:
                kpi_fc.setdefault(r.kpi, []).append(r)
        for kpi_name, _, _ in KPIS:
            fc = kpi_fc.get(kpi_name, [])
            cells = [f"{float(r.value):.1f}% [95% CI: {float(r.lower_ci):.1f}–{float(r.upper_ci):.1f}]" for r in fc]
            if cells:
                lines.append(f"| {kpi_name} | " + " | ".join(cells) + " |")

    # ── 7. Year-over-year drops — biggest declines per KPI ───────────────────
    lines.append("\n## G. Year-Over-Year Drops — Biggest Declines (Source: DHIS2, all org units)")
    lines.append("Any drop ≥ 5pp flagged. Listed by magnitude. Use these to identify bottleneck periods.")
    lines.append("")

    drop_lines = []
    for kpi_name, num_id, den_id in KPIS:
        sql_all = text("""
            SELECT org_unit, period,
                   SUM(CASE WHEN data_element=:num THEN value::numeric ELSE 0 END) /
                   NULLIF(SUM(CASE WHEN data_element=:den THEN value::numeric ELSE 0 END),0)*100 AS cov
            FROM planning_data_ingest
            WHERE data_element IN (:num,:den) AND source='dhis2-nextgen-integration'
            GROUP BY org_unit, period ORDER BY org_unit, period
        """)
        rows_all = db.execute(sql_all, {"num": num_id, "den": den_id}).fetchall()
        by_ou: dict[str, dict[str, float]] = {}
        for r in rows_all:
            if r.cov is not None:
                by_ou.setdefault(r.org_unit, {})[r.period] = round(float(r.cov), 1)
        for ou_id, h in by_ou.items():
            ou_name = OU_LOOKUP.get(ou_id, {}).get("name", ou_id)
            vals = [(p, h[p]) for p in PERIODS if p in h]
            for i in range(len(vals) - 1):
                p1, v1 = vals[i]
                p2, v2 = vals[i + 1]
                drop = v1 - v2
                if drop >= 5.0:
                    label1 = PERIOD_LABEL.get(p1, p1)
                    label2 = PERIOD_LABEL.get(p2, p2)
                    drop_lines.append((drop, f"- **{kpi_name}** | {ou_name}: {v1}% ({label1}) → {v2}% ({label2}) = **−{drop:.1f}pp drop**"))

    drop_lines.sort(key=lambda x: -x[0])
    if drop_lines:
        for _, line in drop_lines[:20]:  # top 20 drops
            lines.append(line)
    else:
        lines.append("No drops ≥ 5pp detected across all KPIs and org units.")

    # ── 8. Inter-region comparison — outliers vs similar units ───────────────
    lines.append("\n## H. Inter-Region Comparison — Performance vs Similar Regions (Source: DHIS2 EFY2017)")
    lines.append("Regions grouped by performance quartile for Penta-3 and Full Immunization Coverage.")
    lines.append("Outliers within a quartile group are flagged.")
    lines.append("")

    for kpi_name, num_id, den_id in [k for k in KPIS if k[0] in ("Penta-3 Coverage", "Full Immunization Coverage")]:
        region_cov = _coverage_by_orgunits(db, num_id, den_id, 2, "2017July")
        if len(region_cov) < 3:
            continue
        vals_list = sorted(region_cov.values())
        n = len(vals_list)
        q1 = vals_list[n // 4]
        q2 = vals_list[n // 2]
        q3 = vals_list[3 * n // 4]

        def _quartile_label(v: float) -> str:
            if v <= q1: return "Q1 (lowest)"
            if v <= q2: return "Q2"
            if v <= q3: return "Q3"
            return "Q4 (highest)"

        lines.append(f"### {kpi_name}")
        lines.append(f"Quartile bounds: Q1≤{q1:.1f}%, Q2≤{q2:.1f}%, Q3≤{q3:.1f}%")

        # Within each quartile, flag the outlier (furthest from quartile median)
        quartiles: dict[str, list] = {"Q1 (lowest)": [], "Q2": [], "Q3": [], "Q4 (highest)": []}
        for oid, v in region_cov.items():
            quartiles[_quartile_label(v)].append((OU_LOOKUP.get(oid, {}).get("name", oid), v))

        for q_label, members in quartiles.items():
            if not members:
                continue
            members.sort(key=lambda x: x[1])
            q_vals = [m[1] for m in members]
            q_median = sorted(q_vals)[len(q_vals) // 2]
            lines.append(f"**{q_label}** (group median: {q_median:.1f}%):")
            for name, v in members:
                deviation = v - q_median
                flag = f" ← outlier (+{deviation:.1f}pp above group)" if deviation > 5 else \
                       f" ← outlier ({deviation:.1f}pp below group)" if deviation < -5 else ""
                lines.append(f"  - {name}: {v}%{flag}")
        lines.append("")

    return "\n".join(lines)


def _call_gemini(prompt: str, history: list[dict] | None = None) -> str:
    """Call Gemini using the google-genai SDK and return the text response."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)

    if history:
        # Build contents list: system context + history + new message
        contents = []
        for item in history:
            role = item["role"]  # "user" or "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=item["parts"][0])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=contents)
    else:
        response = client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)

    return response.text


GENERATE_PROMPT = """## Role and task

You are drafting narrative sections of Ethiopia's Health Sector Development and Investment Plan (HSDIP) Situational Analysis for the immunisation programme. **You do not calculate any figures.** Every value is supplied to you in the structured data below. Your job is to synthesize supplied figures into prose matching the structure and rigor of prior HSDIP situational analyses — not to derive numbers yourself.

This constraint is architectural: the deterministic layer has already computed all percentages, gaps, and disparities. An LLM computing its own percentages is the failure mode this system is built to avoid.

---

## Writing rules (house style — follow exactly)

For every indicator addressed:
1. **State the current value** with year and named source (e.g. "DHIS2 EFY2017", "ARIMA model")
2. **State the HSDIP target** (90% for all antigens in this dataset)
3. **Say explicitly** whether the target was met, missed, or exceeded, and by how much
4. **Break out subnational/demographic disparities** with specific numbers from the data — e.g. "ranging from X% in [best region] to Y% in [worst region]"
5. **Name plausible drivers** — multi-causal and specific, not vague ("various factors")
6. **Close with forward-looking priority actions** for the next plan period
7. **Every substantive claim carries a specific %, rate, or count** with year and source. No "significant improvement" floating without a number.
8. If a data point is missing from the supplied data, flag it as `[DATA GAP — source required]` rather than inventing a number.

---

## Document structure to produce

Generate the following sections. Use the structured data below as your sole source of numbers.

### 1. Executive Summary (4–5 sentences)
Key headline findings: national performance vs target, worst equity gap, forecast outlook.

### 2. KPI Summary Table
Columns: Indicator | Baseline (EFY2011) | Target | Achievement (EFY2017) | Gap | Remark

### 3. Service Delivery Performance
Sub-sections per antigen group (primary series: Penta/OPV/PCV; supplementary: MCV/IPV/Rota; maternal: PAB; composite: Full Immunization). For each: national trend EFY2011–2017, whether target met, regional range.

### 4. Equity and Subnational Disparities
- Geographic equity: regional coverage range for key indicators with specific region names and values
- Zone-level performance
- Woreda-level outliers — name specific woredas below target

### 5. Bottleneck Analysis
**Use section G (Year-Over-Year Drops) as your primary input.** The drops have been pre-computed for you.
- List the top bottleneck drops from section G, ranked by magnitude
- For each: state the KPI, org unit, period of the drop, magnitude in pp
- Interpret each programmatically with named plausible drivers (supply chain disruption, conflict/displacement, data quality issue, seasonal demand drop, etc.)
- Note: specific WHO bottleneck framework (Tanahashi cascade or equivalent) to be confirmed with TWG — [OPEN ITEM]

### 5b. Inter-Region Performance Comparison
**Use section H (Inter-Region Comparison) as your input.** Do not recalculate.
- For each KPI covered in section H, describe the quartile groupings in prose
- Explicitly name the outlier regions within each quartile group and explain their deviation from peers
- Frame this as: "While [region A] and [region B] — both in the same performance tier — achieved X% and Y%, [region C] in the same tier achieved only Z%, suggesting [named programmatic explanation]"
- This is distinct from the national equity gap (§4) — this is about peer comparison within similar-performing groups

### 6. SWOT Analysis
Render as a **2×2 table** (not prose). Organize content within each quadrant under WHO Health System Building Blocks as sub-headers: Service Delivery | Health Workforce | Health Information Systems | Medical Commodities | Health Financing | Leadership & Governance. Cite specific metric values for each bullet.

Rules:
- Strength: coverage ≥ 80% in EFY2017
- Weakness: coverage < 60% in EFY2017
- Opportunity: KPIs where ARIMA forecast EFY2018 > EFY2017 (improving trend)
- Threat: KPIs with largest gap vs 90% target OR declining forecast trend

### 7. PESTEL Analysis
Render as **narrative with bullets** (not a table). Six categories. Each split into "Positive:" and "Negative:" sub-sections with bullets.
- Social: derive from coverage data — equity, access, community demand signals
- Political, Economic, Technological, Environmental, Legal: mark as `[Requires manual input — data not available]` but include at least one data-grounded observation where possible

### 8. Priority Actions (5–7 bullets)
Derived from the analysis above. Specific, actionable, time-bound where possible.

---

## Structured input data (do not recalculate — use these values directly)

{data_context}
"""

CHAT_SYSTEM = """You are an immunisation programme analyst helping a planner review and refine a Situational Analysis report for Ethiopia's Ministry of Health (HSDIP).

## Your role
- Answer questions about findings, data, or methodology
- Explain reasoning for specific findings
- If the planner asks to AMEND a data point or finding (e.g. "change Penta-3 EFY2017 to 85%"), treat it as a REPORT-LEVEL OVERRIDE. Respond with the specific corrected text, clearly marked with **[AMENDMENT]** at the start, followed by the revised passage. The source database is NOT changed.
- If asked to regenerate a full section based on amended figures, do so
- Always be clear about what the data shows vs. what is an assumption
- Never invent figures not in the supplied data. Flag missing data as [DATA GAP]
- When sources conflict, present both and explain the discrepancy

## Current Draft Report
{report}

## Underlying Data
{data_context}
"""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/generate")
def generate(db: Session = Depends(get_db)):
    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    data_context = _build_data_context(db)
    prompt = GENERATE_PROMPT.format(data_context=data_context)

    try:
        report_md = _call_gemini(prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    result = db.execute(text("""
        INSERT INTO sa_reports (report_md) VALUES (:md) RETURNING id, created_at
    """), {"md": report_md}).fetchone()
    db.commit()

    return {
        "id": result.id,
        "state": "draft",
        "report_md": report_md,
        "created_at": result.created_at.isoformat(),
    }


@router.get("/latest")
def get_latest(db: Session = Depends(get_db)):
    report = db.execute(text("""
        SELECT id, state, report_md, overrides, endorsed_at, created_at
        FROM sa_reports ORDER BY id DESC LIMIT 1
    """)).fetchone()

    if not report:
        return {"report": None}

    chat = db.execute(text("""
        SELECT role, content, created_at FROM sa_chat
        WHERE report_id=:rid ORDER BY id
    """), {"rid": report.id}).fetchall()

    return {
        "report": {
            "id": report.id,
            "state": report.state,
            "report_md": report.report_md,
            "overrides": report.overrides,
            "endorsed_at": report.endorsed_at.isoformat() if report.endorsed_at else None,
            "created_at": report.created_at.isoformat(),
        },
        "chat": [{"role": r.role, "content": r.content} for r in chat],
    }


class ChatRequest(BaseModel):
    message: str
    report_id: int


@router.post("/chat")
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    report = db.execute(text(
        "SELECT id, report_md FROM sa_reports WHERE id=:rid"
    ), {"rid": req.report_id}).fetchone()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Load existing chat history
    history_rows = db.execute(text(
        "SELECT role, content FROM sa_chat WHERE report_id=:rid ORDER BY id"
    ), {"rid": req.report_id}).fetchall()

    data_context = _build_data_context(db)
    system_msg = CHAT_SYSTEM.format(report=report.report_md, data_context=data_context)

    # Build Gemini history format
    gemini_history = [{"role": "user", "parts": [system_msg]},
                      {"role": "model", "parts": ["Understood. I'm ready to discuss the Situational Analysis report."]}]
    for row in history_rows:
        gemini_role = "model" if row.role == "assistant" else "user"
        gemini_history.append({"role": gemini_role, "parts": [row.content]})

    # Save user message
    db.execute(text("""
        INSERT INTO sa_chat (report_id, role, content) VALUES (:rid, 'user', :msg)
    """), {"rid": req.report_id, "msg": req.message})

    try:
        reply = _call_gemini(req.message, history=gemini_history)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    # Save assistant reply
    db.execute(text("""
        INSERT INTO sa_chat (report_id, role, content) VALUES (:rid, 'assistant', :msg)
    """), {"rid": req.report_id, "msg": reply})
    db.commit()

    return {"reply": reply}


@router.post("/endorse")
def endorse(db: Session = Depends(get_db)):
    report = db.execute(text(
        "SELECT id FROM sa_reports ORDER BY id DESC LIMIT 1"
    )).fetchone()
    if not report:
        raise HTTPException(status_code=404, detail="No report to endorse")

    db.execute(text("""
        UPDATE sa_reports SET state='endorsed', endorsed_at=NOW() WHERE id=:rid
    """), {"rid": report.id})
    db.commit()
    return {"status": "endorsed", "report_id": report.id}
