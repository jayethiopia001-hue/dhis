"""
Push the synthetic immunisation metadata + history into your local DHIS2 instance.

Before running:
1. Open dhis2_metadata_IMPORT.json and replace every occurrence of
   REPLACE_WITH_YOUR_ROOT_OU_ID and REPLACE_WITH_YOUR_DEFAULT_COC_ID with real IDs from
   your instance (see the two lookup commands below).
2. Do the same replacement in dhis2_dataValueSet_IMPORT.json for REPLACE_WITH_YOUR_DEFAULT_COC_ID.
3. Fill in DHIS2_BASE_URL / DHIS2_USERNAME / DHIS2_PASSWORD below (or set as env vars).
4. Run: python3 push_to_dhis2.py

Not run from Claude's side -- this environment can't reach your local instance (limited
to a small allowlist, and "local" means local to your machine, not this sandbox). Run this
from wherever you already have network access to the instance.

--- Finding the two IDs you need ---
Root org unit (parent for the 14 new regions):
    curl -u USER:PASS "http://localhost:8080/api/organisationUnits.json?filter=level:eq:1&fields=id,name"

Default category option combo:
    curl -u USER:PASS "http://localhost:8080/api/categoryOptionCombos.json?filter=name:eq:default&fields=id"
    (commonly HllvX50cXC0 on a fresh install, but confirm -- don't assume)
"""
import json
import os
import requests

DHIS2_BASE_URL = os.environ.get("DHIS2_BASE_URL", "http://localhost:8080")
DHIS2_USERNAME = os.environ.get("DHIS2_USERNAME", "")
DHIS2_PASSWORD = os.environ.get("DHIS2_PASSWORD", "")

PLACEHOLDER_MARKERS = ("REPLACE_WITH_YOUR_ROOT_OU_ID", "REPLACE_WITH_YOUR_DEFAULT_COC_ID")

def check_no_placeholders(payload_str, filename):
    for marker in PLACEHOLDER_MARKERS:
        if marker in payload_str:
            raise SystemExit(f"{filename} still contains '{marker}' -- replace it before importing.")

def main():
    if not DHIS2_USERNAME:
        raise SystemExit("Set DHIS2_USERNAME / DHIS2_PASSWORD before running.")

    with open("dhis2_metadata_IMPORT.json") as f:
        metadata_str = f.read()
    check_no_placeholders(metadata_str, "dhis2_metadata_IMPORT.json")
    metadata = json.loads(metadata_str)

    print("Importing metadata (org units + data elements)...")
    resp = requests.post(f"{DHIS2_BASE_URL}/api/metadata", json=metadata,
                          auth=(DHIS2_USERNAME, DHIS2_PASSWORD))
    print(resp.status_code)
    print(json.dumps(resp.json(), indent=2)[:2000])
    if resp.status_code >= 300:
        raise SystemExit("Metadata import failed -- fix errors above before importing data values.")

    with open("dhis2_dataValueSet_IMPORT.json") as f:
        dv_str = f.read()
    check_no_placeholders(dv_str, "dhis2_dataValueSet_IMPORT.json")
    dv_payload = json.loads(dv_str)

    print("\nImporting data values (dry run first)...")
    resp = requests.post(f"{DHIS2_BASE_URL}/api/dataValueSets", json=dv_payload,
                          auth=(DHIS2_USERNAME, DHIS2_PASSWORD), params={"dryRun": "true"})
    print(resp.status_code)
    print(json.dumps(resp.json(), indent=2)[:2000])
    print("\nIf the dry run summary looks right, re-run with dryRun removed to commit.")

if __name__ == "__main__":
    main()
