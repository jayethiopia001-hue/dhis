from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db.init_db import init_db
from app.routers import ingest, targets, aggregation, evidence, diagnostics, forecasting, situational_analysis

from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    init_db()
    yield


app = FastAPI(
    title="NextGen Planning API",
    description="API layer for Ethiopia MoH NextGen Planning tool. See docs/SPEC.md for full scope.",
    version="0.1.0",
    lifespan=_lifespan,
)

# Prototype CORS: the DHIS2 fake app runs on a different origin/port
# (localhost:8080 vs this service). Tighten allow_origins to the real DHIS2
# origin(s) before this goes anywhere near production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(targets.router)
app.include_router(aggregation.router)
app.include_router(evidence.router)
app.include_router(diagnostics.router)
app.include_router(forecasting.router)
app.include_router(situational_analysis.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Ensure CORS headers are present even on unhandled 500 errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers={"Access-Control-Allow-Origin": "*"},
    )
