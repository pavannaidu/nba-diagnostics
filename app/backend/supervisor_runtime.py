"""Supervisor prompting and response parsing helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from .databricks_client import maybe_json_load


TOOL_SPECS = {
    "get_patient_history": {"label": "Fetch longitudinal history", "kind": "uc_function"},
    "get_recent_test_audit": {"label": "Audit recent duplicate testing", "kind": "uc_function"},
    "similar_case_genie": {"label": "Retrieve similar-case evidence", "kind": "genie_space"},
    "diagnostic_guidance_ka": {"label": "Retrieve diagnostic guidance", "kind": "knowledge_assistant"},
    "get_test_metadata": {"label": "Load test metadata", "kind": "uc_function"},
}

REQUIRED_RUNTIME_OUTPUTS = ["get_patient_history", "get_recent_test_audit"]
REQUIRED_RUNTIME_CALLS = ["similar_case_genie", "diagnostic_guidance_ka"]
TRACE_TOOL_ORDER = [
    "get_patient_history",
    "get_recent_test_audit",
    "similar_case_genie",
    "diagnostic_guidance_ka",
    "get_test_metadata",
]


def normalize_tool_name(name: str | None) -> str:
    tool_name = name or ""
    for expected_name in TOOL_SPECS:
        if tool_name.endswith(expected_name):
            return expected_name
    return tool_name


def parse_function_output(raw_output: str | None) -> Any:
    if not raw_output:
        return None
    parsed_output = maybe_json_load(raw_output)
    if not isinstance(parsed_output, dict):
        return parsed_output

    rows = parsed_output.get("rows")
    if rows and rows[0]:
        return maybe_json_load(rows[0][0])

    data_array = parsed_output.get("result", {}).get("data_array")
    if data_array and data_array[0]:
        return maybe_json_load(data_array[0][0])

    return parsed_output


def parse_assistant_payload(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    candidates = [cleaned]
    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        candidates.append(cleaned[object_start : object_end + 1])

    for candidate in candidates:
        parsed = maybe_json_load(candidate)
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_supervisor_response(payload: dict[str, Any]) -> dict[str, Any]:
    assistant_messages: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_outputs: dict[str, dict[str, Any]] = {}

    for item in payload.get("output", []):
        item_type = item.get("type")
        if item_type == "message" and item.get("role") == "assistant":
            text_parts = [
                content.get("text")
                for content in item.get("content", [])
                if content.get("type") == "output_text" and content.get("text")
            ]
            if text_parts:
                assistant_messages.append("\n".join(text_parts))
        elif item_type == "function_call":
            arguments = maybe_json_load(item.get("arguments"))
            tool_calls.append(
                {
                    "call_id": item.get("call_id"),
                    "id": item.get("id"),
                    "raw_name": item.get("name"),
                    "name": normalize_tool_name(item.get("name")),
                    "step": item.get("step"),
                    "arguments": arguments if isinstance(arguments, dict) else None,
                }
            )
        elif item_type == "function_call_output":
            normalized_name = normalize_tool_name(item.get("name"))
            if not normalized_name and item.get("step") is not None:
                matched_call = next(
                    (call for call in tool_calls if call.get("step") == item.get("step")),
                    None,
                )
                if matched_call:
                    normalized_name = matched_call["name"]
            if not normalized_name and item.get("call_id") is not None:
                matched_call = next(
                    (call for call in tool_calls if call.get("call_id") == item.get("call_id")),
                    None,
                )
                if matched_call:
                    normalized_name = matched_call["name"]
            if not normalized_name:
                continue
            tool_outputs[normalized_name] = {
                "payload": parse_function_output(item.get("output")),
                "duration_ms": item.get("duration_ms"),
                "step": item.get("step"),
                "raw_name": item.get("name"),
            }

    assistant_text = assistant_messages[-1] if assistant_messages else None
    return {
        "response_id": payload.get("id"),
        "assistant_text": assistant_text,
        "assistant_payload": parse_assistant_payload(assistant_text),
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
    }


def build_runtime_prompt(request_id: str, payload_json: str) -> str:
    return f"""
You are orchestrating a live dog diagnostic recommendation request `{request_id}`.

Use this intake payload as the source of truth:
{payload_json}

Use the attached tools in this sequence:
1. get_patient_history with payload_json set to the full intake payload JSON string
2. get_recent_test_audit with payload_json set to the full intake payload JSON string
3. similar_case_genie
4. diagnostic_guidance_ka
5. get_test_metadata only after selecting the primary action and any visible alternatives, using a JSON array string of the visible test codes

Use patient history and recent duplicate-test audit as hard constraints.
Use Genie for structured similar-case and cohort evidence.
Use the knowledge assistant for diagnostic workflow guidance, repeat-test cautions, and abstain conditions.

Return valid JSON only with:
- visit_summary {{ derived_cohort }}
- recommendation {{ action_type, test_code, test_name, summary, reasons, evidence_chips }}
- why_not_selected [{{ test_code, test_name, disposition, why_not_selected }}]
- evidence_sources [{{ source_type, source_label, summary }}]

Requirements:
- action_type: exactly "recommend_test" or "abstain"
- summary: one sentence, <= 140 characters
- reasons: 2 to 4 short bullets
- evidence_chips: 3 to 5 compact fact labels
- why_not_selected: at most 3 alternatives, each with one short sentence

If evidence is insufficient or recent duplicate testing removes the strongest option, use action_type exactly "abstain".
""".strip()


def summarize_output_preview(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload[:320]
    return json.dumps(payload, default=str)[:320]
