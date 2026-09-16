"""
Evidence Synthesis router.

Workflow:
  1. Planner uploads a file (PDF / Word / Excel).
     → Full text is extracted and stored. No metric parsing, no KPI matching.
  2. Planner writes their objective and optionally selects internal DHIS2 KPIs
     to include as structured context.
  3. POST /evidence/synthesize sends the full document text + DHIS2 KPI data
     + objective to Gemini. The LLM reasons freely over all of it.
  4. Synthesis stored for audit trail. Chat endpoint for follow-up discussion.

Scanned/image-only PDFs (no text layer) are flagged in the upload response.
"""
import io
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.database import get_db
from app.config import settings

router = APIRouter(prefix="/evidence", tags=["evidence"])

# ── KPI registry (for fetching internal DHIS2 data) ──────────────────────────
KPI_TAXONOMY = [
    {"name": "Penta-1 Coverage",          "num_id": "gUTDQGm7wQ4", "den_id": "eBLFFQCHD6R"},
    {"name": "Penta-3 Coverage",          "num_id": "UbbrqQ7B697", "den_id": "z2kPnR9zAwf"},
    {"name": "MCV1 Coverage",             "num_id": "xegDuD9La4v", "den_id": "xbIBbuMFd9W"},
    {"name": "MCV2 Coverage",             "num_id": "PfO4WUDlWuX", "den_id": "gfq4lUY0P6z"},
    {"name": "OPV3 Coverage",             "num_id": "WG81RqwkVHW", "den_id": "q53mBzytPAn"},
    {"name": "PCV-3 Coverage",            "num_id": "Uyy6Skhj4Yj", "den_id": "I4JRfdQAe6N"},
    {"name": "IPV Coverage",              "num_id": "vNMWVmoyXmE", "den_id": "tW3jSOi27n7"},
    {"name": "Full Immunization Coverage","num_id": "M8OvIia8OG5", "den_id": "ZqLnqha9z4M"},
    {"name": "PAB Coverage",              "num_id": "d4vCK4A4FGG", "den_id": "GPqX9cbF8Ph"},
    {"name": "Rotavirus-2 Coverage",      "num_id": "B9m2bnFVuHZ", "den_id": "cGogkdmtsVr"},
]
KPI_BY_NAME = {k["name"]: k for k in KPI_TAXONOMY}


# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_tables(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS evidence_uploads (
            id          SERIAL PRIMARY KEY,
            filename    TEXT NOT NULL,
            filetype    TEXT,
            full_text   TEXT,
            created_at  TIMESTAMP DEFAULT NOW()
        )
    """))
    # Migrate: add full_text column if the table already existed without it
    db.execute(text("""
        ALTER TABLE evidence_uploads ADD COLUMN IF NOT EXISTS full_text TEXT
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS evidence_syntheses (
            id            SERIAL PRIMARY KEY,
            upload_id     INTEGER,
            objective     TEXT,
            selected_kpis TEXT,
            synthesis_md  TEXT,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """))
    # Migrate: add objective/selected_kpis columns if table already existed
    db.execute(text("ALTER TABLE evidence_syntheses ADD COLUMN IF NOT EXISTS objective TEXT"))
    db.execute(text("ALTER TABLE evidence_syntheses ADD COLUMN IF NOT EXISTS selected_kpis TEXT"))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS evidence_chat (
            id            SERIAL PRIMARY KEY,
            synthesis_id  INTEGER NOT NULL,
            role          TEXT NOT NULL,
            content       TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()


# ── Text extraction (full text only — no metric parsing) ─────────────────────

def _extract_text_excel(content: bytes) -> str:
    import openpyxl
    parts = []
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    for sheet_name in wb.sheetnames:
        parts.append(f"[Sheet: {sheet_name}]")
        for row in wb[sheet_name].iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            line = " | ".join(c for c in cells if c)
            if line:
                parts.append(line)
    return "\n".join(parts)


def _extract_text_word(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text.strip())
    for i, table in enumerate(doc.tables):
        parts.append(f"[Table {i+1}]")
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(c for c in cells if c))
    return "\n".join(parts)


def _extract_text_pdf(content: bytes) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(f"[Page {page_num}]\n{page_text}")
            for tbl in (page.extract_tables() or []):
                parts.append(f"[Table on page {page_num}]")
                for row in tbl:
                    cells = [str(c).strip() if c else "" for c in row]
                    line = " | ".join(c for c in cells if c)
                    if line:
                        parts.append(line)
    return "\n\n".join(parts)


# ── Internal DHIS2 KPI data ───────────────────────────────────────────────────

def _fetch_kpi_history(db: Session, kpi_name: str) -> str:
    """Return EFY2011–2017 national coverage series for one KPI as a text block."""
    kpi = KPI_BY_NAME.get(kpi_name)
    if not kpi:
        return f"{kpi_name}: not found"
    PERIODS = ["2011July","2012July","2013July","2014July","2015July","2016July","2017July"]
    rows = db.execute(text("""
        SELECT period,
               SUM(CASE WHEN data_element=:num THEN value::numeric ELSE 0 END) /
               NULLIF(SUM(CASE WHEN data_element=:den THEN value::numeric ELSE 0 END),0)*100 AS cov
        FROM planning_data_ingest
        WHERE data_element IN (:num,:den) AND source='dhis2-nextgen-integration'
        GROUP BY period ORDER BY period
    """), {"num": kpi["num_id"], "den": kpi["den_id"]}).fetchall()
    by_period = {r.period: round(float(r.cov), 1) for r in rows if r.cov}
    series = ", ".join(
        f"EFY{p[:4]}: {by_period[p]}%" for p in PERIODS if p in by_period
    )
    latest = by_period.get("2017July")
    gap = f" | Gap to 90% target: {round(latest - 90.0, 1):+.1f}pp" if latest else ""
    return f"{kpi_name}: {series}{gap}"


# ── Synthesis prompt ──────────────────────────────────────────────────────────

SYNTHESIS_PROMPT = """You are a public health analyst supporting Ethiopia's Ministry of Health HSDIP planning cycle.

## Your task
Reason over the uploaded document and the internal DHIS2 programme data provided below, and produce a synthesis that directly serves the planner's stated objective.

## Writing rules
1. Use both the document and the DHIS2 data in your reasoning — do not ignore either source.
2. Where the document and DHIS2 data agree, say so explicitly with figures.
3. Where they diverge, explain the divergence — do not pick a winner. Name plausible methodological reasons (survey vs. routine data, denominator differences, timing).
4. Every substantive claim carries its source: document title or "DHIS2 Immunisation Services dataset, EFY[year]".
5. Do not invent figures not present in either source. Flag gaps as [DATA GAP — source required].
6. Structure: lead with key findings relevant to the objective, then supporting evidence, then implications for planning.
7. Length: 4–8 paragraphs — substantive enough to use directly in the situational analysis.

## Planner's objective
{objective}

## Internal DHIS2 programme data (selected KPIs, EFY2011–2017 national coverage)
{kpi_data}

## Uploaded document
Filename: {filename}

{doc_text}

---
Produce the evidence synthesis now.
"""

EVIDENCE_CHAT_PROMPT = """You are an evidence analyst helping a planner interrogate and refine an Evidence Synthesis for Ethiopia's HSDIP.

## Planner's original objective
{objective}

## Current synthesis
{synthesis}

## Your role
- Answer questions about the synthesis findings, data, or methodology
- If asked to AMEND a finding, respond with the revised text clearly marked **[AMENDMENT]**
- Never invent figures not in the synthesis or underlying data — flag as [DATA GAP]
- If asked to re-focus the synthesis on a different objective, generate a new synthesis section
"""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/kpis")
def list_kpis():
    """Return available KPI names for the frontend selector."""
    return [k["name"] for k in KPI_TAXONOMY]


@router.post("/upload")
async def upload_evidence(file: UploadFile = File(...), db: Session = Depends(get_db)):
    _ensure_tables(db)

    content = await file.read()
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ("pdf", "docx", "xlsx", "xls"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Upload a PDF, Word (.docx), or Excel (.xlsx/.xls) file.",
        )

    try:
        if ext in ("xlsx", "xls"):
            full_text = _extract_text_excel(content)
        elif ext == "docx":
            full_text = _extract_text_word(content)
        else:
            full_text = _extract_text_pdf(content)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read file: {e}")

    if not full_text.strip():
        raise HTTPException(
            status_code=422,
            detail="No text could be extracted. The file may be image-only (scanned PDF with no text layer).",
        )

    row = db.execute(text("""
        INSERT INTO evidence_uploads (filename, filetype, full_text)
        VALUES (:fn, :ft, :txt) RETURNING id, created_at
    """), {"fn": filename, "ft": ext, "txt": full_text}).fetchone()
    db.commit()

    return {
        "upload_id": row.id,
        "filename": filename,
        "char_count": len(full_text),
        "preview": full_text[:400] + ("…" if len(full_text) > 400 else ""),
    }


class SynthesizeRequest(BaseModel):
    upload_id: Optional[int] = None   # None = internal-data-only synthesis
    objective: str
    selected_kpis: Optional[list[str]] = None


@router.post("/synthesize")
def synthesize(req: SynthesizeRequest, db: Session = Depends(get_db)):
    _ensure_tables(db)

    if not req.objective.strip():
        raise HTTPException(status_code=400, detail="Objective is required.")

    # Fetch document text if an upload was provided
    filename = "(no document uploaded)"
    doc_text = "(No external document — synthesis is based on internal DHIS2 data only.)"
    if req.upload_id:
        upload = db.execute(
            text("SELECT filename, full_text FROM evidence_uploads WHERE id=:uid"),
            {"uid": req.upload_id},
        ).fetchone()
        if not upload:
            raise HTTPException(status_code=404, detail="Upload not found.")
        filename = upload.filename
        doc_text = upload.full_text[:12000]  # cap to avoid token overflow
        if len(upload.full_text) > 12000:
            doc_text += "\n\n[Document truncated — first 12,000 characters shown]"

    # Fetch selected DHIS2 KPI history
    kpi_lines = []
    for kpi_name in (req.selected_kpis or []):
        kpi_lines.append(_fetch_kpi_history(db, kpi_name))

    kpi_data = "\n".join(kpi_lines) if kpi_lines else "(No internal KPIs selected)"

    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="Gemini API key not configured.")

    from google import genai as google_genai
    try:
        client = google_genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=SYNTHESIS_PROMPT.format(
                objective=req.objective,
                kpi_data=kpi_data,
                filename=filename,
                doc_text=doc_text,
            ),
        )
        synthesis_md = response.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    result = db.execute(text("""
        INSERT INTO evidence_syntheses (upload_id, objective, selected_kpis, synthesis_md)
        VALUES (:uid, :obj, :kpis, :md) RETURNING id, created_at
    """), {
        "uid": req.upload_id or 0,
        "obj": req.objective,
        "kpis": ",".join(req.selected_kpis or []),
        "md": synthesis_md,
    }).fetchone()
    db.commit()

    return {
        "synthesis_id": result.id,
        "synthesis_md": synthesis_md,
        "created_at": result.created_at.isoformat(),
    }


class EvidenceChatRequest(BaseModel):
    synthesis_id: int
    message: str


@router.post("/chat")
def evidence_chat(req: EvidenceChatRequest, db: Session = Depends(get_db)):
    _ensure_tables(db)

    synth = db.execute(
        text("SELECT synthesis_md, objective FROM evidence_syntheses WHERE id=:sid"),
        {"sid": req.synthesis_id},
    ).fetchone()
    if not synth:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    if not settings.gemini_api_key:
        raise HTTPException(status_code=500, detail="Gemini API key not configured.")

    from google import genai as google_genai
    try:
        client = google_genai.Client(api_key=settings.gemini_api_key)
        system = EVIDENCE_CHAT_PROMPT.format(
            objective=synth.objective or "(not specified)",
            synthesis=synth.synthesis_md[:6000],
        )
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=f"{system}\n\n---\n\nPlanner: {req.message}",
        )
        reply = response.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

    db.execute(text("""
        INSERT INTO evidence_chat (synthesis_id, role, content)
        VALUES (:sid, 'user', :msg)
    """), {"sid": req.synthesis_id, "msg": req.message})
    db.execute(text("""
        INSERT INTO evidence_chat (synthesis_id, role, content)
        VALUES (:sid, 'assistant', :reply)
    """), {"sid": req.synthesis_id, "reply": reply})
    db.commit()

    return {"reply": reply}

