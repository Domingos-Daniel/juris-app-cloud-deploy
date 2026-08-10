from __future__ import annotations

import time
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.core.auth import get_current_user_full
from app.db.postgres import postgres_manager

router = APIRouter(tags=["admin"], prefix="/admin")


class EditUserRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str | None = None


class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    phone: str = ""
    role: str = "user"


class AddJurisprudenceRequest(BaseModel):
    court: str
    case_number: str = ""
    title: str
    decision_date: str = ""
    legal_branch: str = ""
    summary: str = ""
    url: str = ""


class UpdateUsageLimitsRequest(BaseModel):
    daily_message_limit: int


class ResetPasswordRequest(BaseModel):
    password: str


class ProfessionalProfileRequest(BaseModel):
    status: str = "active"
    display_name: str | None = None
    license_number: str | None = None
    professional_title: str | None = None
    organization_name: str | None = None


async def require_admin(current_user: dict = Depends(get_current_user_full)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso reservado ao administrador")
    return current_user


@router.get("/stats")
async def admin_stats(admin: dict = Depends(require_admin)):
    return postgres_manager.get_admin_stats()


@router.get("/analytics")
async def admin_analytics(admin: dict = Depends(require_admin)):
    return postgres_manager.get_admin_analytics()


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    return postgres_manager.list_users()


@router.post("/users/{user_id}/professional-profile")
async def activate_professional_profile(
    user_id: str,
    payload: ProfessionalProfileRequest,
    admin: dict = Depends(require_admin),
):
    profile = postgres_manager.set_professional_profile_admin(
        user_id,
        status=payload.status or "active",
        display_name=payload.display_name,
        license_number=payload.license_number,
        professional_title=payload.professional_title,
        organization_name=payload.organization_name,
        actor_user_id=admin["id"],
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Utilizador nao encontrado ou estado invalido")
    return {"ok": True, "professional_profile": profile}


@router.patch("/users/{user_id}/professional-profile")
async def update_professional_profile(
    user_id: str,
    payload: ProfessionalProfileRequest,
    admin: dict = Depends(require_admin),
):
    profile = postgres_manager.set_professional_profile_admin(
        user_id,
        status=payload.status,
        display_name=payload.display_name,
        license_number=payload.license_number,
        professional_title=payload.professional_title,
        organization_name=payload.organization_name,
        actor_user_id=admin["id"],
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Utilizador nao encontrado ou estado invalido")
    return {"ok": True, "professional_profile": profile}

@router.get("/settings")
async def get_admin_settings(admin: dict = Depends(require_admin)):
    return {
        "usage_limits": postgres_manager.get_usage_limits(),
    }


@router.put("/settings")
async def update_admin_settings(
    payload: UpdateUsageLimitsRequest, admin: dict = Depends(require_admin)
):
    if payload.daily_message_limit < 0:
        raise HTTPException(status_code=400, detail="O limite nao pode ser negativo")
    return {
        "usage_limits": postgres_manager.update_usage_limits(
            payload.daily_message_limit
        )
    }


@router.post("/users")
async def create_user(payload: CreateUserRequest, admin: dict = Depends(require_admin)):
    from app.core.auth import _hash_password
    import uuid
    if payload.role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="Perfil invalido")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")
    user_id = str(uuid.uuid4())
    pw_hash = _hash_password(payload.password)
    ok = postgres_manager.create_user_admin(user_id, payload.name, payload.email, payload.phone, pw_hash, payload.role)
    if not ok:
        raise HTTPException(status_code=400, detail="Email ja existe")
    return {"ok": True, "id": user_id}


@router.put("/users/{user_id}")
async def edit_user(user_id: str, payload: EditUserRequest, admin: dict = Depends(require_admin)):
    if payload.role is not None and payload.role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="Perfil invalido")
    ok = postgres_manager.update_user_admin(user_id, payload.name, payload.email, payload.phone, payload.role)
    if not ok:
        raise HTTPException(status_code=404, detail="Utilizador nao encontrado")
    return {"ok": True}


@router.put("/users/{user_id}/password")
async def reset_user_password(
    user_id: str,
    payload: ResetPasswordRequest,
    admin: dict = Depends(require_admin),
):
    from app.core.auth import _hash_password
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres")
    ok = postgres_manager.update_user_password_admin(user_id, _hash_password(payload.password))
    if not ok:
        raise HTTPException(status_code=404, detail="Utilizador nao encontrado")
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: dict = Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Nao pode remover a propria conta")
    ok = postgres_manager.delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Utilizador nao encontrado")
    return {"ok": True}


@router.get("/queries")
async def recent_queries(limit: int = 20, admin: dict = Depends(require_admin)):
    return postgres_manager.get_recent_queries(limit)


@router.get("/conversations")
async def admin_conversations(
    limit: int = 60,
    search: str = "",
    admin: dict = Depends(require_admin),
):
    return postgres_manager.list_admin_conversations(limit=limit, search=search or None)


@router.get("/conversations/{chat_id}")
async def admin_conversation_detail(chat_id: str, admin: dict = Depends(require_admin)):
    conversation = postgres_manager.get_admin_conversation(chat_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversa nao encontrada")
    return conversation


@router.post("/docs/remove/{document_id}")
async def remove_document(document_id: str, admin: dict = Depends(require_admin)):
    ok = postgres_manager.delete_document(document_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Documento nao encontrado")
    return {"ok": True}


@router.post("/ingest")
async def trigger_ingestion(admin: dict = Depends(require_admin)):
    try:
        from app.services.pdf.ingestion import run_ingestion
        import asyncio
        asyncio.create_task(run_ingestion())
        return {"ok": True, "message": "Ingestao iniciada em background"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest/status")
async def ingest_status(admin: dict = Depends(require_admin)):
    return postgres_manager.get_legal_segment_stats()


# ── Jurisprudence management ──────────────────────────────────────────

@router.get("/jurisprudence")
async def list_jurisprudence_admin(
    court: str = "", branch: str = "", search: str = "",
    limit: int = 50, offset: int = 0,
    admin: dict = Depends(require_admin),
):
    items, total = postgres_manager.list_jurisprudence(
        court=court or None, legal_branch=branch or None,
        search=search or None, limit=limit, offset=offset
    )
    courts = postgres_manager.list_jurisprudence_courts()
    branches = postgres_manager.list_jurisprudence_branches()
    return {"items": items, "total": total, "courts": courts, "branches": branches}


@router.post("/jurisprudence")
async def add_jurisprudence(payload: AddJurisprudenceRequest, admin: dict = Depends(require_admin)):
    import uuid
    cid = str(uuid.uuid4())
    ok = postgres_manager.insert_jurisprudence_case(
        cid, payload.court, payload.case_number, payload.title,
        payload.decision_date, payload.legal_branch, payload.summary, payload.url
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Erro ao inserir")
    return {"ok": True, "id": cid}


@router.delete("/jurisprudence/{case_id}")
async def delete_jurisprudence(case_id: str, admin: dict = Depends(require_admin)):
    ok = postgres_manager.delete_jurisprudence_case(case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Nao encontrado")
    return {"ok": True}
