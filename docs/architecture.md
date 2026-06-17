# Architecture

This repo has two selectable serving targets behind one Clinical Diagnostic Support response contract: a live Agent Bricks Supervisor and a sibling custom Databricks Apps agent. Both targets orchestrate patient facts, recent duplicate-test guardrails, structured cohort evidence, and curated diagnostic guidance before returning a single next diagnostic action or an abstain decision.

## Runtime Path

1. The React app collects an editable dog visit intake.
2. FastAPI stages the intake in `pavan_naidu.nba.live_intake_requests` for auditability.
3. FastAPI reads `pavan_naidu.nba.runtime_config` to advertise available targets and resolve the selected target.
4. The selected target runs live tools in the same sequence:
   - `runtime_get_patient_history`
   - `runtime_get_recent_test_audit`
   - Genie similar-case evidence
   - Knowledge Assistant diagnostic guidance
   - `runtime_get_test_metadata` for the final visible action set
5. FastAPI normalizes the selected target JSON into the stable `/api/recommendations` response.

The Supervisor target is called through Databricks Model Serving Responses API. The custom target is deployed from `agent_app/` as `idexx-nba-custom-agent-${bundle.target}` and exposes Databricks Apps `/responses` through MLflow `AgentServer`.

## Baseline And Evaluation Assets

The gold views remain useful, but they are not the app serving path:

- `visit_case_packet_gold` seeds reference intakes.
- `visit_candidate_features_gold` supports Genie evidence and regression checks.
- `visit_recommendation_gold` is a baseline comparison asset.
- Legacy functions such as `recommend_for_intake` and `get_visit_packet` are not deployed by the default DAB bootstrap path.
- If baseline-only legacy functions are needed temporarily, run `scripts/deploy_idexx_uc_functions.py --include-legacy`.

## Asset Lifecycle

Databricks Asset Bundles own the app, jobs, volume, and source deployment. The `bootstrap_runtime_assets` bundle job provisions or updates assets that do not yet have native top-level DAB resources in this project:

- Genie space
- Knowledge Assistant
- Supervisor Agent
- Custom Databricks Apps agent
- UC grants and serving endpoint permissions
- `runtime_config` keys consumed by the app

The canonical flow is `make full-deploy TARGET=fevm`, which deploys, bootstraps, starts the custom agent app, starts the Clinical app, and runs smoke tests against both recommendation targets.

## Cleanup Policy

Keep the source tables and gold views because they support the app, Genie, and evaluation. The cleanup script only drops unused legacy/debug UC functions after confirming the live Supervisor does not reference them:

```bash
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM
PYTHONPATH=scripts python scripts/cleanup_legacy_uc_assets.py --profile FEVM --execute
```
