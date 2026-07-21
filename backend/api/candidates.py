import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import verify_token
from database import get_session
from models import CandidateProvider, CandidateSourceState
from services.candidate_pool import refresh_candidate_pool


router = APIRouter()


class CandidateReview(BaseModel):
    status: Literal["pending", "reviewed", "ignored"]


def _candidate_dict(row: CandidateProvider) -> dict:
    return {
        **row.model_dump(exclude={"models_json", "sources_json", "evidence_json", "yaml_draft"}),
        "models": json.loads(row.models_json or "[]"),
        "sources": json.loads(row.sources_json or "[]"),
        "has_yaml_draft": bool(row.yaml_draft),
    }


@router.get("")
def list_candidates(
    status: str | None = None,
    include_configured: bool = False,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    statement = select(CandidateProvider).where(CandidateProvider.is_present == True)
    if status:
        statement = statement.where(CandidateProvider.status == status)
    elif not include_configured:
        statement = statement.where(CandidateProvider.status != "configured")
    rows = session.exec(statement.order_by(CandidateProvider.name)).all()
    return [_candidate_dict(row) for row in rows]


@router.get("/sources")
def list_candidate_sources(
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    return session.exec(select(CandidateSourceState).order_by(CandidateSourceState.source_id)).all()


@router.post("/refresh")
async def refresh_candidates(_=Depends(verify_token)):
    return await refresh_candidate_pool()


@router.get("/{provider_id}/yaml-draft")
def get_yaml_draft(
    provider_id: str,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    row = session.get(CandidateProvider, provider_id)
    if not row or not row.yaml_draft:
        raise HTTPException(404, "Candidate or YAML draft not found")
    if row.admission_status == "excluded":
        raise HTTPException(409, f"Candidate excluded by admission policy: {row.exclusion_reason}")
    return {"provider_id": provider_id, "yaml": row.yaml_draft}


@router.patch("/{provider_id}")
async def review_candidate(
    provider_id: str,
    body: CandidateReview,
    session: Session = Depends(get_session),
    _=Depends(verify_token),
):
    row = session.get(CandidateProvider, provider_id)
    if not row:
        raise HTTPException(404, "Candidate not found")
    if row.status == "configured":
        raise HTTPException(409, "Configured providers cannot be reclassified here")
    if body.status == "reviewed" and row.admission_status == "excluded":
        raise HTTPException(409, f"Candidate excluded by admission policy: {row.exclusion_reason}")
    row.status = body.status
    session.add(row)
    session.commit()
    from services.notifications import reconcile_notifications
    reconcile_notifications(session)
    from services.notifications import broadcast_notifications_updated
    await broadcast_notifications_updated()
    return _candidate_dict(row)
