"""Pydantic request and response models for the IDEXX next-best-action app."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AgentModelOptionOut(BaseModel):
    id: str
    label: str


class AgentTargetOut(BaseModel):
    id: str
    label: str
    transport: Literal["model_serving", "databricks_app"]
    endpoint: str | None = None
    available: bool = False
    default: bool = False
    detail: str | None = None
    model_options: list[AgentModelOptionOut] = Field(default_factory=list)
    default_model_id: str | None = None


class HealthOut(BaseModel):
    status: str
    catalog: str
    schema_name: str
    warehouse_id: str
    supervisor_endpoint: str
    default_agent_target_id: str = "supervisor"
    agent_targets: list[AgentTargetOut] = Field(default_factory=list)


class RecentTestOverrideIn(BaseModel):
    test_code: str
    days_since_same_test: int | None = None


class EditableVisitInput(BaseModel):
    patient_id: str
    seed_visit_id: str | None = None
    dog_name: str | None = None
    breed: str | None = None
    age_years: float | None = None
    life_stage: str | None = None
    as_of_ts: str
    presenting_complaint: str
    severity: str
    urgency_level: str = "routine"
    owner_note: str = ""
    clinician_note: str = ""
    cohort_hint: str | None = None
    vomiting: bool = False
    diarrhea: bool = False
    urinary_accidents: bool = False
    straining_to_urinate: bool = False
    increased_thirst: bool = False
    increased_hunger: bool = False
    lethargy: bool = False
    recent_diet_change: bool = False
    previous_same_issue: bool = False
    appetite_change: str = "stable"
    weight_change_pct: float = 0.0
    duration_days: int = 0
    recent_test_overrides: list[RecentTestOverrideIn] = Field(default_factory=list)


class RecommendationRequestIn(BaseModel):
    agent_target_id: str = "supervisor"
    custom_agent_model_id: str | None = None
    intake: EditableVisitInput

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_intake_payload(cls, value: Any) -> Any:
        if isinstance(value, dict) and "intake" not in value:
            return {"agent_target_id": "supervisor", "intake": value}
        return value


class SampleCaseOut(EditableVisitInput):
    sample_label: str


class SampleCaseListOut(BaseModel):
    items: list[SampleCaseOut] = Field(default_factory=list)


class RecentVisitOut(BaseModel):
    visit_date: str
    cohort: str
    severity: str
    presenting_complaint: str


class HistoryContextOut(BaseModel):
    history_found: bool = False
    patient_id: str | None = None
    as_of_ts: str | None = None
    dog_name: str | None = None
    breed: str | None = None
    age_years: float | None = None
    life_stage: str | None = None
    prior_visit_count: int = 0
    prior_diagnostic_summary: str = "no prior diagnostics recorded"
    prior_treatment_summary: str = "no prior treatments recorded"
    recent_visits: list[RecentVisitOut] = Field(default_factory=list)


class VisitSummaryOut(BaseModel):
    patient_id: str
    seed_visit_id: str | None = None
    dog_name: str
    breed: str
    age_years: float | None = None
    life_stage: str | None = None
    as_of_ts: str
    presenting_complaint: str
    severity: str
    urgency_level: str | None = None
    owner_note: str
    clinician_note: str
    derived_cohort: str


class RecommendationOut(BaseModel):
    action_type: Literal["recommend_test", "abstain"]
    test_code: str | None = None
    test_name: str | None = None
    summary: str | None = None
    reasons: list[str] = Field(default_factory=list)
    evidence_chips: list[str] = Field(default_factory=list)


class ConsideredActionOut(BaseModel):
    test_code: str | None = None
    test_name: str | None = None
    disposition: Literal["not_selected", "suppressed_duplicate", "insufficient_evidence"]
    why_not_selected: str
    days_since_same_test: int | None = None
    suppression_window_days: int | None = None


class EvidenceSourceOut(BaseModel):
    source_type: str
    source_label: str
    summary: str


class TraceStepOut(BaseModel):
    step_index: int
    label: str
    kind: Literal["uc_function", "genie_space", "knowledge_assistant", "supervisor"]
    tool_name: str | None = None
    status: Literal["completed", "missing", "failed"] = "completed"
    duration_ms: int | None = None
    arguments: dict | None = None
    output_preview: str | None = None


class RecommendationRunOut(BaseModel):
    status: Literal["completed", "needs_input", "unavailable"]
    message: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    agent_target_id: str | None = None
    agent_target_label: str | None = None
    custom_agent_model_id: str | None = None
    custom_agent_model_label: str | None = None
    agent_run_id: str | None = None
    supervisor_run_id: str | None = None
    total_duration_ms: int | None = None
    visit: VisitSummaryOut | None = None
    recommendation: RecommendationOut | None = None
    why_not_selected: list[ConsideredActionOut] = Field(default_factory=list)
    suppressed_duplicates: list[ConsideredActionOut] = Field(default_factory=list)
    evidence_sources: list[EvidenceSourceOut] = Field(default_factory=list)
    trace_steps: list[TraceStepOut] = Field(default_factory=list)
