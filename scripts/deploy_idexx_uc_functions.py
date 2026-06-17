#!/usr/bin/env python3
"""Deploy Unity Catalog SQL functions for the IDEXX diagnostics demo."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from generate_idexx_demo_data import execute_statements


DEFAULT_CATALOG = "pavan_naidu"
DEFAULT_SCHEMA = "nba"
DEFAULT_PROFILE = "FEVM"
DEFAULT_WAREHOUSE_ID = "e0c50bf18fca9e7f"


def render_runtime_functions_sql(catalog: str, schema: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    runtime_overrides = (repo_root / "sql" / "create_idexx_runtime_uc_functions.sql").read_text()
    return runtime_overrides.replace("pavan_naidu.nba", f"{catalog}.{schema}")


def render_legacy_functions_sql(catalog: str, schema: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    legacy_sql = (repo_root / "sql" / "create_idexx_uc_functions.sql").read_text()
    return legacy_sql.replace("pavan_naidu.nba", f"{catalog}.{schema}")


def render_functions_sql(catalog: str, schema: str, *, include_legacy: bool = False) -> str:
    statements = [render_runtime_functions_sql(catalog, schema)]
    if include_legacy:
        statements.insert(0, render_legacy_functions_sql(catalog, schema))
    return "\n\n".join(statements)


def split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    index = 0

    while index < len(sql_text):
        char = sql_text[index]

        if char == "'":
            current.append(char)
            # Databricks SQL escapes single quotes inside string literals with ''.
            if in_single_quote and index + 1 < len(sql_text) and sql_text[index + 1] == "'":
                current.append(sql_text[index + 1])
                index += 2
                continue
            in_single_quote = not in_single_quote
            index += 1
            continue

        if char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    return statements


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    parser.add_argument("--output-dir", default="generated")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Also deploy baseline/eval-only legacy functions from create_idexx_uc_functions.sql.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered_sql = render_functions_sql(
        args.catalog,
        args.schema,
        include_legacy=args.include_legacy,
    )
    rendered_path = output_dir / "create_idexx_uc_functions.rendered.sql"
    rendered_path.write_text(rendered_sql)

    execute_statements(
        split_sql_statements(rendered_sql),
        args.profile,
        args.warehouse_id,
    )
    print(f"Deployed Unity Catalog functions to {args.catalog}.{args.schema}")
    print(f"Rendered SQL: {rendered_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
