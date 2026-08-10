from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core.auth import get_current_user_full
from app.db.postgres import postgres_manager

router = APIRouter(tags=["pro"], prefix="/pro")


class ProClientPayload(BaseModel):
    client_type: str = "individual"
    name: str = Field(..., min_length=2)
    email: str = ""
    phone: str = ""
    identification_number: str = ""
    address: str = ""
    notes: str = ""
    conflict_terms: str = ""
    status: str = "active"


class ProCasePayload(BaseModel):
    client_id: str | None = None
    title: str = Field(..., min_length=2)
    case_number: str = ""
    court: str = ""
    opposing_party: str = ""
    legal_branch: str = ""
    status: str = "open"
    priority: str = "normal"
    opened_at: str | None = None
    next_deadline_at: str | None = None
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LinkChatPayload(BaseModel):
    chat_id: str | None = None
    title: str = "Consulta do caso"


class LinkDocumentPayload(BaseModel):
    document_id: str
    label: str = ""


class ProTaskPayload(BaseModel):
    title: str = Field(..., min_length=2)
    description: str = ""
    status: str = "pending"
    priority: str = "normal"
    due_at: str | None = None
    assigned_to: str | None = None


class ProDeadlinePayload(BaseModel):
    title: str = Field(..., min_length=2)
    due_at: str
    source: str = ""
    status: str = "open"
    reminder_days: int = 3


class ProNotePayload(BaseModel):
    body: str = Field(..., min_length=2)
    visibility: str = "internal"


def _value(value: Any, fallback: str = "—") -> str:
    text = str(value or "").strip()
    return text or fallback


def _case_export_markdown(detail: dict[str, Any]) -> str:
    case = detail.get("case") or {}
    chats = detail.get("chats") or []
    documents = detail.get("documents") or []
    tasks = detail.get("tasks") or []
    deadlines = detail.get("deadlines") or []
    notes = detail.get("notes") or []
    timeline = detail.get("timeline") or []

    def lines(items: list[dict[str, Any]], formatter, empty: str) -> list[str]:
        if not items:
            return [f"- {empty}"]
        return [f"- {formatter(item)}" for item in items]

    open_tasks = [item for item in tasks if item.get("status") != "done"]
    open_deadlines = [item for item in deadlines if item.get("status") not in {"done", "closed"}]
    readiness_points = [
        bool(case.get("client_name")),
        bool(case.get("summary")),
        bool(case.get("opposing_party")),
        bool(documents),
        bool(open_deadlines or case.get("next_deadline_at")),
        bool(chats),
    ]
    readiness = round((sum(1 for item in readiness_points if item) / len(readiness_points)) * 100)

    sections = [
        f"# Dossiê profissional — {_value(case.get('title'), 'Caso sem título')}",
        "",
        "## Identificação",
        f"- Cliente: {_value(case.get('client_name'), 'Sem cliente associado')}",
        f"- Área jurídica: {_value(case.get('legal_branch'), 'Não definida')}",
        f"- Estado/Prioridade: {_value(case.get('status'), 'open')} / {_value(case.get('priority'), 'normal')}",
        f"- Processo: {_value(case.get('case_number'))}",
        f"- Tribunal/entidade: {_value(case.get('court'))}",
        f"- Parte contrária: {_value(case.get('opposing_party'))}",
        f"- Próximo prazo: {_value(case.get('next_deadline_at'))}",
        "",
        "## Resumo factual",
        _value(case.get("summary"), "Sem resumo profissional registado."),
        "",
        "## Prontidão do dossiê",
        f"- Percentagem: {readiness}%",
        f"- Documentos ligados: {len(documents)}",
        f"- Chats associados: {len(chats)}",
        f"- Tarefas abertas: {len(open_tasks)}",
        f"- Prazos abertos: {len(open_deadlines)}",
        "",
        "## Documentos",
        *lines(documents, lambda item: f"{_value(item.get('display_name') or item.get('filename'), 'Documento')} — estado: {_value(item.get('status'))}", "Nenhum documento ligado."),
        "",
        "## Tarefas",
        *lines(tasks, lambda item: f"{_value(item.get('title'), 'Tarefa')} — {_value(item.get('status'), 'pending')} — prioridade {_value(item.get('priority'), 'normal')} — prazo {_value(item.get('due_at'))}", "Nenhuma tarefa registada."),
        "",
        "## Prazos",
        *lines(deadlines, lambda item: f"{_value(item.get('title'), 'Prazo')} — {_value(item.get('due_at'))} — fonte: {_value(item.get('source'))} — estado {_value(item.get('status'), 'open')}", "Nenhum prazo registado."),
        "",
        "## Notas internas",
        *lines(notes, lambda item: f"{_value(item.get('created_at'))} — {_value(item.get('author_name'), 'Profissional')}: {_value(item.get('body'))}", "Nenhuma nota interna."),
        "",
        "## Conversas associadas",
        *lines(chats, lambda item: f"{_value(item.get('title'), 'Conversa')} — {item.get('message_count') or 0} mensagens", "Nenhuma conversa associada."),
        "",
        "## Timeline",
        *lines(timeline, lambda item: f"{_value(item.get('created_at'))} — {_value(item.get('event_type'))} — {_value(item.get('actor_name'), 'Sistema')}", "Sem eventos registados."),
        "",
        "---",
        "Gerado pelo jURIS-APP Modo Pro. Revise antes de usar em acto profissional.",
    ]
    return "\n".join(sections).strip() + "\n"


def _profile_is_active(user: dict) -> bool:
    profile = user.get("professional_profile")
    return bool(profile and profile.get("status") == "active" and profile.get("workspace"))


async def require_pro_access(current_user: dict = Depends(get_current_user_full)) -> dict:
    if current_user.get("role") == "admin" or _profile_is_active(current_user):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Modo Pro nao esta ativo para este utilizador",
    )


def _workspace_or_403(current_user: dict) -> dict:
    profile = postgres_manager.get_professional_profile(current_user["id"])
    if profile and profile.get("status") == "active" and profile.get("workspace"):
        return profile["workspace"]
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Ative o perfil profissional antes de gerir clientes e casos",
    )


@router.get("/dashboard")
async def pro_dashboard(current_user: dict = Depends(require_pro_access)):
    profile = postgres_manager.get_professional_profile(current_user["id"])
    if profile and profile.get("status") == "active" and profile.get("workspace"):
        workspace = profile["workspace"]
        return {
            "profile": profile,
            "workspace": workspace,
            **postgres_manager.get_pro_dashboard(workspace["id"]),
        }
    return {
        "profile": profile,
        "workspace": None,
        "admin_overview": postgres_manager.get_pro_admin_overview(),
        "totals": {"clients": 0, "cases": 0, "open_tasks": 0, "open_deadlines": 0},
        "recent_cases": [],
    }


@router.get("/clients")
async def list_clients(search: str = "", current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_clients(workspace["id"], search or None)}


@router.post("/clients")
async def create_client(payload: ProClientPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_client(workspace["id"], current_user["id"], payload.model_dump())


@router.patch("/clients/{client_id}")
async def update_client(client_id: str, payload: ProClientPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_client(workspace["id"], current_user["id"], payload.model_dump(), client_id)


@router.delete("/clients/{client_id}")
async def archive_client(client_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    if not postgres_manager.archive_pro_client(workspace["id"], current_user["id"], client_id):
        raise HTTPException(status_code=404, detail="Cliente nao encontrado")
    return {"ok": True}


@router.get("/cases")
async def list_cases(
    search: str = "",
    status_filter: str = "",
    current_user: dict = Depends(require_pro_access),
):
    workspace = _workspace_or_403(current_user)
    return {
        "items": postgres_manager.list_pro_cases(
            workspace["id"], search or None, status_filter or None
        )
    }


@router.post("/cases")
async def create_case(payload: ProCasePayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_case(workspace["id"], current_user["id"], payload.model_dump())


@router.get("/cases/{case_id}")
async def get_case(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    case = postgres_manager.get_pro_case(workspace["id"], case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Caso nao encontrado")
    return {
        "case": case,
        "chats": postgres_manager.list_pro_case_chats(workspace["id"], case_id),
        "documents": postgres_manager.list_pro_case_documents(workspace["id"], case_id),
        "tasks": postgres_manager.list_pro_tasks(workspace["id"], case_id),
        "deadlines": postgres_manager.list_pro_deadlines(workspace["id"], case_id),
        "notes": postgres_manager.list_pro_notes(workspace["id"], case_id),
        "timeline": postgres_manager.list_pro_timeline(workspace["id"], case_id),
    }


@router.patch("/cases/{case_id}")
async def update_case(case_id: str, payload: ProCasePayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_case(workspace["id"], current_user["id"], payload.model_dump(), case_id)


@router.delete("/cases/{case_id}")
async def archive_case(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    if not postgres_manager.archive_pro_case(workspace["id"], current_user["id"], case_id):
        raise HTTPException(status_code=404, detail="Caso nao encontrado")
    return {"ok": True}


@router.get("/cases/{case_id}/chats")
async def list_case_chats(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_case_chats(workspace["id"], case_id)}


@router.post("/cases/{case_id}/chats")
async def link_case_chat(case_id: str, payload: LinkChatPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    chat_id = payload.chat_id or postgres_manager.create_chat(
        payload.title or "Consulta do caso",
        user_id=current_user["id"],
    )
    if not postgres_manager.link_pro_case_chat(workspace["id"], current_user["id"], case_id, chat_id):
        raise HTTPException(status_code=404, detail="Caso ou chat nao encontrado")
    return {"ok": True, "chat_id": chat_id}


@router.get("/cases/{case_id}/documents")
async def list_case_documents(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_case_documents(workspace["id"], case_id)}


@router.post("/cases/{case_id}/documents")
async def link_case_document(case_id: str, payload: LinkDocumentPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    if not postgres_manager.link_pro_case_document(
        workspace["id"], current_user["id"], case_id, payload.document_id, payload.label
    ):
        raise HTTPException(status_code=404, detail="Caso ou documento nao encontrado")
    return {"ok": True}


@router.get("/cases/{case_id}/tasks")
async def list_case_tasks(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_tasks(workspace["id"], case_id)}


@router.post("/cases/{case_id}/tasks")
async def create_case_task(case_id: str, payload: ProTaskPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_task(workspace["id"], current_user["id"], case_id, payload.model_dump())


@router.patch("/cases/{case_id}/tasks/{task_id}")
async def update_case_task(case_id: str, task_id: str, payload: ProTaskPayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_task(workspace["id"], current_user["id"], case_id, payload.model_dump(), task_id)


@router.get("/cases/{case_id}/deadlines")
async def list_case_deadlines(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_deadlines(workspace["id"], case_id)}


@router.post("/cases/{case_id}/deadlines")
async def create_case_deadline(case_id: str, payload: ProDeadlinePayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_deadline(workspace["id"], current_user["id"], case_id, payload.model_dump())


@router.patch("/cases/{case_id}/deadlines/{deadline_id}")
async def update_case_deadline(case_id: str, deadline_id: str, payload: ProDeadlinePayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.upsert_pro_deadline(workspace["id"], current_user["id"], case_id, payload.model_dump(), deadline_id)


@router.get("/cases/{case_id}/notes")
async def list_case_notes(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_notes(workspace["id"], case_id)}


@router.post("/cases/{case_id}/notes")
async def create_case_note(case_id: str, payload: ProNotePayload, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return postgres_manager.create_pro_note(workspace["id"], current_user["id"], case_id, payload.model_dump())


@router.get("/cases/{case_id}/timeline")
async def case_timeline(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    return {"items": postgres_manager.list_pro_timeline(workspace["id"], case_id)}

@router.get("/cases/{case_id}/export", response_class=PlainTextResponse)
async def export_case(case_id: str, current_user: dict = Depends(require_pro_access)):
    workspace = _workspace_or_403(current_user)
    case = postgres_manager.get_pro_case(workspace["id"], case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Caso nao encontrado")
    detail = {
        "case": case,
        "chats": postgres_manager.list_pro_case_chats(workspace["id"], case_id),
        "documents": postgres_manager.list_pro_case_documents(workspace["id"], case_id),
        "tasks": postgres_manager.list_pro_tasks(workspace["id"], case_id),
        "deadlines": postgres_manager.list_pro_deadlines(workspace["id"], case_id),
        "notes": postgres_manager.list_pro_notes(workspace["id"], case_id),
        "timeline": postgres_manager.list_pro_timeline(workspace["id"], case_id),
    }
    return PlainTextResponse(
        _case_export_markdown(detail),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=pro-case-{case_id}.md"},
    )

