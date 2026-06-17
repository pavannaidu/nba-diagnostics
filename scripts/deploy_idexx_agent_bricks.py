#!/usr/bin/env python3
"""Deploy the IDEXX baseline Genie space and live Supervisor Agent for the synthetic demo."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CATALOG = "pavan_naidu"
DEFAULT_SCHEMA = "nba"
DEFAULT_PROFILE = "FEVM"
DEFAULT_WAREHOUSE_ID = "e0c50bf18fca9e7f"

SPACE_TITLE = "IDEXX NBA Diagnostics Demo"
SUPERVISOR_DISPLAY_NAME = "IDEXX NBA Diagnostics Supervisor"
GENIE_TOOL_ID = "visit_diagnostics_genie"
PATIENT_HISTORY_TOOL_ID = "get_patient_history"
RECOMMEND_FOR_INTAKE_TOOL_ID = "recommend_for_intake"
TEST_METADATA_TOOL_ID = "get_test_metadata"
LEGACY_TOOL_IDS = {
    GENIE_TOOL_ID,
    "visit_packet_lookup",
    "visit_candidate_lookup",
    "cohort_recommendation_summary",
}


def databricks_cmd(args: List[str], profile: str) -> List[str]:
    return ["databricks", *args, "-p", profile, "-o", "json"]


def run_json(args: List[str], profile: str) -> Any:
    env = os.environ.copy()
    env["DATABRICKS_AUTH_STORAGE"] = "plaintext"
    result = subprocess.run(
        databricks_cmd(args, profile),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    output = result.stdout.strip()
    return json.loads(output) if output else {}


def current_user(profile: str) -> Dict[str, Any]:
    return run_json(["current-user", "me"], profile)


def list_spaces(profile: str) -> List[Dict[str, Any]]:
    payload = run_json(["genie", "list-spaces"], profile)
    return payload.get("spaces", [])


def get_space(space_id: str, profile: str, include_serialized: bool = False) -> Dict[str, Any]:
    path = f"/api/2.0/genie/spaces/{space_id}"
    if include_serialized:
        path += "?include_serialized_space=true"
    return run_json(["api", "get", path], profile)


def list_supervisors(profile: str) -> List[Dict[str, Any]]:
    return run_json(["supervisor-agents", "list-supervisor-agents"], profile)


def list_tools(supervisor_name: str, profile: str) -> List[Dict[str, Any]]:
    return run_json(["supervisor-agents", "list-tools", supervisor_name], profile)


def sample_question(question: str) -> Dict[str, List[str]]:
    return {"id": uuid.uuid4().hex, "question": [question]}


def sample_sql(question: str, sql: str) -> Dict[str, List[str]]:
    return {"id": uuid.uuid4().hex, "question": [question], "sql": [sql]}


def build_serialized_space(catalog: str, schema: str) -> str:
    fq = f"{catalog}.{schema}"
    table_identifiers = sorted(
        [
            f"{fq}.visit_case_packet_gold",
            f"{fq}.visit_candidate_features_gold",
            f"{fq}.visit_recommendation_gold",
            f"{fq}.diagnostic_catalog",
        ]
    )
    sample_questions = sorted(
        [
            sample_question("Which visits in the last 30 days have no additional diagnostic recommendation right now?"),
            sample_question("What are the most common top-ranked diagnostics for renal_urinary visits?"),
            sample_question("Show recent GI visits with their top 3 recommended diagnostics and owner note summary."),
            sample_question("Which senior dogs had endocrine_metabolic visits and what was the top recommended test?"),
            sample_question("How many visits by cohort were marked as no additional diagnostic now?"),
            sample_question("For a given visit_id, what prior diagnostics and treatments are influencing the current recommendation?"),
        ],
        key=lambda row: row["id"],
    )
    example_question_sqls = sorted(
        [
            sample_sql(
                "Which visits in the last 30 days have no additional diagnostic recommendation right now?",
                f"""SELECT visit_id, dog_name, cohort, visit_date, presenting_complaint
FROM {fq}.visit_recommendation_gold
WHERE no_additional_diagnostic_now = true
  AND visit_date >= date_sub(current_date(), 30)
ORDER BY visit_date DESC
LIMIT 50""",
            ),
            sample_sql(
                "What are the most common top-ranked diagnostics for renal_urinary visits?",
                f"""SELECT recommended_test_1, recommended_test_1_name, COUNT(*) AS visit_count
FROM {fq}.visit_recommendation_gold
WHERE cohort = 'renal_urinary'
  AND no_additional_diagnostic_now = false
GROUP BY recommended_test_1, recommended_test_1_name
ORDER BY visit_count DESC
LIMIT 10""",
            ),
            sample_sql(
                "Show recent GI visits with their top 3 recommended diagnostics and owner note summary.",
                f"""SELECT r.visit_id, r.dog_name, r.visit_date, p.owner_note,
  r.recommended_test_1_name, r.recommended_test_2_name, r.recommended_test_3_name
FROM {fq}.visit_recommendation_gold r
INNER JOIN {fq}.visit_case_packet_gold p
  ON p.visit_id = r.visit_id
WHERE r.cohort = 'gi'
ORDER BY r.visit_date DESC
LIMIT 25""",
            ),
            sample_sql(
                "Which senior dogs had endocrine_metabolic visits and what was the top recommended test?",
                f"""SELECT dog_name, visit_id, visit_date, recommended_test_1_name, recommended_test_1_score
FROM {fq}.visit_recommendation_gold
INNER JOIN {fq}.visit_case_packet_gold USING (visit_id, patient_id, dog_name, visit_date, cohort, presenting_complaint)
WHERE life_stage = 'senior'
  AND cohort = 'endocrine_metabolic'
ORDER BY visit_date DESC
LIMIT 25""",
            ),
            sample_sql(
                "How many visits by cohort were marked as no additional diagnostic now?",
                f"""SELECT cohort, no_additional_diagnostic_now, COUNT(*) AS visit_count
FROM {fq}.visit_recommendation_gold
GROUP BY cohort, no_additional_diagnostic_now
ORDER BY cohort, no_additional_diagnostic_now""",
            ),
            sample_sql(
                "For a given visit_id, what prior diagnostics and treatments are influencing the current recommendation?",
                f"""SELECT visit_id, dog_name, cohort, prior_diagnostic_summary, prior_treatment_summary,
  recommended_test_1_name, recommended_test_2_name, recommended_test_3_name
FROM {fq}.visit_case_packet_gold packet
INNER JOIN {fq}.visit_recommendation_gold rec USING (visit_id, patient_id, dog_name, visit_date, cohort, presenting_complaint)
ORDER BY visit_date DESC
LIMIT 25""",
            ),
        ],
        key=lambda row: row["id"],
    )
    payload = {
        "version": 2,
        "config": {
            "sample_questions": sample_questions
        },
        "data_sources": {
            "tables": [{"identifier": identifier} for identifier in table_identifiers]
        },
        "instructions": {
            "example_question_sqls": example_question_sqls
        },
    }
    return json.dumps(payload, indent=2)


def ensure_genie_space(catalog: str, schema: str, profile: str, warehouse_id: str, parent_path: str) -> Dict[str, Any]:
    description = (
        "Baseline and evaluation analytics over synthetic dog visit histories for an IDEXX next-best-action demo.\n\n"
        "Use this space to:\n"
        "- Inspect gold recommendation patterns and abstain cases\n"
        "- Review longitudinal history used for baseline comparisons\n"
        "- Slice visits by cohort, life stage, chronic condition, and visit timing\n"
        "- Compare recommendation patterns across wellness, GI, renal/urinary, and endocrine/metabolic workflows\n"
    )
    serialized_space = build_serialized_space(catalog, schema)
    existing = next((space for space in list_spaces(profile) if space.get("title") == SPACE_TITLE), None)
    if existing:
        details = get_space(existing["space_id"], profile, include_serialized=True)
        args = [
            "genie",
            "update-space",
            existing["space_id"],
            "--etag",
            details["etag"],
            "--serialized-space",
            serialized_space,
            "--warehouse-id",
            warehouse_id,
            "--title",
            SPACE_TITLE,
            "--description",
            description,
        ]
        run_json(args, profile)
        return get_space(existing["space_id"], profile, include_serialized=True)

    args = [
        "genie",
        "create-space",
        warehouse_id,
        serialized_space,
        "--title",
        SPACE_TITLE,
        "--description",
        description,
        "--parent-path",
        parent_path,
    ]
    return run_json(args, profile)

def ensure_supervisor(
    space_id: str,
    catalog: str,
    schema: str,
    profile: str,
    *,
    summary_only: bool = False,
) -> Dict[str, Any]:
    if summary_only:
        description = (
            "Summary-only supervisor for the IDEXX diagnostics demo. "
            "Use the structured live recommendation context provided in the prompt and return the final narrative for the app."
        )
        instructions = (
            "You are the narrative synthesis step for a live request-time dog diagnostics demo. "
            "Do not call any tools. Do not query Genie. Use only the structured context provided in the prompt. "
            "Return a concise answer with these sections: Visit Summary, Next Recommended Diagnostic Action, Top Three Diagnostics, Why This Ranking. "
            "Call out abstain behavior and duplicate suppression when the prompt includes them. "
            "Do not present this as production clinical advice."
        )
    else:
        description = (
            "Live request-time dog visit recommendation supervisor for the IDEXX diagnostics demo. "
            "Start from editable visit intake, fetch patient history, score candidate diagnostics, suppress duplicates, and synthesize the next best action."
        )
        instructions = (
            "You answer one primary question: for the current live dog visit intake, what should we recommend next? "
            "Always execute tools in this order for every recommendation run. "
            "The live intake is already staged in Unity Catalog before you run. "
            "First call get_patient_history with no arguments. "
            "Second call recommend_for_intake with no arguments. "
            "Third call get_test_metadata with no arguments. "
            "Use the metadata rows whose test_code values match the recommended_test_codes returned from recommend_for_intake. "
            "Do not use visit-level gold lookup tools, cached visit rows, or Genie for the runtime answer. "
            "Treat any gold views or Genie assets as baseline/eval resources outside the live serving path. "
            "Return a concise answer with these sections: Visit Summary, Next Recommended Diagnostic Action, Top Three Diagnostics, Why This Ranking. "
            "Include whether the system abstains from adding another diagnostic right now, call out duplicate suppression when it changes ranking, "
            "and keep the explanation grounded in the current intake plus longitudinal history. "
            "Do not present this as production clinical advice."
        )
    existing = next(
        (agent for agent in list_supervisors(profile) if agent.get("display_name") == SUPERVISOR_DISPLAY_NAME),
        None,
    )
    if existing:
        run_json(
            [
                "supervisor-agents",
                "update-supervisor-agent",
                existing["name"],
                "description,instructions",
                SUPERVISOR_DISPLAY_NAME,
                "--description",
                description,
                "--instructions",
                instructions,
            ],
            profile,
        )
        agent = run_json(["supervisor-agents", "get-supervisor-agent", existing["name"]], profile)
    else:
        agent = run_json(
            [
                "supervisor-agents",
                "create-supervisor-agent",
                SUPERVISOR_DISPLAY_NAME,
                "--description",
                description,
                "--instructions",
                instructions,
            ],
            profile,
        )

    supervisor_name = agent["name"]
    function_prefix = f"{catalog}.{schema}"
    function_specs = [] if summary_only else [
        {
            "tool_id": PATIENT_HISTORY_TOOL_ID,
            "function_name": f"{function_prefix}.get_patient_history",
            "description": "Fetch compact longitudinal patient history for the latest staged live intake.",
        },
        {
            "tool_id": RECOMMEND_FOR_INTAKE_TOOL_ID,
            "function_name": f"{function_prefix}.recommend_for_intake",
            "description": "Score and rank diagnostics live from the latest staged intake payload, including duplicate suppression and abstain behavior.",
        },
        {
            "tool_id": TEST_METADATA_TOOL_ID,
            "function_name": f"{function_prefix}.get_test_metadata",
            "description": "Fetch the diagnostic metadata catalog so the supervisor can explain the recommended test codes clearly.",
        },
    ]
    desired_tool_ids = {spec["tool_id"] for spec in function_specs}

    tools = list_tools(supervisor_name, profile)
    for tool in tools:
        if tool.get("tool_id") in desired_tool_ids:
            continue
        run_json(
            [
                "supervisor-agents",
                "delete-tool",
                tool["name"],
            ],
            profile,
        )

    # Recreate runtime UC tools on every deploy so the supervisor wrapper does not
    # hang onto stale function metadata after the underlying SQL function body changes.
    tools = list_tools(supervisor_name, profile)
    for tool in tools:
        if tool.get("tool_id") not in desired_tool_ids:
            continue
        run_json(
            [
                "supervisor-agents",
                "delete-tool",
                tool["name"],
            ],
            profile,
        )

    for spec in function_specs:
        run_json(
            [
                "supervisor-agents",
                "create-tool",
                supervisor_name,
                spec["tool_id"],
                "--json",
                json.dumps(
                    {
                        "tool_type": "uc_function",
                        "description": spec["description"],
                        "uc_function": {"name": spec["function_name"]},
                    }
                ),
            ],
            profile,
        )

    return run_json(["supervisor-agents", "get-supervisor-agent", supervisor_name], profile)


def validate_genie(space_id: str, profile: str) -> Dict[str, Any]:
    question = "How many visits are there in each cohort, and how many are currently marked as no additional diagnostic now?"
    return run_json(
        ["genie", "start-conversation", space_id, question, "--timeout", "5m"],
        profile,
    )


def write_outputs(output_dir: Path, genie_payload: Dict[str, Any], supervisor_payload: Dict[str, Any], validation_payload: Optional[Dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "genie_space.json").write_text(json.dumps(genie_payload, indent=2))
    (output_dir / "supervisor_agent.json").write_text(json.dumps(supervisor_payload, indent=2))
    if validation_payload is not None:
        (output_dir / "genie_validation.json").write_text(json.dumps(validation_payload, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    parser.add_argument("--output-dir", default="generated")
    parser.add_argument("--validate-genie", action="store_true")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Deploy the supervisor as a summary-only endpoint without registered tools.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / args.output_dir
    user = current_user(args.profile)
    parent_path = f"/Users/{user['userName']}"

    genie_payload = ensure_genie_space(args.catalog, args.schema, args.profile, args.warehouse_id, parent_path)
    space_id = genie_payload["space_id"]
    supervisor_payload = ensure_supervisor(
        space_id,
        args.catalog,
        args.schema,
        args.profile,
        summary_only=args.summary_only,
    )
    validation_payload = validate_genie(space_id, args.profile) if args.validate_genie else None
    write_outputs(output_dir, genie_payload, supervisor_payload, validation_payload)

    print(f"Genie space: {genie_payload['title']} ({space_id})")
    print(f"Supervisor agent: {supervisor_payload['display_name']} ({supervisor_payload['name']})")
    if validation_payload is not None:
      print("Validated Genie space with a sample conversation.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        raise
