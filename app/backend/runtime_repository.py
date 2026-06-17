"""Repository methods for app data and runtime staging tables."""

from __future__ import annotations

import json
from typing import Any

from .databricks_client import DatabricksRuntimeClient, quote_sql
from .models import EditableVisitInput


DEFAULT_OVERRIDE_CODES = {
    "wellness": ["WELLNESS_PANEL", "HEARTWORM_4DX", "FECAL_SCREEN"],
    "gi": ["GI_PANEL", "FECAL_PCR", "WELLNESS_PANEL"],
    "renal_urinary": ["SDMA_CHEM17", "URINALYSIS", "URINE_CULTURE", "UPC_RATIO"],
    "endocrine_metabolic": ["THYROID_PANEL", "FRUCTOSAMINE", "CORTISOL_SCREEN"],
}

DEFAULT_AGENT_TARGET_ID = "supervisor"
DEFAULT_CUSTOM_AGENT_MODEL_ID = "databricks-claude-sonnet-4-5"
DEFAULT_CUSTOM_AGENT_MODELS = [
    {"id": "databricks-claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
]


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "1", "yes", "y"}:
            return True
        if normalized in {"false", "f", "0", "no", "n", "", "null", "none"}:
            return False
    return bool(value)


class RuntimeRepository:
    def __init__(self, dbx: DatabricksRuntimeClient):
        self.dbx = dbx
        self.settings = dbx.settings

    def health_payload(self) -> dict[str, Any]:
        runtime_config = self.dbx.current_runtime_config()
        agent_targets = self.agent_targets(runtime_config)
        return {
            "status": "ok" if any(target["available"] for target in agent_targets) else "degraded",
            "catalog": self.settings.catalog,
            "schema_name": self.settings.schema,
            "warehouse_id": self.settings.warehouse_id,
            "supervisor_endpoint": runtime_config.get("supervisor_endpoint")
            or self.settings.default_supervisor_endpoint
            or "not provisioned",
            "default_agent_target_id": runtime_config.get("default_agent_target_id")
            or DEFAULT_AGENT_TARGET_ID,
            "agent_targets": agent_targets,
        }

    def agent_targets(self, runtime_config: dict[str, str] | None = None) -> list[dict[str, Any]]:
        runtime_config = runtime_config if runtime_config is not None else self.dbx.current_runtime_config()
        default_target_id = runtime_config.get("default_agent_target_id") or DEFAULT_AGENT_TARGET_ID
        supervisor_endpoint = (
            runtime_config.get("supervisor_endpoint")
            or self.settings.default_supervisor_endpoint
            or ""
        )
        custom_agent_url = runtime_config.get("custom_agent_app_url") or ""
        custom_agent_label = runtime_config.get("custom_agent_label") or "Custom Agent"
        custom_agent_models = self.custom_agent_models(runtime_config)
        default_custom_agent_model_id = self.default_custom_agent_model_id(
            runtime_config,
            custom_agent_models,
        )
        return [
            {
                "id": "supervisor",
                "label": runtime_config.get("supervisor_label") or "Supervisor Agent",
                "transport": "model_serving",
                "endpoint": supervisor_endpoint or None,
                "available": bool(supervisor_endpoint),
                "default": default_target_id == "supervisor",
                "detail": supervisor_endpoint or "Supervisor endpoint is not provisioned.",
            },
            {
                "id": "custom_agent",
                "label": custom_agent_label,
                "transport": "databricks_app",
                "endpoint": custom_agent_url or None,
                "available": bool(custom_agent_url),
                "default": default_target_id == "custom_agent",
                "detail": custom_agent_url or "Custom agent app URL is not provisioned.",
                "model_options": custom_agent_models,
                "default_model_id": default_custom_agent_model_id,
            },
        ]

    def custom_agent_models(self, runtime_config: dict[str, str] | None = None) -> list[dict[str, str]]:
        runtime_config = runtime_config if runtime_config is not None else self.dbx.current_runtime_config()
        raw_options = runtime_config.get("custom_agent_model_options")
        if not raw_options:
            return DEFAULT_CUSTOM_AGENT_MODELS

        try:
            parsed = json.loads(raw_options)
        except json.JSONDecodeError:
            return DEFAULT_CUSTOM_AGENT_MODELS

        options: list[dict[str, str]] = []
        for item in parsed if isinstance(parsed, list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            label = str(item.get("label") or model_id).strip()
            if model_id:
                options.append({"id": model_id, "label": label or model_id})
        return options or DEFAULT_CUSTOM_AGENT_MODELS

    def default_custom_agent_model_id(
        self,
        runtime_config: dict[str, str] | None = None,
        model_options: list[dict[str, str]] | None = None,
    ) -> str:
        runtime_config = runtime_config if runtime_config is not None else self.dbx.current_runtime_config()
        model_options = model_options if model_options is not None else self.custom_agent_models(runtime_config)
        configured_default = runtime_config.get("custom_agent_default_model_id") or DEFAULT_CUSTOM_AGENT_MODEL_ID
        option_ids = {option["id"] for option in model_options}
        if configured_default in option_ids:
            return configured_default
        return model_options[0]["id"] if model_options else DEFAULT_CUSTOM_AGENT_MODEL_ID

    def resolve_custom_agent_model(self, model_id: str | None) -> dict[str, str]:
        runtime_config = self.dbx.current_runtime_config()
        model_options = self.custom_agent_models(runtime_config)
        resolved_model_id = model_id or self.default_custom_agent_model_id(runtime_config, model_options)
        model = next((option for option in model_options if option["id"] == resolved_model_id), None)
        if model is None:
            allowed = ", ".join(option["id"] for option in model_options)
            raise RuntimeError(f"Unknown custom agent model: {resolved_model_id}. Allowed models: {allowed}.")
        return model

    def resolve_agent_target(self, target_id: str | None) -> dict[str, Any]:
        requested_target_id = target_id or DEFAULT_AGENT_TARGET_ID
        targets = self.agent_targets()
        target = next((item for item in targets if item["id"] == requested_target_id), None)
        if target is None:
            raise RuntimeError(f"Unknown recommendation target: {requested_target_id}.")
        if not target.get("available"):
            raise RuntimeError(f"{target['label']} is not available: {target.get('detail')}")
        return target

    def stage_live_intake(self, request_id: str, intake: EditableVisitInput) -> str:
        payload_json = json.dumps(
            intake.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=True,
        )
        fq = f"{self.settings.catalog}.{self.settings.schema}.live_intake_requests"
        self.dbx.execute_statement(
            f"""
            INSERT INTO {fq} (request_id, patient_id, as_of_ts, payload_json, created_at)
            VALUES (
              {quote_sql(request_id)},
              {quote_sql(intake.patient_id)},
              {quote_sql(intake.as_of_ts)},
              {quote_sql(payload_json)},
              current_timestamp()
            )
            """
        )
        return payload_json

    def default_overrides_for_cohort(self, cohort: str | None) -> list[dict[str, Any]]:
        codes = DEFAULT_OVERRIDE_CODES.get(cohort or "", DEFAULT_OVERRIDE_CODES["wellness"])
        return [{"test_code": code, "days_since_same_test": None} for code in codes]

    def list_sample_cases(self) -> list[dict[str, Any]]:
        fq = f"{self.settings.catalog}.{self.settings.schema}"
        rows = self.dbx.fetch_rows(
            f"""
            WITH ranked AS (
              SELECT
                visit_id AS seed_visit_id,
                patient_id,
                dog_name,
                breed,
                age_years,
                life_stage,
                CAST(visit_date AS STRING) AS as_of_ts,
                presenting_complaint,
                severity,
                urgency_level,
                owner_note,
                clinician_note,
                cohort AS cohort_hint,
                vomiting,
                diarrhea,
                urinary_accidents,
                straining_to_urinate,
                increased_thirst,
                increased_hunger,
                lethargy,
                recent_diet_change,
                previous_same_issue,
                appetite_change,
                weight_change_pct,
                duration_days,
                row_number() OVER (
                  PARTITION BY cohort
                  ORDER BY visit_date DESC, visit_id DESC
                ) AS cohort_rank
              FROM {fq}.visit_case_packet_gold
            )
            SELECT
              seed_visit_id,
              patient_id,
              dog_name,
              breed,
              age_years,
              life_stage,
              as_of_ts,
              presenting_complaint,
              severity,
              urgency_level,
              owner_note,
              clinician_note,
              cohort_hint,
              vomiting,
              diarrhea,
              urinary_accidents,
              straining_to_urinate,
              increased_thirst,
              increased_hunger,
              lethargy,
              recent_diet_change,
              previous_same_issue,
              appetite_change,
              weight_change_pct,
              duration_days
            FROM ranked
            WHERE cohort_rank <= 2
            ORDER BY as_of_ts DESC, seed_visit_id DESC
            LIMIT {self.settings.sample_visit_limit}
            """
        )
        sample_cases: list[dict[str, Any]] = []
        for row in rows:
            cohort_hint = row[12]
            sample_cases.append(
                {
                    "sample_label": f"{row[2]} · {row[7]}",
                    "seed_visit_id": row[0],
                    "patient_id": row[1],
                    "dog_name": row[2],
                    "breed": row[3],
                    "age_years": row[4],
                    "life_stage": row[5],
                    "as_of_ts": row[6],
                    "presenting_complaint": row[7],
                    "severity": row[8],
                    "urgency_level": row[9],
                    "owner_note": row[10],
                    "clinician_note": row[11],
                    "cohort_hint": cohort_hint,
                    "vomiting": coerce_bool(row[13]),
                    "diarrhea": coerce_bool(row[14]),
                    "urinary_accidents": coerce_bool(row[15]),
                    "straining_to_urinate": coerce_bool(row[16]),
                    "increased_thirst": coerce_bool(row[17]),
                    "increased_hunger": coerce_bool(row[18]),
                    "lethargy": coerce_bool(row[19]),
                    "recent_diet_change": coerce_bool(row[20]),
                    "previous_same_issue": coerce_bool(row[21]),
                    "appetite_change": row[22] or "stable",
                    "weight_change_pct": row[23] or 0.0,
                    "duration_days": row[24] or 0,
                    "recent_test_overrides": self.default_overrides_for_cohort(cohort_hint),
                }
            )
        return sample_cases

    def fetch_patient_record(self, patient_id: str) -> dict[str, Any]:
        fq = f"{self.settings.catalog}.{self.settings.schema}"
        rows = self.dbx.fetch_rows(
            f"""
            SELECT dog_name, breed, age_years, life_stage
            FROM {fq}.patients
            WHERE patient_id = {quote_sql(patient_id)}
            LIMIT 1
            """
        )
        if not rows:
            return {
                "dog_name": None,
                "breed": None,
                "age_years": None,
                "life_stage": None,
            }
        row = rows[0]
        return {
            "dog_name": row[0],
            "breed": row[1],
            "age_years": row[2],
            "life_stage": row[3],
        }
