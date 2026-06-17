#!/usr/bin/env python3
"""Generate and optionally load a synthetic dog-only IDEXX diagnostics demo dataset."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_CATALOG = "pavan_naidu"
DEFAULT_SCHEMA = "nba"
DEFAULT_PROFILE = "FEVM"
DEFAULT_WAREHOUSE_ID = "e0c50bf18fca9e7f"
DEFAULT_DOG_COUNT = 300
DEFAULT_SEED = 42


DOG_NAMES = [
    "Bailey", "Bella", "Charlie", "Cooper", "Daisy", "Duke", "Ellie", "Finn",
    "Frankie", "Gus", "Hazel", "Henry", "Jax", "Kona", "Lily", "Loki", "Lucy",
    "Luna", "Maggie", "Milo", "Murphy", "Nala", "Ollie", "Penny", "Piper",
    "Rosie", "Ruby", "Sadie", "Scout", "Stella", "Teddy", "Winston", "Zoe",
]


BREEDS = [
    {"breed": "Labrador Retriever", "min_weight": 24.0, "max_weight": 36.0, "renal_bias": 0.1, "gi_bias": 0.0, "endo_bias": 0.1},
    {"breed": "Golden Retriever", "min_weight": 25.0, "max_weight": 34.0, "renal_bias": 0.05, "gi_bias": 0.05, "endo_bias": 0.1},
    {"breed": "French Bulldog", "min_weight": 9.0, "max_weight": 14.0, "renal_bias": 0.0, "gi_bias": 0.15, "endo_bias": 0.05},
    {"breed": "German Shepherd", "min_weight": 22.0, "max_weight": 40.0, "renal_bias": 0.05, "gi_bias": 0.0, "endo_bias": 0.05},
    {"breed": "Poodle", "min_weight": 18.0, "max_weight": 32.0, "renal_bias": 0.05, "gi_bias": 0.05, "endo_bias": 0.15},
    {"breed": "Beagle", "min_weight": 9.0, "max_weight": 14.0, "renal_bias": 0.0, "gi_bias": 0.1, "endo_bias": 0.05},
    {"breed": "Boxer", "min_weight": 22.0, "max_weight": 34.0, "renal_bias": 0.05, "gi_bias": 0.05, "endo_bias": 0.05},
    {"breed": "Dachshund", "min_weight": 7.0, "max_weight": 15.0, "renal_bias": 0.0, "gi_bias": 0.1, "endo_bias": 0.0},
    {"breed": "Yorkshire Terrier", "min_weight": 2.5, "max_weight": 4.5, "renal_bias": 0.1, "gi_bias": 0.05, "endo_bias": 0.0},
    {"breed": "Mixed Breed", "min_weight": 8.0, "max_weight": 30.0, "renal_bias": 0.03, "gi_bias": 0.03, "endo_bias": 0.03},
]


TEST_CATALOG = [
    {
        "test_code": "WELLNESS_PANEL",
        "test_name": "Adult Wellness Panel",
        "category": "chemistry_cbc",
        "specimen_type": "blood",
        "primary_cohort": "wellness",
        "duplicate_suppression_window_days": 240,
    },
    {
        "test_code": "HEARTWORM_4DX",
        "test_name": "SNAP 4Dx Plus",
        "category": "screening",
        "specimen_type": "blood",
        "primary_cohort": "wellness",
        "duplicate_suppression_window_days": 300,
    },
    {
        "test_code": "FECAL_SCREEN",
        "test_name": "Fecal Ova and Parasite Screen",
        "category": "parasitology",
        "specimen_type": "feces",
        "primary_cohort": "wellness",
        "duplicate_suppression_window_days": 150,
    },
    {
        "test_code": "GI_PANEL",
        "test_name": "Canine GI Laboratory Panel",
        "category": "gastroenterology",
        "specimen_type": "blood",
        "primary_cohort": "gi",
        "duplicate_suppression_window_days": 75,
    },
    {
        "test_code": "FECAL_PCR",
        "test_name": "Canine Diarrhea RealPCR Panel",
        "category": "gastroenterology",
        "specimen_type": "feces",
        "primary_cohort": "gi",
        "duplicate_suppression_window_days": 45,
    },
    {
        "test_code": "URINALYSIS",
        "test_name": "Complete Urinalysis",
        "category": "urinary",
        "specimen_type": "urine",
        "primary_cohort": "renal_urinary",
        "duplicate_suppression_window_days": 30,
    },
    {
        "test_code": "URINE_CULTURE",
        "test_name": "Urine Culture and Sensitivity",
        "category": "urinary",
        "specimen_type": "urine",
        "primary_cohort": "renal_urinary",
        "duplicate_suppression_window_days": 45,
    },
    {
        "test_code": "SDMA_CHEM17",
        "test_name": "SDMA with Chemistry 17",
        "category": "renal",
        "specimen_type": "blood",
        "primary_cohort": "renal_urinary",
        "duplicate_suppression_window_days": 60,
    },
    {
        "test_code": "UPC_RATIO",
        "test_name": "Urine Protein Creatinine Ratio",
        "category": "renal",
        "specimen_type": "urine",
        "primary_cohort": "renal_urinary",
        "duplicate_suppression_window_days": 75,
    },
    {
        "test_code": "THYROID_PANEL",
        "test_name": "Canine Thyroid Panel",
        "category": "endocrine",
        "specimen_type": "blood",
        "primary_cohort": "endocrine_metabolic",
        "duplicate_suppression_window_days": 120,
    },
    {
        "test_code": "FRUCTOSAMINE",
        "test_name": "Fructosamine",
        "category": "endocrine",
        "specimen_type": "blood",
        "primary_cohort": "endocrine_metabolic",
        "duplicate_suppression_window_days": 75,
    },
    {
        "test_code": "CORTISOL_SCREEN",
        "test_name": "Resting Cortisol Screen",
        "category": "endocrine",
        "specimen_type": "blood",
        "primary_cohort": "endocrine_metabolic",
        "duplicate_suppression_window_days": 120,
    },
]


SUPPRESSION_WINDOWS = {
    row["test_code"]: row["duplicate_suppression_window_days"] for row in TEST_CATALOG
}


OWNER_NOTE_TEMPLATES = {
    "wellness": [
        "Owner reports {dog_name} is here for a routine preventive visit with no major concerns.",
        "{dog_name} is due for a wellness recheck and owner would like to stay current on screening.",
    ],
    "gi": [
        "Owner reports {dog_name} has had {duration_days} days of vomiting and diarrhea with {energy_phrase}.",
        "{dog_name} has intermittent GI upset for {duration_days} days and the owner is worried about recurrence.",
    ],
    "renal_urinary": [
        "Owner reports {dog_name} is drinking more, having accidents, and symptoms started about {duration_days} days ago.",
        "{dog_name} has urinary signs for {duration_days} days including accidents and some straining.",
    ],
    "endocrine_metabolic": [
        "Owner reports {dog_name} has had increased thirst, appetite changes, and lower energy over the last {duration_days} days.",
        "{dog_name} is eating differently with weight change concerns and symptoms present for {duration_days} days.",
    ],
}


CLINICIAN_NOTE_TEMPLATES = {
    "wellness": [
        "Bright and alert preventive visit. No focal concerns on intake; screening needs reviewed.",
        "Routine wellness assessment. Stable exam history with preventive diagnostics considered by age and last test dates.",
    ],
    "gi": [
        "GI signs noted on intake. Considering stool-based and blood-based workup depending severity and chronicity.",
        "History suggests active GI episode; want to avoid repeating very recent tests unless severity warrants it.",
    ],
    "renal_urinary": [
        "Urinary / renal history reviewed with attention to duplicate suppression on recent UA and chemistry workup.",
        "Current history raises urinary tract or renal monitoring questions; prior test timing may change ranking.",
    ],
    "endocrine_metabolic": [
        "Metabolic / endocrine concern based on intake pattern. Differential depends on thirst, appetite, weight, and prior testing.",
        "History consistent with endocrine screening workflow; test ranking should reflect recent monitoring where applicable.",
    ],
}


CREATE_TABLES_SQL = """
CREATE SCHEMA IF NOT EXISTS {catalog}.{schema};

CREATE OR REPLACE TABLE {catalog}.{schema}.patients (
  patient_id STRING,
  dog_name STRING,
  breed STRING,
  sex STRING,
  spay_neuter_status STRING,
  birth_date DATE,
  age_years DOUBLE,
  weight_kg DOUBLE,
  life_stage STRING,
  chronic_kidney_disease BOOLEAN,
  chronic_gi BOOLEAN,
  chronic_endocrine BOOLEAN,
  food_sensitivity BOOLEAN
);

CREATE OR REPLACE TABLE {catalog}.{schema}.visits (
  visit_id STRING,
  patient_id STRING,
  visit_date DATE,
  clinic_id STRING,
  visit_type STRING,
  presenting_complaint STRING,
  cohort STRING,
  severity STRING,
  days_since_last_visit INT
);

CREATE OR REPLACE TABLE {catalog}.{schema}.visit_signals (
  visit_id STRING,
  vomiting BOOLEAN,
  diarrhea BOOLEAN,
  urinary_accidents BOOLEAN,
  straining_to_urinate BOOLEAN,
  increased_thirst BOOLEAN,
  increased_hunger BOOLEAN,
  appetite_change STRING,
  lethargy BOOLEAN,
  weight_change_pct DOUBLE,
  duration_days INT,
  urgency_level STRING,
  recent_diet_change BOOLEAN,
  previous_same_issue BOOLEAN
);

CREATE OR REPLACE TABLE {catalog}.{schema}.diagnostic_history (
  diagnostic_event_id STRING,
  patient_id STRING,
  source_visit_id STRING,
  test_code STRING,
  test_category STRING,
  ordered_at DATE,
  result_status STRING,
  abnormal_flag BOOLEAN,
  key_finding STRING
);

CREATE OR REPLACE TABLE {catalog}.{schema}.treatments_history (
  treatment_event_id STRING,
  patient_id STRING,
  source_visit_id STRING,
  treatment_name STRING,
  treatment_category STRING,
  started_at DATE,
  response_status STRING,
  note STRING
);

CREATE OR REPLACE TABLE {catalog}.{schema}.visit_notes (
  visit_id STRING,
  owner_note STRING,
  clinician_note STRING
);

CREATE OR REPLACE TABLE {catalog}.{schema}.diagnostic_catalog (
  test_code STRING,
  test_name STRING,
  category STRING,
  specimen_type STRING,
  primary_cohort STRING,
  duplicate_suppression_window_days INT
);

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.live_intake_requests (
  request_id STRING,
  patient_id STRING,
  as_of_ts STRING,
  payload_json STRING,
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS {catalog}.{schema}.runtime_config (
  config_key STRING,
  config_value STRING,
  updated_at TIMESTAMP
);

CREATE OR REPLACE TABLE {catalog}.{schema}.visit_labels (
  visit_id STRING,
  expected_rank_1 STRING,
  expected_rank_2 STRING,
  expected_rank_3 STRING,
  no_additional_diagnostic_now BOOLEAN,
  supported_cohort STRING,
  label_reason_code STRING
);
"""


def stable_uuid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def iso_date(value: dt.date) -> str:
    return value.isoformat()


def quote_sql(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NULL"
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def insert_statements(
    catalog: str,
    schema: str,
    table_name: str,
    columns: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    batch_size: int = 200,
) -> List[str]:
    statements: List[str] = []
    if not rows:
        return statements

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        values_sql = []
        for row in batch:
            values_sql.append(
                "(" + ", ".join(quote_sql(row[column]) for column in columns) + ")"
            )
        statements.append(
            f"INSERT INTO {catalog}.{schema}.{table_name} ({', '.join(columns)}) VALUES\n"
            + ",\n".join(values_sql)
        )
    return statements


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
        raise RuntimeError(payload)
    return payload


def choose_life_stage(age_years: float) -> str:
    if age_years < 2.0:
        return "young_adult"
    if age_years < 7.0:
        return "adult"
    if age_years < 10.0:
        return "mature"
    return "senior"


def pick_breed(rng: random.Random) -> Dict[str, Any]:
    return rng.choice(BREEDS)


def maybe(probability: float, rng: random.Random) -> bool:
    return rng.random() < probability


def generate_patient(index: int, today: dt.date, rng: random.Random) -> Dict[str, Any]:
    breed_meta = pick_breed(rng)
    age_years = round(rng.uniform(1.2, 13.5), 1)
    birth_date = today - dt.timedelta(days=int(age_years * 365.25))
    weight_kg = round(rng.uniform(breed_meta["min_weight"], breed_meta["max_weight"]), 1)
    sex = rng.choice(["M", "F"])
    patient_id = f"DOG-{index:04d}"
    dog_name = rng.choice(DOG_NAMES)
    life_stage = choose_life_stage(age_years)
    chronic_kidney_disease = maybe(0.03 + (0.10 if age_years >= 9 else 0.0) + breed_meta["renal_bias"], rng)
    chronic_gi = maybe(0.05 + breed_meta["gi_bias"], rng)
    chronic_endocrine = maybe(0.04 + (0.08 if age_years >= 8 else 0.0) + breed_meta["endo_bias"], rng)
    food_sensitivity = maybe(0.07 + (0.05 if chronic_gi else 0.0), rng)

    return {
        "patient_id": patient_id,
        "dog_name": dog_name,
        "breed": breed_meta["breed"],
        "sex": sex,
        "spay_neuter_status": rng.choice(["spayed", "neutered", "intact"]),
        "birth_date": birth_date,
        "age_years": age_years,
        "weight_kg": weight_kg,
        "life_stage": life_stage,
        "chronic_kidney_disease": chronic_kidney_disease,
        "chronic_gi": chronic_gi,
        "chronic_endocrine": chronic_endocrine,
        "food_sensitivity": food_sensitivity,
    }


def choose_visit_dates(today: dt.date, visit_count: int, rng: random.Random) -> List[dt.date]:
    start = today - dt.timedelta(days=540)
    current = start + dt.timedelta(days=rng.randint(0, 35))
    dates = []
    for _ in range(visit_count):
        current = current + dt.timedelta(days=rng.randint(35, 110))
        if current >= today:
            current = today - dt.timedelta(days=rng.randint(5, 25))
        dates.append(current)
    dates = sorted(set(dates))
    while len(dates) < visit_count:
        candidate = today - dt.timedelta(days=rng.randint(5, 520))
        if candidate not in dates:
            dates.append(candidate)
    return sorted(dates)


def days_since_last(history: Sequence[Dict[str, Any]], test_code: str, visit_date: dt.date) -> Optional[int]:
    matching = [
        (visit_date - event["ordered_at"]).days
        for event in history
        if event["test_code"] == test_code and event["ordered_at"] < visit_date
    ]
    return min(matching) if matching else None


def due_for_test(
    history: Sequence[Dict[str, Any]],
    test_code: str,
    visit_date: dt.date,
    allow_monitor_override: bool = False,
) -> bool:
    days = days_since_last(history, test_code, visit_date)
    if days is None:
        return True
    window = SUPPRESSION_WINDOWS[test_code]
    if allow_monitor_override:
        return days >= max(45, int(window * 0.65))
    return days >= window


def ranked_tests(
    base_order: Sequence[str],
    history: Sequence[Dict[str, Any]],
    visit_date: dt.date,
    allow_monitor_override: Optional[Sequence[str]] = None,
) -> List[str]:
    override = set(allow_monitor_override or [])
    ranked: List[str] = []
    seen = set()
    for test_code in base_order:
        if test_code in seen:
            continue
        seen.add(test_code)
        if due_for_test(history, test_code, visit_date, test_code in override):
            ranked.append(test_code)
        if len(ranked) == 3:
            break
    return ranked


def complaint_for_cohort(cohort: str, subtype: str, rng: random.Random) -> str:
    mapping = {
        "wellness": ["annual wellness visit", "senior preventive recheck", "routine health maintenance visit"],
        "gi": ["vomiting and diarrhea", "recurrent GI upset", "decreased appetite with GI signs"],
        "renal_urinary": ["increased thirst and urinary accidents", "straining to urinate", "renal monitoring recheck"],
        "endocrine_metabolic": ["PU/PD and weight change", "endocrine screening follow-up", "metabolic concern recheck"],
    }
    return rng.choice(mapping[cohort])


def scenario_from_history(
    patient: Dict[str, Any],
    state: Dict[str, Any],
    visit_date: dt.date,
    rng: random.Random,
) -> Tuple[str, str]:
    last_issue = state.get("last_issue_cohort")
    if last_issue and maybe(0.22, rng):
        if last_issue == "endocrine_metabolic":
            return last_issue, rng.choice(["diabetes", "thyroid", "cortisol"])
        if last_issue == "renal_urinary":
            return last_issue, rng.choice(["uti", "ckd_monitor"])
        if last_issue == "gi":
            return last_issue, rng.choice(["acute", "chronic"])

    weights = [
        ("wellness", 0.34),
        ("gi", 0.24 + (0.12 if patient["chronic_gi"] else 0.0)),
        ("renal_urinary", 0.20 + (0.12 if patient["chronic_kidney_disease"] else 0.0)),
        ("endocrine_metabolic", 0.22 + (0.12 if patient["chronic_endocrine"] else 0.0)),
    ]
    total = sum(weight for _, weight in weights)
    draw = rng.random() * total
    cumulative = 0.0
    chosen = "wellness"
    for cohort, weight in weights:
        cumulative += weight
        if draw <= cumulative:
            chosen = cohort
            break

    subtype_map = {
        "wellness": ["screening"],
        "gi": ["acute", "chronic"],
        "renal_urinary": ["uti", "ckd_monitor"],
        "endocrine_metabolic": ["diabetes", "thyroid", "cortisol"],
    }
    return chosen, rng.choice(subtype_map[chosen])


def render_owner_note(
    cohort: str,
    patient: Dict[str, Any],
    duration_days: int,
    lethargy: bool,
    rng: random.Random,
) -> str:
    template = rng.choice(OWNER_NOTE_TEMPLATES[cohort])
    return template.format(
        dog_name=patient["dog_name"],
        duration_days=duration_days,
        energy_phrase="lower energy" if lethargy else "stable energy",
    )


def render_clinician_note(cohort: str, rng: random.Random) -> str:
    return rng.choice(CLINICIAN_NOTE_TEMPLATES[cohort])


def abnormal_finding(test_code: str, cohort: str, subtype: str, rng: random.Random) -> Tuple[str, bool]:
    findings = {
        "WELLNESS_PANEL": {
            "wellness": ("mild age-appropriate screening abnormalities not yet worked up", maybe(0.18, rng)),
            "gi": ("mild hemoconcentration with GI losses considered", True),
            "renal_urinary": ("mild azotemia and renal chemistry changes", True),
            "endocrine_metabolic": ("metabolic pattern supports endocrine follow-up", True),
        },
        "HEARTWORM_4DX": {"wellness": ("screen negative", False)},
        "FECAL_SCREEN": {"wellness": ("screen negative", False), "gi": ("parasite screen pending or equivocal", maybe(0.35, rng))},
        "GI_PANEL": {"gi": ("GI biomarkers support enteropathy workup", True)},
        "FECAL_PCR": {"gi": ("PCR flagged enteric pathogen target", maybe(0.50, rng))},
        "URINALYSIS": {"renal_urinary": ("urinalysis shows low specific gravity or active sediment", True), "endocrine_metabolic": ("glucosuria or dilute urine present", True)},
        "URINE_CULTURE": {"renal_urinary": ("culture pending or positive bacterial growth", maybe(0.45, rng))},
        "SDMA_CHEM17": {"renal_urinary": ("renal biomarkers elevated for current stage", True)},
        "UPC_RATIO": {"renal_urinary": ("proteinuria trend requires follow-up", True)},
        "THYROID_PANEL": {"endocrine_metabolic": ("thyroid profile is low and consistent with hypothyroid screening concern", True)},
        "FRUCTOSAMINE": {"endocrine_metabolic": ("fructosamine elevated and consistent with diabetes mellitus", True)},
        "CORTISOL_SCREEN": {"endocrine_metabolic": ("cortisol screen positive and merits confirmatory testing", maybe(0.55, rng))},
    }
    finding, abnormal = findings.get(test_code, {}).get(cohort, ("result pending", False))
    return finding, abnormal


def treatment_bundle(cohort: str, subtype: str) -> List[Tuple[str, str]]:
    bundles = {
        ("wellness", "screening"): [("preventive plan update", "monitoring")],
        ("gi", "acute"): [("supportive GI care", "medical"), ("diet trial recommendation", "nutrition")],
        ("gi", "chronic"): [("GI diet transition", "nutrition"), ("probiotic trial", "medical")],
        ("renal_urinary", "uti"): [("pending urine culture treatment plan", "medical")],
        ("renal_urinary", "ckd_monitor"): [("renal diet continuation", "nutrition"), ("renal monitoring plan", "monitoring")],
        ("endocrine_metabolic", "diabetes"): [("glucose monitoring discussion", "monitoring")],
        ("endocrine_metabolic", "thyroid"): [("thyroid medication review", "medical")],
        ("endocrine_metabolic", "cortisol"): [("endocrine follow-up planning", "monitoring")],
    }
    return bundles.get((cohort, subtype), [("follow-up plan", "monitoring")])


def build_visit_payload(
    patient: Dict[str, Any],
    state: Dict[str, Any],
    visit_index: int,
    visit_date: dt.date,
    rng: random.Random,
) -> Dict[str, Any]:
    cohort, subtype = scenario_from_history(patient, state, visit_date, rng)
    last_visit_date = state["visit_dates"][-1] if state["visit_dates"] else None
    days_since_last_visit = (visit_date - last_visit_date).days if last_visit_date else None
    severity = "low"
    visit_type = "wellness"
    duration_days = rng.randint(1, 7)
    previous_same_issue = False
    weight_change_pct = 0.0
    signals = {
        "vomiting": False,
        "diarrhea": False,
        "urinary_accidents": False,
        "straining_to_urinate": False,
        "increased_thirst": False,
        "increased_hunger": False,
        "appetite_change": "stable",
        "lethargy": False,
        "weight_change_pct": 0.0,
        "duration_days": duration_days,
        "urgency_level": "routine",
        "recent_diet_change": False,
        "previous_same_issue": False,
    }
    label_reason_code = f"{cohort}_{subtype}"
    allow_monitor_override: List[str] = []
    test_order: List[str]
    force_abstain = False

    if cohort == "wellness":
        visit_type = "wellness"
        duration_days = 0
        signals["duration_days"] = 0
        signals["urgency_level"] = "routine"
        label_reason_code = "wellness_due"
        base_order = ["WELLNESS_PANEL", "HEARTWORM_4DX", "FECAL_SCREEN", "SDMA_CHEM17"]
        if patient["life_stage"] in {"mature", "senior"} or patient["chronic_kidney_disease"]:
            base_order = ["WELLNESS_PANEL", "SDMA_CHEM17", "HEARTWORM_4DX", "FECAL_SCREEN"]
        test_order = ranked_tests(base_order, state["diagnostics"], visit_date, allow_monitor_override=["SDMA_CHEM17"])
        if not test_order:
            force_abstain = True
            label_reason_code = "wellness_recent_abstain"
    elif cohort == "gi":
        visit_type = "illness"
        signals["vomiting"] = maybe(0.72, rng)
        signals["diarrhea"] = True
        signals["lethargy"] = maybe(0.42, rng)
        signals["recent_diet_change"] = maybe(0.30, rng)
        signals["previous_same_issue"] = maybe(0.28 if patient["chronic_gi"] else 0.12, rng)
        signals["appetite_change"] = rng.choice(["decreased", "stable"])
        duration_days = rng.randint(2, 14 if subtype == "chronic" else 5)
        signals["duration_days"] = duration_days
        signals["weight_change_pct"] = round(rng.uniform(-5.5, -0.5), 1) if subtype == "chronic" else round(rng.uniform(-2.0, 0.5), 1)
        severity = rng.choice(["moderate", "moderate", "high"]) if subtype == "acute" else rng.choice(["low", "moderate"])
        signals["urgency_level"] = "soon" if severity == "high" else "routine"
        previous_same_issue = signals["previous_same_issue"]
        label_reason_code = f"gi_{subtype}"
        base_order = ["GI_PANEL", "FECAL_PCR", "WELLNESS_PANEL", "FECAL_SCREEN"]
        if subtype == "acute":
            base_order = ["FECAL_PCR", "GI_PANEL", "WELLNESS_PANEL", "FECAL_SCREEN"]
        test_order = ranked_tests(base_order, state["diagnostics"], visit_date)
        if previous_same_issue and duration_days <= 3 and maybe(0.25, rng):
            force_abstain = True
            label_reason_code = "gi_recent_workup_abstain"
    elif cohort == "renal_urinary":
        visit_type = "illness" if subtype == "uti" else "recheck"
        signals["increased_thirst"] = maybe(0.68 if subtype == "ckd_monitor" else 0.35, rng)
        signals["urinary_accidents"] = maybe(0.74 if subtype == "uti" else 0.40, rng)
        signals["straining_to_urinate"] = maybe(0.60 if subtype == "uti" else 0.15, rng)
        signals["lethargy"] = maybe(0.36, rng)
        signals["duration_days"] = rng.randint(2, 8 if subtype == "uti" else 21)
        signals["weight_change_pct"] = round(rng.uniform(-4.5, -0.5), 1) if subtype == "ckd_monitor" else round(rng.uniform(-1.5, 0.5), 1)
        signals["appetite_change"] = rng.choice(["decreased", "stable"])
        severity = rng.choice(["moderate", "high"]) if subtype == "uti" else rng.choice(["low", "moderate"])
        signals["urgency_level"] = "soon" if subtype == "uti" else "routine"
        previous_same_issue = maybe(0.32 if patient["chronic_kidney_disease"] else 0.12, rng)
        signals["previous_same_issue"] = previous_same_issue
        allow_monitor_override = ["SDMA_CHEM17", "URINALYSIS", "UPC_RATIO"]
        label_reason_code = f"renal_{subtype}"
        base_order = ["URINALYSIS", "URINE_CULTURE", "SDMA_CHEM17", "UPC_RATIO"]
        if subtype == "ckd_monitor":
            base_order = ["SDMA_CHEM17", "URINALYSIS", "UPC_RATIO", "URINE_CULTURE"]
        test_order = ranked_tests(base_order, state["diagnostics"], visit_date, allow_monitor_override=allow_monitor_override)
        if subtype == "ckd_monitor" and maybe(0.18, rng) and not due_for_test(state["diagnostics"], "SDMA_CHEM17", visit_date, True):
            force_abstain = True
            label_reason_code = "renal_stable_recent_monitoring"
    else:
        visit_type = "illness" if subtype in {"diabetes", "cortisol"} else "recheck"
        signals["increased_thirst"] = maybe(0.78 if subtype in {"diabetes", "cortisol"} else 0.28, rng)
        signals["increased_hunger"] = maybe(0.66 if subtype in {"diabetes", "cortisol"} else 0.18, rng)
        signals["lethargy"] = maybe(0.56 if subtype == "thyroid" else 0.34, rng)
        signals["appetite_change"] = "increased" if subtype in {"diabetes", "cortisol"} else rng.choice(["stable", "decreased"])
        signals["weight_change_pct"] = round(rng.uniform(-8.0, -1.0), 1) if subtype == "diabetes" else round(rng.uniform(1.0, 8.0), 1)
        signals["duration_days"] = rng.randint(7, 35)
        signals["urgency_level"] = "routine"
        signals["previous_same_issue"] = maybe(0.42 if patient["chronic_endocrine"] else 0.10, rng)
        allow_monitor_override = ["THYROID_PANEL", "FRUCTOSAMINE", "CORTISOL_SCREEN"]
        label_reason_code = f"endo_{subtype}"
        if subtype == "diabetes":
            base_order = ["FRUCTOSAMINE", "URINALYSIS", "WELLNESS_PANEL", "SDMA_CHEM17"]
        elif subtype == "thyroid":
            base_order = ["THYROID_PANEL", "WELLNESS_PANEL", "URINALYSIS", "FRUCTOSAMINE"]
        else:
            base_order = ["CORTISOL_SCREEN", "URINALYSIS", "WELLNESS_PANEL", "FRUCTOSAMINE"]
        test_order = ranked_tests(base_order, state["diagnostics"], visit_date, allow_monitor_override=allow_monitor_override)
        if patient["chronic_endocrine"] and maybe(0.16, rng) and signals["previous_same_issue"]:
            force_abstain = True
            label_reason_code = "endo_stable_recent_monitoring"

    if force_abstain:
        expected_rank_1 = None
        expected_rank_2 = None
        expected_rank_3 = None
        no_additional = True
        test_order = []
    else:
        expected_rank_1 = test_order[0] if len(test_order) > 0 else None
        expected_rank_2 = test_order[1] if len(test_order) > 1 else None
        expected_rank_3 = test_order[2] if len(test_order) > 2 else None
        no_additional = expected_rank_1 is None
        if no_additional:
            label_reason_code = f"{cohort}_no_due_tests"

    visit_id = f"VISIT-{patient['patient_id']}-{visit_index + 1:02d}"
    presenting_complaint = complaint_for_cohort(cohort, subtype, rng)
    owner_note = render_owner_note(cohort, patient, signals["duration_days"], signals["lethargy"], rng)
    clinician_note = render_clinician_note(cohort, rng)

    diagnostics_to_add: List[Dict[str, Any]] = []
    if not no_additional:
        for position, test_code in enumerate([expected_rank_1, expected_rank_2, expected_rank_3]):
            if not test_code:
                continue
            chance = 0.88 if position == 0 else 0.64 if position == 1 else 0.42
            if maybe(chance, rng):
                finding, abnormal = abnormal_finding(test_code, cohort, subtype, rng)
                ordered_at = visit_date + dt.timedelta(days=0 if position == 0 else rng.randint(0, 2))
                diagnostics_to_add.append(
                    {
                        "diagnostic_event_id": stable_uuid("diag"),
                        "patient_id": patient["patient_id"],
                        "source_visit_id": visit_id,
                        "test_code": test_code,
                        "test_category": next(row["category"] for row in TEST_CATALOG if row["test_code"] == test_code),
                        "ordered_at": ordered_at,
                        "result_status": "FINAL",
                        "abnormal_flag": abnormal,
                        "key_finding": finding,
                    }
                )

    treatments_to_add: List[Dict[str, Any]] = []
    if cohort != "wellness" or maybe(0.25, rng):
        for treatment_name, treatment_category in treatment_bundle(cohort, subtype):
            treatments_to_add.append(
                {
                    "treatment_event_id": stable_uuid("tx"),
                    "patient_id": patient["patient_id"],
                    "source_visit_id": visit_id,
                    "treatment_name": treatment_name,
                    "treatment_category": treatment_category,
                    "started_at": visit_date,
                    "response_status": rng.choice(["planned", "improving", "monitor"]),
                    "note": f"{cohort} follow-up context for {patient['dog_name']}",
                }
            )

    state["visit_dates"].append(visit_date)
    state["last_issue_cohort"] = cohort if cohort != "wellness" else state.get("last_issue_cohort")
    state["diagnostics"].extend(diagnostics_to_add)
    state["treatments"].extend(treatments_to_add)

    return {
        "visit": {
            "visit_id": visit_id,
            "patient_id": patient["patient_id"],
            "visit_date": visit_date,
            "clinic_id": rng.choice(["IDEXX-DEMO-01", "IDEXX-DEMO-02", "IDEXX-DEMO-03"]),
            "visit_type": visit_type,
            "presenting_complaint": presenting_complaint,
            "cohort": cohort,
            "severity": severity,
            "days_since_last_visit": days_since_last_visit,
        },
        "visit_signals": {
            "visit_id": visit_id,
            **signals,
        },
        "visit_notes": {
            "visit_id": visit_id,
            "owner_note": owner_note,
            "clinician_note": clinician_note,
        },
        "visit_label": {
            "visit_id": visit_id,
            "expected_rank_1": expected_rank_1,
            "expected_rank_2": expected_rank_2,
            "expected_rank_3": expected_rank_3,
            "no_additional_diagnostic_now": no_additional,
            "supported_cohort": cohort,
            "label_reason_code": label_reason_code,
        },
        "diagnostic_history": diagnostics_to_add,
        "treatments_history": treatments_to_add,
    }


def build_dataset(dog_count: int, seed: int) -> Dict[str, List[Dict[str, Any]]]:
    rng = random.Random(seed)
    today = dt.date.today()
    dataset = {
        "patients": [],
        "visits": [],
        "visit_signals": [],
        "diagnostic_history": [],
        "treatments_history": [],
        "visit_notes": [],
        "diagnostic_catalog": [dict(row) for row in TEST_CATALOG],
        "visit_labels": [],
    }

    for index in range(1, dog_count + 1):
        patient = generate_patient(index, today, rng)
        dataset["patients"].append(patient)
        state: Dict[str, Any] = {"visit_dates": [], "diagnostics": [], "treatments": [], "last_issue_cohort": None}
        visit_count = rng.randint(5, 8)
        for visit_index, visit_date in enumerate(choose_visit_dates(today, visit_count, rng)):
            payload = build_visit_payload(patient, state, visit_index, visit_date, rng)
            dataset["visits"].append(payload["visit"])
            dataset["visit_signals"].append(payload["visit_signals"])
            dataset["visit_notes"].append(payload["visit_notes"])
            dataset["visit_labels"].append(payload["visit_label"])
            dataset["diagnostic_history"].extend(payload["diagnostic_history"])
            dataset["treatments_history"].extend(payload["treatments_history"])
    return dataset


def render_load_sql(dataset: Dict[str, List[Dict[str, Any]]], catalog: str, schema: str) -> List[str]:
    statements = [
        statement.strip()
        for statement in CREATE_TABLES_SQL.format(catalog=catalog, schema=schema).split(";")
        if statement.strip()
    ]
    table_order = [
        ("patients", [
            "patient_id", "dog_name", "breed", "sex", "spay_neuter_status", "birth_date",
            "age_years", "weight_kg", "life_stage", "chronic_kidney_disease", "chronic_gi",
            "chronic_endocrine", "food_sensitivity",
        ]),
        ("visits", [
            "visit_id", "patient_id", "visit_date", "clinic_id", "visit_type", "presenting_complaint",
            "cohort", "severity", "days_since_last_visit",
        ]),
        ("visit_signals", [
            "visit_id", "vomiting", "diarrhea", "urinary_accidents", "straining_to_urinate",
            "increased_thirst", "increased_hunger", "appetite_change", "lethargy", "weight_change_pct",
            "duration_days", "urgency_level", "recent_diet_change", "previous_same_issue",
        ]),
        ("diagnostic_history", [
            "diagnostic_event_id", "patient_id", "source_visit_id", "test_code", "test_category",
            "ordered_at", "result_status", "abnormal_flag", "key_finding",
        ]),
        ("treatments_history", [
            "treatment_event_id", "patient_id", "source_visit_id", "treatment_name", "treatment_category",
            "started_at", "response_status", "note",
        ]),
        ("visit_notes", ["visit_id", "owner_note", "clinician_note"]),
        ("diagnostic_catalog", [
            "test_code", "test_name", "category", "specimen_type", "primary_cohort",
            "duplicate_suppression_window_days",
        ]),
        ("visit_labels", [
            "visit_id", "expected_rank_1", "expected_rank_2", "expected_rank_3",
            "no_additional_diagnostic_now", "supported_cohort", "label_reason_code",
        ]),
    ]
    for table_name, columns in table_order:
        statements.extend(insert_statements(catalog, schema, table_name, columns, dataset[table_name]))
    return statements


def write_gold_sql(output_dir: Path, catalog: str, schema: str) -> Path:
    template = (Path(__file__).resolve().parents[1] / "sql" / "create_idexx_demo_gold.sql").read_text()
    rendered = template.replace("pavan_naidu.nba", f"{catalog}.{schema}")
    target = output_dir / "create_idexx_demo_gold.rendered.sql"
    target.write_text(rendered)
    return target


def execute_statements(statements: Sequence[str], profile: str, warehouse_id: str) -> None:
    for idx, statement in enumerate(statements, start=1):
        print(f"[{idx}/{len(statements)}] Executing statement...")
        run_statement(statement, profile, warehouse_id)


def load_gold_sql(gold_sql_path: Path, profile: str, warehouse_id: str) -> None:
    statements = [
        statement.strip()
        for statement in gold_sql_path.read_text().split(";")
        if statement.strip()
    ]
    execute_statements(statements, profile, warehouse_id)


def write_preview(dataset: Dict[str, List[Dict[str, Any]]], output_dir: Path) -> None:
    preview = {
        "patients": dataset["patients"][:5],
        "visits": dataset["visits"][:8],
        "visit_labels": dataset["visit_labels"][:8],
    }
    (output_dir / "preview.json").write_text(json.dumps(preview, default=iso_date, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--warehouse-id", default=DEFAULT_WAREHOUSE_ID)
    parser.add_argument("--dogs", type=int, default=DEFAULT_DOG_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", default="generated")
    parser.add_argument("--execute", action="store_true", help="Execute the generated SQL against Databricks.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = repo_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset = build_dataset(args.dogs, args.seed)
    load_statements = render_load_sql(dataset, args.catalog, args.schema)
    load_sql_path = output_dir / "load_idexx_demo.sql"
    load_sql_path.write_text(";\n\n".join(load_statements) + ";\n")
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {table_name: len(rows) for table_name, rows in dataset.items()},
            indent=2,
            sort_keys=True,
        )
    )
    write_preview(dataset, output_dir)
    gold_sql_path = write_gold_sql(output_dir, args.catalog, args.schema)

    print(textwrap.dedent(
        f"""
        Generated IDEXX demo dataset.
          output_dir: {output_dir}
          dogs: {len(dataset['patients'])}
          visits: {len(dataset['visits'])}
          diagnostics: {len(dataset['diagnostic_history'])}
          load_sql: {load_sql_path}
          gold_sql: {gold_sql_path}
        """
    ).strip())

    if args.execute:
        execute_statements(load_statements, args.profile, args.warehouse_id)
        load_gold_sql(gold_sql_path, args.profile, args.warehouse_id)
        print(f"Loaded synthetic dataset into {args.catalog}.{args.schema}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        raise
