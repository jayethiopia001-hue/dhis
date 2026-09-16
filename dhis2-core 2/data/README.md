# Rebuilding the Immunisation Synthetic Demo in DHIS2

What this is: a 4-level org unit hierarchy (national stand-in -> region -> zone -> woreda)
carrying 12 immunisation indicators (numerator + eligible-population each), covering
EFY2011-2017, all attached to the standard DHIS2 demo database (Sierra Leone).

- **14 regions** -- full Ethiopia region coverage, real anchor data throughout
- **2 of those 14 regions (Amhara, Oromia) also have zone and woreda detail** -- 2 zones
  each, 2 woredas per zone (12 new org units total). The other 12 regions stop at region
  level.
- **"National"** is not a real org unit here -- the regions sit directly under the demo
  database's existing root ("Sierra Leone"). Aggregation mechanics work at that root, but
  it's a stand-in, not a genuine Ethiopia national level.

## Are these files safe if the database gets wiped?

**Yes.** Every JSON file below is a plain file sitting on disk in this folder -- nothing
about them lives inside the DHIS2 database itself. Deleting or resetting the DHIS2
database (dropping the Postgres DB, recreating the Docker volume, etc.) does not touch
these files. As long as this folder survives, you can re-run the commands below against a
freshly reset instance and get back to exactly where you were.

## Files in this folder

**Base build -- 14 regions, region-level data only:**

| File | Contains | Import target |
| --- | --- | --- |
| `dhis2_metadata_IMPORT.json` | 14 region org units + 24 data elements | `/api/metadata` |
| `dhis2_metadata_dataset_ADDON.json` | 1 dataset, wiring the above together, period type `FinancialJuly` | `/api/metadata` |
| `dhis2_dataValueSet_IMPORT.json` | All 1,872 region-level data values (corrected version -- EFY2016 anchor values use the real reported baseline counts, not a recomputed approximation) | `/api/dataValueSets` |

**Woreda/zone extension -- Amhara and Oromia only:**

| File | Contains | Import target |
| --- | --- | --- |
| `dhis2_metadata_WOREDA_ZONE_ADDON.json` | 4 zone org units + 8 woreda org units, parented under Amhara/Oromia | `/api/metadata` |
| `dhis2_metadata_dataset_UPDATE_with_woreda.json` | Same dataset, updated to include all 26 org units (14 original + 12 new). Contains the *full* list, not just the additions -- see note below on why. | `/api/metadata` |
| `dhis2_dataValueSet_WOREDA_ZONE.json` | 2,016 data values at woreda and zone level, split from the real region figures by fixed proportional shares | `/api/dataValueSets` |

**Reference only, nothing to import:**

| File | Contains |
| --- | --- |
| `immunisation_synthetic_history.csv` | Region-level data, human-readable long format |
| `woreda_zone_split.csv` | Woreda/zone-level data, human-readable, includes each woreda's share and which zone/region it rolls into |
| `dhis2_CORRECTION_2016_baseline.json` | Historical artefact from fixing the original region-level import. Superseded -- `dhis2_dataValueSet_IMPORT.json` already has the fix baked in. |
| `push_to_dhis2.py` | Scripted version of the base three-step import |

## Rebuild from scratch, in order

Base build first, then the woreda/zone extension. Run from this folder:

```bash
curl -u admin:district -H "Content-Type: application/json" -d @dhis2_metadata_IMPORT.json "http://localhost:8080/api/metadata"

curl -u admin:district -H "Content-Type: application/json" -d @dhis2_metadata_dataset_ADDON.json "http://localhost:8080/api/metadata"

curl -u admin:district -H "Content-Type: application/json" -d @dhis2_dataValueSet_IMPORT.json "http://localhost:8080/api/dataValueSets"

curl -u admin:district -H "Content-Type: application/json" -d @dhis2_metadata_WOREDA_ZONE_ADDON.json "http://localhost:8080/api/metadata"

curl -u admin:district -H "Content-Type: application/json" -d @dhis2_metadata_dataset_UPDATE_with_woreda.json "http://localhost:8080/api/metadata"

curl -u admin:district -H "Content-Type: application/json" -d @dhis2_dataValueSet_WOREDA_ZONE.json "http://localhost:8080/api/dataValueSets"
```

Check each metadata response for `"status":"OK"` and non-zero `created`/`updated` counts
before moving to the next step. The dataset update step should show `"updated":1` (it's
modifying the existing dataset, not creating a new one).

## Verify it actually worked

Don't just trust the import summaries -- pull real values back and check them by hand.

Region level (the original real anchor):

```bash
curl -u admin:district "http://localhost:8080/api/dataValueSets.json?dataSet=C11WC1lXDMl&period=2016July&orgUnit=cIizHJIQgJl"
```

Amhara's Penta1 numerator (`gUTDQGm7wQ4`) should read exactly **373269** -- a real reported
figure from the source spreadsheet.

Woreda level (the split):

```bash
curl -u admin:district "http://localhost:8080/api/dataValueSets.json?dataSet=C11WC1lXDMl&period=2016July&orgUnit=<amhara_z1_woreda1_id>"
```

Amhara Z1 Woreda 1's Penta1 numerator should read **205298** -- that's 35% of the real
373269 region figure via its zone, not an independently invented number. Org unit IDs for
each woreda/zone are in `dhis2_metadata_WOREDA_ZONE_ADDON.json` or `woreda_zone_split.csv`.

## Key design decision: no reliance on DHIS2's own aggregation

Region-level org units already carry their own directly-entered real values (from the base
build). Rather than removing those and relying on DHIS2's analytics engine to sum
woreda -> zone -> region on the fly, **every level stores its own explicit, pre-computed
value** -- zone values are the direct sum of their two woredas, and region values are
untouched from the original real import. The woreda splits were built so summing them
reconstructs the real region figure exactly (verified above), so all four levels are
consistent by construction.

This was a deliberate choice, not an oversight: letting DHIS2 aggregate live would risk
double-counting at region level (its own stored value *plus* everything now sitting
beneath it in the hierarchy), and would require running DHIS2's analytics tables job
before any of it showed up correctly in Pivot Table or Data Visualizer. Storing explicit
values everywhere means a plain `dataValueSets` API call at any level returns a correct,
final number immediately, which is what our own planning app will be reading from anyway.

If you ever want DHIS2's built-in aggregation to do this work instead, the region-level
direct entries would need to be removed first -- a different setup, not a quick toggle.

## Key IDs this rebuild depends on

These aren't ours -- they belong to the DHIS2 demo database this instance was built on.
If you ever rebuild against a genuinely different instance (not the standard demo DB),
these will need re-deriving using the commands below, not assumed:

| What | ID used | How it was found |
| --- | --- | --- |
| Root org unit (parent for the 14 regions) | `ImspTQPwCqd` (Sierra Leone) | `curl -u admin:district "http://localhost:8080/api/organisationUnits.json?filter=level:eq:1&fields=id,name"` |
| Default category option combo | `HllvX50cXC0` | `curl -u admin:district "http://localhost:8080/api/categoryOptionCombos.json?filter=name:eq:default&fields=id"` |
| Default category combo | `bjDvmb4bfuf` | `curl -u admin:district "http://localhost:8080/api/categoryCombos.json?filter=name:eq:default&fields=id"` |

## Our own IDs (fixed by us, not DHIS2 -- these are safe to hardcode)

Since we assigned these ourselves at creation time, they're stable across rebuilds as
long as you keep re-using these same metadata files:

- Dataset ID: `C11WC1lXDMl` ("Immunisation Services - Synthetic Demo")
- 14 region IDs + 24 data element IDs: `dhis2_metadata_IMPORT.json`, or
  `immunisation_synthetic_history.csv` for the human-readable names they map to
- 4 zone IDs + 8 woreda IDs: `dhis2_metadata_WOREDA_ZONE_ADDON.json`, or
  `woreda_zone_split.csv` for the human-readable names, shares, and which region/zone each
  one belongs to

## Known limitations, worth remembering

- **No real multi-year history.** EFY2011-2015 are synthetic (interpolated toward the real
  EFY2016 baseline, with a deliberate COVID-era dip); only EFY2016 (baseline) and EFY2017
  (target) are real reported figures from the source spreadsheet. See
  `Demo_Spec_Immunisation_Subset.md` and `Immunisation_Build_Spec.docx` for the full
  reasoning, including which 3 of the 15 original indicators were deliberately left with no
  synthetic history at all (measles vaccine wastage rate, drop-out rate, and Woreda >=80%
  coverage -- the source had no baseline for these either).
- **Only 2 of 14 regions have zone/woreda detail.** Amhara and Oromia only. The other 12
  regions stop at region level -- there was no real sub-region data to work from for any of
  them, and extending the same treatment to all 14 was out of scope for this demo.
- **Woreda/zone splits are entirely invented**, unlike the region-level data which anchors
  to one real reported number per indicator. The split shares (e.g. Amhara's four woredas
  at 35/20/30/15%) are plausible population-style proportions, not real sub-region figures
  -- there is no source data at this granularity to anchor to at all.
- **"National" is a stand-in**, not a real Ethiopia national-level org unit -- see above.