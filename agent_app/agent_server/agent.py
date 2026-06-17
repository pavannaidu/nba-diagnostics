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
    "get_patient_history",
    "get_recent_test_audit",
    "similar_case_genie",
    "diagnostic_guidance_ka",
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

Always use the attached tools in this exact sequence:
1. get_patient_history with payload_json set to the full intake payload JSON string
2. get_recent_test_audit with payload_json set to the full intake payload JSON string
3. similar_case_genie
4. diagnostic_guidance_ka
5. get_test_metadata only after selecting the primary action and any visible alternatives, using a JSON array string of the visible test codes

Use patient history and recent duplicate-test audit as hard constraints.
Use Genie for structured similar-case and cohort evidence.
Use the knowledge assistant for diagnostic workflow guidance, repeat-test cautions, and abstain conditions.

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
            get_patient_history,
            get_recent_test_audit,
            similar_case_genie,
            diagnostic_guidance_ka,
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
