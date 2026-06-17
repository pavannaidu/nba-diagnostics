CREATE TABLE IF NOT EXISTS pavan_naidu.nba.live_intake_requests (
  request_id STRING,
  patient_id STRING,
  as_of_ts STRING,
  payload_json STRING,
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pavan_naidu.nba.runtime_config (
  config_key STRING,
  config_value STRING,
  updated_at TIMESTAMP
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.runtime_triage_intake(
  payload_json STRING COMMENT 'EditableVisitInput JSON payload from the app.'
)
RETURNS STRING
LANGUAGE PYTHON
COMMENT 'Deterministically triages editable intake JSON into cohort, acuity, quality, and routing evidence.'
AS $$
import json


def is_present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def is_truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(value)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def number_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def triage(payload_json):
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
            "routing_notes": ["Intake JSON could not be parsed. Request structured intake before recommending."],
        }

    if not isinstance(payload, dict):
        payload = {}

    active_signals = []
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
    elif any(is_truthy(payload.get(field_name)) for field_name in ("urinary_accidents", "straining_to_urinate", "increased_thirst")):
        derived_cohort = "renal_urinary"
    elif any(is_truthy(payload.get(field_name)) for field_name in ("vomiting", "diarrhea", "recent_diet_change")):
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
        routing_notes.append("Insufficient intake detail. Consider abstain or request more context if evidence is weak.")
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


return json.dumps(triage(payload_json), sort_keys=True)
$$;

CREATE OR REPLACE FUNCTION pavan_naidu.nba.runtime_query_pubmed(
  search_query STRING COMMENT 'PubMed search query for supplementary literature context.',
  max_results INT COMMENT 'Maximum PubMed records to return. Clamped to 1 through 5.',
  min_publication_year INT COMMENT 'Optional minimum publication year. Use 0 when not needed.'
)
RETURNS STRING
LANGUAGE PYTHON
COMMENT 'Queries PubMed E-utilities for compact supplementary literature context. Fails soft with unavailable JSON.'
AS $$
import json
import requests


BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "idexx_nba_diagnostics_agents"


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_int(value, default_value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default_value


def clamp_result_limit(value):
    requested = safe_int(value, 5)
    return max(1, min(requested, 5))


def pubmed_count(value):
    return safe_int(value, 0)


def unavailable_payload(query, exc):
    return {
        "source": "pubmed",
        "status": "unavailable",
        "query": query,
        "count": 0,
        "returned": 0,
        "articles": [],
        "notes": ["PubMed was unavailable. Continue using curated and patient-specific evidence."],
        "error": {
            "type": type(exc).__name__,
            "message": str(exc)[:320],
        },
    }


def article_doi(summary):
    article_ids = summary.get("articleids")
    if not isinstance(article_ids, list):
        return None
    for article_id in article_ids:
        if not isinstance(article_id, dict):
            continue
        if str(article_id.get("idtype", "")).lower() == "doi":
            value = article_id.get("value")
            if value:
                return str(value)
    return None


def query_pubmed(search_query, max_results, min_publication_year):
    cleaned_query = clean_text(search_query)
    if not cleaned_query:
        return {
            "source": "pubmed",
            "status": "empty_query",
            "query": "",
            "count": 0,
            "returned": 0,
            "articles": [],
            "notes": ["Empty PubMed search query."],
        }

    result_limit = clamp_result_limit(max_results)
    search_params = {
        "tool": TOOL_NAME,
        "db": "pubmed",
        "retmode": "json",
        "retmax": str(result_limit),
        "sort": "relevance",
        "term": cleaned_query,
    }
    min_year = safe_int(min_publication_year, 0)
    if min_year > 0:
        search_params["datetype"] = "pdat"
        search_params["mindate"] = str(min_year)

    try:
        headers = {"User-Agent": TOOL_NAME + "/0.1"}
        search_response = requests.get(
            BASE_URL + "/esearch.fcgi",
            params=search_params,
            headers=headers,
            timeout=10,
        )
        search_response.raise_for_status()
        search_payload = search_response.json()
        if not isinstance(search_payload, dict):
            raise ValueError("PubMed ESearch returned non-object JSON.")
        search_result = search_payload.get("esearchresult", {})
        if not isinstance(search_result, dict):
            raise ValueError("PubMed ESearch result was malformed.")
        id_list = search_result.get("idlist", [])
        if not isinstance(id_list, list):
            id_list = []
        pubmed_ids = [str(pubmed_id) for pubmed_id in id_list if pubmed_id]

        if not pubmed_ids:
            return {
                "source": "pubmed",
                "status": "ok",
                "query": cleaned_query,
                "count": pubmed_count(search_result.get("count")),
                "returned": 0,
                "articles": [],
                "notes": ["No PubMed records matched the query."],
            }

        summary_response = requests.get(
            BASE_URL + "/esummary.fcgi",
            params={
                "tool": TOOL_NAME,
                "db": "pubmed",
                "retmode": "json",
                "id": ",".join(pubmed_ids),
            },
            headers=headers,
            timeout=10,
        )
        summary_response.raise_for_status()
        summary_payload = summary_response.json()
        if not isinstance(summary_payload, dict):
            raise ValueError("PubMed ESummary returned non-object JSON.")
        summary_result = summary_payload.get("result", {})
        if not isinstance(summary_result, dict):
            raise ValueError("PubMed ESummary result was malformed.")
        summary_uids = summary_result.get("uids")
        if not isinstance(summary_uids, list):
            summary_uids = pubmed_ids

        articles = []
        for pubmed_id in summary_uids:
            summary = summary_result.get(str(pubmed_id))
            if not isinstance(summary, dict):
                continue
            summary_authors = summary.get("authors")
            if not isinstance(summary_authors, list):
                summary_authors = []
            authors = [
                str(author.get("name"))
                for author in summary_authors[:4]
                if isinstance(author, dict) and author.get("name")
            ]
            articles.append(
                {
                    "pmid": str(pubmed_id),
                    "title": summary.get("title"),
                    "journal": summary.get("source"),
                    "pubdate": summary.get("pubdate"),
                    "authors": authors,
                    "doi": article_doi(summary),
                    "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/" + str(pubmed_id) + "/",
                }
            )

        return {
            "source": "pubmed",
            "status": "ok",
            "query": cleaned_query,
            "count": pubmed_count(search_result.get("count")),
            "returned": len(articles),
            "articles": articles,
            "notes": ["PubMed summaries are supplementary literature context, not patient-specific evidence."],
        }
    except Exception as exc:
        return unavailable_payload(cleaned_query, exc)


return json.dumps(query_pubmed(search_query, max_results, min_publication_year), sort_keys=True)
$$;

CREATE OR REPLACE FUNCTION pavan_naidu.nba.runtime_get_patient_history(
  payload_json STRING COMMENT 'EditableVisitInput JSON payload from the app.'
)
RETURNS STRING
COMMENT 'Returns compact longitudinal patient history JSON up to the provided as_of_ts.'
RETURN
COALESCE(
  (
    SELECT to_json(
      named_struct(
        'history_found',
          count(DISTINCT v.visit_id) > 0
          OR count(dh.test_code) > 0
          OR count(th.treatment_name) > 0,
        'patient_id', max(intake.patient_id),
        'as_of_ts', max(intake.as_of_ts),
        'dog_name', max(patient.dog_name),
        'breed', max(patient.breed),
        'age_years', max(patient.age_years),
        'life_stage', max(patient.life_stage),
        'prior_visit_count', count(DISTINCT v.visit_id),
        'prior_diagnostic_summary',
          coalesce(
            concat_ws(
              ' | ',
              sort_array(
                collect_set(
                  CASE
                    WHEN dh.test_code IS NOT NULL THEN concat(
                      date_format(dh.ordered_at, 'yyyy-MM-dd'),
                      ' ',
                      dh.test_code,
                      ': ',
                      coalesce(dh.key_finding, dh.result_status)
                    )
                  END
                )
              )
            ),
            'no prior diagnostics recorded'
          ),
        'prior_treatment_summary',
          coalesce(
            concat_ws(
              ' | ',
              sort_array(
                collect_set(
                  CASE
                    WHEN th.treatment_name IS NOT NULL THEN concat(
                      date_format(th.started_at, 'yyyy-MM-dd'),
                      ' ',
                      th.treatment_name,
                      ' (',
                      th.response_status,
                      ')'
                    )
                  END
                )
              )
            ),
            'no prior treatments recorded'
          ),
        'recent_visits',
          coalesce(
            from_json(
              to_json(
                slice(
                  transform(
                    reverse(
                      array_sort(
                        collect_set(
                          CASE
                            WHEN v.visit_id IS NOT NULL THEN named_struct(
                              'sort_date', v.visit_date,
                              'sort_visit', v.visit_id,
                              'payload',
                                named_struct(
                                  'visit_date', CAST(v.visit_date AS STRING),
                                  'cohort', v.cohort,
                                  'severity', v.severity,
                                  'presenting_complaint', v.presenting_complaint
                                )
                            )
                          END
                        )
                      )
                    ),
                    item -> item.payload
                  ),
                  1,
                  4
                )
              ),
              'array<struct<visit_date:string,cohort:string,severity:string,presenting_complaint:string>>'
            ),
            from_json(
              '[]',
              'array<struct<visit_date:string,cohort:string,severity:string,presenting_complaint:string>>'
            )
          )
      )
    )
    FROM (
      SELECT
        coalesce(nullif(parsed.patient_id, ''), 'UNKNOWN') AS patient_id,
        coalesce(nullif(parsed.as_of_ts, ''), CAST(current_date() AS STRING)) AS as_of_ts,
        coalesce(to_date(parsed.as_of_ts), to_date(to_timestamp(parsed.as_of_ts)), current_date()) AS as_of_date
      FROM (
        SELECT from_json(payload_json, 'struct<patient_id:string,as_of_ts:string>') AS parsed
      ) raw
    ) intake
    LEFT JOIN pavan_naidu.nba.patients patient
      ON patient.patient_id = intake.patient_id
    LEFT JOIN pavan_naidu.nba.visits v
      ON v.patient_id = intake.patient_id
     AND v.visit_date < intake.as_of_date
    LEFT JOIN pavan_naidu.nba.diagnostic_history dh
      ON dh.patient_id = intake.patient_id
     AND dh.ordered_at < intake.as_of_date
    LEFT JOIN pavan_naidu.nba.treatments_history th
      ON th.patient_id = intake.patient_id
     AND th.started_at < intake.as_of_date
  ),
  '{"history_found":false,"patient_id":null,"as_of_ts":null,"prior_visit_count":0,"prior_diagnostic_summary":"no prior diagnostics recorded","prior_treatment_summary":"no prior treatments recorded","recent_visits":[]}'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.runtime_get_recent_test_audit(
  payload_json STRING COMMENT 'EditableVisitInput JSON payload from the app.'
)
RETURNS STRING
COMMENT 'Returns recent same-test timing and duplicate suppression facts for the provided current intake payload.'
RETURN
(
  SELECT coalesce(
    to_json(
      transform(
        array_sort(
          collect_list(
            named_struct(
              'sort_key', audit.test_code,
              'payload',
                named_struct(
                  'test_code', audit.test_code,
                  'test_name', audit.test_name,
                  'category', audit.category,
                  'days_since_same_test', audit.days_since_same_test,
                  'suppression_window_days', audit.suppression_window_days,
                  'suppressed', audit.suppressed,
                  'suppression_reason', audit.suppression_reason,
                  'source', audit.source
                )
            )
          )
        ),
        item -> item.payload
      )
    ),
    '[]'
  )
  FROM (
      SELECT
        ranked.test_code,
        ranked.test_name,
        ranked.category,
        ranked.suppression_window_days,
        coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test) AS days_since_same_test,
        CASE
          WHEN ranked.override_days_since_same_test IS NOT NULL THEN 'override'
          WHEN ranked.history_days_since_same_test IS NOT NULL THEN 'history'
          ELSE NULL
        END AS source,
        coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test) IS NOT NULL
          AND coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test)
            < ranked.suppression_window_days AS suppressed,
        CASE
          WHEN coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test) IS NOT NULL
            AND coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test)
              < ranked.suppression_window_days
          THEN concat(
            'Same test completed ',
            CAST(coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test) AS STRING),
            ' day(s) ago within the ',
            CAST(ranked.suppression_window_days AS STRING),
            '-day duplicate window.'
          )
        END AS suppression_reason
      FROM (
        SELECT
          catalog.test_code,
          catalog.test_name,
          catalog.category,
          catalog.duplicate_suppression_window_days AS suppression_window_days,
          min(
            CASE
              WHEN upper(override.test_code) = catalog.test_code THEN override.days_since_same_test
            END
          ) AS override_days_since_same_test,
          datediff(max(intake.as_of_date), max(dh.ordered_at)) AS history_days_since_same_test
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        CROSS JOIN (
          SELECT
            coalesce(nullif(parsed.patient_id, ''), 'UNKNOWN') AS patient_id,
            coalesce(to_date(parsed.as_of_ts), to_date(to_timestamp(parsed.as_of_ts)), current_date()) AS as_of_date,
            coalesce(
              parsed.recent_test_overrides,
              from_json('[]', 'array<struct<test_code:string,days_since_same_test:int>>')
            ) AS recent_test_overrides
          FROM (
            SELECT from_json(
              payload_json,
              'struct<patient_id:string,as_of_ts:string,recent_test_overrides:array<struct<test_code:string,days_since_same_test:int>>>'
            ) AS parsed
          ) raw
        ) intake
        LEFT JOIN pavan_naidu.nba.diagnostic_history dh
          ON dh.patient_id = intake.patient_id
         AND dh.test_code = catalog.test_code
         AND dh.ordered_at < intake.as_of_date
        LATERAL VIEW OUTER explode(intake.recent_test_overrides) overrides AS override
        GROUP BY
          catalog.test_code,
          catalog.test_name,
          catalog.category,
          catalog.duplicate_suppression_window_days
      ) ranked
      WHERE coalesce(ranked.override_days_since_same_test, ranked.history_days_since_same_test) IS NOT NULL
  ) audit
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.runtime_get_test_metadata(
  test_codes STRING COMMENT 'JSON array string of test codes like ["GI_PANEL","FECAL_PCR"].'
)
RETURNS STRING
COMMENT 'Returns ordered diagnostic metadata JSON for a list of test codes.'
RETURN
(
  SELECT coalesce(
    to_json(
      transform(
        array_sort(
          collect_list(
            named_struct(
              'sort_key', requested.position,
              'payload',
                named_struct(
                  'position', requested.position,
                  'test_code', catalog.test_code,
                  'test_name', catalog.test_name,
                  'category', catalog.category,
                  'specimen_type', catalog.specimen_type,
                  'primary_cohort', catalog.primary_cohort,
                  'clinical_use',
                    concat(
                      'Most often used for ',
                      replace(catalog.primary_cohort, '_', ' '),
                      ' workflows in this system.'
                    )
                )
            )
          )
        ),
        item -> item.payload
      )
    ),
    '[]'
  )
  FROM (
      SELECT
        position + 1 AS position,
        upper(code) AS test_code
      FROM (
        SELECT coalesce(
          from_json(test_codes, 'array<string>'),
          from_json('[]', 'array<string>')
        ) AS codes
      ) parsed
      LATERAL VIEW posexplode(parsed.codes) exploded AS position, code
      WHERE code IS NOT NULL AND trim(code) <> ''
    ) requested
  JOIN pavan_naidu.nba.diagnostic_catalog catalog
    ON catalog.test_code = requested.test_code
);
