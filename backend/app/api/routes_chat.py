from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.auth import get_current_user, get_current_user_full
from app.db.models import ChatRequest, ChatResponse
from app.db.postgres import postgres_manager
from app.services.rag.pipeline import rag_pipeline

router = APIRouter(tags=["chat"])

_BRANCH_LABELS = {
    "administrativo": "Administrativo",
    "civil": "Civil",
    "comercial": "Comercial",
    "constitucional": "Constitucional",
    "familia": "Família",
    "laboral": "Laboral",
    "penal": "Penal",
    "tributario": "Tributário",
}


def _enforce_daily_limit(current_user: dict) -> None:
    usage = postgres_manager.get_user_daily_message_usage(
        current_user["id"], current_user.get("role", "user")
    )
    if usage.get("daily_limit_exempt"):
        return
    limit = int(usage.get("daily_message_limit", 0) or 0)
    used = int(usage.get("messages_used_today", 0) or 0)
    if limit > 0 and used >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Limite diário de {limit} mensagens atingido. "
                "Tente novamente amanhã ou contacte o administrador."
            ),
        )


def _enforce_chat_owner(chat_id: str | None, user_id: str) -> None:
    if chat_id and not postgres_manager.chat_belongs_to_user(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat nao encontrado")


def _infer_case_branch(case_context: dict) -> tuple[str, str]:
    registered = str(case_context.get("legal_branch") or "").strip().casefold()
    combined = " ".join(
        str(case_context.get(key) or "")
        for key in ("title", "summary", "opposing_party", "court", "case_number")
    ).casefold()

    branch_patterns: list[tuple[str, tuple[str, ...]]] = [
        (
            "laboral",
            (
                "desped",
                "trabalhador",
                "contrato de trabalho",
                "salário",
                "salario",
                "empregador",
                "lgt",
                "lei geral do trabalho",
            ),
        ),
        (
            "penal",
            (
                "desacato",
                "crime",
                "arguido",
                "acusado",
                "acusação",
                "acusacao",
                "detido",
                "prisão",
                "prisao",
                "polícia",
                "policia",
                "policial",
                "filmar",
                "filmagem",
                "gravação",
                "gravacao",
                "código penal",
                "codigo penal",
            ),
        ),
        (
            "familia",
            ("divórcio", "divorcio", "alimentos", "menor", "guarda", "família", "familia"),
        ),
        (
            "tributario",
            ("imposto", "tribut", "fiscal", "contribuinte", "iva"),
        ),
        (
            "administrativo",
            ("acto administrativo", "licença", "licenca", "concurso público", "concurso publico"),
        ),
        (
            "comercial",
            ("sociedade", "sócio", "socio", "quota", "assembleia", "empresa"),
        ),
        (
            "civil",
            ("contrato civil", "arrendamento", "dívida", "divida", "responsabilidade civil"),
        ),
    ]
    for branch, terms in branch_patterns:
        if any(term in combined for term in terms):
            return branch, registered
    return registered or "indeterminada", registered


def _case_search_hint(case_context: dict) -> str:
    branch, _registered_branch = _infer_case_branch(case_context)
    summary = str(case_context.get("summary") or "").casefold()
    title = str(case_context.get("title") or "").casefold()
    combined = " ".join([branch, summary, title])
    hints: list[str] = []

    if any(term in combined for term in ["laboral", "trabalho", "trabalhador", "desped"]):
        hints.append(
            "Lei Geral do Trabalho; despedimento; processo disciplinar; audiência prévia; "
            "ilicitude do despedimento; direitos do trabalhador; impugnação judicial; "
            "reintegração; indemnização; Art. 286; Art. 300; Art. 310; Art. 313; prazos de impugnação."
        )
        if "laboral" in branch:
            return " ".join(hints)
    if any(term in combined for term in ["penal", "crime", "arguido", "detido", "polícia", "policia"]):
        hints.append(
            "Código Penal; Código do Processo Penal; Constituição da República de Angola; "
            "filmagem de agente público; gravações, fotografias e filmes; alegação informal de desacato; "
            "desobediência; resistência contra funcionário; crimes contra a autoridade pública; "
            "direitos do arguido; liberdade de expressão e informação; prova penal; detenção; "
            "tipicidade; ilicitude; legitimidade da ordem; violência ou ameaça de violência; ausência de obstrução."
        )
        if "penal" in branch:
            return " ".join(hints)
    if any(term in combined for term in ["civil", "contrato", "dívida", "divida", "indemnização", "indemnizacao"]):
        hints.append(
            "Código Civil e processo civil; incumprimento contratual; responsabilidade civil; indemnização; prova documental."
        )
        if "civil" in branch:
            return " ".join(hints)
    if any(term in combined for term in ["família", "familia", "menor", "alimentos", "divórcio", "divorcio"]):
        hints.append(
            "Direito da família; poder parental; alimentos; divórcio; interesse superior da criança."
        )
        if "famil" in branch:
            return " ".join(hints)
    if any(term in combined for term in ["administrativo", "estado", "licença", "licenca", "concurso público", "concurso publico"]):
        hints.append(
            "Direito administrativo; acto administrativo; reclamação; recurso hierárquico; impugnação contenciosa."
        )
        if "administrativo" in branch:
            return " ".join(hints)

    return " ".join(hints)


def _compact_text(value: Any, *, limit: int = 260) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if not text:
        return ""
    return text if len(text) <= limit else f"{text[: limit - 3].rstrip()}..."


def _record_lines(records: list[dict], formatter, *, empty: str, limit: int = 5) -> list[str]:
    selected = [formatter(item) for item in records[:limit]]
    selected = [line for line in selected if line]
    return selected or [empty]


def _case_context_text(chat_id: str | None, user_id: str) -> str:
    if not chat_id:
        return ""
    case_context = postgres_manager.get_pro_case_context_for_chat(chat_id, user_id)
    if not case_context:
        return ""
    workspace_id = case_context.get("workspace_id")
    case_id = case_context.get("id")
    documents: list[dict] = []
    tasks: list[dict] = []
    deadlines: list[dict] = []
    notes: list[dict] = []
    timeline: list[dict] = []
    chats: list[dict] = []
    if workspace_id and case_id:
        documents = postgres_manager.list_pro_case_documents(workspace_id, case_id)
        tasks = postgres_manager.list_pro_tasks(workspace_id, case_id)
        deadlines = postgres_manager.list_pro_deadlines(workspace_id, case_id)
        notes = postgres_manager.list_pro_notes(workspace_id, case_id)
        timeline = postgres_manager.list_pro_timeline(workspace_id, case_id)
        chats = postgres_manager.list_pro_case_chats(workspace_id, case_id)
    client_name = case_context.get("client_name") or "o cliente"
    title = case_context.get("title") or "caso sem título"
    effective_branch, registered_branch = _infer_case_branch(case_context)
    search_hint = _case_search_hint(case_context)
    open_tasks = [task for task in tasks if task.get("status") != "done"]
    open_deadlines = [deadline for deadline in deadlines if deadline.get("status") != "done"]
    recent_notes = notes[:3]
    useful_events = timeline[:6]
    dossier_flags = []
    if not documents:
        dossier_flags.append("não há documentos ligados ao caso")
    if not case_context.get("summary"):
        dossier_flags.append("resumo profissional vazio")
    if not deadlines and not case_context.get("next_deadline_at"):
        dossier_flags.append("não há prazos registados")
    if not case_context.get("opposing_party"):
        dossier_flags.append("parte contrária não definida")
    context_lines = [
        "Contexto profissional do caso associado:",
        f"Título: {title}",
        f"Cliente: {client_name}",
        f"Contacto do cliente: telefone={case_context.get('client_phone') or 'Não definido'}; email={case_context.get('client_email') or 'Não definido'}; ID={case_context.get('client_identification_number') or 'Não definido'}",
        f"Morada/observações do cliente: {_compact_text(case_context.get('client_address'), limit=180) or 'Não definido'}",
        f"Notas do cliente: {_compact_text(case_context.get('client_notes'), limit=260) or 'Sem notas'}",
        f"Termos de conflito/interesse: {_compact_text(case_context.get('client_conflict_terms'), limit=220) or 'Sem conflito registado'}",
        f"N.º do processo: {case_context.get('case_number') or 'Não definido'}",
        f"Tribunal/entidade: {case_context.get('court') or 'Não definido'}",
        f"Parte contrária: {case_context.get('opposing_party') or 'Não definido'}",
        f"Área jurídica: {effective_branch or 'Não definida'}",
        (
            f"Área jurídica registada pelo profissional: {registered_branch or 'Não definida'}"
            if registered_branch and registered_branch != effective_branch
            else f"Área jurídica registada pelo profissional: {registered_branch or effective_branch or 'Não definida'}"
        ),
        (
            "Nota de classificação do caso: a área jurídica foi inferida pelos factos do resumo/título "
            f"como {_BRANCH_LABELS.get(effective_branch, effective_branch or 'indeterminada')} para melhorar a recuperação."
            if registered_branch and registered_branch != effective_branch
            else "Nota de classificação do caso: a área jurídica registada é compatível com os factos disponíveis."
        ),
        f"Estado/prioridade: {case_context.get('status') or 'open'} / {case_context.get('priority') or 'normal'}",
        f"Próximo prazo: {case_context.get('next_deadline_at') or 'Não definido'}",
        f"Resumo interno: {case_context.get('summary') or 'Sem resumo interno'}",
        "Dossiê profissional disponível:",
        "Documentos ligados: "
        + " | ".join(
            _record_lines(
                documents,
                lambda doc: f"{doc.get('display_name') or doc.get('filename') or 'Documento'} ({doc.get('status') or 'estado não definido'})",
                empty="nenhum documento ligado",
                limit=4,
            )
        ),
        "Tarefas abertas: "
        + " | ".join(
            _record_lines(
                open_tasks,
                lambda task: f"{task.get('title') or 'Tarefa'}; prioridade={task.get('priority') or 'normal'}; prazo={task.get('due_at') or 'sem data'}; estado={task.get('status') or 'pendente'}",
                empty="nenhuma tarefa aberta",
                limit=5,
            )
        ),
        "Prazos registados: "
        + " | ".join(
            _record_lines(
                open_deadlines,
                lambda deadline: f"{deadline.get('title') or 'Prazo'}; data={deadline.get('due_at') or 'sem data'}; fonte={deadline.get('source') or 'sem fonte'}; estado={deadline.get('status') or 'aberto'}",
                empty="nenhum prazo registado",
                limit=5,
            )
        ),
        "Notas internas recentes: "
        + " | ".join(
            _record_lines(
                recent_notes,
                lambda note: f"{note.get('author_name') or 'Profissional'}: {_compact_text(note.get('body'), limit=220)}",
                empty="sem notas internas recentes",
                limit=3,
            )
        ),
        "Eventos recentes do caso: "
        + " | ".join(
            _record_lines(
                useful_events,
                lambda event: f"{event.get('event_type') or 'evento'} por {event.get('actor_name') or 'Sistema'} em {event.get('created_at') or 'data não definida'}",
                empty="sem timeline relevante",
                limit=6,
            )
        ),
        f"Chats associados ao caso: {len(chats)} conversa(s).",
        "Lacunas do dossiê: " + ("; ".join(dossier_flags) if dossier_flags else "dossiê mínimo preenchido."),
        (
            f"Pergunta contextualizada para pesquisa jurídica: quais são os direitos, riscos e próximos passos de "
            f"{client_name} no caso '{title}', considerando apenas os factos do resumo interno e a legislação aplicável."
        ),
        (
            "Regra de personalização: se a pergunta mencionar 'cliente', 'ele', 'ela' "
            f"ou o assunto deste caso, trate como referência a {client_name} "
            "e use o nome do cliente quando isso tornar a resposta mais clara."
        ),
        (
            "Regra de suficiência: se o contexto profissional identificar o caso e a pesquisa recuperar normas pertinentes, "
            "responda de forma prática e fundamentada; não peça reformulação apenas porque a pergunta usou termos genéricos."
        ),
        (
            "Regra Angola/Modo Pro: trate o caso como trabalho de escritório jurídico angolano. "
            "Priorize legislação angolana, prazos, prova, actos perante tribunal/entidade competente, "
            "riscos processuais e necessidade de validação por advogado antes de submissão formal."
        ),
        "Use este contexto apenas como enquadramento factual fornecido pelo profissional. Não invente factos adicionais.",
    ]
    if search_hint:
        context_lines.insert(10, f"Termos jurídicos úteis para recuperação: {search_hint}")
    return "\n".join(context_lines)


def _with_case_context(history: list[str], chat_id: str | None, user_id: str) -> list[str]:
    if not chat_id:
        return history
    case_context = postgres_manager.get_pro_case_context_for_chat(chat_id, user_id)
    if not case_context:
        return history
    effective_branch, registered_branch = _infer_case_branch(case_context)
    contextual_user_anchor = (
        "Utilizador: No caso profissional associado, "
        f"o cliente {case_context.get('client_name') or 'não identificado'} "
        f"está ligado ao assunto '{case_context.get('title') or 'caso sem título'}'. "
        f"Área jurídica inferida: {effective_branch or 'não definida'}. "
        f"Área registada: {registered_branch or 'não definida'}. "
        f"Parte contrária: {case_context.get('opposing_party') or 'não definida'}. "
        f"Resumo do caso: {case_context.get('summary') or 'sem resumo interno'}."
    )
    return [*history, contextual_user_anchor, _case_context_text(chat_id, user_id)]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, current_user: dict = Depends(get_current_user_full)
) -> ChatResponse:
    try:
        _enforce_chat_owner(payload.chat_id, current_user["id"])
        _enforce_daily_limit(current_user)
        case_query_context = _case_context_text(payload.chat_id, current_user["id"])
        return await rag_pipeline.answer_query(
            payload.question,
            provider=payload.provider,
            conversation_history=_with_case_context(
                payload.conversation_history, payload.chat_id, current_user["id"]
            ),
            chat_id=payload.chat_id,
            active_document_id=payload.active_document_id,
            user_id=current_user["id"],
            query_context=case_query_context,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao responder pergunta: {exc}"
        ) from exc


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest, current_user: dict = Depends(get_current_user_full)
):
    try:
        _enforce_chat_owner(payload.chat_id, current_user["id"])
        _enforce_daily_limit(current_user)
        case_query_context = _case_context_text(payload.chat_id, current_user["id"])
        return StreamingResponse(
            rag_pipeline.answer_query_stream_safe(
                payload.question,
                provider=payload.provider,
                conversation_history=_with_case_context(
                    payload.conversation_history, payload.chat_id, current_user["id"]
                ),
                chat_id=payload.chat_id,
                active_document_id=payload.active_document_id,
                user_id=current_user["id"],
                query_context=case_query_context,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha ao responder pergunta: {exc}"
        ) from exc


@router.post("/chat/preflight")
async def chat_preflight(
    payload: ChatRequest, current_user: dict = Depends(get_current_user)
):
    """Lightweight classification + clarifying gate without retrieval or LLM generation."""
    try:
        _enforce_chat_owner(payload.chat_id, current_user["id"])
        result = await rag_pipeline.preflight_classify(
            payload.question,
            provider=payload.provider,
            conversation_history=_with_case_context(
                payload.conversation_history, payload.chat_id, current_user["id"]
            ),
            chat_id=payload.chat_id,
            user_id=current_user["id"],
            query_context=_case_context_text(payload.chat_id, current_user["id"]),
        )
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Falha na classificacao: {exc}"
        ) from exc
