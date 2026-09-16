# NextGen Planning API — FastAPI Service Specification

**Project:** NextGen Planning (INV-089469) — AI-enabled health sector planning tool for Ethiopia's MoH, targeting the Woreda-Based Health Sector Planning (WBHSP) process
**This document:** Build spec for the FastAPI service layer, intended to be handed to a coding agent
**Status:** Phase 2 (Design & Specification) prototype/dev build — not the committed Phase 3 production deployment
**Source material this spec is grounded in:** `Requirements_for_AI.xlsx` (Process Assessment + Issue Mapping), `Implementation_plan.xlsx`, the INV-089469 Investment Document, and the COMPASS diagnostic framework

---

## 1. Purpose

Build the API/backend-for-frontend layer that sits between:
- **DHIS2** (system of record — local instance for dev, MoH's instance in production)
- **A deterministic calculation/validation layer** (target formulas, aggregation roll-up, baseline derivation)
- **An ML/statistical layer** (target disaggregation and recommendation)
- **A GenAI layer** (Claude, via direct Anthropic API — evidence synthesis, SWOT/PESTLE drafting)
- **The planner-facing front end**

**Explicitly not in scope:** trend analysis or outlier detection. FASTER already performs this function for MoH; duplicating it is a hard constraint violation regardless of how it's framed elsewhere (see §7, flagged conflict).

---

## 2. Critical finding that determines build order

The Issue Mapping sheet catalogues 55 known issues in the current DHIS2/Plan Setting App, mapped to process steps and technology type. The distribution matters a lot for sequencing:

| Process step | Share of issues | Technology type | Nature of the issue |
|---|---|---|---|
| Set woreda targets given indicative plan | 53% (29 issues) | ML/Statistical (downstream) | Target-in-% → target-in-# formula linkage broken across dozens of indicators; eligible population missing/not visualised for many indicators; no validation on negative targets or target-vs-baseline direction |
| Plan aggregation — arithmetic roll-up | 20% (11 issues) | Deterministic | Aggregation wrong at zonal/regional level; template-vs-analysis-mode discrepancies mean consolidated outputs can't be trusted |
| Gather evidence — data extraction | 24% (13 issues) | Deterministic | Baselines not auto-fetching from DHIS2; baseline-in-% not derived from baseline-in-# (or vice versa); some baseline values are impossible (e.g. >100%, >5000%) |

**None of this is an AI problem.** Every single one of these is broken arithmetic, missing validation, or a field-visibility config error. The Process Assessment sheet independently confirms this: "Gather evidence — data extraction" and "Plan aggregation" are both tagged **Deterministic automation**, not GenAI or ML.

**This dictates build order.** ML target recommendations and GenAI evidence synthesis will produce untrustworthy output if they sit on top of unreliable baselines and broken aggregation — and per the deterministic-first principle underlying COMPASS Gate 1, none of this plumbing needs AI to fix it in the first place. Building the ML/GenAI layers before the deterministic layer is solid would also almost certainly fail a Gate 1-style justification (why deploy AI on top of a broken calculation, when the calculation itself is the thing that needs fixing?).

**Build order for this FastAPI service:**
1. **Deterministic data layer** (baseline fetch/derivation, target formula linkage, validation rules, aggregation roll-up) — first milestone, and the one with disproportionate value per the issue mapping (97% of catalogued issues sit here or downstream of here)
2. **ML/Statistical layer** (target disaggregation and recommendation) — depends on (1) being correct
3. **GenAI layer** (evidence synthesis, SWOT/PESTLE drafting) — can be developed in parallel with (1)/(2) since it consumes evidence documents rather than the target-setting arithmetic, but should not be presented as production-ready until (1) is solid, since synthesis quality depends on clean baseline data

---

## 3. Architecture overview

```
                     ┌─────────────────────────┐
                     │   Planner-facing front   │
                     │   end (out of scope of   │
                     │   this spec)             │
                     └───────────┬─────────────┘
                                 │ REST/JSON
                     ┌───────────▼─────────────┐
                     │   FastAPI service        │
                     │  (this spec)              │
                     │                           │
                     │  ┌─────────────────────┐  │
                     │  │ Deterministic layer │  │──► DHIS2 REST API
                     │  │ (baseline, targets, │  │    (local dev instance now;
                     │  │  aggregation)       │  │     MoH instance in prod)
                     │  └─────────────────────┘  │
                     │  ┌─────────────────────┐  │
                     │  │ ML/Statistical layer│  │
                     │  │ (target disagg. &   │  │
                     │  │  recommendation)    │  │
                     │  └─────────────────────┘  │
                     │  ┌─────────────────────┐  │
                     │  │ GenAI layer          │  │──► Anthropic API (Claude)
                     │  │ (evidence synthesis, │  │    direct SDK call, no
                     │  │  SWOT/PESTLE draft)  │  │    agent framework needed
                     │  └─────────────────────┘  │    for MVP scope
                     └───────────────────────────┘
```

Deployment target is Cloud Run for the API + Compute Engine (or GKE) for DHIS2 — see separate conversation notes on this; not blocking for building the API itself, which should be developed and tested locally against the existing local DHIS2 instance first.

---

## 4. DHIS2 integration details

- **Local dev instance:** `localhost:8080`, DHIS2 v2.40.0.1, Sierra Leone demo database extended with synthetic Ethiopian data
- **Confirmed instance IDs:** root org unit `ImspTQPwCqd`, default categoryOptionCombo `HllvX50cXC0`, default categoryCombo `bjDvmb4bfuf`
- **Demo data source:** `sample_indicative_plan.xlsx`, `subset` tab, Objective 1.1.3 Immunisation Services, 15 indicators across 14 Ethiopian regions, extended with woreda/zone data for Amhara and Oromia (2 zones each, 2 woredas per zone)
- **Endpoints needed:** `dataValueSets` (read/write), `analytics` (read, for aggregated views — but see next point), `metadata` (org units, data elements, category combos)
- **Avoid double-counting:** store zone and woreda values explicitly in your own data model rather than relying solely on DHIS2 analytics aggregation
- **Numerator computation rule:** always recompute as coverage % × eligible population; never take a numerator directly from a source file if it can be derived
- **Metadata vs data imports:** if the service ever writes back to DHIS2, metadata payloads go through the Metadata importer, not the Data Value Set importer; `categoryCombo` and `categoryOptionCombo` are distinct object types with their own IDs

---

## 5. Functional components

### 5.1 Deterministic data layer (build first)

- **Baseline auto-fetch and derivation**
  - Pull baseline-in-# from DHIS2 automatically per indicator/org unit
  - Where baseline-in-% is missing but baseline-in-# and eligible population exist, derive it (and vice versa) — do not require manual entry when it's calculable
  - Flag (don't silently accept) impossible values — e.g. a baseline percentage >100% or in the thousands — for human review rather than propagating them
- **Target formula linkage**
  - Bi-directional calculation between target-in-% and target-in-# via eligible population, so that entering either field updates the other
  - Directional validation: indicators can be "increasing is good" or "decreasing is good" (e.g. mortality vs coverage) — target validation rules need to know which, and reject targets that move the wrong direction relative to baseline without an explicit override
  - Reject negative targets outright
  - Reject targets below baseline for increasing-good indicators (and above baseline for decreasing-good indicators) unless explicitly overridden with a reason
  - Require eligible population wherever an indicator's target is expressed in raw numbers (target-in-#) rather than percentage only
- **Aggregation roll-up**
  - Woreda → zone → region → national, stored explicitly at each level (not inferred purely from DHIS2 analytics)
  - Reconciliation checks: total should match the sum of children; flag and surface which specific woredas are driving any gap between aggregate and target, rather than just a pass/fail
  - Resolve template-vs-analysis-mode discrepancy: aggregated values shown in the planning template must match values shown in analysis mode for the same org unit/indicator/period — this needs to be a single source of truth read by both, not two parallel calculations

### 5.2 ML/Statistical layer (target setting)

Per the Process Assessment sheet, this is explicitly **ML/Statistical**, not GenAI — it works on structured numeric data (baselines, population, historical achievement), not documents or free text.

- **National indicative plan disaggregation:** propose a split of the national indicative target across regions/woredas using historical baselines, population data, and achievement rates; flag where a proposed split would be unrealistic for a given woreda (the dataset needs to handle ~800 woredas' worth of specific scenarios)
- **Woreda target recommendation:** given the indicative range for a woreda, recommend an achievable target considering baseline, historical trajectory, and available resources; flag divergence from the indicative range for human review
- **Explicitly not automated:** final allocation decisions. These are politically sensitive and require negotiation between administrative levels — the system recommends and flags, it does not decide

### 5.3 GenAI layer (Claude, direct API — no agent framework needed for MVP)

- **Evidence synthesis:** draft a synthesis narrative across multiple structured/unstructured evidence sources; identify convergent/divergent findings as a first-pass summary for human review
- **Situational analysis drafting:** generate a first-pass SWOT/PESTLE from structured evidence inputs
- Runs as an **enrichment pass** between automated Situational Analysis and planner review — not a separate upstream stage
- **Draft/Endorsed state:** planning content exists in exactly two states; transition to Endorsed happens only via an explicit front-end action, never automatically
- **Staleness tracking:** tracked at the SWOT quadrant / PESTEL category level, surfaced as a warning rather than a hard block on stale content
- Use the Anthropic Python SDK directly for these calls (tool use where needed for pulling supporting data) — no ADK, no separate agent framework required at this scope; keeps the dependency footprint minimal and keeps the model choice aligned with what's already been represented to the Gates Foundation (Claude for POC)

### 5.4 Costing (deterministic)

- Align available resources to planned interventions; highlight funding/resource gaps

---

## 6. Explicitly out of scope

- **Trend analysis / outlier detection** — this is FASTER's job; do not build it here regardless of what other project documents suggest (see flagged conflict below)
- **Final target allocation decisions** — recommend and flag only, human sign-off required
- **UC-HL-05** (Target Recommendation Engine) scope — likely duplicates the Indicative Target Setting work above; needs TWG resolution before building a second implementation of the same thing

---

## 7. Flagged conflict — please resolve before/during build

`Implementation_plan.xlsx` lists a **"Trend/forecast development tool"** as a task under the "AI analytics engine build" theme. This directly conflicts with the standing FASTER non-duplication constraint (FASTER already performs trend analysis and outlier detection, and no NextGen Planning use case is meant to replicate it). Recommend excluding this specific task from the FastAPI build scope unless someone has explicitly reconciled this with Yordanos/the TWG. Don't let it get built by default just because it's sitting in the implementation plan spreadsheet.

---

## 8. Suggested API surface (draft — the coding agent should refine field-level detail)

```
GET  /dhis2/baseline/{org_unit}/{data_element}          → fetch + derive baseline (# and %)
POST /targets/calculate                                  → compute target-in-#/target-in-% given baseline, eligible, directionality; returns validation errors/warnings
GET  /targets/disaggregate/{indicator}/{period}          → ML-recommended split of national indicative target to regions/woredas
POST /targets/woreda-recommendation                      → ML-recommended woreda target given indicative range + history
POST /aggregation/rollup                                 → woreda→zone→region→national with reconciliation flags
GET  /aggregation/reconcile/{indicator}/{period}          → which woredas are driving a gap
POST /evidence/synthesize                                → GenAI enrichment pass (evidence narrative)
POST /evidence/situational-analysis                       → GenAI first-pass SWOT/PESTLE draft
GET  /plans/{plan_id}                                     → current state (Draft/Endorsed), staleness by quadrant/category
POST /plans/{plan_id}/endorse                             → explicit Draft→Endorsed transition
```

---

## 9. Non-functional requirements

- **Auth:** role-based (planner / endorser / admin) — stub with API key or simple JWT for the prototype; MoH's actual identity provider is a Phase 3 decision
- **Audit/logging:** every AI-generated field must carry a flag distinguishing it from human-entered/validated content, plus a validation status
- **Config:** DHIS2 base URL + credentials and the Anthropic API key via environment variables locally; move to Secret Manager once cloud-deployed
- **Testing priority:** unit test coverage on the deterministic calculation layer (§5.1) is the highest-value testing investment given the issue mapping data — this is where 97% of known problems live, and it's the layer everything else depends on

---

## 10. Suggested stack

- **FastAPI** + Pydantic v2 for request/response models
- **httpx** (async) for DHIS2 API calls
- **Anthropic Python SDK**, direct — no agent framework for MVP scope
- **pandas** for aggregation/reconciliation logic
- **pytest** for the deterministic layer's test suite in particular
- Local dev against the existing `localhost:8080` DHIS2 instance; Docker Compose if you want FastAPI + DHIS2 running together locally

---

## 11. Open questions to flag to the agent / resolve before finalizing

1. Is the "Trend/forecast development tool" line in the implementation plan actually meant to be built, given the FASTER constraint? (§7)
2. UC-HL-05 vs. Indicative Target Setting overlap — is the ML/Statistical target-setting work in §5.2 one system or two?
3. Component naming (Tana/Abay/Tekeze/Dashen/Buna) is pending TWG validation — this spec uses functional names throughout; map to final component names once confirmed
4. Cloud deployment split (Cloud Run for API, Compute Engine/GKE for DHIS2) is a separate, unresolved item — doesn't block building against local DHIS2 now
5. Confirm whether directional validation rules (increasing/decreasing indicators) need to be sourced from DHIS2 metadata or maintained as a separate reference table, since this isn't necessarily standard DHIS2 metadata
