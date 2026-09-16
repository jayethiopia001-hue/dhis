# NextGen Planning — ai-repo

FastAPI service for the Ethiopia MoH NextGen Planning tool (INV-089469).

**Full scope and build-order rationale:** see [`docs/SPEC.md`](docs/SPEC.md) — this is the
spec everything here is built against, and it's kept in the repo so any
future agent/dev picking this up has the same context.

## Where this sits right now (Step 1)

Only the **ingest** path is implemented: a landing endpoint that accepts
DHIS2-shaped data value payloads and writes them into Postgres. Everything
else (deterministic calc layer, ML target-setting, GenAI evidence
synthesis) is stubbed out as empty routers with `TODO`s pointing at the
relevant SPEC.md section, so the structure is already there when you get to
them — see `app/routers/targets.py`, `aggregation.py`, `evidence.py`.

Per SPEC.md §2, the deterministic layer (baseline fetch/derivation, target
formula linkage, aggregation roll-up) is the next real milestone, not the
ML or GenAI layers — build order matters here.

## Repo layout

```
ai-repo/
├── docs/SPEC.md              full build spec — read this first
├── app/
│   ├── main.py                FastAPI app, CORS, router registration
│   ├── config.py              settings (reads .env)
│   ├── db/
│   │   ├── database.py        SQLAlchemy engine/session
│   │   ├── models.py          PlanningDataIngest landing table
│   │   └── init_db.py         run once to create tables
│   ├── routers/
│   │   ├── ingest.py          POST /ingest/push  — implemented (step 1)
│   │   ├── targets.py         stub — ML target setting (SPEC §5.2)
│   │   ├── aggregation.py     stub — deterministic roll-up (SPEC §5.1)
│   │   └── evidence.py        stub — GenAI synthesis (SPEC §5.3)
│   ├── schemas/ingest.py       Pydantic models for the ingest payload
│   └── services/postgres_writer.py
├── dhis2-app/                 the "fake" DHIS2 planning app (step 1 button)
│   ├── manifest.webapp
│   └── index.html
├── .env.example                template — copy to .env, or just edit .env directly
├── .env                        gitignored — drop real credentials here
└── requirements.txt
```

## Setup

1. **Install dependencies**

   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Fill in `.env`** with your GCP Postgres credentials (already created
   from `.env.example` — just edit the values):

   ```
   POSTGRES_HOST=
   POSTGRES_PORT=5432
   POSTGRES_DB=
   POSTGRES_USER=
   POSTGRES_PASSWORD=
   ```

   **Note on reaching GCP Postgres from your machine:**
   - If it's **Cloud SQL**, the cleanest path is the [Cloud SQL Auth
     Proxy](https://cloud.google.com/sql/docs/postgres/connect-auth-proxy) —
     run it locally, point `POSTGRES_HOST` at `127.0.0.1` and the proxy's
     local port, rather than exposing a public IP.
   - If it's **Postgres on a Compute Engine VM**, make sure the VM's
     firewall allows your current IP on `POSTGRES_PORT` (5432 by default).

3. **Create the table**

   ```
   python -m app.db.init_db
   ```

4. **Run the API**

   ```
   uvicorn app.main:app --reload --port 8000
   ```

   Check `http://localhost:8000/health` and `http://localhost:8000/docs`
   (FastAPI's auto-generated Swagger UI — useful for testing `/ingest/push`
   directly before wiring up the DHIS2 app).

## Installing the fake DHIS2 app

1. Zip the contents of `dhis2-app/` (the manifest and index.html need to be
   at the root of the zip, not nested in a folder).
2. In DHIS2: **App Management → Install App → Upload local zip**.
3. Launch the app from the DHIS2 app menu, set the FastAPI base URL field
   (defaults to `http://localhost:8000`), click the button.
4. Check the FastAPI logs / Postgres table (`planning_data_ingest`) to
   confirm rows landed.

Before it'll pull real data, swap the placeholder UIDs in
`dhis2-app/index.html` (`REPLACE_WITH_REAL_DATA_ELEMENT_UID` /
`REPLACE_WITH_REAL_DATASET_UID`) for real ones from your demo dataset — until
then it falls back to a hardcoded sample payload, so the pipeline is
testable end-to-end either way.

## CORS note

`app/main.py` currently allows all origins (`allow_origins=["*"]`) — fine
for this local prototype stage, but tighten to the actual DHIS2 origin
before this goes anywhere near a shared or production environment.
