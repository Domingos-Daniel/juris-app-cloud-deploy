from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.core.auth import get_current_user
from app.db.postgres import postgres_manager

router = APIRouter(prefix="/jurisprudence", tags=["Jurisprudence"])


class HoldingItem(BaseModel):
    id: str
    holding_text: str
    legal_branch: Optional[str] = None
    topic_route: Optional[str] = None


class JurisprudenceCase(BaseModel):
    id: str
    court: str
    chamber: Optional[str] = None
    case_number: Optional[str] = None
    title: str
    decision_date: Optional[str] = None
    publication_date: Optional[str] = None
    url: str
    pdf_url: Optional[str] = None
    legal_branch: Optional[str] = None
    topic_route: Optional[str] = None
    summary: Optional[str] = None
    holdings: list[HoldingItem] = []


class JurisprudenceListItem(BaseModel):
    id: str
    court: str
    case_number: Optional[str] = None
    title: str
    decision_date: Optional[str] = None
    publication_date: Optional[str] = None
    legal_branch: Optional[str] = None
    summary: Optional[str] = None
    url: str


class JurisprudenceListResponse(BaseModel):
    items: list[JurisprudenceListItem]
    total: int
    courts: list[str] = []
    branches: list[str] = []


@router.get("", response_model=JurisprudenceListResponse)
async def list_jurisprudence(
    court: Optional[str] = Query(None, description="Filter by court"),
    branch: Optional[str] = Query(None, alias="legal_branch", description="Filter by legal branch"),
    search: Optional[str] = Query(None, description="Search in title, summary, case_number"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    items, total = postgres_manager.list_jurisprudence(
        court=court,
        legal_branch=branch,
        search=search,
        limit=limit,
        offset=offset,
    )
    courts = postgres_manager.list_jurisprudence_courts()
    branches = postgres_manager.list_jurisprudence_branches()
    return JurisprudenceListResponse(
        items=[JurisprudenceListItem(**c) for c in items],
        total=total,
        courts=courts,
        branches=branches,
    )


@router.get("/{case_id}", response_model=JurisprudenceCase)
async def get_jurisprudence_case(
    case_id: str,
    current_user: dict = Depends(get_current_user),
):
    case = postgres_manager.get_jurisprudence_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Caso de jurisprudencia nao encontrado")
    return JurisprudenceCase(**case)
