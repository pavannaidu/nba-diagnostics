CREATE SCHEMA IF NOT EXISTS pavan_naidu.nba;

CREATE OR REPLACE FUNCTION pavan_naidu.nba.get_visit_packet(
  visit_id STRING COMMENT 'Visit identifier like VISIT-DOG-0001-04.'
)
RETURNS STRING
COMMENT 'Returns a JSON object with the compact visit packet and current top three diagnostic recommendations for a single synthetic dog visit.'
RETURN
COALESCE(
  (
    SELECT to_json(
      named_struct(
        'visit_id', packet.visit_id,
        'patient_id', packet.patient_id,
        'dog_name', packet.dog_name,
        'breed', packet.breed,
        'age_years', packet.age_years,
        'life_stage', packet.life_stage,
        'visit_date', packet.visit_date,
        'cohort', packet.cohort,
        'severity', packet.severity,
        'presenting_complaint', packet.presenting_complaint,
        'vomiting', packet.vomiting,
        'diarrhea', packet.diarrhea,
        'urinary_accidents', packet.urinary_accidents,
        'straining_to_urinate', packet.straining_to_urinate,
        'increased_thirst', packet.increased_thirst,
        'increased_hunger', packet.increased_hunger,
        'lethargy', packet.lethargy,
        'duration_days', packet.duration_days,
        'urgency_level', packet.urgency_level,
        'prior_visit_count', packet.prior_visit_count,
        'owner_note', packet.owner_note,
        'clinician_note', packet.clinician_note,
        'prior_diagnostic_summary', packet.prior_diagnostic_summary,
        'prior_treatment_summary', packet.prior_treatment_summary,
        'recommended_test_1_name', rec.recommended_test_1_name,
        'recommended_test_1_score', rec.recommended_test_1_score,
        'recommended_test_2_name', rec.recommended_test_2_name,
        'recommended_test_2_score', rec.recommended_test_2_score,
        'recommended_test_3_name', rec.recommended_test_3_name,
        'recommended_test_3_score', rec.recommended_test_3_score,
        'no_additional_diagnostic_now', rec.no_additional_diagnostic_now
      )
    )
    FROM pavan_naidu.nba.visit_case_packet_gold packet
    LEFT JOIN pavan_naidu.nba.visit_recommendation_gold rec
      ON rec.visit_id = packet.visit_id
    WHERE packet.visit_id = get_visit_packet.visit_id
    LIMIT 1
  ),
  '{"error":"visit_not_found"}'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.get_visit_candidate_diagnostics(
  visit_id STRING COMMENT 'Visit identifier like VISIT-DOG-0001-04.'
)
RETURNS STRING
COMMENT 'Returns a JSON array of ranked diagnostic candidates, including suppressed duplicate tests, for a single synthetic visit.'
RETURN
COALESCE(
  (
    SELECT to_json(
      collect_list(
        named_struct(
          'recommendation_rank', ranked.recommendation_rank,
          'test_code', ranked.test_code,
          'test_name', ranked.test_name,
          'category', ranked.category,
          'primary_cohort', ranked.primary_cohort,
          'utility_score', ranked.utility_score,
          'suppress_duplicate', ranked.suppress_duplicate,
          'days_since_same_test', ranked.days_since_same_test
        )
      )
    )
    FROM (
      SELECT
        row_number() OVER (
          ORDER BY utility_score DESC, test_code
        ) AS recommendation_rank,
        test_code,
        test_name,
        category,
        primary_cohort,
        utility_score,
        suppress_duplicate,
        days_since_same_test
      FROM pavan_naidu.nba.visit_candidate_features_gold
      WHERE visit_id = get_visit_candidate_diagnostics.visit_id
      ORDER BY utility_score DESC, test_code
      LIMIT 10
    ) ranked
  ),
  '[]'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.get_cohort_recommendation_summary(
  cohort STRING COMMENT 'Clinical cohort. One of wellness, gi, renal_urinary, or endocrine_metabolic.'
)
RETURNS STRING
COMMENT 'Returns a JSON array of aggregate visit counts and top recommendation mix for one cohort.'
RETURN
COALESCE(
  (
    SELECT to_json(
      collect_list(
        named_struct(
          'cohort', summary.cohort,
          'no_additional_diagnostic_now', summary.no_additional_diagnostic_now,
          'recommended_test_1', summary.recommended_test_1,
          'recommended_test_1_name', summary.recommended_test_1_name,
          'visit_count', summary.visit_count,
          'avg_top_score', summary.avg_top_score
        )
      )
    )
    FROM (
      SELECT
        cohort,
        no_additional_diagnostic_now,
        recommended_test_1,
        recommended_test_1_name,
        count(*) AS visit_count,
        round(avg(recommended_test_1_score), 2) AS avg_top_score
      FROM pavan_naidu.nba.visit_recommendation_gold
      WHERE cohort = get_cohort_recommendation_summary.cohort
      GROUP BY
        cohort,
        no_additional_diagnostic_now,
        recommended_test_1,
        recommended_test_1_name
      ORDER BY no_additional_diagnostic_now DESC, visit_count DESC, recommended_test_1
    ) summary
  ),
  '[]'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.get_patient_history(
  in_patient_id STRING COMMENT 'Patient identifier like DOG-0001.',
  in_as_of_ts STRING COMMENT 'ISO date or timestamp for the current intake.'
)
RETURNS STRING
COMMENT 'Returns compact longitudinal patient history JSON up to the provided as_of_ts.'
RETURN
COALESCE(
  (
    SELECT to_json(
      named_struct(
        'history_found',
          CASE
            WHEN (
              SELECT count(*)
              FROM pavan_naidu.nba.visits v
              WHERE v.patient_id = in_patient_id
                AND v.visit_date < coalesce(
                  to_date(in_as_of_ts),
                  to_date(to_timestamp(in_as_of_ts)),
                  current_date()
                )
            ) > 0 THEN true
            WHEN (
              SELECT count(*)
              FROM pavan_naidu.nba.diagnostic_history dh
              WHERE dh.patient_id = in_patient_id
                AND dh.ordered_at < coalesce(
                  to_date(in_as_of_ts),
                  to_date(to_timestamp(in_as_of_ts)),
                  current_date()
                )
            ) > 0 THEN true
            WHEN (
              SELECT count(*)
              FROM pavan_naidu.nba.treatments_history th
              WHERE th.patient_id = in_patient_id
                AND th.started_at < coalesce(
                  to_date(in_as_of_ts),
                  to_date(to_timestamp(in_as_of_ts)),
                  current_date()
                )
            ) > 0 THEN true
            ELSE false
          END,
        'patient_id',
          in_patient_id,
        'as_of_ts',
          coalesce(
            nullif(in_as_of_ts, ''),
            CAST(current_date() AS STRING)
          ),
        'dog_name',
          (
            SELECT p.dog_name
            FROM pavan_naidu.nba.patients p
            WHERE p.patient_id = in_patient_id
            LIMIT 1
          ),
        'breed',
          (
            SELECT p.breed
            FROM pavan_naidu.nba.patients p
            WHERE p.patient_id = in_patient_id
            LIMIT 1
          ),
        'age_years',
          (
            SELECT p.age_years
            FROM pavan_naidu.nba.patients p
            WHERE p.patient_id = in_patient_id
            LIMIT 1
          ),
        'life_stage',
          (
            SELECT p.life_stage
            FROM pavan_naidu.nba.patients p
            WHERE p.patient_id = in_patient_id
            LIMIT 1
          ),
        'prior_visit_count',
          (
            SELECT count(*)
            FROM pavan_naidu.nba.visits v
            WHERE v.patient_id = in_patient_id
              AND v.visit_date < coalesce(
                to_date(in_as_of_ts),
                to_date(to_timestamp(in_as_of_ts)),
                current_date()
              )
          ),
        'prior_diagnostic_summary',
          coalesce(
            (
              SELECT concat_ws(
                ' | ',
                sort_array(
                  collect_list(
                    concat(
                      date_format(dh.ordered_at, 'yyyy-MM-dd'),
                      ' ',
                      dh.test_code,
                      ': ',
                      coalesce(dh.key_finding, dh.result_status)
                    )
                  )
                )
              )
              FROM pavan_naidu.nba.diagnostic_history dh
              WHERE dh.patient_id = in_patient_id
                AND dh.ordered_at < coalesce(
                  to_date(in_as_of_ts),
                  to_date(to_timestamp(in_as_of_ts)),
                  current_date()
                )
            ),
            'no prior diagnostics recorded'
          ),
        'prior_treatment_summary',
          coalesce(
            (
              SELECT concat_ws(
                ' | ',
                sort_array(
                  collect_list(
                    concat(
                      date_format(th.started_at, 'yyyy-MM-dd'),
                      ' ',
                      th.treatment_name,
                      ' (',
                      th.response_status,
                      ')'
                    )
                  )
                )
              )
              FROM pavan_naidu.nba.treatments_history th
              WHERE th.patient_id = in_patient_id
                AND th.started_at < coalesce(
                  to_date(in_as_of_ts),
                  to_date(to_timestamp(in_as_of_ts)),
                  current_date()
                )
            ),
            'no prior treatments recorded'
          ),
        'recent_visits',
          coalesce(
            from_json(
              (
                SELECT to_json(
                  collect_list(
                    named_struct(
                      'visit_date', CAST(recent.visit_date AS STRING),
                      'cohort', recent.cohort,
                      'severity', recent.severity,
                      'presenting_complaint', recent.presenting_complaint
                    )
                  )
                )
                FROM (
                  SELECT
                    v.visit_date,
                    v.cohort,
                    v.severity,
                    v.presenting_complaint
                  FROM pavan_naidu.nba.visits v
                  WHERE v.patient_id = in_patient_id
                    AND v.visit_date < coalesce(
                      to_date(in_as_of_ts),
                      to_date(to_timestamp(in_as_of_ts)),
                      current_date()
                    )
                  ORDER BY v.visit_date DESC, v.visit_id DESC
                  LIMIT 4
                ) recent
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
    FROM pavan_naidu.nba.diagnostic_catalog
    LIMIT 1
  ),
  '{"history_found":false,"patient_id":null,"as_of_ts":null,"prior_visit_count":0,"prior_diagnostic_summary":"no prior diagnostics recorded","prior_treatment_summary":"no prior treatments recorded","recent_visits":[]}'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.recommend_for_intake(
  payload_json STRING COMMENT 'EditableVisitInput JSON payload from the app.'
)
RETURNS STRING
COMMENT 'Computes a live next-best diagnostic recommendation from the current intake payload and source tables.'
RETURN
COALESCE(
  (
    SELECT to_json(
      named_struct(
        'patient_id',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), ''),
            'UNKNOWN'
          ),
        'seed_visit_id',
          nullif(get_json_object(recommend_for_intake.payload_json, '$.seed_visit_id'), ''),
        'dog_name',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.dog_name'), ''),
            (
              SELECT patient.dog_name
              FROM pavan_naidu.nba.patients patient
              WHERE patient.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
              LIMIT 1
            ),
            coalesce(
              nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), ''),
              'UNKNOWN'
            )
          ),
        'breed',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.breed'), ''),
            (
              SELECT patient.breed
              FROM pavan_naidu.nba.patients patient
              WHERE patient.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
              LIMIT 1
            ),
            'Unknown'
          ),
        'age_years',
          coalesce(
            CAST(nullif(get_json_object(recommend_for_intake.payload_json, '$.age_years'), '') AS DOUBLE),
            (
              SELECT patient.age_years
              FROM pavan_naidu.nba.patients patient
              WHERE patient.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
              LIMIT 1
            )
          ),
        'life_stage',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.life_stage'), ''),
            (
              SELECT patient.life_stage
              FROM pavan_naidu.nba.patients patient
              WHERE patient.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
              LIMIT 1
            )
          ),
        'as_of_ts',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.as_of_ts'), ''),
            CAST(current_date() AS STRING)
          ),
        'severity',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.severity'), ''),
            'moderate'
          ),
        'urgency_level',
          coalesce(
            nullif(get_json_object(recommend_for_intake.payload_json, '$.urgency_level'), ''),
            'routine'
          ),
        'derived_cohort',
          CASE
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false)
            THEN 'renal_urinary'
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false)
              AND (
                coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                OR coalesce(CAST(nullif(get_json_object(recommend_for_intake.payload_json, '$.weight_change_pct'), '') AS DOUBLE), 0D) <= -3D
              )
            THEN 'endocrine_metabolic'
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.recent_diet_change') = 'true', false)
            THEN 'gi'
            WHEN nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') IN (
              'wellness', 'gi', 'renal_urinary', 'endocrine_metabolic'
            )
            THEN nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '')
            ELSE 'wellness'
          END,
        'recommended_test_codes',
          CASE
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'renal_urinary'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 30 THEN 'URINALYSIS' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 45 THEN 'URINE_CULTURE' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 60 THEN 'SDMA_CHEM17' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 75 THEN 'UPC_RATIO' END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'endocrine_metabolic'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                    OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 75 THEN 'FRUCTOSAMINE' END
                    ELSE
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 120 THEN 'THYROID_PANEL' END
                  END,
                  CASE WHEN coalesce(
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END
                  END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END
                  END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END
                  END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END
                  END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END
                  END,
                    1000000
                  ) >= 120 THEN 'THYROID_PANEL' END,
                  CASE WHEN coalesce(
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                      OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END END,
                    1000000
                  ) >= 75 THEN 'FRUCTOSAMINE' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 120 THEN 'CORTISOL_SCREEN' END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.recent_diet_change') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'gi'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 45 THEN 'FECAL_PCR' END
                    ELSE
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 75 THEN 'GI_PANEL' END
                  END,
                  CASE WHEN coalesce(
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN NULL
                    ELSE CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END END,
                    1000000
                  ) >= 75 THEN 'GI_PANEL' END,
                  CASE WHEN coalesce(
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END END,
                    1000000
                  ) >= 45 THEN 'FECAL_PCR' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 240 THEN 'WELLNESS_PANEL' END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            ELSE slice(
              filter(
                array(
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 240 THEN 'WELLNESS_PANEL' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 300 THEN 'HEARTWORM_4DX' END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 150 THEN 'FECAL_SCREEN' END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
          END,
        'recommended_diagnostics',
          CASE
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'renal_urinary'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 30 THEN named_struct('recommendation_rank', 1, 'test_code', 'URINALYSIS', 'test_name', 'Complete Urinalysis', 'category', 'urinary', 'primary_cohort', 'renal_urinary', 'utility_score', 84D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'ranked first for lower urinary signs') END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 45 THEN named_struct('recommendation_rank', 2, 'test_code', 'URINE_CULTURE', 'test_name', 'Urine Culture and Sensitivity', 'category', 'urinary', 'primary_cohort', 'renal_urinary', 'utility_score', 76D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'confirms infectious lower urinary differential') END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 60 THEN named_struct('recommendation_rank', 3, 'test_code', 'SDMA_CHEM17', 'test_name', 'SDMA with Chemistry 17', 'category', 'renal', 'primary_cohort', 'renal_urinary', 'utility_score', 62D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'adds renal chemistry context') END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 75 THEN named_struct('recommendation_rank', 4, 'test_code', 'UPC_RATIO', 'test_name', 'Urine Protein Creatinine Ratio', 'category', 'renal', 'primary_cohort', 'renal_urinary', 'utility_score', 54D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'protein-loss follow-up if renal concern persists') END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'endocrine_metabolic'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                    OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 75 THEN named_struct('recommendation_rank', 1, 'test_code', 'FRUCTOSAMINE', 'test_name', 'Fructosamine', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 82D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'ranked first for polyphagia or glucose-pattern concern') END
                    ELSE
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 120 THEN named_struct('recommendation_rank', 1, 'test_code', 'THYROID_PANEL', 'test_name', 'Canine Thyroid Panel', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 80D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'ranked first for endocrine or metabolic screening') END
                  END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                    OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                    THEN
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 120 THEN named_struct('recommendation_rank', 2, 'test_code', 'THYROID_PANEL', 'test_name', 'Canine Thyroid Panel', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 74D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'common endocrine next step in this profile') END END,
                  CASE WHEN NOT (
                    coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                    OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                  ) THEN
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 75 THEN named_struct('recommendation_rank', 2, 'test_code', 'FRUCTOSAMINE', 'test_name', 'Fructosamine', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 74D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'checks longer-horizon glycemic status') END END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 120 THEN named_struct('recommendation_rank', 3, 'test_code', 'CORTISOL_SCREEN', 'test_name', 'Resting Cortisol Screen', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 66D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'screens for adrenal contribution') END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
              OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.recent_diet_change') = 'true', false)
              OR nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') = 'gi'
            THEN slice(
              filter(
                array(
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                    THEN
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 45 THEN named_struct('recommendation_rank', 1, 'test_code', 'FECAL_PCR', 'test_name', 'Canine Diarrhea RealPCR Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 82D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'ranked first for diarrhea-heavy GI presentation') END
                    ELSE
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) >= 75 THEN named_struct('recommendation_rank', 1, 'test_code', 'GI_PANEL', 'test_name', 'Canine GI Laboratory Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 82D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'ranked first for upper GI and vomiting presentation') END
                  END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false) THEN
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 75 THEN named_struct('recommendation_rank', 2, 'test_code', 'GI_PANEL', 'test_name', 'Canine GI Laboratory Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 76D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'supports GI workup for the current presentation') END END,
                  CASE WHEN NOT coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false) THEN
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 45 THEN named_struct('recommendation_rank', 2, 'test_code', 'FECAL_PCR', 'test_name', 'Canine Diarrhea RealPCR Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 70D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'follow-up infectious or parasitic GI rule-out') END END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 240 THEN named_struct('recommendation_rank', 3, 'test_code', 'WELLNESS_PANEL', 'test_name', 'Adult Wellness Panel', 'category', 'chemistry_cbc', 'primary_cohort', 'wellness', 'utility_score', 48D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'adds broad chemistry and CBC context for GI signs') END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
            ELSE slice(
              filter(
                array(
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 240 THEN named_struct('recommendation_rank', 1, 'test_code', 'WELLNESS_PANEL', 'test_name', 'Adult Wellness Panel', 'category', 'chemistry_cbc', 'primary_cohort', 'wellness', 'utility_score', 74D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'baseline wellness workflow') END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 300 THEN named_struct('recommendation_rank', 2, 'test_code', 'HEARTWORM_4DX', 'test_name', 'SNAP 4Dx Plus', 'category', 'screening', 'primary_cohort', 'wellness', 'utility_score', 63D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'routine preventive infectious disease screening') END,
                  CASE WHEN coalesce(
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                    CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                    1000000
                  ) >= 150 THEN named_struct('recommendation_rank', 3, 'test_code', 'FECAL_SCREEN', 'test_name', 'Fecal Ova and Parasite Screen', 'category', 'parasitology', 'primary_cohort', 'wellness', 'utility_score', 55D, 'suppress_duplicate', false, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'routine preventive parasite screening') END
                ),
                item -> item IS NOT NULL
              ),
              1,
              3
            )
          END,
        'considered_alternatives',
          from_json(
            '[]',
            'array<struct<recommendation_rank:int,test_code:string,test_name:string,category:string,primary_cohort:string,utility_score:double,suppress_duplicate:boolean,days_since_same_test:int,reason_summary:string>>'
          ),
        'suppressed_duplicates',
          filter(
            array(
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 240 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'WELLNESS_PANEL', 'test_name', 'Adult Wellness Panel', 'category', 'chemistry_cbc', 'primary_cohort', 'wellness', 'utility_score', 74D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'HEARTWORM_4DX' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 300 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'HEARTWORM_4DX', 'test_name', 'SNAP 4Dx Plus', 'category', 'screening', 'primary_cohort', 'wellness', 'utility_score', 63D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 150 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'FECAL_SCREEN', 'test_name', 'Fecal Ova and Parasite Screen', 'category', 'parasitology', 'primary_cohort', 'wellness', 'utility_score', 55D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 75 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'GI_PANEL', 'test_name', 'Canine GI Laboratory Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 82D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FECAL_PCR' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 45 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'FECAL_PCR', 'test_name', 'Canine Diarrhea RealPCR Panel', 'category', 'gastroenterology', 'primary_cohort', 'gi', 'utility_score', 82D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 30 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'URINALYSIS', 'test_name', 'Complete Urinalysis', 'category', 'urinary', 'primary_cohort', 'renal_urinary', 'utility_score', 84D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINE_CULTURE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 45 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'URINE_CULTURE', 'test_name', 'Urine Culture and Sensitivity', 'category', 'urinary', 'primary_cohort', 'renal_urinary', 'utility_score', 76D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'SDMA_CHEM17' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 60 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'SDMA_CHEM17', 'test_name', 'SDMA with Chemistry 17', 'category', 'renal', 'primary_cohort', 'renal_urinary', 'utility_score', 62D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'UPC_RATIO' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 75 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'UPC_RATIO', 'test_name', 'Urine Protein Creatinine Ratio', 'category', 'renal', 'primary_cohort', 'renal_urinary', 'utility_score', 54D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 120 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'THYROID_PANEL', 'test_name', 'Canine Thyroid Panel', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 80D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'FRUCTOSAMINE' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 75 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'FRUCTOSAMINE', 'test_name', 'Fructosamine', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 82D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END,
              CASE WHEN coalesce(
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'CORTISOL_SCREEN' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                1000000
              ) < 120 THEN named_struct('recommendation_rank', CAST(NULL AS INT), 'test_code', 'CORTISOL_SCREEN', 'test_name', 'Resting Cortisol Screen', 'category', 'endocrine', 'primary_cohort', 'endocrine_metabolic', 'utility_score', 66D, 'suppress_duplicate', true, 'days_since_same_test', CAST(NULL AS INT), 'reason_summary', 'suppressed because the same test was marked as recent in the live intake') END
            ),
            item -> item IS NOT NULL
          ),
        'no_additional_diagnostic_now',
          false,
        'explanation_bullets',
          split(
            concat_ws(
              '<<<SEP>>>',
              concat(
                'Derived the live visit context as ',
                replace(
                  CASE
                    WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false)
                      OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false)
                    THEN 'renal_urinary'
                    WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false)
                      AND (
                        coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false)
                        OR coalesce(nullif(get_json_object(recommend_for_intake.payload_json, '$.appetite_change'), ''), 'stable') = 'increased'
                        OR coalesce(CAST(nullif(get_json_object(recommend_for_intake.payload_json, '$.weight_change_pct'), '') AS DOUBLE), 0D) <= -3D
                      )
                    THEN 'endocrine_metabolic'
                    WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false)
                      OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false)
                      OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.recent_diet_change') = 'true', false)
                    THEN 'gi'
                    WHEN nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '') IN (
                      'wellness', 'gi', 'renal_urinary', 'endocrine_metabolic'
                    )
                    THEN nullif(get_json_object(recommend_for_intake.payload_json, '$.cohort_hint'), '')
                    ELSE 'wellness'
                  END,
                  '_',
                  ' '
                ),
                ' from the editable intake and available patient history.'
              ),
              CASE
                WHEN concat_ws(
                  ', ',
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false) THEN 'vomiting' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false) THEN 'diarrhea' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false) THEN 'urinary accidents' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false) THEN 'straining to urinate' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false) THEN 'increased thirst' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false) THEN 'increased hunger' END,
                  CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.lethargy') = 'true', false) THEN 'lethargy' END
                ) <> ''
                THEN concat(
                  'Active signals: ',
                  concat_ws(
                    ', ',
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.vomiting') = 'true', false) THEN 'vomiting' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.diarrhea') = 'true', false) THEN 'diarrhea' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false) THEN 'urinary accidents' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false) THEN 'straining to urinate' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_thirst') = 'true', false) THEN 'increased thirst' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.increased_hunger') = 'true', false) THEN 'increased hunger' END,
                    CASE WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.lethargy') = 'true', false) THEN 'lethargy' END
                  ),
                  '.'
                )
                ELSE 'No major structured symptom flags were captured, so the ranking leaned on the selected cohort hint and live override context.'
              END,
              CASE
                WHEN (
                  SELECT count(*)
                  FROM pavan_naidu.nba.visits visit
                  WHERE visit.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
                    AND visit.visit_date < coalesce(
                      to_date(get_json_object(recommend_for_intake.payload_json, '$.as_of_ts')),
                      to_date(to_timestamp(get_json_object(recommend_for_intake.payload_json, '$.as_of_ts'))),
                      current_date()
                    )
                ) > 0
                THEN concat(
                  'Found ',
                  CAST(
                    (
                      SELECT count(*)
                      FROM pavan_naidu.nba.visits visit
                      WHERE visit.patient_id = nullif(get_json_object(recommend_for_intake.payload_json, '$.patient_id'), '')
                        AND visit.visit_date < coalesce(
                          to_date(get_json_object(recommend_for_intake.payload_json, '$.as_of_ts')),
                          to_date(to_timestamp(get_json_object(recommend_for_intake.payload_json, '$.as_of_ts'))),
                          current_date()
                        )
                    ) AS STRING
                  ),
                  ' prior visit(s) before this intake.'
                )
                ELSE 'No prior visit history was found, so the recommendation relied on the current intake and live override inputs.'
              END,
              CASE
                WHEN (
                  CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                ) > 0
                THEN concat(
                  'Applied live recent-test recency overrides to ',
                  CAST(
                    (
                      CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                      + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                      + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                      + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                      + CASE WHEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) IS NOT NULL THEN 1 ELSE 0 END
                    ) AS STRING
                  ),
                  ' diagnostic(s).'
                )
              END,
              CASE
                WHEN size(
                  filter(
                    array(
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) < 240 THEN 'WELLNESS_PANEL' END,
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'GI_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) < 75 THEN 'GI_PANEL' END,
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) < 30 THEN 'URINALYSIS' END,
                      CASE WHEN coalesce(
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                        CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'THYROID_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                        1000000
                      ) < 120 THEN 'THYROID_PANEL' END
                    ),
                    item -> item IS NOT NULL
                  )
                ) > 0
                THEN 'One or more candidate diagnostics were suppressed because the same test was marked as recent in the current intake.'
              END,
              CASE
                WHEN size(
                  CASE
                    WHEN coalesce(get_json_object(recommend_for_intake.payload_json, '$.straining_to_urinate') = 'true', false)
                      OR coalesce(get_json_object(recommend_for_intake.payload_json, '$.urinary_accidents') = 'true', false)
                    THEN slice(
                      filter(
                        array(
                          CASE WHEN coalesce(
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'URINALYSIS' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                            1000000
                          ) >= 30 THEN 'URINALYSIS' END
                        ),
                        item -> item IS NOT NULL
                      ),
                      1,
                      3
                    )
                    ELSE slice(
                      filter(
                        array(
                          CASE WHEN coalesce(
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[0].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[1].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[2].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[3].days_since_same_test') AS INT) END,
                            CASE WHEN get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].test_code') = 'WELLNESS_PANEL' THEN CAST(get_json_object(recommend_for_intake.payload_json, '$.recent_test_overrides[4].days_since_same_test') AS INT) END,
                            1000000
                          ) >= 240 THEN 'WELLNESS_PANEL' END
                        ),
                        item -> item IS NOT NULL
                      ),
                      1,
                      3
                    )
                  END
                ) = 0
                THEN 'All high-priority candidates were suppressed or unavailable, so the system abstained from adding another diagnostic right now.'
                ELSE 'Returned the highest-priority unsuppressed diagnostics for the live intake profile.'
              END
            ),
            '<<<SEP>>>'
          )
      )
    )
    FROM pavan_naidu.nba.diagnostic_catalog
    LIMIT 1
  ),
  '{}'
);

CREATE OR REPLACE FUNCTION pavan_naidu.nba.get_test_metadata(
  test_codes STRING COMMENT 'JSON array string of test codes like [\"GI_PANEL\",\"FECAL_PCR\"].'
)
RETURNS STRING
COMMENT 'Returns ordered diagnostic metadata JSON for a list of test codes.'
RETURN
COALESCE(
  concat(
    '[',
    concat_ws(
      ',',
      (
        SELECT to_json(
          named_struct(
            'position', 1,
            'test_code', catalog.test_code,
            'test_name', catalog.test_name,
            'category', catalog.category,
            'specimen_type', catalog.specimen_type,
            'primary_cohort', catalog.primary_cohort,
            'clinical_use', concat('Most often used for ', replace(catalog.primary_cohort, '_', ' '), ' workflows in this demo.')
          )
        )
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        WHERE catalog.test_code = get_json_object(get_test_metadata.test_codes, '$[0]')
        LIMIT 1
      ),
      (
        SELECT to_json(
          named_struct(
            'position', 2,
            'test_code', catalog.test_code,
            'test_name', catalog.test_name,
            'category', catalog.category,
            'specimen_type', catalog.specimen_type,
            'primary_cohort', catalog.primary_cohort,
            'clinical_use', concat('Most often used for ', replace(catalog.primary_cohort, '_', ' '), ' workflows in this demo.')
          )
        )
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        WHERE catalog.test_code = get_json_object(get_test_metadata.test_codes, '$[1]')
        LIMIT 1
      ),
      (
        SELECT to_json(
          named_struct(
            'position', 3,
            'test_code', catalog.test_code,
            'test_name', catalog.test_name,
            'category', catalog.category,
            'specimen_type', catalog.specimen_type,
            'primary_cohort', catalog.primary_cohort,
            'clinical_use', concat('Most often used for ', replace(catalog.primary_cohort, '_', ' '), ' workflows in this demo.')
          )
        )
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        WHERE catalog.test_code = get_json_object(get_test_metadata.test_codes, '$[2]')
        LIMIT 1
      ),
      (
        SELECT to_json(
          named_struct(
            'position', 4,
            'test_code', catalog.test_code,
            'test_name', catalog.test_name,
            'category', catalog.category,
            'specimen_type', catalog.specimen_type,
            'primary_cohort', catalog.primary_cohort,
            'clinical_use', concat('Most often used for ', replace(catalog.primary_cohort, '_', ' '), ' workflows in this demo.')
          )
        )
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        WHERE catalog.test_code = get_json_object(get_test_metadata.test_codes, '$[3]')
        LIMIT 1
      ),
      (
        SELECT to_json(
          named_struct(
            'position', 5,
            'test_code', catalog.test_code,
            'test_name', catalog.test_name,
            'category', catalog.category,
            'specimen_type', catalog.specimen_type,
            'primary_cohort', catalog.primary_cohort,
            'clinical_use', concat('Most often used for ', replace(catalog.primary_cohort, '_', ' '), ' workflows in this demo.')
          )
        )
        FROM pavan_naidu.nba.diagnostic_catalog catalog
        WHERE catalog.test_code = get_json_object(get_test_metadata.test_codes, '$[4]')
        LIMIT 1
      )
    ),
    ']'
  )
);
