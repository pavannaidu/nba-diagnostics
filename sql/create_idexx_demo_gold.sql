CREATE SCHEMA IF NOT EXISTS pavan_naidu.nba;

CREATE OR REPLACE VIEW pavan_naidu.nba.visit_case_packet_gold AS
WITH prior_diag AS (
  SELECT
    v.visit_id,
    concat_ws(
      ' | ',
      sort_array(
        collect_list(
          concat(date_format(dh.ordered_at, 'yyyy-MM-dd'), ' ', dh.test_code, ': ', coalesce(dh.key_finding, dh.result_status))
        )
      )
    ) AS prior_diagnostic_summary
  FROM pavan_naidu.nba.visits v
  LEFT JOIN pavan_naidu.nba.diagnostic_history dh
    ON dh.patient_id = v.patient_id
   AND dh.ordered_at < v.visit_date
  GROUP BY v.visit_id
),
prior_tx AS (
  SELECT
    v.visit_id,
    concat_ws(
      ' | ',
      sort_array(
        collect_list(
          concat(date_format(th.started_at, 'yyyy-MM-dd'), ' ', th.treatment_name, ' (', th.response_status, ')')
        )
      )
    ) AS prior_treatment_summary
  FROM pavan_naidu.nba.visits v
  LEFT JOIN pavan_naidu.nba.treatments_history th
    ON th.patient_id = v.patient_id
   AND th.started_at < v.visit_date
  GROUP BY v.visit_id
),
visit_counts AS (
  SELECT
    current_visit.visit_id,
    count(prior_visit.visit_id) AS prior_visit_count
  FROM pavan_naidu.nba.visits current_visit
  LEFT JOIN pavan_naidu.nba.visits prior_visit
    ON prior_visit.patient_id = current_visit.patient_id
   AND prior_visit.visit_date < current_visit.visit_date
  GROUP BY current_visit.visit_id
)
SELECT
  v.visit_id,
  v.patient_id,
  p.dog_name,
  p.breed,
  p.sex,
  p.spay_neuter_status,
  p.age_years,
  p.weight_kg,
  p.life_stage,
  p.chronic_kidney_disease,
  p.chronic_gi,
  p.chronic_endocrine,
  p.food_sensitivity,
  v.visit_date,
  v.clinic_id,
  v.visit_type,
  v.presenting_complaint,
  v.cohort,
  v.severity,
  v.days_since_last_visit,
  vs.vomiting,
  vs.diarrhea,
  vs.urinary_accidents,
  vs.straining_to_urinate,
  vs.increased_thirst,
  vs.increased_hunger,
  vs.appetite_change,
  vs.lethargy,
  vs.weight_change_pct,
  vs.duration_days,
  vs.urgency_level,
  vs.recent_diet_change,
  vs.previous_same_issue,
  vn.owner_note,
  vn.clinician_note,
  vc.prior_visit_count,
  coalesce(pd.prior_diagnostic_summary, 'no prior diagnostics recorded') AS prior_diagnostic_summary,
  coalesce(pt.prior_treatment_summary, 'no prior treatments recorded') AS prior_treatment_summary
FROM pavan_naidu.nba.visits v
INNER JOIN pavan_naidu.nba.patients p
  ON p.patient_id = v.patient_id
INNER JOIN pavan_naidu.nba.visit_signals vs
  ON vs.visit_id = v.visit_id
INNER JOIN pavan_naidu.nba.visit_notes vn
  ON vn.visit_id = v.visit_id
INNER JOIN visit_counts vc
  ON vc.visit_id = v.visit_id
LEFT JOIN prior_diag pd
  ON pd.visit_id = v.visit_id
LEFT JOIN prior_tx pt
  ON pt.visit_id = v.visit_id;

CREATE OR REPLACE VIEW pavan_naidu.nba.visit_candidate_features_gold AS
WITH prior_same_test AS (
  SELECT
    v.visit_id,
    dc.test_code,
    min(datediff(v.visit_date, dh.ordered_at)) AS days_since_same_test
  FROM pavan_naidu.nba.visits v
  CROSS JOIN pavan_naidu.nba.diagnostic_catalog dc
  LEFT JOIN pavan_naidu.nba.diagnostic_history dh
    ON dh.patient_id = v.patient_id
   AND dh.test_code = dc.test_code
   AND dh.ordered_at < v.visit_date
  GROUP BY v.visit_id, dc.test_code
)
SELECT
  v.visit_id,
  v.patient_id,
  v.visit_date,
  v.cohort,
  dc.test_code,
  dc.test_name,
  dc.category,
  dc.specimen_type,
  dc.primary_cohort,
  dc.duplicate_suppression_window_days,
  pst.days_since_same_test,
  CASE
    WHEN pst.days_since_same_test IS NOT NULL
      AND pst.days_since_same_test < dc.duplicate_suppression_window_days
    THEN true
    ELSE false
  END AS suppress_duplicate,
  CASE
    WHEN dc.test_code = 'WELLNESS_PANEL' AND v.cohort = 'wellness' THEN 70
    WHEN dc.test_code = 'HEARTWORM_4DX' AND v.cohort = 'wellness' THEN 55
    WHEN dc.test_code = 'FECAL_SCREEN' AND v.cohort = 'wellness' THEN 45
    WHEN dc.test_code = 'GI_PANEL' AND v.cohort = 'gi' THEN 72
    WHEN dc.test_code = 'FECAL_PCR' AND v.cohort = 'gi' THEN 66
    WHEN dc.test_code = 'URINALYSIS' AND v.cohort = 'renal_urinary' THEN 72
    WHEN dc.test_code = 'URINE_CULTURE' AND v.cohort = 'renal_urinary' THEN 63
    WHEN dc.test_code = 'SDMA_CHEM17' AND v.cohort = 'renal_urinary' THEN 60
    WHEN dc.test_code = 'UPC_RATIO' AND v.cohort = 'renal_urinary' THEN 52
    WHEN dc.test_code = 'THYROID_PANEL' AND v.cohort = 'endocrine_metabolic' THEN 72
    WHEN dc.test_code = 'FRUCTOSAMINE' AND v.cohort = 'endocrine_metabolic' THEN 72
    WHEN dc.test_code = 'CORTISOL_SCREEN' AND v.cohort = 'endocrine_metabolic' THEN 68
    ELSE 18
  END
  + CASE WHEN vs.vomiting AND dc.test_code IN ('GI_PANEL', 'FECAL_PCR', 'WELLNESS_PANEL') THEN 18 ELSE 0 END
  + CASE WHEN vs.diarrhea AND dc.test_code IN ('FECAL_PCR', 'FECAL_SCREEN', 'GI_PANEL') THEN 20 ELSE 0 END
  + CASE WHEN vs.urinary_accidents AND dc.test_code IN ('URINALYSIS', 'URINE_CULTURE') THEN 22 ELSE 0 END
  + CASE WHEN vs.straining_to_urinate AND dc.test_code IN ('URINALYSIS', 'URINE_CULTURE') THEN 25 ELSE 0 END
  + CASE WHEN vs.increased_thirst AND dc.test_code IN ('SDMA_CHEM17', 'URINALYSIS', 'FRUCTOSAMINE', 'CORTISOL_SCREEN') THEN 18 ELSE 0 END
  + CASE WHEN vs.increased_hunger AND dc.test_code IN ('FRUCTOSAMINE', 'THYROID_PANEL', 'CORTISOL_SCREEN') THEN 16 ELSE 0 END
  + CASE WHEN vs.lethargy AND dc.test_code IN ('WELLNESS_PANEL', 'THYROID_PANEL', 'SDMA_CHEM17') THEN 12 ELSE 0 END
  + CASE WHEN p.chronic_kidney_disease AND dc.test_code IN ('SDMA_CHEM17', 'URINALYSIS', 'UPC_RATIO') THEN 12 ELSE 0 END
  + CASE WHEN p.chronic_endocrine AND dc.test_code IN ('THYROID_PANEL', 'FRUCTOSAMINE', 'CORTISOL_SCREEN') THEN 12 ELSE 0 END
  + CASE WHEN p.chronic_gi AND dc.test_code IN ('GI_PANEL', 'FECAL_PCR') THEN 10 ELSE 0 END
  - CASE
      WHEN pst.days_since_same_test IS NOT NULL
        AND pst.days_since_same_test < dc.duplicate_suppression_window_days
      THEN 40
      ELSE 0
    END AS utility_score
FROM pavan_naidu.nba.visits v
INNER JOIN pavan_naidu.nba.patients p
  ON p.patient_id = v.patient_id
INNER JOIN pavan_naidu.nba.visit_signals vs
  ON vs.visit_id = v.visit_id
CROSS JOIN pavan_naidu.nba.diagnostic_catalog dc
LEFT JOIN prior_same_test pst
  ON pst.visit_id = v.visit_id
 AND pst.test_code = dc.test_code;

CREATE OR REPLACE VIEW pavan_naidu.nba.visit_recommendation_gold AS
WITH ranked AS (
  SELECT
    cf.visit_id,
    cf.test_code,
    cf.test_name,
    cf.category,
    cf.utility_score,
    row_number() OVER (
      PARTITION BY cf.visit_id
      ORDER BY cf.utility_score DESC, cf.test_code
    ) AS recommendation_rank
  FROM pavan_naidu.nba.visit_candidate_features_gold cf
  WHERE cf.suppress_duplicate = false
),
aggregated AS (
  SELECT
    visit_id,
    max(CASE WHEN recommendation_rank = 1 THEN test_code END) AS recommended_test_1,
    max(CASE WHEN recommendation_rank = 1 THEN test_name END) AS recommended_test_1_name,
    max(CASE WHEN recommendation_rank = 1 THEN utility_score END) AS recommended_test_1_score,
    max(CASE WHEN recommendation_rank = 2 THEN test_code END) AS recommended_test_2,
    max(CASE WHEN recommendation_rank = 2 THEN test_name END) AS recommended_test_2_name,
    max(CASE WHEN recommendation_rank = 2 THEN utility_score END) AS recommended_test_2_score,
    max(CASE WHEN recommendation_rank = 3 THEN test_code END) AS recommended_test_3,
    max(CASE WHEN recommendation_rank = 3 THEN test_name END) AS recommended_test_3_name,
    max(CASE WHEN recommendation_rank = 3 THEN utility_score END) AS recommended_test_3_score
  FROM ranked
  WHERE recommendation_rank <= 3
  GROUP BY visit_id
)
SELECT
  packet.visit_id,
  packet.patient_id,
  packet.dog_name,
  packet.visit_date,
  packet.cohort,
  packet.presenting_complaint,
  agg.recommended_test_1,
  agg.recommended_test_1_name,
  agg.recommended_test_1_score,
  agg.recommended_test_2,
  agg.recommended_test_2_name,
  agg.recommended_test_2_score,
  agg.recommended_test_3,
  agg.recommended_test_3_name,
  agg.recommended_test_3_score,
  CASE
    WHEN agg.recommended_test_1 IS NULL THEN true
    WHEN agg.recommended_test_1_score < 45 THEN true
    ELSE false
  END AS no_additional_diagnostic_now
FROM pavan_naidu.nba.visit_case_packet_gold packet
LEFT JOIN aggregated agg
  ON agg.visit_id = packet.visit_id;
