"""API routes for the IDEXX next-best-action app."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .models import (
    HealthOut,
    RecommendationRequestIn,
    RecommendationRunOut,
    SampleCaseListOut,
)
from .service import RecommendationService


router = APIRouter(prefix="/api", tags=["nba"])
service = RecommendationService()


@router.get("/health", response_model=HealthOut, operation_id="getHealth")
def get_health() -> HealthOut:
    return HealthOut(**service.health_payload())


@router.get("/sample-cases", response_model=SampleCaseListOut, operation_id="listSampleCases")
def list_sample_cases() -> SampleCaseListOut:
    try:
        return SampleCaseListOut(items=service.list_sample_cases())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/recommendations", response_model=RecommendationRunOut, operation_id="runRecommendation")
def run_recommendation(payload: RecommendationRequestIn) -> RecommendationRunOut:
    missing_fields = service.missing_fields(payload.intake)
    if missing_fields:
        return RecommendationRunOut(
            status="needs_input",
            message="Add a little more intake detail before running the live recommendation.",
            missing_fields=missing_fields,
            agent_target_id=payload.agent_target_id,
        )

    try:
        result = service.run_live_recommendation(
            payload.intake,
            agent_target_id=payload.agent_target_id,
            custom_agent_model_id=payload.custom_agent_model_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RecommendationRunOut(**result)
