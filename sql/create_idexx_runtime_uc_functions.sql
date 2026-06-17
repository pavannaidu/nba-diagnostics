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
