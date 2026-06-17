"""Thin app service facade for live request-time recommendations."""

from __future__ import annotations

import json
import logging
import time
import uuid
from functools import cached_property
from typing import Any

from .databricks_client import DatabricksRuntimeClient
from .models import EditableVisitInput
from .recommendation_assembler import RecommendationAssembler
from .runtime_repository import RuntimeRepository
from .supervisor_runtime import (
    REQUIRED_RUNTIME_CALLS,
    REQUIRED_RUNTIME_OUTPUTS,
    build_runtime_prompt,
    parse_supervisor_response,
)


LOGGER = logging.getLogger(__name__)


class RecommendationService:
    @cached_property
    def dbx(self) -> DatabricksRuntimeClient:
        return DatabricksRuntimeClient()

    @cached_property
    def repository(self) -> RuntimeRepository:
        return RuntimeRepository(self.dbx)

    @cached_property
    def assembler(self) -> RecommendationAssembler:
        return RecommendationAssembler(self.repository)

    def health_payload(self) -> dict[str, Any]:
        return self.repository.health_payload()

    def list_sample_cases(self) -> list[dict[str, Any]]:
        return self.repository.list_sample_cases()

    def missing_fields(self, payload: EditableVisitInput) -> list[str]:
        missing: list[str] = []
        if not payload.patient_id.strip():
            missing.append("patient_id")
        if not payload.as_of_ts.strip():
            missing.append("as_of_ts")
        if not payload.presenting_complaint.strip():
            missing.append("presenting_complaint")
        if not payload.severity.strip():
            missing.append("severity")
        has_signal = any(
            [
                payload.vomiting,
                payload.diarrhea,
                payload.urinary_accidents,
                payload.straining_to_urinate,
                payload.increased_thirst,
                payload.increased_hunger,
                payload.lethargy,
            ]
        )
        has_note = bool(payload.owner_note.strip() or payload.clinician_note.strip())
        if not has_signal and not has_note:
            missing.append("clinical_context")
        return missing

    def call_agent_target(
        self,
        target: dict[str, Any],
        prompt: str,
        *,
        custom_agent_model_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        endpoint = target.get("endpoint")
        if not endpoint:
            raise RuntimeError(f"{target['label']} is not provisioned.")
        if target.get("transport") == "model_serving":
            return endpoint, self.dbx.call_model_serving_responses_endpoint(endpoint, prompt)
        if target.get("transport") == "databricks_app":
            return endpoint, self.dbx.call_app_responses_endpoint(
                endpoint,
                prompt,
                custom_agent_model_id=custom_agent_model_id,
            )
        raise RuntimeError(f"Unsupported recommendation target transport: {target.get('transport')}")

    def validate_runtime_tool_contract(self, parsed: dict[str, Any]) -> None:
        tool_outputs = parsed.get("tool_outputs") or {}
        tool_calls = parsed.get("tool_calls") or []
        called_tools = {tool.get("name") for tool in tool_calls}

        missing_outputs = [
            tool_name for tool_name in REQUIRED_RUNTIME_OUTPUTS if tool_name not in tool_outputs
        ]
        if missing_outputs:
            raise RuntimeError(
                "Agent target did not return all required runtime tool outputs: "
                + ", ".join(missing_outputs)
            )

        missing_calls = [
            tool_name for tool_name in REQUIRED_RUNTIME_CALLS if tool_name not in called_tools
        ]
        if missing_calls:
            raise RuntimeError(
                "Agent target did not call all required evidence tools: " + ", ".join(missing_calls)
            )

    def fallback_agent_name(self, target_id: str | None) -> str:
        try:
            return str(self.repository.resolve_agent_target(target_id).get("endpoint") or "agent unavailable")
        except Exception:  # noqa: BLE001
            runtime_config = self.dbx.current_runtime_config()
            return (
                runtime_config.get("supervisor_endpoint")
                or self.dbx.settings.default_supervisor_endpoint
                or "agent unavailable"
            )

    def run_live_recommendation(
        self,
        intake: EditableVisitInput,
        *,
        agent_target_id: str | None = None,
        custom_agent_model_id: str | None = None,
    ) -> dict[str, Any]:
        overall_start = time.perf_counter()
        synthesis_duration_ms: int | None = None
        agent_run_id: str | None = None
        target = self.repository.resolve_agent_target(agent_target_id)
        custom_agent_model = (
            self.repository.resolve_custom_agent_model(custom_agent_model_id)
            if target.get("transport") == "databricks_app"
            else None
        )
        agent_name = str(target.get("endpoint") or self.fallback_agent_name(agent_target_id))
        parsed: dict[str, Any] | None = None
        request_id = uuid.uuid4().hex
        staged_payload_json = json.dumps(
            intake.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=True,
        )

        try:
            staged_payload_json = self.repository.stage_live_intake(request_id, intake)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Unable to stage live intake request %s: %s", request_id, exc)

        try:
            synthesis_start = time.perf_counter()
            agent_name, agent_payload = self.call_agent_target(
                target,
                build_runtime_prompt(request_id, staged_payload_json),
                custom_agent_model_id=custom_agent_model["id"] if custom_agent_model else None,
            )
            parsed = parse_supervisor_response(agent_payload)
            synthesis_duration_ms = int((time.perf_counter() - synthesis_start) * 1000)
            agent_run_id = parsed.get("response_id")
            self.validate_runtime_tool_contract(parsed)
            total_duration_ms = int((time.perf_counter() - overall_start) * 1000)
            return self.assembler.build_completed_result(
                intake=intake,
                parsed_response=parsed,
                supervisor_name=agent_name,
                supervisor_run_id=agent_run_id,
                total_duration_ms=total_duration_ms,
                synthesis_duration_ms=synthesis_duration_ms,
                agent_target_id=target["id"],
                agent_target_label=target["label"],
                custom_agent_model_id=custom_agent_model["id"] if custom_agent_model else None,
                custom_agent_model_label=custom_agent_model["label"] if custom_agent_model else None,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Live recommendation unavailable for patient %s: %s", intake.patient_id, exc)
            total_duration_ms = int((time.perf_counter() - overall_start) * 1000)
            if synthesis_duration_ms is None:
                synthesis_duration_ms = total_duration_ms
            if isinstance(exc, RuntimeError):
                message = f"Live recommendation was unavailable: {exc}"
            else:
                message = f"Live recommendation was unavailable: {type(exc).__name__}: {exc}"
            return self.assembler.build_unavailable_result(
                intake=intake,
                message=message,
                total_duration_ms=total_duration_ms,
                synthesis_duration_ms=synthesis_duration_ms,
                supervisor_name=agent_name,
                supervisor_run_id=agent_run_id,
                parsed_response=parsed,
                agent_target_id=target["id"],
                agent_target_label=target["label"],
                custom_agent_model_id=custom_agent_model["id"] if custom_agent_model else None,
                custom_agent_model_label=custom_agent_model["label"] if custom_agent_model else None,
            )
