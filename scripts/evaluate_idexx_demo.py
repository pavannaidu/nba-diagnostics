#!/usr/bin/env python3
"""Lightweight validation for the IDEXX synthetic demo tables."""

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


def fetch_rows(statement: str, profile: str, warehouse_id: str) -> List[List[str]]:
    payload = run_statement(statement, profile, warehouse_id)
    return payload.get("result", {}).get("data_array", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    args = parser.parse_args()

    fq = f"{args.catalog}.{args.schema}"
    count_sql = f"""
    SELECT 'patients' AS table_name, count(*) AS row_count FROM {fq}.patients
    UNION ALL
    SELECT 'visits', count(*) FROM {fq}.visits
    UNION ALL
    SELECT 'visit_signals', count(*) FROM {fq}.visit_signals
    UNION ALL
    SELECT 'diagnostic_history', count(*) FROM {fq}.diagnostic_history
    UNION ALL
    SELECT 'treatments_history', count(*) FROM {fq}.treatments_history
    UNION ALL
    SELECT 'visit_notes', count(*) FROM {fq}.visit_notes
    UNION ALL
    SELECT 'visit_labels', count(*) FROM {fq}.visit_labels
    UNION ALL
    SELECT 'diagnostic_catalog', count(*) FROM {fq}.diagnostic_catalog
    ORDER BY table_name
    """
    metric_sql = f"""
    WITH ranked AS (
      SELECT
        visit_id,
        test_code,
        row_number() OVER (PARTITION BY visit_id ORDER BY utility_score DESC, test_code) AS rank_position
      FROM {fq}.visit_candidate_features_gold
      WHERE suppress_duplicate = false
    ),
    pivoted AS (
      SELECT
        visit_id,
        max(CASE WHEN rank_position = 1 THEN test_code END) AS predicted_rank_1,
        max(CASE WHEN rank_position = 2 THEN test_code END) AS predicted_rank_2,
        max(CASE WHEN rank_position = 3 THEN test_code END) AS predicted_rank_3
      FROM ranked
      WHERE rank_position <= 3
      GROUP BY visit_id
    )
    SELECT
      count(*) AS visits_scored,
      round(avg(CASE WHEN p.predicted_rank_1 = l.expected_rank_1 THEN 1.0 ELSE 0.0 END), 4) AS top1_match_rate,
      round(avg(CASE WHEN array_contains(array(p.predicted_rank_1, p.predicted_rank_2, p.predicted_rank_3), l.expected_rank_1) THEN 1.0 ELSE 0.0 END), 4) AS top3_contains_rank1_rate,
      round(avg(CASE WHEN l.no_additional_diagnostic_now THEN 1.0 ELSE 0.0 END), 4) AS abstain_rate
    FROM {fq}.visit_labels l
    INNER JOIN pivoted p
      ON p.visit_id = l.visit_id
    """

    print("Row counts:")
    for table_name, row_count in fetch_rows(count_sql, args.profile, args.warehouse_id):
      print(f"  {table_name}: {row_count}")

    metrics = fetch_rows(metric_sql, args.profile, args.warehouse_id)
    if metrics:
      visits_scored, top1_match, top3_match, abstain_rate = metrics[0]
      print("\nHeuristic ranking checks:")
      print(f"  visits_scored: {visits_scored}")
      print(f"  top1_match_rate: {top1_match}")
      print(f"  top3_contains_rank1_rate: {top3_match}")
      print(f"  abstain_rate: {abstain_rate}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        raise

