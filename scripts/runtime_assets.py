#!/usr/bin/env python3
"""Shared helpers for provisioning and smoke testing runtime recommendation assets."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import AppAccessControlRequest, AppPermissionLevel

from deploy_idexx_agent_bricks import build_serialized_space
from deploy_idexx_uc_functions import render_runtime_functions_sql, split_sql_statements


REPO_ROOT = Path(__file__).resolve().parents[1]

SPACE_TITLE = "IDEXX Diagnostic Recommendation Evidence"
SPACE_TITLE_ALIASES = {
    SPACE_TITLE,
    "IDEXX NBA Diagnostics Demo",
}
KA_DISPLAY_NAME = "IDEXX Diagnostic Guidance Assistant"
SUPERVISOR_DISPLAY_NAME = "IDEXX Diagnostic Recommendation Supervisor"
SUPERVISOR_DISPLAY_NAME_ALIASES = {
    SUPERVISOR_DISPLAY_NAME,
    "IDEXX NBA Diagnostics Supervisor",
}
GUIDANCE_SOURCE_DISPLAY_NAME = "Dog Diagnostic Guidance"
GUIDANCE_SOURCE_DESCRIPTION = (
    "Curated dog-only diagnostic workflow guidance used for runtime recommendation grounding."
)
KNOWLEDGE_ASSISTANT_EXAMPLES = [
    {
        "question": (
            "For a mature dog with increased thirst, urinary accidents, decreased appetite, and renal monitoring history, "
            "what guidance should shape the next diagnostic recommendation?"
        ),
        "guidelines": (
            "Use renal_urinary_workup.md and duplicate_suppression_policy.md. Explain when URINALYSIS, SDMA_CHEM17, "
            "URINE_CULTURE, or UPC_RATIO is appropriate, and call out recent-test suppression if the same test was performed recently."
        ),
    },
    {
        "question": (
            "For a dog with vomiting, diarrhea, recent diet change, and moderate severity, what GI diagnostic guidance should be used?"
        ),
        "guidelines": (
            "Use gi_workup.md. Prefer GI_PANEL for broad GI characterization, FECAL_PCR when diarrhea or infectious risk is prominent, "
            "and avoid preventive fecal screening as the lead action for active symptomatic GI disease."
        ),
    },
    {
        "question": (
            "For a dog with increased thirst, increased hunger, weight change, and a recent same endocrine test, "
            "how should endocrine guidance and duplicate suppression affect the answer?"
        ),
        "guidelines": (
            "Use endocrine_metabolic_workup.md and duplicate_suppression_policy.md. Treat recent same-test completion as a hard guardrail; "
            "recommend the next supported action only if current facts justify it, otherwise abstain."
        ),
    },
    {
        "question": (
            "For a current visit with missing complaint, no meaningful symptom signals, and empty clinical notes, "
            "what guidance should the recommendation workflow follow?"
        ),
        "guidelines": (
            "Use abstain_and_needs_input_policy.md. Do not force a diagnostic; identify missing clinical context and support needs_input or abstain behavior."
        ),
    },
]
SUPERVISOR_EXAMPLE_CASES = [
    {
        "request_id": "example-renal-urinary-live-intake",
        "payload": {
            "patient_id": "DOG-0201",
            "seed_visit_id": "VISIT-0201-20260603",
            "dog_name": "Gus",
            "breed": "Dachshund",
            "age_years": 7.7,
            "life_stage": "mature",
            "as_of_ts": "2026-06-03",
            "presenting_complaint": "renal monitoring recheck with increased thirst and urinary accidents",
            "severity": "low",
            "urgency_level": "routine",
            "owner_note": "Owner reports Gus is drinking more, having urinary accidents, and symptoms started about 18 days ago.",
            "clinician_note": "Current history raises urinary tract or renal monitoring questions; prior test timing may change the recommendation.",
            "cohort_hint": "renal_urinary",
            "vomiting": False,
            "diarrhea": False,
            "urinary_accidents": True,
            "straining_to_urinate": False,
            "increased_thirst": True,
            "increased_hunger": False,
            "lethargy": False,
            "recent_diet_change": False,
            "previous_same_issue": True,
            "appetite_change": "decreased",
            "weight_change_pct": -1.6,
            "duration_days": 18,
            "recent_test_overrides": [
                {"test_code": "SDMA_CHEM17", "days_since_same_test": None},
                {"test_code": "URINALYSIS", "days_since_same_test": None},
                {"test_code": "URINE_CULTURE", "days_since_same_test": None},
                {"test_code": "UPC_RATIO", "days_since_same_test": None},
            ],
        },
        "guidelines": (
            "Behave exactly like the app runtime: call triage_intake, get_patient_history, and get_recent_test_audit with the full payload_json, "
            "then similar_case_genie, diagnostic_guidance_ka, query_pubmed, and get_test_metadata for selected codes. Prefer one renal/urinary action "
            "supported by history, duplicate audit, Genie, and KA. Return JSON only with summary, 2-4 reasons, 3-5 evidence_chips, "
            "at most 3 why_not_selected alternatives, and no scores. Cite only PubMed PMIDs returned by query_pubmed."
        ),
    },
    {
        "request_id": "example-gi-edited-intake",
        "payload": {
            "patient_id": "DOG-0314",
            "seed_visit_id": "VISIT-0314-20260530",
            "dog_name": "Milo",
            "breed": "Mixed Breed",
            "age_years": 5.4,
            "life_stage": "adult",
            "as_of_ts": "2026-05-30",
            "presenting_complaint": "vomiting and diarrhea after recent diet change",
            "severity": "moderate",
            "urgency_level": "soon",
            "owner_note": "Owner reports two days of vomiting, loose stool, and lower appetite after changing food.",
            "clinician_note": "Edited intake emphasizes active GI signs; no known recent GI panel or fecal PCR in the current window.",
            "cohort_hint": "gi",
            "vomiting": True,
            "diarrhea": True,
            "urinary_accidents": False,
            "straining_to_urinate": False,
            "increased_thirst": False,
            "increased_hunger": False,
            "lethargy": True,
            "recent_diet_change": True,
            "previous_same_issue": False,
            "appetite_change": "decreased",
            "weight_change_pct": -0.8,
            "duration_days": 2,
            "recent_test_overrides": [
                {"test_code": "GI_PANEL", "days_since_same_test": None},
                {"test_code": "FECAL_PCR", "days_since_same_test": None},
                {"test_code": "FECAL_SCREEN", "days_since_same_test": 220},
                {"test_code": "WELLNESS_PANEL", "days_since_same_test": 180},
            ],
        },
        "guidelines": (
            "Treat the edited intake as source of truth, not the seed label. Use tools in required order. Recommend one GI-oriented action "
            "only if supported by current symptoms and evidence. Prefer broader GI workup or infectious diarrhea testing over preventive fecal screening. "
            "Return concise JSON with alternatives explaining why stool-only or wellness options were not selected."
        ),
    },
    {
        "request_id": "example-duplicate-suppression-live-intake",
        "payload": {
            "patient_id": "DOG-0418",
            "seed_visit_id": "VISIT-0418-20260530",
            "dog_name": "Zoe",
            "breed": "Labrador Retriever",
            "age_years": 9.1,
            "life_stage": "senior",
            "as_of_ts": "2026-05-30",
            "presenting_complaint": "increased thirst and hunger with weight change",
            "severity": "low",
            "urgency_level": "routine",
            "owner_note": "Owner reports increased drinking, increased hunger, and gradual weight change over the last month.",
            "clinician_note": "Consider endocrine or metabolic screening, but user-entered recency indicates fructosamine was completed recently.",
            "cohort_hint": "endocrine_metabolic",
            "vomiting": False,
            "diarrhea": False,
            "urinary_accidents": False,
            "straining_to_urinate": False,
            "increased_thirst": True,
            "increased_hunger": True,
            "lethargy": False,
            "recent_diet_change": False,
            "previous_same_issue": True,
            "appetite_change": "increased",
            "weight_change_pct": -4.2,
            "duration_days": 30,
            "recent_test_overrides": [
                {"test_code": "FRUCTOSAMINE", "days_since_same_test": 12},
                {"test_code": "THYROID_PANEL", "days_since_same_test": None},
                {"test_code": "CORTISOL_SCREEN", "days_since_same_test": None},
                {"test_code": "SDMA_CHEM17", "days_since_same_test": 140},
            ],
        },
        "guidelines": (
            "The duplicate audit is a hard constraint. Do not recommend FRUCTOSAMINE as the lead action if it is inside the repeat-test window. "
            "Use Genie and KA to decide whether THYROID_PANEL, CORTISOL_SCREEN, or abstain is better supported. Include the suppressed duplicate "
            "in why_not_selected with a short recency explanation."
        ),
    },
    {
        "request_id": "example-needs-input-live-intake",
        "payload": {
            "patient_id": "DOG-UNKNOWN",
            "seed_visit_id": None,
            "dog_name": "Unknown",
            "breed": None,
            "age_years": None,
            "life_stage": None,
            "as_of_ts": "2026-06-10",
            "presenting_complaint": "",
            "severity": "",
            "urgency_level": "routine",
            "owner_note": "",
            "clinician_note": "",
            "cohort_hint": None,
            "vomiting": False,
            "diarrhea": False,
            "urinary_accidents": False,
            "straining_to_urinate": False,
            "increased_thirst": False,
            "increased_hunger": False,
            "lethargy": False,
            "recent_diet_change": False,
            "previous_same_issue": False,
            "appetite_change": "stable",
            "weight_change_pct": 0,
            "duration_days": 0,
            "recent_test_overrides": [],
        },
        "guidelines": (
            "Do not force a diagnostic. If the app layer has not already rejected the request, use the available tools only as needed, "
            "then return valid JSON with action_type abstain, no test_code, a short summary that more intake detail is needed, "
            "and reasons naming missing complaint, severity, and clinical context. Do not invent patient history."
        ),
    },
    {
        "request_id": "example-wellness-live-intake",
        "payload": {
            "patient_id": "DOG-0250",
            "seed_visit_id": "VISIT-0250-20260601",
            "dog_name": "Finn",
            "breed": "Golden Retriever",
            "age_years": 10.2,
            "life_stage": "senior",
            "as_of_ts": "2026-06-01",
            "presenting_complaint": "routine senior preventive visit",
            "severity": "low",
            "urgency_level": "routine",
            "owner_note": "Owner reports Finn is stable with no active vomiting, diarrhea, urinary accidents, or appetite change.",
            "clinician_note": "Senior wellness visit; evaluate preventive screening while avoiding recently completed duplicate testing.",
            "cohort_hint": "wellness",
            "vomiting": False,
            "diarrhea": False,
            "urinary_accidents": False,
            "straining_to_urinate": False,
            "increased_thirst": False,
            "increased_hunger": False,
            "lethargy": False,
            "recent_diet_change": False,
            "previous_same_issue": False,
            "appetite_change": "stable",
            "weight_change_pct": 0.1,
            "duration_days": 0,
            "recent_test_overrides": [
                {"test_code": "WELLNESS_PANEL", "days_since_same_test": None},
                {"test_code": "HEARTWORM_4DX", "days_since_same_test": 400},
                {"test_code": "FECAL_SCREEN", "days_since_same_test": 380},
            ],
        },
        "guidelines": (
            "Use the same runtime tool sequence. Because symptom burden is low, favor preventive wellness guidance and similar-case evidence. "
            "Recommend one preventive action only if not recently duplicated; otherwise abstain or explain why another preventive test is better supported."
        ),
    },
    {
        "request_id": "example-new-patient-live-intake",
        "payload": {
            "patient_id": "DOG-NEW-001",
            "seed_visit_id": None,
            "dog_name": "Scout",
            "breed": "Beagle",
            "age_years": 4.0,
            "life_stage": "adult",
            "as_of_ts": "2026-06-10",
            "presenting_complaint": "diarrhea with lethargy",
            "severity": "moderate",
            "urgency_level": "soon",
            "owner_note": "Owner reports three days of diarrhea, mild lethargy, and no known prior diagnostics at this clinic.",
            "clinician_note": "Unknown patient history; rely on current intake, duplicate audit, Genie cohort evidence, and KA guidance.",
            "cohort_hint": "gi",
            "vomiting": False,
            "diarrhea": True,
            "urinary_accidents": False,
            "straining_to_urinate": False,
            "increased_thirst": False,
            "increased_hunger": False,
            "lethargy": True,
            "recent_diet_change": False,
            "previous_same_issue": False,
            "appetite_change": "decreased",
            "weight_change_pct": -1.0,
            "duration_days": 3,
            "recent_test_overrides": [
                {"test_code": "GI_PANEL", "days_since_same_test": None},
                {"test_code": "FECAL_PCR", "days_since_same_test": None},
                {"test_code": "FECAL_SCREEN", "days_since_same_test": None},
            ],
        },
        "guidelines": (
            "Unknown patient history should not block a recommendation if the current intake is sufficient. Still call triage_intake, get_patient_history, and duplicate audit, "
            "then use Genie, KA, and query_pubmed to choose one GI-oriented action or abstain. Do not invent longitudinal history or PubMed citations."
        ),
    },
]
SUPERVISOR_CONNECT_TIMEOUT_SECONDS = int(
    os.getenv("NBA_SUPERVISOR_CONNECT_TIMEOUT_SECONDS", "15")
)
SUPERVISOR_READ_TIMEOUT_SECONDS = int(
    os.getenv("NBA_SUPERVISOR_READ_TIMEOUT_SECONDS", "300")
)

TOOL_IDS = {
    "intake_triage": "triage_intake",
    "patient_history": "get_patient_history",
    "recent_test_audit": "get_recent_test_audit",
    "similar_case_genie": "similar_case_genie",
    "diagnostic_guidance_ka": "diagnostic_guidance_ka",
    "query_pubmed": "query_pubmed",
    "test_metadata": "get_test_metadata",
}

RUNTIME_TABLES = [
    "patients",
    "visits",
    "diagnostic_history",
    "treatments_history",
    "diagnostic_catalog",
    "live_intake_requests",
    "runtime_config",
]
RUNTIME_VIEWS = [
    "visit_case_packet_gold",
]
RUNTIME_FUNCTIONS = [
    "runtime_triage_intake",
    "runtime_query_pubmed",
    "runtime_get_patient_history",
    "runtime_get_recent_test_audit",
    "runtime_get_test_metadata",
]

GENIE_VIEW_NAMES = {
    "visit_case_packet_gold",
    "visit_candidate_features_gold",
    "visit_recommendation_gold",
}


@dataclass(frozen=True)
class RuntimePrincipal:
    acl_field: str
    name: str

    @property
    def sql_identifier(self) -> str:
        return f"`{self.name.replace('`', '``')}`"


def normalize_host(host: str | None) -> str | None:
    if not host:
        return None
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"https://{host}"


def quote_sql(value: Any) -> str:
    if value is None:
        return "NULL"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


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


def build_supervisor_example_question(request_id: str, payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return f"""
You are orchestrating a live dog diagnostic recommendation request `{request_id}`.

Use this intake payload as the source of truth:
{payload_json}

Use the attached tools in this sequence:
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


def build_supervisor_examples() -> list[dict[str, str]]:
    return [
        {
            "question": build_supervisor_example_question(case["request_id"], case["payload"]),
            "guidelines": case["guidelines"],
        }
        for case in SUPERVISOR_EXAMPLE_CASES
    ]


def build_workspace_client(profile: str | None = None) -> WorkspaceClient:
    if os.getenv("DATABRICKS_RUNTIME_VERSION") or os.getenv("DB_IS_DRIVER"):
        host = normalize_host(os.getenv("DATABRICKS_HOST"))
        return WorkspaceClient(host=host) if host else WorkspaceClient()

    explicit_host = normalize_host(os.getenv("DATABRICKS_HOST"))
    explicit_token = os.getenv("DATABRICKS_TOKEN")
    if explicit_host and explicit_token:
        return WorkspaceClient(host=explicit_host, token=explicit_token)

    os.environ.setdefault("DATABRICKS_AUTH_STORAGE", "plaintext")
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def api_json(
    client: WorkspaceClient,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    headers = {"Content-Type": "application/json"} if body is not None else None
    return client.api_client.do(method, path=path, query=query, body=body, headers=headers)


def execute_statement(
    client: WorkspaceClient,
    warehouse_id: str,
    statement: str,
) -> dict[str, Any]:
    payload = api_json(
        client,
        "POST",
        "/api/2.0/sql/statements/",
        body={
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
        },
    )
    state = payload.get("status", {}).get("state")
    if state != "SUCCEEDED":
        error = payload.get("status", {}).get("error", {}).get("message")
        raise RuntimeError(error or str(payload))
    return payload


def fetch_rows(
    client: WorkspaceClient,
    warehouse_id: str,
    statement: str,
) -> list[list[Any]]:
    payload = execute_statement(client, warehouse_id, statement)
    return payload.get("result", {}).get("data_array", [])


def fetch_scalar(
    client: WorkspaceClient,
    warehouse_id: str,
    statement: str,
) -> Any:
    rows = fetch_rows(client, warehouse_id, statement)
    if not rows or not rows[0]:
        return None
    return rows[0][0]


def render_gold_sql(catalog: str, schema: str) -> str:
    return (REPO_ROOT / "sql" / "create_idexx_demo_gold.sql").read_text().replace(
        "pavan_naidu.nba",
        f"{catalog}.{schema}",
    )


def apply_runtime_sql(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
) -> None:
    statements = split_sql_statements(render_gold_sql(catalog, schema))
    statements.extend(split_sql_statements(render_runtime_functions_sql(catalog, schema)))
    for statement in statements:
        execute_statement(client, warehouse_id, statement)


def extract_list(payload: Any, preferred_key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        preferred_key,
        "items",
        "results",
        "objects",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def stage_guidance_documents(
    client: WorkspaceClient,
    catalog: str,
    schema: str,
    volume_name: str,
) -> str:
    source_dir = REPO_ROOT / "knowledge" / "guidance"
    volume_dir = f"/Volumes/{catalog}/{schema}/{volume_name}/guidance"
    try:
        client.files.get_directory_metadata(volume_dir)
    except Exception:  # noqa: BLE001
        client.files.create_directory(volume_dir)

    # Knowledge Assistant sources only support document formats. Examples are
    # seeded through the AgentBricks examples API, not indexed as source files.
    for source_path in sorted(source_dir.glob("*.md")):
        client.files.upload(
            f"{volume_dir}/{source_path.name}",
            BytesIO(source_path.read_bytes()),
            overwrite=True,
        )

    for file_info in client.files.list_directory_contents(volume_dir):
        file_path = getattr(file_info, "path", None)
        if file_path and file_path.endswith(".json"):
            client.files.delete(file_path)

    return volume_dir


def list_genie_spaces(client: WorkspaceClient) -> list[dict[str, Any]]:
    payload = api_json(client, "GET", "/api/2.0/genie/spaces")
    return extract_list(payload, "spaces")


def get_genie_space(
    client: WorkspaceClient,
    space_id: str,
    *,
    include_serialized: bool = False,
) -> dict[str, Any]:
    query = {"include_serialized_space": "true"} if include_serialized else None
    payload = api_json(client, "GET", f"/api/2.0/genie/spaces/{space_id}", query=query)
    return payload if isinstance(payload, dict) else {}


def query_genie_space(
    client: WorkspaceClient,
    space_id: str,
    question: str,
    *,
    timeout_seconds: int = 120,
    poll_interval_seconds: int = 3,
) -> dict[str, Any]:
    started = api_json(
        client,
        "POST",
        f"/api/2.0/genie/spaces/{space_id}/start-conversation",
        body={"content": question},
    )
    if not isinstance(started, dict):
        raise RuntimeError("Genie conversation did not return a valid response.")

    conversation_id = started.get("conversation_id")
    if not conversation_id and isinstance(started.get("conversation"), dict):
        conversation_id = started["conversation"].get("conversation_id") or started["conversation"].get("id")

    message_id = started.get("message_id")
    if not message_id and isinstance(started.get("message"), dict):
        message_id = started["message"].get("message_id") or started["message"].get("id")

    if not conversation_id or not message_id:
        raise RuntimeError(f"Unable to resolve Genie conversation identifiers: {started}")

    deadline = time.time() + timeout_seconds
    last_message: dict[str, Any] = started
    while time.time() < deadline:
        message = api_json(
            client,
            "GET",
            (
                f"/api/2.0/genie/spaces/{space_id}/conversations/"
                f"{conversation_id}/messages/{message_id}"
            ),
        )
        if isinstance(message, dict):
            last_message = message
        status = str(last_message.get("status", "")).upper()
        if status == "COMPLETED":
            return last_message
        if status in {"FAILED", "CANCELLED", "TIMEOUT"}:
            return last_message
        time.sleep(poll_interval_seconds)

    raise RuntimeError(
        f"Genie conversation did not complete within {timeout_seconds} seconds."
    )


def ensure_genie_space(
    client: WorkspaceClient,
    catalog: str,
    schema: str,
    warehouse_id: str,
    parent_path: str,
) -> dict[str, Any]:
    description = (
        "Structured evidence workspace for synthetic dog diagnostic recommendation analysis.\n\n"
        "Use this space to inspect similar cases, abstain patterns, repeat-test behavior, "
        "and cohort-level diagnostic tendencies."
    )
    serialized_space = build_serialized_space(catalog, schema)
    existing = next(
        (space for space in list_genie_spaces(client) if space.get("title") in SPACE_TITLE_ALIASES),
        None,
    )
    if existing:
        details = get_genie_space(client, existing["space_id"], include_serialized=True)
        payload = api_json(
            client,
            "PATCH",
            f"/api/2.0/genie/spaces/{existing['space_id']}",
            body={
                "description": description,
                "etag": details.get("etag"),
                "serialized_space": serialized_space,
                "title": SPACE_TITLE,
                "warehouse_id": warehouse_id,
            },
        )
        return payload if isinstance(payload, dict) else details

    created = api_json(
        client,
        "POST",
        "/api/2.0/genie/spaces",
        body={
            "description": description,
            "parent_path": parent_path,
            "serialized_space": serialized_space,
            "title": SPACE_TITLE,
            "warehouse_id": warehouse_id,
        },
    )
    return created if isinstance(created, dict) else {}


def list_knowledge_assistants(client: WorkspaceClient) -> list[dict[str, Any]]:
    payload = api_json(client, "GET", "/api/2.1/knowledge-assistants")
    return extract_list(payload, "knowledge_assistants")


def get_knowledge_assistant(
    client: WorkspaceClient,
    assistant_name: str,
) -> dict[str, Any]:
    payload = api_json(client, "GET", f"/api/2.1/{assistant_name}")
    return payload if isinstance(payload, dict) else {}


def list_knowledge_sources(client: WorkspaceClient, assistant_name: str) -> list[dict[str, Any]]:
    payload = api_json(client, "GET", f"/api/2.1/{assistant_name}/knowledge-sources")
    return extract_list(payload, "knowledge_sources")


def list_agent_examples(client: WorkspaceClient, parent_name: str) -> list[dict[str, Any]]:
    payload = api_json(
        client,
        "GET",
        f"/api/2.1/{parent_name}/examples",
        query={"page_size": 100, "limit": 100},
    )
    return extract_list(payload, "examples")


def seed_agent_examples(
    client: WorkspaceClient,
    parent_name: str,
    examples: list[dict[str, str]],
    *,
    delete_unmanaged: bool = False,
) -> None:
    existing_examples = list_agent_examples(client, parent_name)
    existing_by_question = {}
    for existing_example in existing_examples:
        question = str(existing_example.get("question", "")).strip()
        if question:
            existing_by_question[question] = existing_example

    desired_questions = {example["question"].strip() for example in examples}
    unmanaged_examples = [
        existing_example
        for existing_example in existing_examples
        if str(existing_example.get("question", "")).strip() not in desired_questions
        and existing_example.get("name")
    ]

    for example in examples:
        question = example["question"].strip()
        guidelines = [example["guidelines"].strip()]
        existing = existing_by_question.get(question)
        if existing and existing.get("name"):
            try:
                api_json(
                    client,
                    "PATCH",
                    f"/api/2.1/{existing['name']}",
                    query={"update_mask": "question,guidelines"},
                    body={"question": question, "guidelines": guidelines},
                )
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: unable to update example {existing['name']}: {exc}")
        else:
            recycled = unmanaged_examples.pop(0) if unmanaged_examples else None
            if recycled and recycled.get("name"):
                try:
                    api_json(
                        client,
                        "PATCH",
                        f"/api/2.1/{recycled['name']}",
                        query={"update_mask": "question,guidelines"},
                        body={"question": question, "guidelines": guidelines},
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"Warning: unable to recycle example {recycled['name']}: {exc}")
            else:
                try:
                    api_json(
                        client,
                        "POST",
                        f"/api/2.1/{parent_name}/examples",
                        body={"question": question, "guidelines": guidelines},
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"Warning: unable to create example for {parent_name}: {exc}")

    if delete_unmanaged:
        for existing_example in unmanaged_examples:
            name = existing_example.get("name")
            if name:
                try:
                    api_json(client, "DELETE", f"/api/2.1/{name}")
                except Exception as exc:  # noqa: BLE001
                    print(f"Warning: unable to delete unmanaged example {name}: {exc}")


def ensure_knowledge_assistant(
    client: WorkspaceClient,
    guidance_path: str,
) -> dict[str, Any]:
    description = (
        "Grounds live dog diagnostic recommendations in curated workflow and repeat-test guidance."
    )
    instructions = (
        "Answer only from the attached dog diagnostic guidance documents. "
        "Be concise, prefer operational guidance, and call out duplicate-test cautions or abstain conditions when relevant."
    )
    existing = next(
        (
            assistant
            for assistant in list_knowledge_assistants(client)
            if assistant.get("display_name") == KA_DISPLAY_NAME
        ),
        None,
    )

    if existing:
        assistant = api_json(
            client,
            "PATCH",
            f"/api/2.1/{existing['name']}",
            query={"update_mask": "description,instructions"},
            body={
                "description": description,
                "display_name": KA_DISPLAY_NAME,
                "instructions": instructions,
            },
        )
    else:
        assistant = api_json(
            client,
            "POST",
            "/api/2.1/knowledge-assistants",
            body={
                "description": description,
                "display_name": KA_DISPLAY_NAME,
                "instructions": instructions,
            },
        )

    assistant_name = assistant["name"]
    existing_sources = list_knowledge_sources(client, assistant_name)
    has_guidance_source = any(
        source.get("display_name") == GUIDANCE_SOURCE_DISPLAY_NAME
        for source in existing_sources
    )

    if not has_guidance_source:
        api_json(
            client,
            "POST",
            f"/api/2.1/{assistant_name}/knowledge-sources",
            body={
                "description": GUIDANCE_SOURCE_DESCRIPTION,
                "display_name": GUIDANCE_SOURCE_DISPLAY_NAME,
                "files": {"path": guidance_path},
                "source_type": "files",
            },
        )
    api_json(client, "POST", f"/api/2.1/{assistant_name}/knowledge-sources:sync", body={})
    seed_agent_examples(client, assistant_name, KNOWLEDGE_ASSISTANT_EXAMPLES, delete_unmanaged=True)
    return get_knowledge_assistant(client, assistant_name)


def list_supervisor_agents(client: WorkspaceClient) -> list[dict[str, Any]]:
    payload = api_json(client, "GET", "/api/2.1/supervisor-agents")
    return extract_list(payload, "supervisor_agents")


def get_supervisor_agent(
    client: WorkspaceClient,
    supervisor_name: str,
) -> dict[str, Any]:
    payload = api_json(client, "GET", f"/api/2.1/{supervisor_name}")
    return payload if isinstance(payload, dict) else {}


def list_supervisor_tools(client: WorkspaceClient, supervisor_name: str) -> list[dict[str, Any]]:
    payload = api_json(client, "GET", f"/api/2.1/{supervisor_name}/tools")
    return extract_list(payload, "tools")


def build_supervisor_instructions() -> str:
    return (
        "You answer one question: for the current dog visit intake, what diagnostic action should we recommend next? "
        "Always orchestrate the attached tools in this sequence: "
        "1) triage_intake using the full intake payload serialized as a JSON string, "
        "2) get_patient_history using the full intake payload serialized as a JSON string, "
        "3) get_recent_test_audit using the full intake payload serialized as a JSON string, "
        "4) similar_case_genie, "
        "5) diagnostic_guidance_ka, "
        "6) query_pubmed with a concise veterinary literature query, max_results set to 3, and min_publication_year set to 2018, "
        "7) get_test_metadata only after you have chosen the primary action and the visible alternatives, passing a JSON array string of the visible test codes. "
        "Use triage output for derived cohort, acuity, active signals, missing fields, quality flags, and routing notes. "
        "Use patient history and duplicate-test audit as hard constraints. "
        "Use Genie for structured similar-case and cohort evidence. "
        "Use the knowledge assistant for guidance, repeat-test cautions, and abstain conditions. "
        "Use PubMed only as supplementary literature context, not as patient-specific evidence and not to override duplicate-test suppression. "
        "When mentioning PubMed, cite only PMIDs returned by query_pubmed. Do not invent PubMed citations, URLs, or markdown links. "
        "If query_pubmed returns zero articles, say PubMed returned no matching records or omit PubMed from evidence_sources. "
        "If no specific diagnostic is sufficiently supported, abstain instead of forcing an action. "
        "Return valid JSON only with these fields: "
        "visit_summary { derived_cohort }, "
        "recommendation { action_type, test_code, test_name, summary, reasons, evidence_chips }, "
        "why_not_selected [{ test_code, test_name, disposition, why_not_selected }], "
        "evidence_sources [{ source_type, source_label, summary }]. "
        "The recommendation.action_type value must be exactly recommend_test or abstain. "
        "Use action_type exactly abstain if evidence is insufficient or duplicate suppression removes the strongest option."
    )


def ensure_supervisor_agent(
    client: WorkspaceClient,
    catalog: str,
    schema: str,
    genie_space_id: str,
    knowledge_assistant_id: str,
) -> dict[str, Any]:
    description = (
        "Live request-time supervisor for dog diagnostic recommendations grounded in patient facts, structured evidence, and workflow guidance."
    )
    instructions = build_supervisor_instructions()
    existing = next(
        (
            agent
            for agent in list_supervisor_agents(client)
            if agent.get("display_name") in SUPERVISOR_DISPLAY_NAME_ALIASES
        ),
        None,
    )

    if existing:
        agent = api_json(
            client,
            "PATCH",
            f"/api/2.1/{existing['name']}",
            query={"update_mask": "description,instructions"},
            body={
                "description": description,
                "display_name": SUPERVISOR_DISPLAY_NAME,
                "instructions": instructions,
            },
        )
    else:
        agent = api_json(
            client,
            "POST",
            "/api/2.1/supervisor-agents",
            body={
                "description": description,
                "display_name": SUPERVISOR_DISPLAY_NAME,
                "instructions": instructions,
            },
        )

    supervisor_name = agent["name"]
    for tool in list_supervisor_tools(client, supervisor_name):
        tool_name = tool.get("name")
        if tool_name:
            api_json(client, "DELETE", f"/api/2.1/{tool_name}")

    tool_specs = [
        {
            "tool_id": TOOL_IDS["intake_triage"],
            "body": {
                "description": "Triage editable intake JSON into derived cohort, acuity, active signals, missing fields, quality flags, and routing notes.",
                "tool_type": "uc_function",
                "uc_function": {"name": f"{catalog}.{schema}.runtime_triage_intake"},
            },
        },
        {
            "tool_id": TOOL_IDS["patient_history"],
            "body": {
                "description": "Fetch compact longitudinal patient history using the full intake payload JSON string.",
                "tool_type": "uc_function",
                "uc_function": {"name": f"{catalog}.{schema}.runtime_get_patient_history"},
            },
        },
        {
            "tool_id": TOOL_IDS["recent_test_audit"],
            "body": {
                "description": "Fetch recent same-test timing and duplicate suppression facts using the full intake payload JSON string.",
                "tool_type": "uc_function",
                "uc_function": {"name": f"{catalog}.{schema}.runtime_get_recent_test_audit"},
            },
        },
        {
            "tool_id": TOOL_IDS["similar_case_genie"],
            "body": {
                "description": "Retrieve structured cohort and similar-case evidence from curated gold views.",
                "tool_type": "genie_space",
                "genie_space": {"id": genie_space_id},
            },
        },
        {
            "tool_id": TOOL_IDS["diagnostic_guidance_ka"],
            "body": {
                "description": "Retrieve diagnostic workflow guidance and repeat-test cautions from curated dog guidance documents.",
                "tool_type": "knowledge_assistant",
                "knowledge_assistant": {"knowledge_assistant_id": knowledge_assistant_id},
            },
        },
        {
            "tool_id": TOOL_IDS["query_pubmed"],
            "body": {
                "description": "Query PubMed for compact supplementary veterinary literature context. Returns unavailable JSON instead of raising when PubMed is unavailable.",
                "tool_type": "uc_function",
                "uc_function": {"name": f"{catalog}.{schema}.runtime_query_pubmed"},
            },
        },
        {
            "tool_id": TOOL_IDS["test_metadata"],
            "body": {
                "description": "Fetch diagnostic catalog metadata for the chosen action and visible alternatives using a JSON array string of test codes.",
                "tool_type": "uc_function",
                "uc_function": {"name": f"{catalog}.{schema}.runtime_get_test_metadata"},
            },
        },
    ]

    for spec in tool_specs:
        api_json(
            client,
            "POST",
            f"/api/2.1/{supervisor_name}/tools",
            query={"tool_id": spec["tool_id"]},
            body=spec["body"],
        )

    seed_agent_examples(client, supervisor_name, build_supervisor_examples(), delete_unmanaged=True)
    return get_supervisor_agent(client, supervisor_name)


def write_runtime_config(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    config_map: dict[str, str],
) -> None:
    fq_table = f"{catalog}.{schema}.runtime_config"
    keys = ", ".join(quote_sql(key) for key in config_map)
    execute_statement(client, warehouse_id, f"DELETE FROM {fq_table} WHERE config_key IN ({keys})")

    values_sql = ",\n".join(
        f"({quote_sql(key)}, {quote_sql(value)}, current_timestamp())"
        for key, value in sorted(config_map.items())
    )
    execute_statement(
        client,
        warehouse_id,
        f"""
        INSERT INTO {fq_table} (config_key, config_value, updated_at)
        VALUES
        {values_sql}
        """,
    )


def load_runtime_config(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
) -> dict[str, str]:
    rows = fetch_rows(
        client,
        warehouse_id,
        f"SELECT config_key, config_value FROM {catalog}.{schema}.runtime_config",
    )
    return {
        str(row[0]): str(row[1])
        for row in rows
        if row and row[0] is not None and row[1] is not None
    }


def resolve_serving_endpoint_id(
    client: WorkspaceClient,
    endpoint_name: str,
) -> str:
    payload = api_json(client, "GET", f"/api/2.0/serving-endpoints/{endpoint_name}")
    if isinstance(payload, dict) and payload.get("id"):
        return str(payload["id"])
    return endpoint_name


def normalize_resource_id(name_or_id: str) -> str:
    return name_or_id.rsplit("/", 1)[-1]


def genie_source_identifiers(catalog: str, schema: str) -> list[str]:
    serialized_space = json.loads(build_serialized_space(catalog, schema))
    tables = serialized_space.get("data_sources", {}).get("tables", [])
    identifiers: list[str] = []
    for table in tables:
        identifier = table.get("identifier") if isinstance(table, dict) else None
        if isinstance(identifier, str) and identifier:
            identifiers.append(identifier)
    return identifiers


def patch_permissions(
    client: WorkspaceClient,
    object_type: str,
    object_id: str,
    principal: RuntimePrincipal,
    permission_level: str,
) -> None:
    acl_entry = {
        "permission_level": permission_level,
        principal.acl_field: principal.name,
    }
    api_json(
        client,
        "PATCH",
        f"/api/2.0/permissions/{object_type}/{object_id}",
        body={"access_control_list": [acl_entry]},
    )


def build_runtime_grant_statements(
    catalog: str,
    schema: str,
    principal: RuntimePrincipal,
) -> list[str]:
    fq_schema = f"{catalog}.{schema}"
    statements = [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {principal.sql_identifier}",
        f"GRANT USE SCHEMA ON SCHEMA {fq_schema} TO {principal.sql_identifier}",
    ]
    statements.extend(
        f"GRANT SELECT ON TABLE {fq_schema}.{table_name} TO {principal.sql_identifier}"
        for table_name in RUNTIME_TABLES
    )
    statements.append(
        f"GRANT MODIFY ON TABLE {fq_schema}.live_intake_requests TO {principal.sql_identifier}"
    )
    statements.extend(
        f"GRANT SELECT ON VIEW {fq_schema}.{view_name} TO {principal.sql_identifier}"
        for view_name in RUNTIME_VIEWS
    )
    statements.extend(
        f"GRANT EXECUTE ON FUNCTION {fq_schema}.{function_name} TO {principal.sql_identifier}"
        for function_name in RUNTIME_FUNCTIONS
    )
    for identifier in genie_source_identifiers(catalog, schema):
        object_name = identifier.rsplit(".", 1)[-1]
        object_type = "VIEW" if object_name in GENIE_VIEW_NAMES else "TABLE"
        statements.append(
            f"GRANT SELECT ON {object_type} {identifier} TO {principal.sql_identifier}"
        )

    deduped_statements: list[str] = []
    seen_statements: set[str] = set()
    for statement in statements:
        if statement in seen_statements:
            continue
        seen_statements.add(statement)
        deduped_statements.append(statement)
    return deduped_statements


def resolve_runtime_principals(
    client: WorkspaceClient,
    app_name: str,
    debug_user_name: str,
) -> tuple[list[RuntimePrincipal], str]:
    app = client.apps.get(app_name)
    app_service_principal = app.service_principal_client_id
    if not app_service_principal:
        raise RuntimeError(f"Unable to resolve service principal for app {app_name}.")

    principals = [
        RuntimePrincipal("service_principal_name", app_service_principal),
        RuntimePrincipal("user_name", debug_user_name),
    ]
    deduped_principals: list[RuntimePrincipal] = []
    seen_principals: set[tuple[str, str]] = set()
    for principal in principals:
        key = (principal.acl_field, principal.name)
        if key in seen_principals:
            continue
        seen_principals.add(key)
        deduped_principals.append(principal)
    return deduped_principals, app_service_principal


def resolve_app_url(client: WorkspaceClient, app_name: str) -> str:
    try:
        app = client.apps.get(app_name)
        app_dict = app.as_dict() if hasattr(app, "as_dict") else {}
        for value in [
            getattr(app, "url", None),
            getattr(app, "app_url", None),
            app_dict.get("url"),
            app_dict.get("app_url"),
        ]:
            if value:
                return str(value)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: unable to resolve app URL for {app_name} from SDK: {exc}")

    try:
        payload = api_json(client, "GET", f"/api/2.0/apps/{app_name}")
        if isinstance(payload, dict):
            for key in ("url", "app_url"):
                if payload.get(key):
                    return str(payload[key])
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: unable to resolve app URL for {app_name} from REST API: {exc}")

    return ""


def grant_app_use_permission(
    client: WorkspaceClient,
    app_name: str,
    principal: RuntimePrincipal,
) -> None:
    if principal.acl_field == "service_principal_name":
        request = AppAccessControlRequest(
            service_principal_name=principal.name,
            permission_level=AppPermissionLevel.CAN_USE,
        )
    elif principal.acl_field == "user_name":
        request = AppAccessControlRequest(
            user_name=principal.name,
            permission_level=AppPermissionLevel.CAN_USE,
        )
    elif principal.acl_field == "group_name":
        request = AppAccessControlRequest(
            group_name=principal.name,
            permission_level=AppPermissionLevel.CAN_USE,
        )
    else:
        raise RuntimeError(f"Unsupported app permission principal: {principal.acl_field}")

    client.apps.update_permissions(app_name, access_control_list=[request])


def grant_runtime_permissions(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    app_name: str,
    debug_user_name: str,
    genie_space_id: str,
    knowledge_assistant_id: str,
    knowledge_assistant_endpoint: str,
    supervisor_name: str,
    supervisor_endpoint: str,
    custom_agent_app_name: str | None = None,
) -> dict[str, str]:
    if not knowledge_assistant_endpoint:
        raise RuntimeError("Knowledge Assistant endpoint name was not resolved during bootstrap.")
    if not supervisor_endpoint:
        raise RuntimeError("Supervisor endpoint name was not resolved during bootstrap.")

    principals, app_service_principal = resolve_runtime_principals(
        client,
        app_name,
        debug_user_name,
    )
    custom_agent_service_principal = ""
    custom_agent_app_url = ""
    custom_agent_app_principals: list[RuntimePrincipal] = []
    if custom_agent_app_name:
        custom_agent_app_url = resolve_app_url(client, custom_agent_app_name)
        custom_agent_app_principals = [
            RuntimePrincipal("service_principal_name", app_service_principal),
            RuntimePrincipal("user_name", debug_user_name),
        ]
        try:
            custom_principals, custom_agent_service_principal = resolve_runtime_principals(
                client,
                custom_agent_app_name,
                debug_user_name,
            )
            principals.extend(custom_principals)
        except Exception as exc:  # noqa: BLE001
            print(
                "Warning: unable to resolve custom agent app principal "
                f"for {custom_agent_app_name}: {exc}"
            )

    deduped_principals: list[RuntimePrincipal] = []
    seen_principals: set[tuple[str, str]] = set()
    for principal in principals:
        key = (principal.acl_field, principal.name)
        if key in seen_principals:
            continue
        seen_principals.add(key)
        deduped_principals.append(principal)
    principals = deduped_principals

    deduped_custom_agent_app_principals: list[RuntimePrincipal] = []
    seen_app_principals: set[tuple[str, str]] = set()
    for principal in custom_agent_app_principals:
        key = (principal.acl_field, principal.name)
        if key in seen_app_principals:
            continue
        seen_app_principals.add(key)
        deduped_custom_agent_app_principals.append(principal)
    custom_agent_app_principals = deduped_custom_agent_app_principals

    knowledge_assistant_endpoint_id = resolve_serving_endpoint_id(
        client,
        knowledge_assistant_endpoint,
    )
    supervisor_endpoint_id = resolve_serving_endpoint_id(client, supervisor_endpoint)

    for principal in principals:
        for statement in build_runtime_grant_statements(catalog, schema, principal):
            execute_statement(client, warehouse_id, statement)

        patch_permissions(client, "warehouses", warehouse_id, principal, "CAN_USE")
        patch_permissions(client, "genie", genie_space_id, principal, "CAN_RUN")
        patch_permissions(
            client,
            "knowledge-assistants",
            normalize_resource_id(knowledge_assistant_id),
            principal,
            "CAN_QUERY",
        )
        patch_permissions(
            client,
            "supervisor-agents",
            normalize_resource_id(supervisor_name),
            principal,
            "CAN_QUERY",
        )
        patch_permissions(
            client,
            "serving-endpoints",
            knowledge_assistant_endpoint_id,
            principal,
            "CAN_MANAGE",
        )
        patch_permissions(
            client,
            "serving-endpoints",
            supervisor_endpoint_id,
            principal,
            "CAN_MANAGE",
        )

    if custom_agent_app_name:
        for principal in custom_agent_app_principals:
            grant_app_use_permission(client, custom_agent_app_name, principal)

    return {
        "app_service_principal": app_service_principal,
        "custom_agent_service_principal": custom_agent_service_principal,
        "custom_agent_app_url": custom_agent_app_url,
        "debug_user_name": debug_user_name,
    }


def build_runtime_prompt(request_id: str, payload_json: str) -> str:
    return f"""
You are orchestrating a live dog diagnostic recommendation request `{request_id}`.

Use this intake payload as the source of truth:
{payload_json}

Use the attached tools in this sequence:
1. triage_intake with payload_json set to the full intake payload JSON string
2. get_patient_history with payload_json set to the full intake payload JSON string
3. get_recent_test_audit with payload_json set to the full intake payload JSON string
4. similar_case_genie
5. diagnostic_guidance_ka
6. query_pubmed with a concise veterinary literature query, max_results set to 3, and min_publication_year set to 2018
7. get_test_metadata after selecting the primary action and visible alternatives, using a JSON array string of the visible test codes

Use triage output for derived cohort, acuity, active signals, missing fields, quality flags, and routing notes.
Use patient history and recent duplicate-test audit as hard constraints.
Use Genie for structured similar-case and cohort evidence.
Use the knowledge assistant for diagnostic workflow guidance, repeat-test cautions, and abstain conditions.
Use PubMed only as supplementary literature context. Do not treat PubMed as patient-specific evidence or use it to override duplicate-test suppression.
When mentioning PubMed, cite only PMIDs returned by query_pubmed. Do not invent PubMed citations, URLs, or markdown links.
If query_pubmed returns zero articles, say PubMed returned no matching records or omit PubMed from evidence_sources.

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

If evidence is insufficient or duplicate suppression removes the strongest option, use action_type exactly "abstain".
""".strip()


def stage_live_intake(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    request_id = uuid.uuid4().hex
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    execute_statement(
        client,
        warehouse_id,
        f"""
        INSERT INTO {catalog}.{schema}.live_intake_requests (request_id, patient_id, as_of_ts, payload_json, created_at)
        VALUES (
          {quote_sql(request_id)},
          {quote_sql(payload.get('patient_id', 'UNKNOWN'))},
          {quote_sql(payload.get('as_of_ts', ''))},
          {quote_sql(payload_json)},
          current_timestamp()
        )
        """,
    )
    return request_id, payload_json


def call_supervisor(
    client: WorkspaceClient,
    supervisor_endpoint: str,
    prompt: str,
) -> dict[str, Any]:
    host = normalize_host(client.config.host)
    if not host:
        raise RuntimeError("Unable to resolve Databricks host for supervisor call.")

    headers = client.config.authenticate()
    headers["Content-Type"] = "application/json"
    body = {
        "model": supervisor_endpoint,
        "stream": False,
        "input": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    candidate_paths = [
        "/api/2.0/serving-endpoints/responses",
        "/serving-endpoints/responses",
    ]
    last_error: Exception | None = None
    max_attempts = 3
    for path in candidate_paths:
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    f"{host.rstrip('/')}{path}",
                    headers=headers,
                    json=body,
                    timeout=(
                        SUPERVISOR_CONNECT_TIMEOUT_SECONDS,
                        SUPERVISOR_READ_TIMEOUT_SECONDS,
                    ),
                )
                response.raise_for_status()
                payload = parse_responses_api_body(response)
                if isinstance(payload, dict) and payload.get("error_code"):
                    raise RuntimeError(
                        f"{payload.get('error_code')}: {payload.get('message', 'Supervisor request failed.')}"
                    )
                return payload
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 502, 503, 504} and attempt < max_attempts:
                    time.sleep(min(2 ** (attempt - 1), 5))
                    continue
                last_error = exc
                if status_code in {400, 404, 405}:
                    break
                raise RuntimeError(
                    f"Supervisor endpoint returned HTTP {status_code or 'unknown'}."
                ) from exc
            except requests.Timeout as exc:
                raise RuntimeError(
                    "Supervisor response exceeded the runtime timeout "
                    f"({SUPERVISOR_READ_TIMEOUT_SECONDS}s)."
                ) from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"Supervisor request failed: {exc}") from exc

    if last_error is not None:
        raise last_error
    raise RuntimeError("Supervisor call did not return a response.")


def maybe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def normalize_tool_name(name: str | None) -> str:
    tool_name = name or ""
    for expected_name in TOOL_IDS.values():
        if tool_name.endswith(expected_name):
            return expected_name
    return tool_name


def parse_function_output(raw_output: str | None) -> Any:
    if not raw_output:
        return None
    parsed_output = maybe_json(raw_output)
    if not isinstance(parsed_output, dict):
        return parsed_output

    rows = parsed_output.get("rows")
    if rows and rows[0]:
        return maybe_json(rows[0][0])

    data_array = parsed_output.get("result", {}).get("data_array")
    if data_array and data_array[0]:
        return maybe_json(data_array[0][0])

    return parsed_output


def parse_assistant_json(text: str | None) -> dict[str, Any] | None:
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
        parsed = maybe_json(candidate.strip())
        if isinstance(parsed, dict):
            return parsed
    return None


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
            if parsed.get("error_code"):
                raise RuntimeError(
                    f"{parsed.get('error_code')}: {parsed.get('message', 'Supervisor request failed.')}"
                )
            events.append(parsed)

    for event in reversed(events):
        if event.get("output") is not None or event.get("id") is not None:
            return event

    body_preview = response.text.strip()[:500]
    raise RuntimeError(
        body_preview or "Supervisor request returned an unreadable responses payload."
    )


def parse_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    assistant_texts: list[str] = []
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
                assistant_texts.append("\n".join(text_parts))
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "call_id": item.get("call_id"),
                    "id": item.get("id"),
                    "raw_name": item.get("name"),
                    "name": normalize_tool_name(item.get("name")),
                    "step": item.get("step"),
                    "arguments": maybe_json(item.get("arguments")),
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

    assistant_text = assistant_texts[-1] if assistant_texts else None

    return {
        "assistant_text": assistant_text,
        "assistant_json": parse_assistant_json(assistant_text),
        "tool_calls": tool_calls,
        "tool_outputs": tool_outputs,
        "response_id": payload.get("id"),
    }


def fetch_smoke_intake(
    client: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
) -> dict[str, Any]:
    rows = fetch_rows(
        client,
        warehouse_id,
        f"""
        SELECT
          visit_id,
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
          duration_days
        FROM {catalog}.{schema}.visit_case_packet_gold
        ORDER BY visit_date DESC, visit_id DESC
        LIMIT 1
        """,
    )
    if not rows:
        raise RuntimeError("No sample intake rows were found in visit_case_packet_gold.")
    row = rows[0]
    return {
        "seed_visit_id": row[0],
        "patient_id": row[1],
        "dog_name": row[2],
        "breed": row[3],
        "age_years": row[4],
        "life_stage": row[5],
        "as_of_ts": row[6],
        "presenting_complaint": row[7],
        "severity": row[8],
        "urgency_level": row[9] or "routine",
        "owner_note": row[10] or "",
        "clinician_note": row[11] or "",
        "cohort_hint": row[12],
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
        "recent_test_overrides": [],
    }


@dataclass
class BootstrapResult:
    guidance_path: str
    genie_space_id: str
    knowledge_assistant_id: str
    knowledge_assistant_name: str
    knowledge_assistant_endpoint: str
    supervisor_name: str
    supervisor_endpoint: str
    custom_agent_app_name: str
    custom_agent_app_url: str
    app_service_principal: str
    custom_agent_service_principal: str
    debug_user_name: str
