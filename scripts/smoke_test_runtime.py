#!/usr/bin/env python3
"""Smoke test the provisioned runtime recommendation assets."""

from __future__ import annotations

import argparse
import json
import time

import requests

from runtime_lib import (
    TOOL_IDS,
    build_runtime_prompt,
    build_workspace_client,
    call_supervisor,
    fetch_smoke_intake,
    fetch_scalar,
    load_runtime_config,
    maybe_json,
    parse_response_payload,
    query_genie_space,
    quote_sql,
    stage_live_intake,
    list_supervisor_tools,
    normalize_host,
    parse_responses_api_body,
)


ERROR_MARKERS = {
    "unresolved_column",
    "payload_json cannot be resolved",
    "permission_denied",
    "tables_missing_exception",
    "no access to",
    "does not have permission",
    "permission 'view' on endpoint",
}


def payload_text(payload: object) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, default=str)


def assert_no_error_text(label: str, payload: object, markers: set[str] | None = None) -> None:
    text = payload_text(payload).lower()
    for marker in markers or ERROR_MARKERS:
        if marker in text:
            raise RuntimeError(f"{label} returned an error payload: {payload_text(payload)[:500]}")


def require_tool_output(
    parsed_payload: dict[str, object],
    tool_name: str,
    label: str,
    *,
    allow_error_text: bool = False,
) -> dict[str, object]:
    tool_outputs = parsed_payload.get("tool_outputs")
    if not isinstance(tool_outputs, dict):
        raise RuntimeError("Agent response did not include tool outputs.")
    output = tool_outputs.get(tool_name)
    if not isinstance(output, dict):
        raise RuntimeError(f"Agent response did not return an output for {label}.")
    payload = output.get("payload")
    if payload is None or payload_text(payload).strip() == "":
        raise RuntimeError(f"{label} returned an empty payload.")
    if not allow_error_text:
        assert_no_error_text(label, payload)
    return output


def validate_pubmed_payload(label: str, payload: object) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} did not return parseable JSON.")
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise RuntimeError(f"{label} did not include a status.")
    articles = payload.get("articles")
    error = payload.get("error")
    if not isinstance(articles, list) and not isinstance(error, dict):
        raise RuntimeError(f"{label} must include articles or a structured error.")


def require_tool_order(
    label: str,
    tool_call_sequence: list[str],
    earlier_tool: str,
    later_tool: str,
) -> None:
    if earlier_tool not in tool_call_sequence or later_tool not in tool_call_sequence:
        return
    if tool_call_sequence.index(earlier_tool) > tool_call_sequence.index(later_tool):
        raise RuntimeError(f"{label} called {later_tool} before {earlier_tool}.")


def call_app_responses(client, app_url: str, prompt: str) -> dict[str, object]:
    host = normalize_host(app_url)
    if not host:
        raise RuntimeError("Custom agent app URL is not configured.")
    headers = client.config.authenticate()
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization:
        headers.setdefault("X-Databricks-Authorization", authorization)
    response = requests.post(
        f"{host.rstrip('/')}/responses",
        headers=headers,
        json={
            "stream": False,
            "input": [{"role": "user", "content": prompt}],
        },
        timeout=(15, 300),
    )
    response.raise_for_status()
    return parse_responses_api_body(response)


def validate_recommendation_response(
    label: str,
    parsed: dict[str, object],
) -> tuple[dict[str, object], set[str]]:
    assistant_json = parsed.get("assistant_json")
    if not isinstance(assistant_json, dict):
        raise RuntimeError(f"{label} did not return a JSON object.")
    recommendation = assistant_json.get("recommendation")
    if not isinstance(recommendation, dict):
        raise RuntimeError(f"{label} JSON did not include a recommendation object.")
    action_type = recommendation.get("action_type")
    if action_type not in {"recommend_test", "abstain"}:
        raise RuntimeError(
            f"{label} recommendation.action_type must be recommend_test or abstain; got {action_type!r}."
        )
    if action_type == "recommend_test":
        for field_name in ("test_code", "test_name", "summary", "reasons", "evidence_chips"):
            if recommendation.get(field_name) in (None, "", []):
                raise RuntimeError(
                    f"{label} recommendation is missing required field {field_name!r}."
                )
    tool_call_sequence = [call.get("name", "") for call in parsed.get("tool_calls", [])]
    tool_call_names = set(tool_call_sequence)
    if not tool_call_sequence or tool_call_sequence[0] != TOOL_IDS["intake_triage"]:
        raise RuntimeError(f"{label} did not call triage_intake first during smoke test.")
    if TOOL_IDS["intake_triage"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call triage_intake during smoke test.")
    if TOOL_IDS["patient_history"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call get_patient_history during smoke test.")
    if TOOL_IDS["recent_test_audit"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call get_recent_test_audit during smoke test.")
    if TOOL_IDS["similar_case_genie"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call the Genie evidence tool during smoke test.")
    if TOOL_IDS["diagnostic_guidance_ka"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call the guidance KA during smoke test.")
    if TOOL_IDS["query_pubmed"] not in tool_call_names:
        raise RuntimeError(f"{label} did not call query_pubmed during smoke test.")

    require_tool_order(
        label,
        tool_call_sequence,
        TOOL_IDS["diagnostic_guidance_ka"],
        TOOL_IDS["query_pubmed"],
    )
    require_tool_order(
        label,
        tool_call_sequence,
        TOOL_IDS["query_pubmed"],
        TOOL_IDS["test_metadata"],
    )

    triage_output = require_tool_output(
        parsed,
        TOOL_IDS["intake_triage"],
        f"{label} triage_intake",
    )
    if not isinstance(triage_output.get("payload"), dict):
        raise RuntimeError(f"{label} triage_intake did not return parseable JSON.")

    history_output = require_tool_output(
        parsed,
        TOOL_IDS["patient_history"],
        f"{label} get_patient_history",
    )
    if not isinstance(history_output.get("payload"), dict):
        raise RuntimeError(f"{label} get_patient_history did not return parseable JSON.")

    recent_audit_output = require_tool_output(
        parsed,
        TOOL_IDS["recent_test_audit"],
        f"{label} get_recent_test_audit",
    )
    if not isinstance(recent_audit_output.get("payload"), list):
        raise RuntimeError(f"{label} get_recent_test_audit did not return a JSON array.")

    pubmed_output = require_tool_output(
        parsed,
        TOOL_IDS["query_pubmed"],
        f"{label} query_pubmed",
        allow_error_text=True,
    )
    validate_pubmed_payload(f"{label} query_pubmed", pubmed_output.get("payload"))

    return recommendation, tool_call_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="pavan_naidu")
    parser.add_argument("--schema", default="nba")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--guidance-volume-name", default="runtime_guidance")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--wait-seconds", type=int, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = build_workspace_client(args.profile)
    runtime_config = load_runtime_config(client, args.warehouse_id, args.catalog, args.schema)

    required_keys = {
        "genie_space_id",
        "knowledge_assistant_id",
        "knowledge_assistant_endpoint",
        "app_service_principal",
        "debug_user_name",
        "supervisor_name",
        "supervisor_endpoint",
        "custom_agent_app_name",
        "custom_agent_app_url",
    }
    missing_keys = sorted(key for key in required_keys if not runtime_config.get(key))
    if missing_keys:
        raise RuntimeError(f"runtime_config is missing required keys: {', '.join(missing_keys)}")

    tools = list_supervisor_tools(client, runtime_config["supervisor_name"])
    tool_ids = {tool.get("tool_id") for tool in tools}
    missing_tools = sorted(set(TOOL_IDS.values()) - tool_ids)
    if missing_tools:
        raise RuntimeError(f"Supervisor is missing expected tools: {', '.join(missing_tools)}")

    intake = fetch_smoke_intake(client, args.warehouse_id, args.catalog, args.schema)
    request_id, payload_json = stage_live_intake(
        client,
        args.warehouse_id,
        args.catalog,
        args.schema,
        intake,
    )
    direct_triage = fetch_scalar(
        client,
        args.warehouse_id,
        (
            f"SELECT {args.catalog}.{args.schema}.runtime_triage_intake("
            f"{quote_sql(payload_json)})"
        ),
    )
    assert_no_error_text("Direct runtime_triage_intake", direct_triage)
    parsed_direct_triage = maybe_json(direct_triage)
    if not isinstance(parsed_direct_triage, dict):
        raise RuntimeError("Direct runtime_triage_intake did not return parseable JSON.")

    direct_pubmed = fetch_scalar(
        client,
        args.warehouse_id,
        (
            f"SELECT {args.catalog}.{args.schema}.runtime_query_pubmed("
            "'canine chronic kidney disease SDMA', 2, 2018)"
        ),
    )
    parsed_direct_pubmed = maybe_json(direct_pubmed)
    validate_pubmed_payload("Direct runtime_query_pubmed", parsed_direct_pubmed)

    direct_history = fetch_scalar(
        client,
        args.warehouse_id,
        (
            f"SELECT {args.catalog}.{args.schema}.runtime_get_patient_history("
            f"{quote_sql(payload_json)})"
        ),
    )
    assert_no_error_text("Direct runtime_get_patient_history", direct_history)
    parsed_direct_history = maybe_json(direct_history)
    if not isinstance(parsed_direct_history, dict):
        raise RuntimeError("Direct runtime_get_patient_history did not return parseable JSON.")

    direct_genie = query_genie_space(
        client,
        runtime_config["genie_space_id"],
        "How many visits are there in each cohort, and how many are currently marked as no additional diagnostic now?",
        timeout_seconds=120,
    )
    assert_no_error_text("Direct Genie conversation", direct_genie)
    if str(direct_genie.get("status", "")).upper() != "COMPLETED":
        raise RuntimeError(
            f"Direct Genie conversation did not complete successfully: {direct_genie}"
        )

    direct_ka_payload = call_supervisor(
        client,
        runtime_config["knowledge_assistant_endpoint"],
        (
            "For a dog with increased thirst and urinary accidents, "
            "what diagnostic guidance and repeat-test cautions should be considered?"
        ),
    )
    direct_ka = parse_response_payload(direct_ka_payload)
    assert_no_error_text("Direct Knowledge Assistant query", direct_ka.get("assistant_text"))
    if not isinstance(direct_ka.get("assistant_text"), str) or not direct_ka["assistant_text"].strip():
        raise RuntimeError("Direct Knowledge Assistant query did not return assistant text.")

    prompt = build_runtime_prompt(request_id, payload_json)

    deadline = time.time() + args.wait_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            supervisor_payload = call_supervisor(
                client,
                runtime_config["supervisor_endpoint"],
                prompt,
            )
            supervisor_parsed = parse_response_payload(supervisor_payload)
            supervisor_recommendation, supervisor_tool_calls = validate_recommendation_response(
                "Supervisor",
                supervisor_parsed,
            )

            custom_payload = call_app_responses(
                client,
                runtime_config["custom_agent_app_url"],
                prompt,
            )
            custom_parsed = parse_response_payload(custom_payload)
            custom_recommendation, custom_tool_calls = validate_recommendation_response(
                "Custom agent",
                custom_parsed,
            )

            print(
                json.dumps(
                    {
                        "request_id": request_id,
                        "supervisor_response_id": supervisor_parsed.get("response_id"),
                        "custom_agent_response_id": custom_parsed.get("response_id"),
                        "supervisor_endpoint": runtime_config["supervisor_endpoint"],
                        "custom_agent_app_url": runtime_config["custom_agent_app_url"],
                        "supervisor_tool_call_names": sorted(supervisor_tool_calls),
                        "custom_agent_tool_call_names": sorted(custom_tool_calls),
                        "direct_triage": parsed_direct_triage,
                        "direct_pubmed": parsed_direct_pubmed,
                        "direct_history_patient_id": parsed_direct_history.get("patient_id"),
                        "direct_genie_status": direct_genie.get("status"),
                        "supervisor_recommendation": supervisor_recommendation,
                        "custom_agent_recommendation": custom_recommendation,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(20)

    raise RuntimeError(f"Smoke test did not succeed before timeout: {last_error}")


if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)
