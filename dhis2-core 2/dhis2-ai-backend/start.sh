#!/bin/bash
# Start the FastAPI backend with correct SSL certs for macOS
set -a
source "$(dirname "$0")/.env"
set +a
cd "$(dirname "$0")"
python3 -m uvicorn app.main:app --port 8000
