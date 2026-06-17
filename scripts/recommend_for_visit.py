#!/usr/bin/env python3
"""Show the next-best diagnostic recommendation for a single synthetic dog visit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List


DEFAULT_CATALOG = "pavan_naidu"
DEFAULT_SCHEMA = "nba"
DEFAULT_PROFILE = "FEVM"
DEFAULT_WAREHOUSE_ID = "e0c50bf18fca9e7f"


def quote_sql(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def run_statement(statement: str, profile: str, warehouse_id: str) -> Dict[str, Any]:
    env = os.environ.copy()
    env["DATABRICKS_AUTH_STORAGE"] = "plaintext"
    cmd = [
        "databricks",
        "api",
        "post",
        "/api/2.0/sql/statements/",
        "-p",
        profile,
        "-o",
        "json",
        "--json",
        json.dumps(
            {
                "statement": statement,
                "warehouse_id": warehouse_id,
                "wait_timeout": "50s",
            }
        ),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    status = payload.get("status", {}).get("state")
    if status != "SUCCEEDED":
        raise RuntimeError(f"Statement failed: {payload}")
    return payload


def fetch_json_scalar(statement: str, profile: str, warehouse_id: str) -> Any:
    payload = run_statement(statement, profile, warehouse_id)
    rows = payload.get("result", {}).get("data_array", [])
    if not rows or not rows[0]:
        return None
    raw_value = rows[0][0]
    return json.loads(raw_value) if raw_value else None


def build_signal_summary(packet: Dict[str, Any]) -> str:
    signals: List[str] = []
    if packet.get("vomiting"):
        signals.append("vomiting")
    if packet.get("diarrhea"):
        signals.append("diarrhea")
    if packet.get("urinary_accidents"):
        signals.append("urinary accidents")
    if packet.get("straining_to_urinate"):
        signals.append("straining to urinate")
    if packet.get("increased_thirst"):
        signals.append("increased thirst")
    if packet.get("increased_hunger"):
        signals.append("increased hunger")
    if packet.get("lethargy"):
        signals.append("lethargy")
    if not signals:
        return "No major structured symptom flags were captured for this visit."
    return "Structured symptom flags: " + ", ".join(signals) + "."


def build_explanations(packet: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[str]:
    explanations = [
        f"This visit is being worked up as a {packet['cohort']} case with {packet['severity']} severity.",
        build_signal_summary(packet),
    ]

    if packet.get("duration_days") is not None:
        explanations.append(
            f"The current issue has been present for about {packet['duration_days']} day(s), which affects how aggressive the next diagnostic step should be."
        )
    if packet.get("prior_visit_count"):
        explanations.append(
            f"This patient has {packet['prior_visit_count']} prior visit(s) in the history, so the recommendation can use longitudinal context rather than treating this as a first-time presentation."
        )

    if packet.get("prior_diagnostic_summary") and packet["prior_diagnostic_summary"] != "no prior diagnostics recorded":
        explanations.append(
            "Prior diagnostics are part of the context, which helps avoid repeating very recent tests and supports longitudinal follow-up."
        )
    if packet.get("prior_treatment_summary") and packet["prior_treatment_summary"] != "no prior treatments recorded":
        explanations.append(
            "Prior treatment response is included so the recommendation can reflect what has already been tried."
        )

    unsuppressed = [candidate for candidate in candidates if not candidate.get("suppress_duplicate")]
    if len(unsuppressed) >= 2:
        top_gap = unsuppressed[0]["utility_score"] - unsuppressed[1]["utility_score"]
        if top_gap > 0:
            explanations.append(
                f"The leading recommendation has a {top_gap}-point advantage over the next alternative, which makes it the clearest next action."
            )
        else:
            explanations.append(
                "The top two recommendations are effectively tied, so the demo presents both as strong next-step options."
            )

    suppressed = [candidate for candidate in candidates if candidate.get("suppress_duplicate")]
    if suppressed:
        explanations.append(
            f"{len(suppressed)} candidate test(s) were down-ranked by duplicate suppression because the same diagnostic was run recently."
        )

    if packet.get("no_additional_diagnostic_now"):
        explanations.append(
            "The visit is currently below the recommendation threshold, so the suggested next action is to avoid additional diagnostics for now."
        )

    return explanations


def build_result(packet: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    recommended = []
    for rank in range(1, 4):
        test_name = packet.get(f"recommended_test_{rank}_name")
        score = packet.get(f"recommended_test_{rank}_score")
        if test_name is None:
            continue
        recommended.append(
            {
                "rank": rank,
                "test_name": test_name,
                "score": score,
            }
        )

    unsuppressed = [candidate for candidate in candidates if not candidate.get("suppress_duplicate")]
    suppressed = [candidate for candidate in candidates if candidate.get("suppress_duplicate")]

    return {
        "visit": {
            "visit_id": packet["visit_id"],
            "patient_id": packet["patient_id"],
            "dog_name": packet["dog_name"],
            "breed": packet["breed"],
            "age_years": packet["age_years"],
            "life_stage": packet["life_stage"],
            "visit_date": packet["visit_date"],
            "cohort": packet["cohort"],
            "severity": packet["severity"],
            "presenting_complaint": packet["presenting_complaint"],
            "owner_note": packet["owner_note"],
            "clinician_note": packet["clinician_note"],
        },
        "next_best_action": {
            "no_additional_diagnostic_now": packet["no_additional_diagnostic_now"],
            "recommended_diagnostics": recommended,
            "why": build_explanations(packet, candidates),
        },
        "considered_alternatives": unsuppressed[3:],
        "suppressed_duplicates": suppressed,
        "history_context": {
            "prior_diagnostic_summary": packet["prior_diagnostic_summary"],
            "prior_treatment_summary": packet["prior_treatment_summary"],
        },
    }


def print_result(result: Dict[str, Any]) -> None:
    visit = result["visit"]
    action = result["next_best_action"]

    print(f"Visit: {visit['visit_id']}")
    print(
        f"Patient: {visit['dog_name']} | {visit['breed']} | {visit['age_years']} years | {visit['life_stage']}"
    )
    print(
        f"Presentation: {visit['presenting_complaint']} | cohort={visit['cohort']} | severity={visit['severity']}"
    )
    print(f"Visit date: {visit['visit_date']}")
    print(f"Owner note: {visit['owner_note']}")
    print(f"Clinician note: {visit['clinician_note']}")
    print()

    if action["no_additional_diagnostic_now"]:
        print("Recommended next action: no additional diagnostic now")
    else:
        print("Recommended next diagnostics:")
        for recommendation in action["recommended_diagnostics"]:
            print(
                f"  {recommendation['rank']}. {recommendation['test_name']} "
                f"(score={recommendation['score']})"
            )
    print()

    print("Why these recommendations:")
    for explanation in action["why"]:
        print(f"  - {explanation}")
    print()

    print("History context:")
    print(f"  - Prior diagnostics: {result['history_context']['prior_diagnostic_summary']}")
    print(f"  - Prior treatments: {result['history_context']['prior_treatment_summary']}")

    if result["considered_alternatives"]:
        print()
        print("Other considered diagnostics:")
        for candidate in result["considered_alternatives"][:5]:
            print(
                f"  - {candidate['test_name']} "
                f"(score={candidate.get('utility_score')}, days_since_same_test={candidate.get('days_since_same_test')})"
            )

    print()
    if result["suppressed_duplicates"]:
        print("Suppressed duplicates:")
        for candidate in result["suppressed_duplicates"][:5]:
            print(
                f"  - {candidate['test_name']} "
                f"(days_since_same_test={candidate.get('days_since_same_test')})"
            )
    else:
        print("Suppressed duplicates: none")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("visit_id", help="Visit identifier like VISIT-DOG-0001-04.")
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    parser.add_argument("--json", action="store_true", help="Print the full structured result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    function_prefix = f"{args.catalog}.{args.schema}"
    visit_literal = quote_sql(args.visit_id)

    packet = fetch_json_scalar(
        f"SELECT {function_prefix}.get_visit_packet({visit_literal})",
        args.profile,
        args.warehouse_id,
    )
    if not packet or packet.get("error") == "visit_not_found":
        raise SystemExit(f"Visit not found: {args.visit_id}")

    candidates = fetch_json_scalar(
        f"SELECT {function_prefix}.get_visit_candidate_diagnostics({visit_literal})",
        args.profile,
        args.warehouse_id,
    ) or []
    candidates = sorted(candidates, key=lambda candidate: candidate.get("recommendation_rank", 999))

    result = build_result(packet, candidates)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_result(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        raise
