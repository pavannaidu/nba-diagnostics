#!/usr/bin/env python3
"""Grant Unity Catalog permissions required by the Databricks App runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from typing import Sequence

from generate_idexx_demo_data import DEFAULT_CATALOG, DEFAULT_PROFILE, DEFAULT_SCHEMA, run_statement


DEFAULT_APP_NAME = "idexx-nba-diagnostics-fevm"
RUNTIME_TABLES = [
    "patients",
    "visits",
    "diagnostic_history",
    "treatments_history",
    "diagnostic_catalog",
    "live_intake_requests",
]
RUNTIME_VIEWS = [
    "visit_case_packet_gold",
]
UC_FUNCTIONS = [
    "runtime_triage_intake",
    "runtime_query_pubmed",
    "runtime_get_patient_history",
    "runtime_get_recent_test_audit",
    "runtime_get_test_metadata",
    "get_visit_packet",
    "get_visit_candidate_diagnostics",
    "get_cohort_recommendation_summary",
    "get_patient_history",
    "recommend_for_intake",
    "get_test_metadata",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", required=True)
    return parser.parse_args()


def run_cli_json(command: Sequence[str]) -> dict:
    env = os.environ.copy()
    env["DATABRICKS_AUTH_STORAGE"] = "plaintext"
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def resolve_service_principal_client_id(app_name: str, profile: str) -> str:
    payload = run_cli_json(
        ["databricks", "apps", "get", app_name, "-p", profile, "-o", "json"]
    )
    service_principal_client_id = payload.get("service_principal_client_id")
    if not service_principal_client_id:
        raise RuntimeError(f"Unable to resolve service principal client ID for app {app_name}.")
    return service_principal_client_id


def build_grant_statements(catalog: str, schema: str, principal: str) -> list[str]:
    principal_sql = f"`{principal.replace('`', '``')}`"
    fq_schema = f"{catalog}.{schema}"
    statements = [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {principal_sql}",
        f"GRANT USE SCHEMA ON SCHEMA {fq_schema} TO {principal_sql}",
    ]
    statements.extend(
        f"GRANT SELECT ON TABLE {fq_schema}.{table_name} TO {principal_sql}"
        for table_name in RUNTIME_TABLES
    )
    statements.append(
        f"GRANT MODIFY ON TABLE {fq_schema}.live_intake_requests TO {principal_sql}"
    )
    statements.extend(
        f"GRANT SELECT ON VIEW {fq_schema}.{view_name} TO {principal_sql}"
        for view_name in RUNTIME_VIEWS
    )
    statements.extend(
        f"GRANT EXECUTE ON FUNCTION {fq_schema}.{function_name} TO {principal_sql}"
        for function_name in UC_FUNCTIONS
    )
    return statements


def main() -> int:
    args = parse_args()
    principal = resolve_service_principal_client_id(args.app_name, args.profile)
    statements = build_grant_statements(args.catalog, args.schema, principal)

    print(f"Granting runtime permissions to {principal}")
    for statement in statements:
        print(f"  {statement}")
        run_statement(statement, args.profile, args.warehouse_id)

    print("Runtime grants applied successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
