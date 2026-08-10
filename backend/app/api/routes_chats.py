from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.db.models import ChatListItem, ChatListResponse
from app.db.postgres import postgres_manager


router = APIRouter(tags=["chats"])


class SaveEditVersionRequest(BaseModel):
    edit_index: int
    version_index: int
    messages: list[dict]


def _require_chat_owner(chat_id: str, user_id: str) -> None:
    if not postgres_manager.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat nao encontrado")


@router.get("/chats/{chat_id}/versions")
async def list_edit_versions(
    chat_id: str,
    current_user: dict = Depends(get_current_user),
):
    try:
        _require_chat_owner(chat_id, current_user["id"])
        return postgres_manager.get_edit_versions(chat_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao listar versoes: {exc}"
        ) from exc


@router.post("/chats/{chat_id}/versions")
async def save_edit_version(
    chat_id: str,
    body: SaveEditVersionRequest,
    current_user: dict = Depends(get_current_user),
):
    try:
        _require_chat_owner(chat_id, current_user["id"])
        postgres_manager.save_edit_version(
            chat_id=chat_id,
            edit_index=body.edit_index,
            version_index=body.version_index,
            version_data=body.messages,
        )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao salvar versao: {exc}"
        ) from exc


@router.get("/chats", response_model=ChatListResponse)
async def list_chats(
    current_user: dict = Depends(get_current_user),
) -> ChatListResponse:
    try:
        items = [
            ChatListItem(**item)
            for item in postgres_manager.list_chats(user_id=current_user["id"])
        ]
        return ChatListResponse(items=items)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao listar chats: {exc}"
        ) from exc


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: str, current_user: dict = Depends(get_current_user)):
    try:
        deleted = postgres_manager.delete_chat(chat_id, user_id=current_user["id"])
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat nao encontrado")
        return {"ok": True, "deleted": chat_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao eliminar chat: {exc}"
        ) from exc


@router.delete("/chats")
async def delete_all_chats(current_user: dict = Depends(get_current_user)):
    try:
        count = postgres_manager.delete_all_chats(user_id=current_user["id"])
        return {"ok": True, "deleted_count": count}
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao eliminar chats: {exc}"
        ) from exc
