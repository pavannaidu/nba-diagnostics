# IDEXX Clinical Diagnostic Support

Dog-only synthetic data and a Databricks App for request-time diagnostic next-step recommendations. Clinical Diagnostic Support can send the same editable intake to either a live Agent Bricks Supervisor or a sibling custom Databricks Apps agent.

## What Is Runtime

- `GET /api/sample-cases` loads reference intakes from synthetic gold views.
- `GET /api/health` advertises selectable recommendation targets from `pavan_naidu.nba.runtime_config`.
- `POST /api/recommendations` submits the edited intake to the selected target, defaulting to the Supervisor endpoint.
- Runtime UC tools provide patient facts, supplementary literature, and hard guardrails:
  - `runtime_triage_intake`
  - `runtime_query_pubmed`
  - `runtime_get_patient_history`
  - `runtime_get_recent_test_audit`
  - `runtime_get_test_metadata`
- Genie and Knowledge Assistant provide similar-case evidence and curated diagnostic guidance; PubMed adds supplementary literature context.
- The custom agent exposes `/responses` from `agent_app/` using MLflow `AgentServer`; it uses the same core tool names, prompt contract, and JSON response shape as the Supervisor.
- Both targets call `query_pubmed` during runtime. PubMed failures return structured unavailable payloads and do not block recommendations.

## What Is Baseline

The visit-level gold views and legacy heuristic functions remain available for evaluation and regression checks, but they are not the app serving path:

- `visit_case_packet_gold`
- `visit_candidate_features_gold`
- `visit_recommendation_gold`
- `get_visit_packet`
- `get_visit_candidate_diagnostics`
- `get_cohort_recommendation_summary`
- `recommend_for_intake`
- `scripts/recommend_for_visit.py`

## Default Environment

- Catalog: `pavan_naidu`
- Schema: `nba`
- Bundle target: `fevm`
- Databricks profile: `FEVM`
- SQL warehouse: `e0c50bf18fca9e7f`

## Canonical Deployment

```bash
make frontend-build
make deploy TARGET=fevm
make bootstrap TARGET=fevm
make custom-agent-run TARGET=fevm
make app-run TARGET=fevm
make smoke TARGET=fevm
```

Or run the full sequence:

```bash
make full-deploy TARGET=fevm
```

The bundle deploys the Clinical app, custom agent app, jobs, and managed volume. The bootstrap job creates or updates Genie, Knowledge Assistant, Supervisor, UC functions/views, grants, and `runtime_config`.

Bootstrap deploys only the live runtime UC functions by default. Legacy baseline/debug functions can be removed after verification:

```bash
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM --execute
```

## Local Development

```bash
make frontend-build
uvicorn app:app --app-dir app --reload
```

For frontend-only iteration:

```bash
npm --prefix app/frontend run dev
```

## Verification

```bash
make py-compile
npm --prefix app/frontend run build
databricks bundle validate -t fevm
make smoke TARGET=fevm
```

See `docs/architecture.md` for runtime boundaries and `docs/development.md` for day-to-day commands.
