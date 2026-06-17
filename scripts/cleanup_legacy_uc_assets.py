#!/usr/bin/env python3
"""Drop unused legacy/debug UC functions from the NBA schema."""

from __future__ import annotations

import argparse
import json

from runtime_assets import (
    build_workspace_client,
    execute_statement,
    fetch_rows,
    list_supervisor_tools,
    load_runtime_config,
)


DEFAULT_CATALOG = "pavan_naidu"
DEFAULT_SCHEMA = "nba"
DEFAULT_PROFILE = "FEVM"
DEFAULT_WAREHOUSE_ID = "e0c50bf18fca9e7f"

ACTIVE_RUNTIME_FUNCTIONS = {
    "runtime_triage_intake",
    "runtime_query_pubmed",
    "runtime_get_patient_history",
    "runtime_get_recent_test_audit",
    "runtime_get_test_metadata",
}

LEGACY_OR_DEBUG_FUNCTIONS = {
    "_codex_test_anchor",
    "_codex_test_payload_projection",
    "debug_python_uc",
    "debug_stage_lookup",
    "get_cohort_recommendation_summary",
    "get_patient_history",
    "get_test_metadata",
    "get_visit_candidate_diagnostics",
    "get_visit_packet",
    "recommend_for_intake",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually drop unused functions. Without this flag, only prints the plan.",
    )
    return parser.parse_args()


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def list_schema_functions(client, warehouse_id: str, catalog: str, schema: str) -> set[str]:
    rows = fetch_rows(
        client,
        warehouse_id,
        f"""
        SELECT routine_name
        FROM {catalog}.information_schema.routines
        WHERE routine_schema = {quote_literal(schema)}
          AND routine_type = 'FUNCTION'
        """,
    )
    return {str(row[0]) for row in rows if row and row[0]}


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def supervisor_uc_function_names(client, warehouse_id: str, catalog: str, schema: str) -> set[str]:
    try:
        config = load_runtime_config(client, warehouse_id, catalog, schema)
        supervisor_name = config.get("supervisor_name")
        if not supervisor_name:
            return set()
        tools = list_supervisor_tools(client, supervisor_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unable to verify Supervisor tools before cleanup: {exc}") from exc

    names: set[str] = set()
    for tool in tools:
        uc_name = (tool.get("uc_function") or {}).get("name")
        if isinstance(uc_name, str) and uc_name:
            names.add(uc_name.rsplit(".", 1)[-1])
    return names


def main() -> int:
    args = parse_args()
    client = build_workspace_client(args.profile)
    existing_functions = list_schema_functions(
        client,
        args.warehouse_id,
        args.catalog,
        args.schema,
    )
    supervisor_functions = supervisor_uc_function_names(
        client,
        args.warehouse_id,
        args.catalog,
        args.schema,
    )

    protected_functions = ACTIVE_RUNTIME_FUNCTIONS | supervisor_functions
    drop_candidates = sorted(
        function_name
        for function_name in LEGACY_OR_DEBUG_FUNCTIONS
        if function_name in existing_functions and function_name not in protected_functions
    )

    plan = {
        "mode": "execute" if args.execute else "dry_run",
        "catalog": args.catalog,
        "schema": args.schema,
        "existing_functions": sorted(existing_functions),
        "supervisor_uc_functions": sorted(supervisor_functions),
        "protected_functions": sorted(protected_functions),
        "drop_candidates": drop_candidates,
    }
    print(json.dumps(plan, indent=2, sort_keys=True))

    if not args.execute:
        return 0

    for function_name in drop_candidates:
        execute_statement(
            client,
            args.warehouse_id,
            (
                f"DROP FUNCTION IF EXISTS "
                f"{quote_identifier(args.catalog)}.{quote_identifier(args.schema)}.{quote_identifier(function_name)}"
            ),
        )
        print(f"Dropped {args.catalog}.{args.schema}.{function_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
