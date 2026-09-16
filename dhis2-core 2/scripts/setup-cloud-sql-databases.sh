#!/usr/bin/env bash
set -euo pipefail

# Creates the two logical databases used by this deployment on one Cloud SQL
# PostgreSQL instance. DHIS2 creates its own schema on first startup; the AI
# service creates its tables through app/db/init_db.py.

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
: "${CLOUD_SQL_INSTANCE:?Set CLOUD_SQL_INSTANCE to the instance name or full connection name}"

DHIS2_DATABASE="${DHIS2_DATABASE:-dhis2}"
AI_DATABASE="${AI_DATABASE:-planning_ai}"
DHIS2_USER="${DHIS2_USER:-dhis2_app}"
AI_USER="${AI_USER:-planning_ai_app}"

# gcloud sql admin commands require only the instance name, while application
# connectors use the full project:region:instance connection name.
if [[ "${CLOUD_SQL_INSTANCE}" == *:* ]]; then
  CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE##*:}"
fi

read -r -s -p "Password for ${DHIS2_USER}: " DHIS2_PASSWORD
printf '\n'
read -r -s -p "Password for ${AI_USER}: " AI_PASSWORD
printf '\n'

gcloud config set project "${GCP_PROJECT_ID}" >/dev/null

gcloud sql databases create "${DHIS2_DATABASE}" \
  --instance="${CLOUD_SQL_INSTANCE}" \
  || echo "Database ${DHIS2_DATABASE} already exists; continuing."

gcloud sql databases create "${AI_DATABASE}" \
  --instance="${CLOUD_SQL_INSTANCE}" \
  || echo "Database ${AI_DATABASE} already exists; continuing."

if gcloud sql users list --instance="${CLOUD_SQL_INSTANCE}" \
  --format="value(name)" | grep -qx "${DHIS2_USER}"; then
  gcloud sql users set-password "${DHIS2_USER}" \
    --instance="${CLOUD_SQL_INSTANCE}" \
    --password="${DHIS2_PASSWORD}"
else
  gcloud sql users create "${DHIS2_USER}" \
    --instance="${CLOUD_SQL_INSTANCE}" \
    --password="${DHIS2_PASSWORD}"
fi

if gcloud sql users list --instance="${CLOUD_SQL_INSTANCE}" \
  --format="value(name)" | grep -qx "${AI_USER}"; then
  gcloud sql users set-password "${AI_USER}" \
    --instance="${CLOUD_SQL_INSTANCE}" \
    --password="${AI_PASSWORD}"
else
  gcloud sql users create "${AI_USER}" \
    --instance="${CLOUD_SQL_INSTANCE}" \
    --password="${AI_PASSWORD}"
fi

unset DHIS2_PASSWORD AI_PASSWORD

cat <<EOF

Cloud SQL setup complete.

DHIS2:
  database: ${DHIS2_DATABASE}
  user:     ${DHIS2_USER}

AI:
  database: ${AI_DATABASE}
  user:     ${AI_USER}

The DHIS2 schema is created by DHIS2 on first startup.
The AI tables are created by app/db/init_db.py during FastAPI startup.
EOF
