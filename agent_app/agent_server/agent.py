from __future__ import annotations

import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, AsyncGenerator

import mlflow
import requests
from agents import Agent, Runner, function_tool, set_default_openai_api, set_default_openai_client
from agents.tracing import set_trace_processors
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.utils import get_session_id, process_agent_stream_events


LOGGER = logging.getLogger(__name__)
TOOL_ORDER = [
    "triage_intake",
    "get_patient_history",
    "get_recent_test_audit",
    "similar_case_genie",
    "diagnostic_guidance_ka",
    "query_pubmed",
    "get_test_metadata",
]
DEFAULT_CUSTOM_AGENT_MODEL_ID = "databricks-claude-sonnet-4-5"
DEFAULT_CUSTOM_AGENT_MODELS = [
    {"id": "databricks-claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
]

set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")


def configure_mlflow_tracing() -> None:
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    experiment_id = os.getenv("MLFLOW_EXPERIMENT_ID")
    experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME")
    try:
        if experiment_id:
            mlflow.set_experiment(experiment_id=experiment_id)
        elif experiment_name:
            mlflow.set_experiment(experiment_name=experiment_name)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Unable to configure MLflow experiment for tracing: %s", exc)

    # AgentServer owns the request trace. OpenAI autologging creates additional
    # spans for chat-completion payloads whose list-valued fields currently fail
    # Databricks span finalization, which drops the whole trace.
    mlflow.openai.autolog(disable=True)
    set_trace_processors([])
    logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)


configure_mlflow_tracing()


def trace_preview(value: Any, *, limit: int = 4000) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def set_span_preview(span: Any, field: str, value: Any, *, limit: int = 4000) -> None:
    span.set_attribute(f"{field}.preview", trace_preview(value, limit=limit))


def normalize_host(host: str | None) -> str | None:
    if not host:
        return None
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"https://{host}"


def quote_sql(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(value)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def number_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def triage_intake_payload(payload_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return {
            "derived_cohort": "unknown",
            "active_signals": [],
            "signal_count": 0,
            "acuity": "unknown",
            "missing_fields": ["payload_json"],
            "quality_flags": ["invalid_json"],
            "routing_notes": [
                "Intake JSON could not be parsed. Request structured intake before recommending."
            ],
        }

    if not isinstance(payload, dict):
        payload = {}

    active_signals: list[str] = []
    boolean_signal_fields = [
        "vomiting",
        "diarrhea",
        "urinary_accidents",
        "straining_to_urinate",
        "increased_thirst",
        "increased_hunger",
        "lethargy",
        "recent_diet_change",
        "previous_same_issue",
    ]
    for field_name in boolean_signal_fields:
        if is_truthy(payload.get(field_name)):
            active_signals.append(field_name)

    appetite_change = clean_text(payload.get("appetite_change")).lower()
    if appetite_change and appetite_change not in {"stable", "normal", "unchanged", "none"}:
        active_signals.append("appetite_change")

    weight_change_pct = number_value(payload.get("weight_change_pct"))
    if weight_change_pct is not None and abs(weight_change_pct) >= 2:
        active_signals.append("weight_change")

    missing_fields = [
        field_name
        for field_name in ("patient_id", "as_of_ts", "presenting_complaint", "severity")
        if not is_present(payload.get(field_name))
    ]

    owner_note = clean_text(payload.get("owner_note"))
    clinician_note = clean_text(payload.get("clinician_note"))
    if not active_signals and not owner_note and not clinician_note:
        missing_fields.append("clinical_context")

    quality_flags = [f"missing_{field_name}" for field_name in missing_fields]
    if not payload:
        quality_flags.append("empty_payload")
    if "clinical_context" in missing_fields:
        quality_flags.append("no_clinical_context")
    if not is_present(payload.get("seed_visit_id")):
        quality_flags.append("fresh_visit")
    if payload.get("recent_test_overrides"):
        quality_flags.append("recent_overrides_present")

    allowed_cohorts = {
        "renal_urinary",
        "gi",
        "endocrine_metabolic",
        "wellness",
        "general",
    }
    cohort_hint = clean_text(payload.get("cohort_hint")).lower()
    if cohort_hint in allowed_cohorts:
        derived_cohort = cohort_hint
    elif (
        is_truthy(payload.get("increased_thirst"))
        and is_truthy(payload.get("increased_hunger"))
    ) or "weight_change" in active_signals:
        derived_cohort = "endocrine_metabolic"
    elif any(
        is_truthy(payload.get(field_name))
        for field_name in ("urinary_accidents", "straining_to_urinate", "increased_thirst")
    ):
        derived_cohort = "renal_urinary"
    elif any(
        is_truthy(payload.get(field_name))
        for field_name in ("vomiting", "diarrhea", "recent_diet_change")
    ):
        derived_cohort = "gi"
    elif not active_signals:
        derived_cohort = "wellness"
    else:
        derived_cohort = "general"

    severity = clean_text(payload.get("severity")).lower()
    urgency = clean_text(payload.get("urgency_level")).lower()
    signal_count = len(active_signals)
    high_severity = severity in {"high", "severe", "critical", "emergency"}
    high_urgency = urgency in {"urgent", "emergency", "immediate", "stat"}
    moderate_severity = severity in {"moderate", "medium"}
    moderate_urgency = urgency in {"soon", "priority", "expedited"}
    if high_severity or high_urgency or (is_truthy(payload.get("lethargy")) and signal_count >= 3):
        acuity = "high"
    elif moderate_severity or moderate_urgency or signal_count >= 2:
        acuity = "moderate"
    else:
        acuity = "low"

    routing_notes = [f"Use {derived_cohort.replace('_', '/')} guidance."]
    if missing_fields:
        routing_notes.append("Missing intake fields: " + ", ".join(missing_fields) + ".")
    if "clinical_context" in missing_fields:
        routing_notes.append(
            "Insufficient intake detail. Consider abstain or request more context if evidence is weak."
        )
    if payload.get("recent_test_overrides"):
        routing_notes.append("Use recent-test audit as a hard duplicate constraint.")
    if acuity == "high":
        routing_notes.append("Prioritize urgent or high-acuity diagnostic guidance.")

    return {
        "derived_cohort": derived_cohort,
        "active_signals": active_signals,
        "signal_count": signal_count,
        "acuity": acuity,
        "missing_fields": missing_fields,
        "quality_flags": quality_flags,
        "routing_notes": routing_notes,
    }


@lru_cache(maxsize=1)
def workspace_client() -> WorkspaceClient:
    host = normalize_host(os.getenv("DATABRICKS_HOST"))
    return WorkspaceClient(host=host) if host else WorkspaceClient()


def request_headers() -> dict[str, str]:
    headers = workspace_client().config.authenticate()
    headers["Content-Type"] = "application/json"
    return headers


def api_host() -> str:
    host = normalize_host(workspace_client().config.host or os.getenv("DATABRICKS_HOST"))
    if not host:
        raise RuntimeError("Unable to resolve Databricks workspace host.")
    return host.rstrip("/")


def execute_statement(statement: str, *, timeout_seconds: int = 90) -> dict[str, Any]:
    response = requests.post(
        f"{api_host()}/api/2.0/sql/statements/",
        headers=request_headers(),
        json={
            "statement": statement,
            "warehouse_id": os.getenv("DATABRICKS_WAREHOUSE_ID", ""),
            "wait_timeout": "50s",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    state = payload.get("status", {}).get("state")
    if state != "SUCCEEDED":
        raise RuntimeError(payload.get("status", {}).get("error", {}).get("message", str(payload)))
    return payload


def fetch_rows(statement: str) -> list[list[Any]]:
    return execute_statement(statement).get("result", {}).get("data_array", [])


def fetch_json_scalar(statement: str) -> Any:
    rows = fetch_rows(statement)
    if not rows or not rows[0]:
        return None
    return maybe_json(rows[0][0])


def run_traced_tool(name: str, inputs: dict[str, Any], callback) -> str:
    with mlflow.start_span(
        name=name,
        span_type="TOOL",
        attributes={"tool.name": name},
    ) as span:
        set_span_preview(span, "inputs", inputs, limit=2000)
        output = callback()
        set_span_preview(span, "outputs", output)
        return output


@lru_cache(maxsize=1)
def runtime_config() -> dict[str, str]:
    catalog = os.getenv("DATABRICKS_CATALOG", "pavan_naidu")
    schema = os.getenv("DATABRICKS_SCHEMA", "nba")
    rows = fetch_rows(f"SELECT config_key, config_value FROM {catalog}.{schema}.runtime_config")
    return {
        str(row[0]): str(row[1])
        for row in rows
        if row and row[0] is not None and row[1] is not None
    }


def custom_agent_models() -> list[dict[str, str]]:
    raw_options = runtime_config().get("custom_agent_model_options")
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


def default_custom_agent_model_id(model_options: list[dict[str, str]] | None = None) -> str:
    model_options = model_options if model_options is not None else custom_agent_models()
    configured_default = (
        runtime_config().get("custom_agent_default_model_id")
        or os.getenv("NBA_CUSTOM_AGENT_LLM_ENDPOINT")
        or DEFAULT_CUSTOM_AGENT_MODEL_ID
    )
    option_ids = {option["id"] for option in model_options}
    if configured_default in option_ids:
        return configured_default
    return model_options[0]["id"] if model_options else DEFAULT_CUSTOM_AGENT_MODEL_ID


def request_custom_model_id(request: ResponsesAgentRequest) -> str:
    custom_inputs = getattr(request, "custom_inputs", None)
    requested_model = None
    if isinstance(custom_inputs, dict):
        requested_model = custom_inputs.get("model_id")

    model_options = custom_agent_models()
    resolved_model_id = str(requested_model or default_custom_agent_model_id(model_options)).strip()
    option_ids = {option["id"] for option in model_options}
    if resolved_model_id not in option_ids:
        allowed = ", ".join(sorted(option_ids))
        raise RuntimeError(f"Unknown custom agent model: {resolved_model_id}. Allowed models: {allowed}.")
    return resolved_model_id


def parse_responses_api_body(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return payload

    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            continue
        parsed = maybe_json(raw_data)
        if isinstance(parsed, dict):
            events.append(parsed)

    for event in reversed(events):
        if event.get("output") is not None or event.get("id") is not None:
            return event
    raise RuntimeError(response.text.strip()[:500] or "Unreadable Responses API payload.")


def call_responses_endpoint(endpoint: str, prompt: str) -> dict[str, Any]:
    request_body = {
        "model": endpoint,
        "stream": False,
        "input": [{"role": "user", "content": prompt}],
    }
    candidate_paths = [
        "/api/2.0/serving-endpoints/responses",
        "/serving-endpoints/responses",
    ]
    last_error: Exception | None = None
    for path in candidate_paths:
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    f"{api_host()}{path}",
                    headers=request_headers(),
                    json=request_body,
                    timeout=(
                        int(os.getenv("NBA_SUPERVISOR_CONNECT_TIMEOUT_SECONDS", "15")),
                        int(os.getenv("NBA_SUPERVISOR_READ_TIMEOUT_SECONDS", "300")),
                    ),
                )
                response.raise_for_status()
                return parse_responses_api_body(response)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 502, 503, 504} and attempt < 3:
                    time.sleep(min(2 ** (attempt - 1), 5))
                    continue
                last_error = exc
                if status_code in {400, 404, 405}:
                    break
                raise
            except requests.Timeout:
                raise
            except requests.RequestException as exc:
                last_error = exc
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("Responses API call did not return a response.")


def extract_assistant_text(payload: dict[str, Any]) -> str:
    messages: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        text_parts = [
            content.get("text")
            for content in item.get("content", [])
            if content.get("type") == "output_text" and content.get("text")
        ]
        if text_parts:
            messages.append("\n".join(text_parts))
    return messages[-1] if messages else json.dumps(payload, default=str)[:2000]


def query_genie_space(question: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
    space_id = runtime_config().get("genie_space_id")
    if not space_id:
        raise RuntimeError("genie_space_id is missing from runtime_config.")

    started = requests.post(
        f"{api_host()}/api/2.0/genie/spaces/{space_id}/start-conversation",
        headers=request_headers(),
        json={"content": question},
        timeout=30,
    )
    started.raise_for_status()
    payload = started.json()
    conversation_id = payload.get("conversation_id")
    if not conversation_id and isinstance(payload.get("conversation"), dict):
        conversation_id = payload["conversation"].get("conversation_id") or payload["conversation"].get("id")
    message_id = payload.get("message_id")
    if not message_id and isinstance(payload.get("message"), dict):
        message_id = payload["message"].get("message_id") or payload["message"].get("id")
    if not conversation_id or not message_id:
        raise RuntimeError(f"Unable to resolve Genie conversation identifiers: {payload}")

    deadline = time.time() + timeout_seconds
    last_message: dict[str, Any] = payload
    while time.time() < deadline:
        response = requests.get(
            (
                f"{api_host()}/api/2.0/genie/spaces/{space_id}/conversations/"
                f"{conversation_id}/messages/{message_id}"
            ),
            headers=request_headers(),
            timeout=30,
        )
        response.raise_for_status()
        last_message = response.json()
        status = str(last_message.get("status", "")).upper()
        if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}:
            return last_message
        time.sleep(3)
    raise RuntimeError(f"Genie conversation did not complete within {timeout_seconds} seconds.")


@function_tool
def triage_intake(payload_json: str) -> str:
    """Triage editable intake JSON into cohort, acuity, active signals, missing fields, and routing notes."""
    return run_traced_tool(
        "triage_intake",
        {"payload_json": payload_json},
        lambda: json.dumps(triage_intake_payload(payload_json), sort_keys=True),
    )


@function_tool
def get_patient_history(payload_json: str) -> str:
    """Fetch compact longitudinal patient history using the full intake payload JSON string."""
    catalog = os.getenv("DATABRICKS_CATALOG", "pavan_naidu")
    schema = os.getenv("DATABRICKS_SCHEMA", "nba")
    return run_traced_tool(
        "get_patient_history",
        {"payload_json": payload_json},
        lambda: json.dumps(
            fetch_json_scalar(
                f"SELECT {catalog}.{schema}.runtime_get_patient_history({quote_sql(payload_json)})"
            ),
            default=str,
        ),
    )


@function_tool
def get_recent_test_audit(payload_json: str) -> str:
    """Fetch recent same-test timing and duplicate suppression facts using the full intake payload JSON string."""
    catalog = os.getenv("DATABRICKS_CATALOG", "pavan_naidu")
    schema = os.getenv("DATABRICKS_SCHEMA", "nba")
    return run_traced_tool(
        "get_recent_test_audit",
        {"payload_json": payload_json},
        lambda: json.dumps(
            fetch_json_scalar(
                f"SELECT {catalog}.{schema}.runtime_get_recent_test_audit({quote_sql(payload_json)})"
            ),
            default=str,
        ),
    )


@function_tool
def similar_case_genie(question: str) -> str:
    """Retrieve structured cohort and similar-case evidence from curated gold views."""
    return run_traced_tool(
        "similar_case_genie",
        {"question": question},
        lambda: json.dumps(query_genie_space(question), default=str)[:6000],
    )


@function_tool
def diagnostic_guidance_ka(question: str) -> str:
    """Retrieve diagnostic workflow guidance and repeat-test cautions from curated dog guidance documents."""
    def call_guidance() -> str:
        endpoint = runtime_config().get("knowledge_assistant_endpoint")
        if not endpoint:
            raise RuntimeError("knowledge_assistant_endpoint is missing from runtime_config.")
        return extract_assistant_text(call_responses_endpoint(endpoint, question))[:6000]

    return run_traced_tool(
        "diagnostic_guidance_ka",
        {"question": question},
        call_guidance,
    )


@function_tool
def query_pubmed(
    search_query: str,
    max_results: int = 5,
    min_publication_year: int = 0,
) -> str:
    """Query the governed PubMed UC function for supplementary literature context."""
    catalog = os.getenv("DATABRICKS_CATALOG", "pavan_naidu")
    schema = os.getenv("DATABRICKS_SCHEMA", "nba")
    try:
        result_limit = max(1, min(int(max_results), 5))
    except (TypeError, ValueError):
        result_limit = 5
    try:
        min_year = int(min_publication_year)
    except (TypeError, ValueError):
        min_year = 0
    cleaned_query = str(search_query or "")
    return run_traced_tool(
        "query_pubmed",
        {
            "search_query": cleaned_query,
            "max_results": result_limit,
            "min_publication_year": min_year,
        },
        lambda: json.dumps(
            fetch_json_scalar(
                f"SELECT {catalog}.{schema}.runtime_query_pubmed("
                f"{quote_sql(cleaned_query)}, {result_limit}, {min_year})"
            ),
            default=str,
        ),
    )


@function_tool
def get_test_metadata(test_codes: str) -> str:
    """Fetch diagnostic catalog metadata using a JSON array string of selected and visible test codes."""
    catalog = os.getenv("DATABRICKS_CATALOG", "pavan_naidu")
    schema = os.getenv("DATABRICKS_SCHEMA", "nba")
    return run_traced_tool(
        "get_test_metadata",
        {"test_codes": test_codes},
        lambda: json.dumps(
            fetch_json_scalar(
                f"SELECT {catalog}.{schema}.runtime_get_test_metadata({quote_sql(test_codes)})"
            ),
            default=str,
        ),
    )


def build_agent_instructions() -> str:
    return """
You answer one question: for the current dog visit intake, what diagnostic action should we recommend next?

Always use the core diagnostic tools in this exact sequence:
1. triage_intake with payload_json set to the full intake payload JSON string
2. get_patient_history with payload_json set to the full intake payload JSON string
3. get_recent_test_audit with payload_json set to the full intake payload JSON string
4. similar_case_genie
5. diagnostic_guidance_ka
6. query_pubmed with a concise veterinary literature query, max_results set to 3, and min_publication_year set to 2018
7. get_test_metadata only after selecting the primary action and any visible alternatives, using a JSON array string of the visible test codes

Use triage output for derived cohort, acuity, active signals, missing fields, quality flags, and routing notes.
Use patient history and recent duplicate-test audit as hard constraints.
Use Genie for structured similar-case and cohort evidence.
Use the knowledge assistant for diagnostic workflow guidance, repeat-test cautions, and abstain conditions.
Use PubMed only as supplementary literature context. Do not treat PubMed as patient-specific evidence or use it to override duplicate-test suppression.
When mentioning PubMed, cite only PMIDs returned by query_pubmed. Do not invent PubMed citations, URLs, or markdown links.
If query_pubmed returns zero articles, say PubMed returned no matching records or omit PubMed from evidence_sources.

Return valid JSON only with:
- visit_summary { derived_cohort }
- recommendation { action_type, test_code, test_name, summary, reasons, evidence_chips }
- why_not_selected [{ test_code, test_name, disposition, why_not_selected }]
- evidence_sources [{ source_type, source_label, summary }]

Requirements:
- action_type: exactly "recommend_test" or "abstain"
- summary: one sentence, <= 140 characters
- reasons: 2 to 4 short bullets
- evidence_chips: 3 to 5 compact fact labels
- why_not_selected: at most 3 alternatives, each with one short sentence

If evidence is insufficient or recent duplicate testing removes the strongest option, use action_type exactly "abstain".
""".strip()


def create_agent(model_id: str | None = None) -> Agent:
    return Agent(
        name="IDEXX Custom Diagnostic Agent",
        instructions=build_agent_instructions(),
        model=model_id or default_custom_agent_model_id(),
        tools=[
            triage_intake,
            get_patient_history,
            get_recent_test_audit,
            similar_case_genie,
            diagnostic_guidance_ka,
            query_pubmed,
            get_test_metadata,
        ],
    )


def request_messages(request: ResponsesAgentRequest) -> list[dict[str, Any]]:
    return [item.model_dump() for item in request.input]


def attach_trace_metadata(request: ResponsesAgentRequest, *, handler: str, model_id: str) -> None:
    metadata = {"agent_target_id": "custom_agent", "handler": handler, "custom_agent_model_id": model_id}
    if session_id := get_session_id(request):
        metadata["mlflow.trace.session"] = session_id
    mlflow.update_current_trace(metadata=metadata)


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    messages = request_messages(request)
    model_id = request_custom_model_id(request)
    with mlflow.start_span(
        name="custom_agent.responses",
        span_type="AGENT",
        attributes={"agent_target_id": "custom_agent", "stream": False, "model": model_id},
    ) as span:
        attach_trace_metadata(request, handler="invoke", model_id=model_id)
        set_span_preview(span, "inputs", {"input": messages})
        agent = create_agent(model_id)
        with mlflow.start_span(
            name="openai_agents.runner",
            span_type="AGENT",
            attributes={"model": agent.model, "agent.name": agent.name},
        ) as runner_span:
            set_span_preview(runner_span, "inputs", {"input": messages})
            result = await Runner.run(agent, messages)
            output = [item.to_input_item() for item in result.new_items]
            set_span_preview(runner_span, "outputs", {"output": output})
        set_span_preview(span, "outputs", {"output": output})
    return ResponsesAgentResponse(output=output)


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    messages = request_messages(request)
    model_id = request_custom_model_id(request)
    with mlflow.start_span(
        name="custom_agent.responses_stream",
        span_type="AGENT",
        attributes={"agent_target_id": "custom_agent", "stream": True, "model": model_id},
    ) as span:
        attach_trace_metadata(request, handler="stream", model_id=model_id)
        set_span_preview(span, "inputs", {"input": messages})
        agent = create_agent(model_id)
        emitted_events = 0
        result = Runner.run_streamed(agent, input=messages)
        async for event in process_agent_stream_events(result.stream_events()):
            emitted_events += 1
            yield event
        set_span_preview(span, "outputs", {"emitted_events": emitted_events})
