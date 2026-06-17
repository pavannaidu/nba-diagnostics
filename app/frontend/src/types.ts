export type AgentModelOption = {
  id: string;
  label: string;
};

export type AgentTarget = {
  id: string;
  label: string;
  transport: "model_serving" | "databricks_app";
  endpoint?: string | null;
  available: boolean;
  default: boolean;
  detail?: string | null;
  model_options: AgentModelOption[];
  default_model_id?: string | null;
};

export type HealthResponse = {
  status: string;
  catalog: string;
  schema_name: string;
  warehouse_id: string;
  supervisor_endpoint: string;
  default_agent_target_id: string;
  agent_targets: AgentTarget[];
};

export type RecentTestOverride = {
  test_code: string;
  days_since_same_test: number | null;
};

export type EditableVisitInput = {
  patient_id: string;
  seed_visit_id?: string | null;
  dog_name?: string | null;
  breed?: string | null;
  age_years?: number | null;
  life_stage?: string | null;
  as_of_ts: string;
  presenting_complaint: string;
  severity: string;
  urgency_level: string;
  owner_note: string;
  clinician_note: string;
  cohort_hint?: string | null;
  vomiting: boolean;
  diarrhea: boolean;
  urinary_accidents: boolean;
  straining_to_urinate: boolean;
  increased_thirst: boolean;
  increased_hunger: boolean;
  lethargy: boolean;
  recent_diet_change: boolean;
  previous_same_issue: boolean;
  appetite_change: string;
  weight_change_pct: number;
  duration_days: number;
  recent_test_overrides: RecentTestOverride[];
};

export type SampleCase = EditableVisitInput & {
  sample_label: string;
};

export type ConsideredAction = {
  test_code?: string | null;
  test_name?: string | null;
  disposition: "not_selected" | "suppressed_duplicate" | "insufficient_evidence";
  why_not_selected: string;
  days_since_same_test?: number | null;
  suppression_window_days?: number | null;
};

export type EvidenceSource = {
  source_type: string;
  source_label: string;
  summary: string;
  links?: EvidenceSourceLink[];
};

export type EvidenceSourceLink = {
  label: string;
  url: string;
  pmid?: string | null;
};

export type EvidenceView = {
  key: string;
  eyebrow: string;
  title: string;
  points: string[];
  links: EvidenceSourceLink[];
};

export type TraceStep = {
  step_index: number;
  label: string;
  kind: "uc_function" | "genie_space" | "knowledge_assistant" | "supervisor";
  tool_name?: string | null;
  status: "completed" | "missing" | "failed";
  duration_ms?: number | null;
  arguments?: Record<string, unknown> | null;
  output_preview?: string | null;
};

export type RecommendationRun = {
  status: "completed" | "needs_input" | "unavailable";
  message?: string | null;
  missing_fields: string[];
  agent_target_id?: string | null;
  agent_target_label?: string | null;
  custom_agent_model_id?: string | null;
  custom_agent_model_label?: string | null;
  agent_run_id?: string | null;
  supervisor_run_id?: string | null;
  total_duration_ms?: number | null;
  visit?: {
    patient_id: string;
    seed_visit_id?: string | null;
    dog_name: string;
    breed: string;
    age_years?: number | null;
    life_stage?: string | null;
    as_of_ts: string;
    presenting_complaint: string;
    severity: string;
    urgency_level?: string | null;
    owner_note: string;
    clinician_note: string;
    derived_cohort: string;
  } | null;
  recommendation?: {
    action_type: "recommend_test" | "abstain";
    test_code?: string | null;
    test_name?: string | null;
    summary?: string | null;
    reasons: string[];
    evidence_chips: string[];
  } | null;
  why_not_selected: ConsideredAction[];
  suppressed_duplicates: ConsideredAction[];
  evidence_sources: EvidenceSource[];
  trace_steps: TraceStep[];
};

export type PanelKey = "intake" | "recommendation";
