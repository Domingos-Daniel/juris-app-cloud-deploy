from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time as _time
from dataclasses import asdict, replace

from cachetools import TTLCache
from app.core.config import get_settings
from app.db.models import ChatResponse, RetrievedChunk, SourceItem
from app.db.postgres import postgres_manager
from app.services.legal.text_normalization import normalize_legal_text

logger = logging.getLogger(__name__)
MAX_OFFICIAL_EVIDENCES = 12
MAX_COMBINED_EVIDENCES = 12

# Cache de classificações — evita chamar o LLM duas vezes (preflight + stream)
# Key: (query_normalized, tuple(history), provider)
# TTL: 5 minutos (classificações raramente mudam entre preflight e stream)
_classification_cache: TTLCache = TTLCache(maxsize=128, ttl=300)


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


SOURCE_JURISPRUDENCE_STOPWORDS = {
    "acordao",
    "acórdão",
    "acordaos",
    "acórdãos",
    "jurisprudencia",
    "jurisprudência",
    "relevante",
    "relevantes",
    "sistema",
    "directa",
    "direta",
    "ponto",
    "expressamente",
    "encontrou",
    "encontrei",
}


def _source_is_jurisprudence(chunk: RetrievedChunk) -> bool:
    meta = chunk.metadata or {}
    return meta.get("document_kind") == "jurisprudence"


def _jurisprudence_source_is_relevant(question: str, chunk: RetrievedChunk) -> bool:
    if not _source_is_jurisprudence(chunk):
        return True
    searchable = normalize_legal_text(
        f"{chunk.title or ''} {chunk.source or ''} {chunk.text or ''}"
    ).casefold()
    if (chunk.title or "").casefold().startswith("categoria:"):
        return False
    query = normalize_legal_text(question or "").casefold()
    tokens = [
        token
        for token in re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", query)
        if token not in SOURCE_JURISPRUDENCE_STOPWORDS
    ]
    legal_tokens = [
        token
        for token in tokens
        if token
        in {
            "burla",
            "despedimento",
            "filmagem",
            "filmar",
            "agente",
            "policial",
            "policia",
            "polícia",
            "desobediencia",
            "desobediência",
            "resistencia",
            "resistência",
            "resistiu",
            "violencia",
            "violência",
            "ameaca",
            "ameaça",
            "tribunal",
            "recurso",
            "arguido",
            "trabalhador",
            "empregador",
        }
    ]
    if not legal_tokens:
        return False
    return any(token in searchable for token in legal_tokens)


def _apply_deterministic_context_override(query: str, classification):
    """Prefer deterministic legal signals when the effective query is explicit.

    The LLM classifier can over-weight generic words like "direitos" and route a
    Pro case question to constitutional law, even when the case context clearly
    says "despedimento" and "Lei Geral do Trabalho". This keeps the override
    narrow: only patterns marked as force_* by the existing pre-classifier win.
    """
    try:
        from app.services.legal.pre_classifier import pre_classify

        pre = pre_classify(query)
    except Exception:
        return classification
    is_pro_case_context = "Contexto profissional do caso associado:" in query
    has_context_branch = bool(
        is_pro_case_context
        and pre.get("main_branch")
        and pre.get("main_branch") not in {"misto", "indeterminado"}
    )
    if not pre or not (
        pre.get("force_main_branch")
        or pre.get("force_topic_route")
        or has_context_branch
    ):
        return classification

    updates = {
        "semantic_confidence": max(float(classification.semantic_confidence or 0), 0.72),
        "needs_clarification": bool(pre.get("needs_clarification")),
        "clarifying_questions": pre.get("clarifying_questions", [])
        if pre.get("needs_clarification")
        else [],
        "explicit_branch_override": True,
    }
    if pre.get("request_type"):
        updates["request_type"] = pre["request_type"]
    if pre.get("specificity"):
        updates["specificity"] = pre["specificity"]
    if pre.get("audience"):
        updates["audience"] = pre["audience"]
    if (pre.get("force_main_branch") or has_context_branch) and pre.get("main_branch"):
        updates["main_branch"] = pre["main_branch"]
    if (pre.get("force_topic_route") or has_context_branch) and pre.get("topic_route"):
        updates["topic_route"] = pre["topic_route"]

    pre_main_branch = pre.get("main_branch")
    branch_candidates = [
        pre_main_branch if pre_main_branch not in {"misto", "indeterminado"} else None,
        *(pre.get("branch_candidates") or []),
    ]
    if not has_context_branch:
        branch_candidates.extend(classification.branch_candidates or [])
    updates["branch_candidates"] = _unique_preserve_order(branch_candidates)

    requested_diplomas = [*(pre.get("requested_diplomas") or [])]
    if not has_context_branch:
        requested_diplomas.extend(classification.requested_diplomas or [])
    elif not requested_diplomas:
        requested_diplomas.extend(classification.requested_diplomas or [])
    updates["requested_diplomas"] = _unique_preserve_order(requested_diplomas)

    requested_articles = [*(pre.get("requested_article_numbers") or [])]
    if not has_context_branch:
        requested_articles.extend(classification.requested_article_numbers or [])
    elif not requested_articles:
        requested_articles.extend(classification.requested_article_numbers or [])
    updates["requested_article_numbers"] = _unique_preserve_order(requested_articles)

    return classification.model_copy(update=updates)


def _extract_context_value(context: str, label: str) -> str:
    prefix = f"{label}:"
    for line in context.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _compact_pro_search_query(
    normalized_query: str,
    query_context: str | None,
    classification,
) -> str | None:
    if not query_context or "Contexto profissional do caso associado:" not in query_context:
        return None

    branch = _extract_context_value(query_context, "Área jurídica")
    summary = _extract_context_value(query_context, "Resumo interno")
    terms = _extract_context_value(
        query_context, "Termos jurídicos úteis para recuperação"
    )
    client = _extract_context_value(query_context, "Cliente")

    pieces = [normalized_query]
    if client and client != "Não definido":
        pieces.append(f"Cliente: {client}")
    if branch and branch != "Não definida":
        pieces.append(f"Área jurídica: {branch}")
    if summary and summary != "Sem resumo interno":
        pieces.append(f"Factos relevantes: {summary}")
    if terms:
        pieces.append(f"Termos jurídicos: {terms}")
    branch_lower = branch.casefold()
    if "laboral" in branch_lower:
        pieces.append("Diplomas prováveis: Lei Geral do Trabalho")
    elif "penal" in branch_lower:
        pieces.append("Diplomas prováveis: Código Penal; Código do Processo Penal")
    elif "civil" in branch_lower:
        pieces.append("Diplomas prováveis: Código Civil; Código de Processo Civil")
    elif classification.requested_diplomas:
        pieces.append(f"Diplomas prováveis: {', '.join(classification.requested_diplomas)}")

    return "\n".join(pieces)


def _pro_current_question_needs_direct_retrieval(
    normalized_query: str,
    query_context: str | None,
) -> bool:
    if not query_context or "Contexto profissional do caso associado:" not in query_context:
        return False
    query = normalize_legal_text(normalized_query or "").casefold()
    if not query:
        return False
    return any(
        marker in query
        for marker in (
            "desacato",
            "desobediência",
            "desobediencia",
            "resistência",
            "resistencia",
            "resistiu",
            "resistir",
            "violência",
            "violencia",
            "ameaça",
            "ameaca",
            "filmar",
            "filmagem",
            "fotograf",
            "gravação",
            "gravacao",
        )
    )


def _query_requests_user_document_only(query: str | None) -> bool:
    normalized = normalize_legal_text(query or "").casefold()
    if not normalized:
        return False
    mentions_document = any(
        marker in normalized
        for marker in (
            "documento carregado",
            "documento activo",
            "documento ativo",
            "pdf carregado",
            "pdf activo",
            "pdf ativo",
            "dossier carregado",
            "dossier",
            "anexo",
        )
    )
    if not mentions_document:
        return False
    exclusive_markers = (
        "apenas sobre o conteúdo",
        "apenas sobre o conteudo",
        "só sobre o conteúdo",
        "so sobre o conteudo",
        "sem usar fontes oficiais",
        "não sobre fontes oficiais",
        "nao sobre fontes oficiais",
        "não sobre fontes externas",
        "nao sobre fontes externas",
        "o documento traz",
        "o documento contém",
        "o documento contem",
        "o dossier traz",
        "o dossier contém",
        "o dossier contem",
        "o pdf traz",
        "o pdf contém",
        "o pdf contem",
    )
    if any(marker in normalized for marker in exclusive_markers):
        return True
    comparative_markers = (
        "compara",
        "cruza",
        "confronta",
        "de acordo com a lei",
        "face à lei",
        "face a lei",
        "em conformidade",
        "cumpre",
        "legalidade",
    )
    return not any(marker in normalized for marker in comparative_markers)


def _personalize_pro_answer(answer: str, query_context: str | None) -> str:
    if not answer or not query_context:
        return answer
    if "Contexto profissional do caso associado:" not in query_context:
        return answer
    lowered = answer.casefold()
    if any(
        marker in lowered
        for marker in (
            "informacao disponivel",
            "informação disponível",
            "nao foi possivel encontrar",
            "não foi possível encontrar",
        )
    ):
        return answer
    client = _extract_context_value(query_context, "Cliente")
    if not client or client == "Não definido" or client.casefold() in lowered:
        return answer
    summary = _extract_context_value(query_context, "Resumo interno")
    if summary and summary != "Sem resumo interno":
        return (
            f"Para {client}, considerando os factos registados no caso ({summary}), "
            "a orientação inicial é:\n\n"
            f"{answer}"
        )
    return f"Para {client}, considerando o caso associado, a orientação inicial é:\n\n{answer}"


def _pro_case_source_fallback(
    answer: str,
    query_context: str | None,
    sources: list[SourceItem],
) -> str:
    if answer and answer.strip():
        return answer
    if not query_context or "Contexto profissional do caso associado:" not in query_context or not sources:
        return answer

    client = _extract_context_value(query_context, "Cliente") or "o cliente"
    title = _extract_context_value(query_context, "Título") or "caso associado"
    branch = _extract_context_value(query_context, "Área jurídica") or "não definida"
    summary = _extract_context_value(query_context, "Resumo interno") or "sem resumo interno"
    source_lines = []
    for source in sources[:4]:
        reference = source.title or source.source or "Fonte recuperada"
        article = f" — Art. {source.article_number}" if source.article_number else ""
        page = f", p. {source.page}" if source.page else ""
        excerpt = normalize_legal_text((source.excerpt or source.attribution_text or "")[:360]).strip()
        source_lines.append(f"- **{reference}{article}{page}**: {excerpt or 'fonte recuperada sem excerto disponível.'}")

    return normalize_legal_text(
        f"""### Leitura profissional do caso
Para {client}, no caso **{title}**, a resposta deve ser tratada como matéria de **{branch}**, com base nos factos registados: {summary}

### Base recuperada
{chr(10).join(source_lines)}

### Próximos passos
- Confirmar os factos, datas, documentos e identidade das pessoas envolvidas.
- Separar o que é prova documental, prova testemunhal e enquadramento jurídico.
- Usar as fontes acima como ponto de partida e validar os requisitos específicos antes de qualquer acto processual.

### Nota
A geração principal não devolveu texto suficiente, por isso esta síntese foi montada apenas com o contexto profissional do caso e as fontes recuperadas."""
    ).strip()


def _source_items_from_evidence(evidences: list, limit: int = 5) -> list[SourceItem]:
    selected: list[SourceItem] = []
    seen: set[tuple[str, int | None, str]] = set()
    for evidence in evidences:
        chunk = evidence.chunk
        key = (chunk.title, chunk.page, chunk.article_number or "")
        if key in seen:
            continue
        seen.add(key)
        meta = chunk.metadata or {}
        selected.append(
            SourceItem(
                title=normalize_legal_text(chunk.title),
                source=normalize_legal_text(chunk.source),
                link_original=chunk.link_original,
                deep_link=(
                    f"{chunk.link_original}#page={chunk.page}"
                    if chunk.link_original and chunk.page and "#page=" not in chunk.link_original
                    else chunk.link_original
                ),
                page=chunk.page,
                article_number=normalize_legal_text(chunk.article_number) or None,
                law_status=chunk.law_status,
                excerpt=normalize_legal_text(chunk.text[:780]),
                attribution_text=normalize_legal_text(chunk.text[:300]) if chunk.text else None,
                source_scope=chunk.source_scope,
                source_kind=meta.get("document_kind"),
                document_id=chunk.document_id,
            )
        )
        if len(selected) >= limit:
            break
    return selected


def _is_legal_citation(content: str) -> bool:
    """Check if content looks like a legal citation (contains Art/artigo followed by number)."""
    stripped = content.strip()
    return bool(re.search(r"Art(?:igo|igos|\.)?\s*\d|[Aa]rtigo\s+\d", stripped))


def _looks_like_citation_ahead(text: str, max_dist: int = 80) -> bool:
    """Check if text within max_dist chars ahead looks like a citation start."""
    window = text[:max_dist] if len(text) > max_dist else text
    return bool(re.search(r"Art(?:igo|igos|\.)?\s*\d|[Aa]rtigo\s+\d", window))


def _find_close(text: str, start: int, pair: str) -> tuple[int, int] | None:
    """Find the closing bracket for a citation opened with pair ([[' or '((').

    Returns (close_idx, close_len) or None. Prefers balanced close (]] / ))),
    falls back to single close (] / )) if balanced not found nearby.
    """
    balanced_close = "]]" if pair == "[[" else "))"
    single_close = "]" if pair == "[[" else ")"

    # Try balanced close first — look within 220 chars (covers diploma names)
    search_end = min(len(text), start + 220)
    balanced_idx = text.find(balanced_close, start, search_end)
    if balanced_idx >= 0:
        content = text[start:balanced_idx]
        if _is_legal_citation(content):
            return (balanced_idx, 2)

    # Fall back to single close — look within 120 chars
    search_end = min(len(text), start + 120)
    single_idx = text.find(single_close, start, search_end)
    if single_idx >= 0:
        content = text[start:single_idx]
        if _is_legal_citation(content):
            return (single_idx, 1)

    return None


def _normalize_brackets(text: str) -> str:
    """Parse-based bracket normalizer.

    Handles both balanced [[...]]/((...)) and unbalanced [[...]/((...) patterns
    by preferring balanced close markers, falling back to single closes for
    unambiguous citation context.
    """
    if not text:
        return text
    text = text.replace("\x1f", "").replace("\x1e", "").replace("\x1d", "")
    text = text.replace("\x1c", "").replace("\x1b", "")

    result: list[str] = []
    i = 0
    n = len(text)

    while i < n:
        if i + 1 < n:
            pair = text[i] + text[i + 1]

            if pair in ("[[", "(("):
                content_start = i + 2

                if _looks_like_citation_ahead(text[content_start:]):
                    close_info = _find_close(text, content_start, pair)

                    if close_info is not None:
                        close_idx, close_len = close_info
                        content = text[content_start:close_idx]
                        content = _normalize_brackets(content)
                        result.append("[")
                        result.append(content)
                        result.append("]")
                        i = close_idx + close_len
                        continue

        result.append(text[i])
        i += 1

    return "".join(result)


from app.services.legal import (
    legal_classifier,
    legal_composer,
    legal_confidence_service,
    legal_retrieval_service,
    legal_validation_service,
)
from app.services.legal.article_numbers import extract_requested_article_numbers
from app.services.legal.article_verifier import article_verifier
from app.services.llm.deepseek_client import deepseek_client
from app.services.legal.models import (
    ClarificationPrompt,
    ConfidenceResult,
    RetrievalEvidence,
    ValidationIssue,
    ValidationResult,
    RetrievalResult,
)
from app.services.legal.reranker import llm_reranker
from app.services.llm.router import llm_router
from app.services.legal.evidence_verifier import evidence_verifier
from app.services.rag.query_expander import query_expander
from app.services.rag.vector_store import legislation_vector_store


def _needs_clarification_from_classification(
    classification,
    semantic_confidence: float,
    has_follow_up_context: bool = False,
) -> bool:
    """Uses the classifier's own output metrics to detect genuinely vague queries.

    No hardcoded regex — relies on:
    1. Semantic router confidence (proxy for how well the query matches any known legal branch)
    2. main_branch (classifier's best guess at legal area)
    3. topic_route (specificity of sub-topic)
    4. Presence of requested diplomas or article numbers
    5. Quality of the generated search_query

    This gate acts as a safety net if the LLM classifier fails to set needs_clarification
    despite the prompt instructions.
    """
    # Follow-up context should not be treated as vague by default.
    # Short follow-up prompts like "fale mais" often have low semantic confidence,
    # but they are still answerable when there is conversation history/state.
    if has_follow_up_context and (
        classification.is_follow_up or classification.specificity == "follow_up"
    ):
        return False

    branch_known = classification.main_branch != "indeterminado"
    has_legal_signal = bool(
        classification.requested_diplomas
        or classification.requested_article_numbers
        or classification.search_query
        or classification.specificity in {"factual", "validacao_base_legal", "comparacao_multi_ramo"}
        or classification.topic_route not in {"geral", "", None}
    )
    if branch_known and has_legal_signal:
        return False

    # Extreme low confidence: the query doesn't match any legal prototype.
    # Even if the LLM guessed a branch, it's unreliable — flag it.
    if semantic_confidence < 0.15:
        return True

    branch_unknown = classification.main_branch == "indeterminado"
    topic_generic = classification.topic_route == "geral"
    no_diplomas = not classification.requested_diplomas
    no_articles = not classification.requested_article_numbers

    if branch_unknown and topic_generic and no_diplomas and no_articles:
        if semantic_confidence < 0.3:
            return True
        if (
            not classification.search_query
            or len(classification.search_query.strip()) < 10
        ):
            return True

    # LLM guessed a branch but semantic confidence is still very low,
    # and the query has no structure (no diplomas, no articles, generic topic)
    # The LLM is guessing — don't trust it blindly
    if (
        semantic_confidence < 0.25
        and no_diplomas
        and no_articles
        and topic_generic
        and not classification.search_query
    ):
        return True

    return False


CLARIFYING_QUESTIONS_GENERAL = [
    "Qual e a area juridica principal do seu caso? (ex.: trabalho, familia, penal, fiscal)",
    "Qual e o facto concreto que aconteceu e o que pretende resolver?",
    "Se tiver, indique artigo, diploma ou entidade envolvida.",
]


def _clarification_prompt(question: str, options: list[str]) -> ClarificationPrompt:
    return ClarificationPrompt(
        question=question,
        options=list(dict.fromkeys(option.strip() for option in options if option.strip()))[:4],
    )


def _extract_case_subject(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    if not cleaned:
        return "o caso"
    return cleaned[:90].rstrip(" ,.;:!?") or "o caso"


def _contextual_clarifying_questions(
    query: str,
    classification,
) -> list[str]:
    normalized = normalize_legal_text(query).casefold()
    subject = _extract_case_subject(query)

    if any(term in normalized for term in ("policia", "polícia", "prendeu", "detido", "detencao", "detenção")):
        return [
            f"No caso sobre {subject}, a polícia explicou o motivo da detenção ou indicou algum crime?",
            "Foram levados para esquadra/tribunal, libertados no mesmo dia ou ainda estão detidos?",
            "Houve violência, apreensão de documentos/telemóveis ou participação numa manifestação/reunião política?",
        ]
    if any(term in normalized for term in ("despedido", "despedimento", "contrato", "salario", "salário", "trabalhador")):
        return [
            "O contrato era por tempo indeterminado, a termo ou não havia contrato escrito?",
            "Recebeu aviso prévio, carta de despedimento ou processo disciplinar?",
            "Pretende calcular direitos, impugnar o despedimento ou saber que documentos reunir?",
        ]
    if any(term in normalized for term in ("roubou", "furto", "furtou", "burla", "dinheiro", "kz", "kzs")):
        return [
            "O bem/dinheiro era exclusivamente seu, comum do casal/família ou foi entregue voluntariamente?",
            "Tem provas como transferência bancária, mensagens, testemunhas ou recibos?",
            "Pretende apresentar queixa-crime, recuperar o valor por via civil ou tentar acordo primeiro?",
        ]
    if any(term in normalized for term in ("terreno", "casa", "propriedade", "posse", "despejo")):
        return [
            "Tem documento de compra, declaração da administração/soba, registo predial ou apenas posse de facto?",
            "O conflito é sobre venda, ocupação, herança, despejo ou legalização?",
            "Pretende impedir a ocupação, recuperar o imóvel ou regularizar a propriedade?",
        ]
    if classification.main_branch != "indeterminado":
        branch_label = str(classification.main_branch).replace("_", " ")
        return [
            f"No ramo {branch_label}, qual foi exactamente o acto ou decisão que quer analisar?",
            "Em que data aconteceu e quem são as pessoas ou entidades envolvidas?",
            "Pretende orientação prática, artigos aplicáveis, minuta/queixa ou avaliação de riscos?",
        ]
    return CLARIFYING_QUESTIONS_GENERAL


def _contextual_clarification_prompts(
    query: str,
    classification,
) -> list[ClarificationPrompt]:
    normalized = normalize_legal_text(query).casefold()

    if any(term in normalized for term in ("policia", "polícia", "prendeu", "detido", "detencao", "detenção")):
        return [_clarification_prompt(
            "Qual é a situação atual depois da intervenção da polícia?",
            ["Fomos libertados", "Continuamos detidos", "Fomos levados à esquadra ou tribunal", "Não sei informar"],
        )]
    if any(term in normalized for term in ("despedido", "despedimento", "contrato", "salario", "salário", "trabalhador")):
        return [_clarification_prompt(
            "Que tipo de vínculo laboral existia?",
            ["Contrato por tempo indeterminado", "Contrato a termo", "Acordo verbal ou sem contrato escrito", "Não sei informar"],
        )]
    if any(term in normalized for term in ("roubou", "furto", "furtou", "burla", "dinheiro", "kz", "kzs")):
        return [_clarification_prompt(
            "Como esse dinheiro ou bem chegou à posse da outra pessoa?",
            ["Foi retirado sem autorização", "Entreguei como empréstimo", "Era bem ou dinheiro comum", "Não sei informar"],
        )]
    if any(term in normalized for term in ("terreno", "casa", "propriedade", "posse", "despejo")):
        return [_clarification_prompt(
            "Que documento ou prova possui sobre o imóvel?",
            ["Registo predial ou escritura", "Contrato ou declaração de compra", "Declaração administrativa ou do soba", "Apenas posse de facto"],
        )]
    if classification.main_branch != "indeterminado":
        return [_clarification_prompt(
            "Qual é o principal resultado que pretende obter?",
            ["Conhecer os meus direitos", "Saber os passos e prazos", "Avaliar riscos ou responsabilidade", "Identificar os artigos aplicáveis"],
        )]
    return [_clarification_prompt(
        "Qual é a área mais próxima do problema que pretende resolver?",
        ["Trabalho", "Família ou património", "Crime ou polícia", "Administração pública ou impostos"],
    )]

CLARIFYING_QUESTIONS_FOLLOW_UP = [
    "Pretende que eu aprofunde a resposta anterior, compare com outra norma ou transforme em passos praticos?",
    "Qual e o ponto exacto que quer detalhar agora (artigo, prazo, procedimento, prova ou risco)?",
    "Quer manter o mesmo diploma da resposta anterior ou mudar para outro?",
]

SHORT_FOLLOW_UP_MARKERS = (
    "fale mais",
    "mais detalhes",
    "detalha",
    "detalhe",
    "explique melhor",
    "continua",
    "continue",
    "aprofunda",
    "aprofundar",
    "e depois",
    "e agora",
    "nesse caso",
    "no meu caso",
)


def _looks_short_follow_up_prompt(query: str) -> bool:
    normalized = (query or "").strip().casefold()
    if not normalized:
        return False
    if any(marker in normalized for marker in SHORT_FOLLOW_UP_MARKERS):
        return True
    # Detect enumerated referential follow-ups: "crime 1", "item 3", "fale do 2", etc.
    _ENUM_REF_LOOKS = re.compile(
        r"(?:crime|item|ponto|n[úu]mero|al[íi]nea|inciso)\s+\d+"
        r"|fale\s+(?:sobre|do|da|d[oa]s?)\s+(?:o\s+)?\d+"
        r"|\b(?:o|a)\s+(?:primeiro|segundo|terceiro|quarto|quinto|último|ultimo)\b",
        re.IGNORECASE,
    )
    if _ENUM_REF_LOOKS.search(query) and len(normalized.split()) <= 10:
        return True
    return len(normalized.split()) <= 4 and normalized in {
        "sim",
        "e",
        "ok",
        "certo",
        "entendi",
        "pode continuar",
        "prossiga",
        "e o artigo",
    }


def _has_follow_up_context(
    history: list[str], classification, chat_state: dict | None
) -> bool:
    metadata = (chat_state or {}).get("metadata") or {}
    return bool(
        history
        or classification.is_follow_up
        or classification.specificity == "follow_up"
        or metadata.get("last_requested_article")
        or metadata.get("last_requested_diploma")
    )


def _default_clarifying_questions(
    query: str,
    classification,
    history: list[str],
    chat_state: dict | None,
) -> list[str]:
    if _has_follow_up_context(
        history, classification, chat_state
    ) and _looks_short_follow_up_prompt(query):
        return CLARIFYING_QUESTIONS_FOLLOW_UP
    return _contextual_clarifying_questions(query, classification)


def _default_clarification_prompts(
    query: str,
    classification,
    history: list[str],
    chat_state: dict | None,
) -> list[ClarificationPrompt]:
    if _has_follow_up_context(history, classification, chat_state) and _looks_short_follow_up_prompt(query):
        return [_clarification_prompt(
            "Que parte da resposta anterior pretende aprofundar?",
            ["Artigos aplicáveis", "Prazos e procedimento", "Provas necessárias", "Riscos e alternativas"],
        )]
    return _contextual_clarification_prompts(query, classification)


def _ensure_clarification_prompts(
    query: str,
    classification,
    history: list[str],
    chat_state: dict | None,
) -> None:
    prompts = list(getattr(classification, "clarification_prompts", []) or [])
    prompts = [prompt for prompt in prompts if prompt.question.strip()]
    if not prompts or not prompts[0].options:
        prompts = _default_clarification_prompts(query, classification, history, chat_state)
    classification.clarification_prompts = prompts[:1]
    classification.clarifying_questions = [prompt.question for prompt in prompts[:1]]


def _clarification_enriched_query(query: str, clarification_context: dict | None) -> str:
    if not clarification_context:
        return query
    original = str(clarification_context.get("original_question") or "").strip()
    prompt = str(clarification_context.get("question") or "").strip()
    answer = str(clarification_context.get("answer") or query).strip()
    if not original or not prompt or not answer:
        return query
    return (
        f"Pergunta original do utilizador: {original}\n"
        f"Esclarecimento solicitado: {prompt}\n"
        f"Resposta do utilizador ao esclarecimento: {answer}"
    )


def _clarification_repeats_previous(
    classification,
    clarification_context: dict | None,
) -> bool:
    if not clarification_context or not classification.clarification_prompts:
        return False
    previous = normalize_legal_text(
        str(clarification_context.get("question") or "")
    ).casefold().strip(" .?!")
    current = normalize_legal_text(
        classification.clarification_prompts[0].question
    ).casefold().strip(" .?!")
    return bool(previous and current and previous == current)


def _progressive_clarification_prompt(
    clarification_context: dict | None,
) -> ClarificationPrompt | None:
    if not clarification_context:
        return None
    original = normalize_legal_text(
        str(clarification_context.get("original_question") or "")
    ).casefold()
    previous = normalize_legal_text(
        str(clarification_context.get("question") or "")
    ).casefold()
    answer = normalize_legal_text(
        str(clarification_context.get("answer") or "")
    ).casefold()
    if len(original.split()) > 8 or not any(term in previous for term in ("area", "área")):
        return None
    if "trabalho" in answer:
        return _clarification_prompt(
            "O que aconteceu concretamente na relação de trabalho?",
            ["Fui despedido", "Não recebi salário ou outros valores", "Tive um acidente de trabalho", "É outra situação laboral"],
        )
    if any(term in answer for term in ("criminal", "crime", "policia", "polícia")):
        return _clarification_prompt(
            "Qual é a situação criminal ou policial que pretende analisar?",
            ["Fui detido ou chamado pela polícia", "Fui vítima de um crime", "Fui acusado de um crime", "É outra situação penal"],
        )
    if any(term in answer for term in ("familia", "família", "patrimonio", "património")):
        return _clarification_prompt(
            "Qual é o conflito familiar ou patrimonial principal?",
            ["Divórcio ou união", "Guarda ou alimentos de filhos", "Herança", "Bens, dívida ou propriedade"],
        )
    return _clarification_prompt(
        "O que aconteceu concretamente e o que pretende resolver?",
        ["Quero conhecer os meus direitos", "Quero recuperar um valor ou bem", "Quero contestar uma decisão", "Prefiro escrever os detalhes"],
    )


def _apply_progressive_clarification(
    classification,
    clarification_context: dict | None,
) -> None:
    prompt = _progressive_clarification_prompt(clarification_context)
    if not prompt:
        return
    classification.needs_clarification = True
    classification.clarification_prompts = [prompt]
    classification.clarifying_questions = [prompt.question]


def _should_refresh_clarifying_questions(questions: list[str] | None) -> bool:
    if not questions:
        return True
    joined = " ".join(questions).casefold()
    generic_markers = (
        "área do problema",
        "area do problema",
        "área juridica",
        "area juridica",
        "facto concreto",
        "artigo, diploma ou entidade",
    )
    return any(marker in joined for marker in generic_markers)


def _clarifying_message(query: str, history: list[str], classification) -> str:
    if history and _looks_short_follow_up_prompt(query):
        return (
            "Percebi que quer dar seguimento ao tema anterior. "
            "Para responder com rigor juridico e utilidade pratica, preciso de um detalhe adicional."
        )
        if classification.main_branch != "indeterminado":
            return "Tenho elementos iniciais, mas ainda falta um dado essencial para uma resposta juridica completa e bem fundamentada."
    return "Para lhe dar uma orientacao juridica precisa e fundamentada, preciso de 1-2 detalhes sobre o seu caso."


def _stabilize_follow_up_classification(
    query: str,
    history: list[str],
    classification,
    chat_state: dict | None,
):
    if (
        _has_follow_up_context(history, classification, chat_state)
        and _looks_short_follow_up_prompt(query)
        and not classification.is_follow_up
        and classification.specificity != "follow_up"
    ):
        return classification.model_copy(
            update={
                "is_follow_up": True,
                "specificity": "follow_up",
            }
        )
    return classification


# Separators that split multi-topic queries into sub-questions
# Matches: "E", "e", "e tambem", "alem disso", "bem como", "ou", "vs", "versus", "??"
MULTI_TOPIC_SEPARATORS = re.compile(
    r"(?:\s+[Ee]\s+(?:tamb[ée]m\s+)?)"
    r"|(?:\s+(?:al[ée]m\s+disso|bem\s+como|ou|vs\.?|versus)\s+)"
    r"|(?:\n{2,})"
    r"|(?:\?\s+(?:[A-Za-zÀ-ÿ]))",
)


def _detect_multi_topic(query: str) -> bool:
    """Detect queries with multiple distinct legal topics.

    Uses separator-based splitting with validation:
    - Both parts must have >= 3 words (substantial enough to be a topic)
    - Need at least 2 substantial sub-questions
    """
    parts = MULTI_TOPIC_SEPARATORS.split(query)
    if len(parts) < 2:
        return False

    substantial = 0
    for part in parts:
        words = part.strip().split()
        if len(words) >= 2 and any(len(w) > 2 for w in words):
            substantial += 1

    return substantial >= 2


REQUESTED_DIPLOMA_SLUGS = {
    "Lei Geral do Trabalho": "lei-geral-do-trabalho-lei-12-23",
    "Código Penal": "codigo-penal-lei-38-20",
    "Codigo Penal": "codigo-penal-lei-38-20",
    "Código Civil": "codigo-civil",
    "Codigo Civil": "codigo-civil",
    "Constituição da República de Angola": "constituicao-republica-angola-2022",
    "Constituicao da Republica de Angola": "constituicao-republica-angola-2022",
    "Código do Processo Penal": "codigo-processo-penal-lei-39-20",
    "Codigo do Processo Penal": "codigo-processo-penal-lei-39-20",
    "Código de Processo Penal": "codigo-processo-penal-lei-39-20",
    "Codigo de Processo Penal": "codigo-processo-penal-lei-39-20",
    "Código de Processo do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Codigo de Processo do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Lei do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Lei n.º 2/94": "lei-n-o-2-94-de-14-de-janeiro",
    "Lei do Bilhete de Identidade": "lei-bilhete-identidade-4-09",
    "Lei das Sociedades Comerciais": "lei-sociedades-comerciais-1-04",
    "Código Geral Tributário": "codigo-geral-tributario-21-14",
    "Codigo Geral Tributario": "codigo-geral-tributario-21-14",
    "Código de Família": "codigo-familia-lei-1-88",
    "Codigo de Familia": "codigo-familia-lei-1-88",
    "Lei de Terras": "lei-terras-9-04",
}

DIPLOMA_TITLE_HINTS = {
    "lei geral do trabalho": "lei-geral-do-trabalho-lei-12-23",
    "codigo penal": "codigo-penal-lei-38-20",
    "código penal": "codigo-penal-lei-38-20",
    "codigo civil": "codigo-civil",
    "código civil": "codigo-civil",
    "constituicao da republica de angola": "constituicao-republica-angola-2022",
    "constituição da república de angola": "constituicao-republica-angola-2022",
    "codigo do processo penal": "codigo-processo-penal-lei-39-20",
    "código do processo penal": "codigo-processo-penal-lei-39-20",
    "codigo de processo penal": "codigo-processo-penal-lei-39-20",
    "código de processo penal": "codigo-processo-penal-lei-39-20",
    "codigo de processo do contencioso administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "código de processo do contencioso administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "lei do contencioso administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "lei n.º 2/94": "lei-n-o-2-94-de-14-de-janeiro",
    "lei n.o 2/94": "lei-n-o-2-94-de-14-de-janeiro",
    "lei 2/94": "lei-n-o-2-94-de-14-de-janeiro",
    "lei do bilhete de identidade": "lei-bilhete-identidade-4-09",
    "lei das sociedades comerciais": "lei-sociedades-comerciais-1-04",
    "codigo geral tributario": "codigo-geral-tributario-21-14",
    "código geral tributário": "codigo-geral-tributario-21-14",
    "codigo de familia": "codigo-familia-lei-1-88",
    "código de família": "codigo-familia-lei-1-88",
    "lei de terras": "lei-terras-9-04",
}

SLUG_TO_DIPLOMA_NAME = {
    "lei-geral-do-trabalho-lei-12-23": "Lei Geral do Trabalho",
    "codigo-penal-lei-38-20": "Código Penal",
    "codigo-civil": "Código Civil",
    "constituicao-republica-angola-2022": "Constituição da República de Angola",
    "codigo-processo-penal-lei-39-20": "Código do Processo Penal",
    "codigo-processo-contencioso-administrativo-33-22": "Lei do Contencioso Administrativo",
    "lei-bilhete-identidade-4-09": "Lei do Bilhete de Identidade",
    "lei-sociedades-comerciais-1-04": "Lei das Sociedades Comerciais",
    "codigo-geral-tributario-21-14": "Código Geral Tributário",
    "codigo-familia-lei-1-88": "Código de Família",
    "lei-terras-9-04": "Lei de Terras",
}


def _requested_diploma_slugs_from_names(diplomas: list[str] | tuple[str, ...]) -> set[str]:
    slugs: set[str] = set()
    for diploma in diplomas or []:
        if not str(diploma).strip():
            continue
        exact = REQUESTED_DIPLOMA_SLUGS.get(str(diploma))
        if exact:
            slugs.add(exact)
            continue
        normalized = normalize_legal_text(str(diploma)).casefold()
        mapped = DIPLOMA_TITLE_HINTS.get(normalized)
        if mapped:
            slugs.add(mapped)
            continue
        if "processo penal" in normalized and (
            "codigo" in normalized or "código" in normalized or normalized in {"cpp", "ccp"}
        ):
            slugs.add("codigo-processo-penal-lei-39-20")
        elif "codigo penal" in normalized or "código penal" in normalized:
            slugs.add("codigo-penal-lei-38-20")
    return slugs


def _chunk_has_requested_slug(chunk, requested_slugs: set[str]) -> bool:
    if not requested_slugs:
        return True
    metadata = chunk.metadata or {}
    slug = metadata.get("diploma_slug")
    return bool(slug and slug in requested_slugs)
FOLLOW_UP_REFERENCE_MARKERS = (
    "esse mesmo artigo",
    "esse artigo",
    "o mesmo artigo",
    "mesmo artigo",
    "essa mesma norma",
    "essa norma",
    "esse diploma",
    "o mesmo diploma",
    "esse mesmo diploma",
    "mesmo código",
    "mesmo codigo",
    "código anterior",
    "codigo anterior",
)
FOLLOW_UP_DIPLOMA_MARKERS = (
    "esse diploma",
    "o mesmo diploma",
    "mesmo código",
    "mesmo codigo",
    "código anterior",
    "codigo anterior",
    "essa lei",
    "essa constituicao",
    "essa constituição",
)

class RAGPipeline:
    @staticmethod
    def _coerce_ai_preferences(prefs: dict | str | None) -> dict:
        if isinstance(prefs, dict):
            return prefs
        if isinstance(prefs, str):
            try:
                parsed = json.loads(prefs)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _effective_audience(default_audience: str, prefs: dict | str | None) -> str:
        coerced = RAGPipeline._coerce_ai_preferences(prefs)
        audience = coerced.get("audience")
        if audience in {"leigo", "tecnico"}:
            return audience
        if coerced.get("tone") == "simples":
            return "leigo"
        if coerced.get("tone") == "didatico":
            return "didatico"
        return default_audience

    @staticmethod
    def _max_tokens_for_preferences(audience: str, prefs: dict | str | None) -> int:
        coerced = RAGPipeline._coerce_ai_preferences(prefs)
        detail_level = coerced.get("detail_level", "normal")
        if detail_level == "breve":
            return 380
        if detail_level == "detalhado":
            return 520
        if audience == "leigo":
            return 300
        if audience == "tecnico":
            return 420
        if audience == "didatico":
            return 360
        return 340

    @staticmethod
    def _query_requests_detailed_answer(query: str) -> bool:
        return bool(
            re.search(
                r"\b(detalh|aprofund|fundament|parecer|analise tecnica|análise técnica|requisitos|condi[cç][oõ]es|validade|v[aá]lido|direitos|il[ií]cito|l[ií]cito|artigos aplic[aá]veis|requisitos completos|minuta|estrategia|estratégia|diferen[cç]a|distin[cç][aã]o|reclama[cç][aã]o|recurso hier[aá]rquico|impugna[cç][aã]o contenciosa|sob quais ramos|quais ramos|multi[- ]?disciplinar|protec[cç][aã]o de dados|prote[cç][aã]o de dados|pondera[cç][aã]o|direitos em conflito)\b",
                (query or "").casefold(),
            )
        )

    @staticmethod
    def _with_jurisprudence_priority(
        evidences: list, limit: int, branch: str | None = None
    ) -> list:
        ordered = sorted(evidences, key=lambda e: e.score, reverse=True)
        jurisprudence = [
            evidence
            for evidence in ordered
            if getattr(evidence, "retrieval_reason", "") == "jurisprudence"
            or (
                (evidence.chunk.metadata or {}).get("document_kind") == "jurisprudence"
                and branch
                and (evidence.chunk.metadata or {}).get("legal_branch") == branch
            )
        ][:2]
        if not jurisprudence:
            return ordered[:limit]

        selected: list = []
        seen: set[tuple] = set()
        for evidence in jurisprudence + ordered:
            chunk = evidence.chunk
            key = (
                chunk.document_id,
                chunk.title,
                chunk.page,
                chunk.article_number,
                (chunk.metadata or {}).get("document_kind"),
            )
            if key in seen:
                continue
            seen.add(key)
            selected.append(evidence)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _normalize_text(text: str) -> str:
        return (text or "").strip().casefold()

    @staticmethod
    def _recent_user_questions(history: list[str]) -> list[str]:
        return [
            item.split(":", 1)[1].strip()
            for item in history
            if item.lower().startswith("utilizador:") and ":" in item
        ]

    @staticmethod
    def _extract_articles_from_text(text: str) -> list[str]:
        return extract_requested_article_numbers(text or "")

    @staticmethod
    def _history_anchor_diploma(history: list[str]) -> str | None:
        user_questions = RAGPipeline._recent_user_questions(history)
        for item in reversed(user_questions):
            normalized = RAGPipeline._normalize_text(item)
            if re.search(
                r"\b(cpp|ccp)\b|c[oó]digo\s+(do|de)?\s*processo\s+penal",
                normalized,
            ):
                return "Código do Processo Penal"
            for diploma_name in REQUESTED_DIPLOMA_SLUGS:
                if diploma_name.casefold() in normalized:
                    return diploma_name
        return None

    @staticmethod
    def _history_anchor_article(history: list[str]) -> str | None:
        user_questions = RAGPipeline._recent_user_questions(history)
        for item in reversed(user_questions):
            matches = RAGPipeline._extract_articles_from_text(item)
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _looks_referential_follow_up(query: str, classification) -> bool:
        normalized = RAGPipeline._normalize_text(query)
        if any(marker in normalized for marker in FOLLOW_UP_REFERENCE_MARKERS):
            return True
        if (
            classification.specificity != "follow_up"
            and not classification.is_follow_up
        ):
            return False
        return any(marker in normalized for marker in FOLLOW_UP_REFERENCE_MARKERS)

    @staticmethod
    def _hydrate_follow_up_context(
        query: str,
        history: list[str],
        classification,
        chat_state: dict | None,
    ):
        if not RAGPipeline._looks_referential_follow_up(query, classification):
            return classification

        metadata = (chat_state or {}).get("metadata") or {}
        anchor_article = (
            (chat_state or {}).get("active_article")
            or metadata.get("unresolved_requested_article")
            or metadata.get("last_requested_article")
            or RAGPipeline._history_anchor_article(history)
        )
        anchor_slug = (
            (chat_state or {}).get("diploma_slug")
            or metadata.get("active_diploma_slug")
            or metadata.get("last_requested_diploma_slug")
        )
        anchor_diploma = (
            SLUG_TO_DIPLOMA_NAME.get(anchor_slug or "")
            or metadata.get("last_requested_diploma")
            or RAGPipeline._history_anchor_diploma(history)
        )

        normalized_query = RAGPipeline._normalize_text(query)
        same_diploma_reference = any(
            marker in normalized_query for marker in FOLLOW_UP_DIPLOMA_MARKERS
        )
        requested_articles = list(classification.requested_article_numbers)
        if not requested_articles and anchor_article and anchor_article not in requested_articles:
            requested_articles.append(anchor_article)

        requested_diplomas = list(classification.requested_diplomas)
        if anchor_diploma and same_diploma_reference:
            requested_diplomas = [anchor_diploma]
        elif anchor_diploma and anchor_diploma not in requested_diplomas:
            requested_diplomas.append(anchor_diploma)

        requires_strict = classification.requires_strict_corpus_match
        if requested_articles or any(
            marker in RAGPipeline._normalize_text(query)
            for marker in FOLLOW_UP_DIPLOMA_MARKERS
        ):
            requires_strict = True

        updates = {
                "requested_article_numbers": requested_articles,
                "requested_diplomas": requested_diplomas,
                "needs_article_validation": bool(requested_articles)
                or classification.needs_article_validation,
                "requires_strict_corpus_match": requires_strict,
        }
        if anchor_diploma == "Código do Processo Penal":
            updates.update(
                {
                    "main_branch": "penal",
                    "topic_route": "cpp",
                    "branch_candidates": ["penal"],
                    "requested_diplomas": ["Código do Processo Penal"],
                }
            )
        elif anchor_diploma == "Código Penal":
            updates.update(
                {
                    "main_branch": "penal",
                    "topic_route": "penal_substantivo",
                    "branch_candidates": ["penal"],
                    "requested_diplomas": ["Código Penal"],
                }
            )

        return classification.model_copy(update=updates)

    @staticmethod
    async def _classify_query(query: str, history: list[str], provider: str | None):
        cache_key = (query.strip().casefold(), tuple(history or ()), provider)
        cached = _classification_cache.get(cache_key)
        if cached is not None:
            logger.debug("Classification cache HIT for: %s", query[:60])
            return cached
        try:
            result = await legal_classifier.classify(query, history, provider=provider)
        except TypeError:
            result = await legal_classifier.classify(query, history)
        _classification_cache[cache_key] = result
        return result

    @staticmethod
    def _derive_search_query(query: str, history: list[str], classification) -> str:
        normalized_query = (query or "").strip()
        query_lower = normalized_query.casefold()

        # Transformation follow-ups ("resuma", "fale mais") —
        # anchor the retrieval on the last substantive user question, not the command itself.
        if classification.is_transformation and history:
            for item in reversed(history):
                if item.startswith("Utilizador:") and not any(
                    m in item.casefold()
                    for m in (
                        "resum",
                        "fale mais",
                        "simplif",
                        "percebi",
                        "entendi",
                        "compreendi",
                        "continue",
                        "explique",
                        "detalh",
                        "aprofund",
                        "traduz",
                    )
                ):
                    return item.replace("Utilizador:", "").strip()
            return normalized_query

        # Detect enumerated referential follow-ups:
        # "crime 1", "item 3", "ponto 2", "número 4", "fale do 1", "o primeiro", etc.
        _ENUM_REF = re.compile(
            r"(?:crime|item|ponto|n[úu]mero|al[íi]nea|inciso)\s+\d+"
            r"|fale\s+(?:sobre|do|da|d[oa]s?)\s+(?:o\s+)?(?:\d+|primeiro|segundo|terceiro|quarto|quinto|último|ultimo)"
            r"|\b(?:o|a)\s+(?:primeiro|segundo|terceiro|quarto|quinto|último|ultimo)\b",
            re.IGNORECASE,
        )
        if _ENUM_REF.search(normalized_query) and history:
            # Anchor retrieval on the last user question for broader context
            for item in reversed(history):
                if item.startswith("Utilizador:"):
                    return item.replace("Utilizador:", "").strip()
            return normalized_query

        article_matches = extract_requested_article_numbers(normalized_query)
        if not article_matches and classification.requested_article_numbers:
            article_matches = list(classification.requested_article_numbers)

        if article_matches and classification.requested_diplomas:
            return f"artigo {article_matches[0]} {classification.requested_diplomas[0]}"

        if article_matches:
            return f"artigo {article_matches[0]}"

        short_follow_up = len(normalized_query.split()) < 5 and history
        if not short_follow_up:
            return normalized_query

        if re.fullmatch(r"[Ee]?\s*o?\s*\d+[?!.]?", normalized_query):
            number_match = re.search(r"\d+[.]?\d*", normalized_query)
            if number_match:
                article_number = number_match.group(0).replace(".", "")
                diploma = (
                    classification.requested_diplomas[0]
                    if classification.requested_diplomas
                    else ""
                )
                base_query = f"artigo {article_number}"
                return f"{base_query} {diploma}".strip()

        for item in reversed(history):
            if item.startswith("Utilizador:"):
                return item.replace("Utilizador:", "").strip()
        return normalized_query

    @staticmethod
    def _basis_slug_from_retrieval(item, retrieval) -> str:
        for evidence in retrieval.official_evidence + retrieval.user_evidence:
            chunk = evidence.chunk
            if (
                chunk.title == item.diploma
                and chunk.page == item.page
                and chunk.source_scope == item.source_scope
            ):
                metadata = chunk.metadata or {}
                slug = metadata.get("diploma_slug")
                if slug:
                    return slug

        diploma_key = (
            re.sub(r"\s*\(.*?\)\s*$", "", (item.diploma or "")).strip().lower()
        )
        return DIPLOMA_TITLE_HINTS.get(diploma_key, "")

    async def answer_query(
        self,
        query: str,
        provider: str | None = None,
        conversation_history: list[str] | None = None,
        chat_id: str | None = None,
        active_document_id: str | None = None,
        user_id: str | int | None = None,
        query_context: str | None = None,
        clarification_context: dict | None = None,
    ) -> ChatResponse:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise ValueError("A pergunta não pode estar vazia.")
        analysis_query = _clarification_enriched_query(
            normalized_query, clarification_context
        )
        effective_query = (
            f"{analysis_query}\n\n{query_context.strip()}"
            if query_context and query_context.strip()
            else analysis_query
        )

        current_chat_id = chat_id
        history = conversation_history or []
        provider_used = provider or get_settings().default_llm_provider
        chat_state = (
            postgres_manager.get_conversation_state(current_chat_id, user_id=user_id)
            if current_chat_id
            else None
        )
        _conv_diploma_slug = (
            (chat_state or {}).get("diploma_slug")
            or ((chat_state or {}).get("metadata") or {}).get("last_requested_diploma_slug")
            or ((chat_state or {}).get("metadata") or {}).get("active_diploma_slug")
        )
        _conv_diploma_names = []
        _prev_diploma = ((chat_state or {}).get("metadata") or {}).get("last_requested_diploma")
        if _prev_diploma:
            _conv_diploma_names.append(str(_prev_diploma))

        classification = await self._classify_query(effective_query, history, provider)
        classification = self._hydrate_follow_up_context(
            effective_query, history, classification, chat_state
        )
        classification = _stabilize_follow_up_classification(
            effective_query, history, classification, chat_state
        )
        classification = _apply_deterministic_context_override(
            effective_query, classification
        )
        _apply_progressive_clarification(classification, clarification_context)
        # Augment conversation context with classification's diploma detection
        if classification.requested_diplomas:
            _conv_diploma_names.extend(classification.requested_diplomas)
            from app.services.legal.retrieval import _requested_diploma_slugs
            _req_slugs = _requested_diploma_slugs(classification)
            if _req_slugs:
                _conv_diploma_slug = _conv_diploma_slug or next(iter(_req_slugs))

        # Detect very vague or off-topic queries using classifier's own output metrics
        if active_document_id:
            classification.needs_clarification = False
            classification.clarifying_questions = []
            classification.clarification_prompts = []
        elif (
            not classification.needs_clarification
            and not classification.clarifying_questions
        ):
            if _needs_clarification_from_classification(
                classification,
                classification.semantic_confidence,
                has_follow_up_context=_has_follow_up_context(
                    history, classification, chat_state
                ),
            ):
                classification.needs_clarification = True
                classification.clarifying_questions = _default_clarifying_questions(
                    normalized_query, classification, history, chat_state
                )
        elif classification.needs_clarification and _should_refresh_clarifying_questions(
            classification.clarifying_questions
        ):
            classification.clarifying_questions = _default_clarifying_questions(
                normalized_query, classification, history, chat_state
            )

        if classification.needs_clarification:
            _ensure_clarification_prompts(
                normalized_query, classification, history, chat_state
            )
            if _clarification_repeats_previous(classification, clarification_context):
                classification.needs_clarification = False
                classification.clarifying_questions = []
                classification.clarification_prompts = []

        # Multi-topic detection: force multi-branch when query has "E" separating topics
        if (
            not classification.needs_multi_branch_handling
            and classification.main_branch != "misto"
            and _detect_multi_topic(effective_query)
        ):
            classification.needs_multi_branch_handling = True
            # Expand branch candidates: add pre-classifier + keyword detection
            from app.services.legal.pre_classifier import pre_classify

            pre = pre_classify(effective_query)
            pre_branch = pre.get("main_branch") if pre else None
            if pre_branch and pre_branch not in classification.branch_candidates:
                classification.branch_candidates = list(
                    classification.branch_candidates
                ) + [pre_branch]

            # Keyword-based branch detection for branches not covered by pre-classifier
            branch_kw = {
                "comercial": r"s[oó]cio|sociedad|quotas?|societ[aá]rio|accionista|delibera[cç][aã]o",
                "civil": r"contrato|obriga[cç][aã]o|responsabilidade civil|indemniza[cç][aã]o|arrendamento",
                "penal": r"crime|pena|pris[aã]o|furto|roubo|homic[ií]dio|burla",
                "tributario": r"imposto|IVA|IRC|reten[cç][aã]o na fonte",
                "familia": r"div[oó]rcio|casamento|filhos?|alimentos|paternidade|ado[cç][aã]o",
                "constitucional": r"constitui[cç][aã]o|direitos fundamentais|liberdade",
                "administrativo": r"funcion[aá]rio|acto administrativo|concurso p[uú]blico|licen[cç]a",
            }
            q_lower = effective_query.lower()
            for branch, pat in branch_kw.items():
                if branch in classification.branch_candidates:
                    continue
                if re.search(pat, q_lower):
                    classification.branch_candidates = list(
                        classification.branch_candidates
                    ) + [branch]
                    break

            # Inject secondary branch diplomas into requested_diplomas
            from app.services.legal.retrieval import BRANCH_DIPLOMAS as _BD

            for branch in classification.branch_candidates:
                if branch == classification.main_branch:
                    continue
                branch_diplomas = _BD.get(branch, tuple())
                for diploma_name in branch_diplomas:
                    if diploma_name not in classification.requested_diplomas:
                        classification.requested_diplomas = list(
                            classification.requested_diplomas
                        ) + [diploma_name]
                        break
                break

        # Follow-up questions with short queries — use latest user question for retrieval
        search_query = self._derive_search_query(
            effective_query, history, classification
        )
        search_query = (
            _compact_pro_search_query(normalized_query, query_context, classification)
            or search_query
        )
        classification.query_text = search_query
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and classification.is_transformation
        ):
            classification = classification.model_copy(
                update={"is_transformation": False, "transformation_type": None}
            )

        if classification.needs_clarification and classification.clarifying_questions:
            clarifying_answer = _clarifying_message(
                normalized_query, history, classification
            )
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=normalized_query,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            clarification_request = (
                classification.clarification_prompts[0].model_dump()
                if classification.clarification_prompts
                else None
            )
            if clarification_request is not None:
                clarification_request["original_question"] = (
                    str((clarification_context or {}).get("original_question") or "").strip()
                    or normalized_query
                )
            stored_answer = clarifying_answer
            if clarification_request and clarification_request.get("question"):
                stored_answer = f"{clarifying_answer}\n\nAntes de continuar: {clarification_request['question']}"
            postgres_manager.append_chat_exchange(
                chat_id=current_chat_id,
                question=normalized_query,
                answer=stored_answer,
                provider_used=provider_used,
                sources=[],
                active_document_id=active_document_id,
                assistant_metadata={
                    "answer_mode": "clarifying",
                    "clarifying_questions": classification.clarifying_questions,
                    "clarification_request": clarification_request,
                },
            )
            postgres_manager.save_query(question=normalized_query, answer=stored_answer)
            return ChatResponse(
                answer=clarifying_answer,
                sources=[],
                provider_used=provider_used,
                chat_id=current_chat_id,
                active_document_id=active_document_id,
                answer_mode="clarifying",
                classification=classification.model_dump(),
                clarifying_questions=classification.clarifying_questions,
                clarification_request=clarification_request,
            )

        if (
            classification.requires_strict_corpus_match
            and classification.requested_diplomas
        ):
            available_slugs = legislation_vector_store.available_diploma_slugs()
            requested_slugs = _requested_diploma_slugs_from_names(
                classification.requested_diplomas
            )
            if requested_slugs and not requested_slugs.intersection(available_slugs):
                answer = (
                    "O tema pedido ainda não está coberto de forma suficiente no corpus jurídico público actualmente indexado. "
                    "A resposta segura para esta rota exige que o diploma prioritário esteja carregado e validado localmente."
                )
                if not current_chat_id:
                    current_chat_id = postgres_manager.create_chat(
                        title=normalized_query,
                        active_document_id=active_document_id,
                        user_id=user_id,
                    )
                postgres_manager.append_chat_exchange(
                    chat_id=current_chat_id,
                    question=normalized_query,
                    answer=answer,
                    provider_used=provider_used,
                    sources=[],
                    active_document_id=active_document_id,
                )
                postgres_manager.save_query(question=normalized_query, answer=answer)
                return ChatResponse(
                    answer=answer,
                    sources=[],
                    provider_used=provider_used,
                    chat_id=current_chat_id,
                    active_document_id=active_document_id,
                    answer_mode="refused",
                    confidence={
                        "level": "baixa",
                        "score": 0.0,
                        "reasons": [
                            "O diploma prioritário ainda não está disponível no corpus indexado."
                        ],
                    },
                    classification=classification.model_dump(),
                    legal_basis=[],
                    validation_issues=[
                        {
                            "code": "corpus_gap",
                            "message": "O diploma prioritário desta rota ainda não está disponível no corpus indexado.",
                            "severity": "high",
                        }
                    ],
                )
        # Se for uma transformação (ex: "diz em termos simples"), podemos reutilizar o contexto do histórico
        # ou apenas permitir que o LLM processe a resposta anterior sem exigir novos documentos.
        if active_document_id and classification.is_transformation:
            classification = classification.model_copy(
                update={"is_transformation": False}
            )
        if classification.is_transformation and not history:
            classification = classification.model_copy(
                update={"is_transformation": False}
            )
        is_transformation = classification.is_transformation

        if is_transformation:
            retrieval = RetrievalResult(
                classification=classification,
                official_evidence=[],
                user_evidence=[],
                missing_branches=[],
            )
        else:
            expanded_queries = query_expander.expand(search_query, classification)
            results = await asyncio.gather(
                *[
                    legal_retrieval_service.retrieve(
                        qv,
                        classification,
                        conversation_history=history,
                        active_document_id=active_document_id,
                        user_id=user_id,
                        conversation_diploma_slug=_conv_diploma_slug,
                        conversation_diploma_names=_conv_diploma_names if _conv_diploma_names else None,
                    )
                    for qv in expanded_queries[:2]
                ]
            )
            all_evidences: list = []
            all_user_evidences: list = []
            for partial in results:
                all_evidences.extend(partial.official_evidence)
                all_user_evidences.extend(partial.user_evidence)
            if _pro_current_question_needs_direct_retrieval(
                normalized_query, query_context
            ):
                direct_current = await legal_retrieval_service.retrieve(
                    normalized_query,
                    classification,
                    conversation_history=history,
                    active_document_id=active_document_id,
                    user_id=user_id,
                    conversation_diploma_slug=_conv_diploma_slug,
                    conversation_diploma_names=_conv_diploma_names if _conv_diploma_names else None,
                )
                all_evidences.extend(direct_current.official_evidence)
                all_user_evidences.extend(direct_current.user_evidence)

            if all_evidences:
                all_evidences = self._with_jurisprudence_priority(
                    all_evidences, MAX_OFFICIAL_EVIDENCES, classification.main_branch
                )
            combined_evidences = self._with_jurisprudence_priority(
                all_evidences + all_user_evidences,
                MAX_COMBINED_EVIDENCES,
                classification.main_branch,
            )
            if combined_evidences:
                retrieval = RetrievalResult(
                    classification=classification,
                    official_evidence=all_evidences,
                    user_evidence=all_user_evidences,
                    missing_branches=[],
                    retrieved_chunks=[e.chunk for e in combined_evidences],
                )
            else:
                retrieval = await legal_retrieval_service.retrieve(
                    classification.search_query or normalized_query,
                    classification,
                    conversation_history=history,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )

            retrieval = self._filter_retrieval_by_branch(retrieval, classification)
            retrieval = self._force_requested_article_evidence(retrieval, classification)

            has_verified_direct_evidence = any(
                getattr(ev, "retrieval_reason", "")
                in {
                    "requested_article_direct",
                    "dynamic_cross_reference",
                    "legal_concept_rescue",
                }
                for ev in retrieval.official_evidence
            )
            if len(retrieval.retrieved_chunks) > 10 and not has_verified_direct_evidence:
                chunk_texts = [chunk.text or "" for chunk in retrieval.retrieved_chunks]
                relevance = await llm_reranker.rerank(
                    normalized_query,
                    chunk_texts,
                    provider=provider,
                )
                min_len = min(len(retrieval.official_evidence), len(relevance))
                retrieval.official_evidence = [
                    retrieval.official_evidence[i]
                    for i in range(min_len)
                    if relevance[i]
                ]
                if not retrieval.official_evidence and min_len > 0:
                    retrieval.official_evidence = retrieval.official_evidence[:5]
                chunks = []
                seen = set()
                for ev in retrieval.official_evidence + retrieval.user_evidence:
                    cid = id(ev.chunk)
                    if cid not in seen:
                        seen.add(cid)
                        chunks.append(ev.chunk)
                retrieval.retrieved_chunks = chunks

        if not retrieval.retrieved_chunks and not is_transformation:
            answer = (
                "Não encontrei contexto jurídico suficiente no índice actual para responder com segurança. "
                "Reformule a pergunta com mais detalhe, indique o diploma pretendido ou peça o artigo exacto a confirmar."
            )
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=normalized_query,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            postgres_manager.append_chat_exchange(
                chat_id=current_chat_id,
                question=normalized_query,
                answer=answer,
                provider_used=provider_used,
                sources=[],
                active_document_id=active_document_id,
            )
            postgres_manager.save_query(question=normalized_query, answer=answer)
            return ChatResponse(
                answer=answer,
                sources=[],
                provider_used=provider_used,
                chat_id=current_chat_id,
                active_document_id=active_document_id,
                answer_mode="refused",
                confidence={
                    "level": "baixa",
                    "score": 0.0,
                    "reasons": [
                        "Sem contexto jurídico recuperado suficiente para uma resposta verificável."
                    ],
                },
                classification=classification.model_dump(),
                legal_basis=[],
                validation_issues=[
                    {
                        "code": "no_context",
                        "message": "Sem contexto jurídico suficiente no índice actual.",
                        "severity": "high",
                    }
                ],
            )

        ai_preferences = (
            self._coerce_ai_preferences(postgres_manager.get_user_preferences(str(user_id)))
            if user_id
            else {}
        )
        effective_audience = self._effective_audience(
            classification.audience, ai_preferences
        )
        response_max_tokens = self._max_tokens_for_preferences(
            effective_audience, ai_preferences
        )
        professional_context = bool(
            query_context and "Contexto profissional do caso associado:" in query_context
        )
        if self._query_requests_detailed_answer(normalized_query):
            response_max_tokens = max(response_max_tokens, 920)
        if professional_context:
            response_max_tokens = max(response_max_tokens, 980)
        prompt = legal_composer.build_prompt(
            effective_query,
            classification,
            retrieval.retrieved_chunks,
            conversation_history=history,
            ai_preferences=ai_preferences,
            professional_context=professional_context,
        )
        _tllm = _time.time()
        try:
            raw_answer, provider_used = await llm_router.generate(
                prompt,
                provider=provider,
                max_tokens=response_max_tokens,
            )
        except RuntimeError as exc:
            answer_text = str(exc)
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=normalized_query,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            postgres_manager.append_chat_exchange(
                chat_id=current_chat_id,
                question=normalized_query,
                answer=answer_text,
                provider_used=provider_used,
                sources=[],
                active_document_id=active_document_id,
            )
            postgres_manager.save_query(question=normalized_query, answer=answer_text)
            return ChatResponse(
                answer=answer_text,
                sources=[],
                provider_used=provider_used,
                chat_id=current_chat_id,
                active_document_id=active_document_id,
                answer_mode="refused",
                confidence={
                    "level": "baixa",
                    "score": 0.0,
                    "reasons": [answer_text],
                },
                classification=classification.model_dump(),
                legal_basis=[],
                validation_issues=[
                    {
                        "code": "llm_unavailable",
                        "message": answer_text,
                        "severity": "high",
                    }
                ],
            )
        _tllm = _time.time() - _tllm
        _tpp = _time.time()
        answer_draft = legal_composer.parse_llm_json(raw_answer)
        answer_draft = legal_composer.constrain_draft_to_context(
            answer_draft, retrieval.retrieved_chunks
        )
        validation = legal_validation_service.validate(
            classification, retrieval, answer_draft
        )

        # Em transformações, forçamos o modo grounded se o LLM conseguiu gerar algo útil,
        # ignorando a falta de novos chunks oficiais.
        if is_transformation and answer_draft.rich_content:
            validation.sufficient_legal_support = True
            validation.answer_mode = "grounded"
            validation.issues = []

        if classification.topic_route == "cpp" and validation.answer_mode == "grounded":
            answer_numbers = {
                part
                for part in re.findall(
                    r"\b\d+[.,]?\d*\b", answer_draft.rich_content or ""
                )
            }
            context_text = " ".join(
                (evidence.chunk.text or "") for evidence in retrieval.official_evidence
            ).lower()
            if answer_numbers and not any(
                number in context_text for number in answer_numbers
            ):
                issues = list(validation.issues)
                if not any(
                    issue.code == "processual_specificity_gap" for issue in issues
                ):
                    issues.append(
                        ValidationIssue(
                            code="processual_specificity_gap",
                            message="A base processual recuperada ainda não confirma com precisão suficiente o ponto específico perguntado no CPP.",
                            severity="medium",
                        )
                    )
                validation = validation.model_copy(
                    update={
                        "answer_mode": "limited",
                        "sufficient_legal_support": False,
                        "issues": issues,
                    }
                )
                answer_draft = legal_composer.fallback_from_validation(
                    validation, original_draft=answer_draft
                )
        cpp_answer_text = (answer_draft.rich_content or "").lower()
        if classification.topic_route == "cpp" and validation.answer_mode == "grounded":
            if any(
                marker in cpp_answer_text
                for marker in (
                    "não é explicitamente mencionado",
                    "nao e explicitamente mencionado",
                    "não menciona",
                    "nao menciona",
                )
            ):
                issues = list(validation.issues)
                if not any(
                    issue.code == "processual_specificity_gap" for issue in issues
                ):
                    issues.append(
                        ValidationIssue(
                            code="processual_specificity_gap",
                            message="A base processual recuperada ainda não confirma com precisão suficiente o ponto específico perguntado no CPP.",
                            severity="medium",
                        )
                    )
                validation = validation.model_copy(
                    update={
                        "answer_mode": "limited",
                        "sufficient_legal_support": False,
                        "issues": issues,
                    }
                )
        if not is_transformation:
            tracking_query = effective_query if query_context else normalized_query
            if not legal_composer.answer_tracks_question(
                tracking_query, answer_draft, classification
            ):
                if query_context and "Contexto profissional do caso associado:" in query_context and retrieval.official_evidence:
                    validation = validation.model_copy(
                        update={
                            "answer_mode": "grounded_with_caveat",
                            "sufficient_legal_support": False,
                        }
                    )
                else:
                    answer_draft = legal_composer.fallback_from_validation(
                        validation, original_draft=answer_draft
                    )
                    if validation.answer_mode == "grounded":
                        validation = validation.model_copy(
                            update={"answer_mode": "limited"}
                        )
            if legal_composer.answer_looks_like_json_artifact(
                answer_draft.rich_content
            ):
                answer_draft = legal_composer.fallback_from_validation(
                    validation, original_draft=answer_draft
                )
        verified_articles = []
        to_verify = [
            (
                item.article or "",
                self._basis_slug_from_retrieval(item, retrieval),
                item.page,
            )
            for item in validation.confirmed_legal_basis
            + validation.prudential_legal_basis
            if item.article and self._basis_slug_from_retrieval(item, retrieval)
        ]
        if to_verify:
            try:
                verified_articles = await asyncio.wait_for(
                    article_verifier.verify_batch(to_verify), timeout=1.5
                )
            except asyncio.TimeoutError:
                verified_articles = []
                logger.info("Article verification timed out — proceeding without it")
            unverified = [va for va in verified_articles if va.status == "not_found"]
            has_unsupported = any(
                issue.code == "unsupported_article" for issue in validation.issues
            )
            if (
                unverified
                and not has_unsupported
                and validation.answer_mode != "grounded_with_caveat"
            ):
                evidence_count = len(retrieval.official_evidence)
                severity = (
                    "medium"
                    if validation.answer_mode == "grounded" and evidence_count <= 2
                    else "high"
                )
                validation.issues.append(
                    ValidationIssue(
                        code="unverified_article",
                        message=f"{len(unverified)} artigo(s) citados não foram confirmados no corpus indexado.",
                        severity=severity,
                    )
                )
            if (
                classification.needs_article_validation
                or classification.requires_strict_corpus_match
            ) and any(
                va.status not in {"confirmed", "confirmed_in_text"}
                for va in verified_articles
            ):
                if classification.topic_route not in {"identificacao_civil", "sucessoes"}:
                    validation = validation.model_copy(
                        update={
                            "answer_mode": "limited",
                            "sufficient_legal_support": False,
                            "issues": validation.issues,
                        }
                    )
                    answer_draft = legal_composer.fallback_from_validation(
                        validation, original_draft=None
                    )

        requested_articles = list(classification.requested_article_numbers)
        if not requested_articles:
            requested_articles = extract_requested_article_numbers(
                classification.query_text or ""
            )
        confirmed_articles = {
            va.article.replace(".", "")
            for va in verified_articles
            if va.status in {"confirmed", "confirmed_in_text"} and va.article
        }
        if requested_articles and not confirmed_articles.intersection(
            requested_articles
        ):
            validation.issues.append(
                ValidationIssue(
                    code="requested_article_not_recovered",
                    message="O artigo exacto pedido não foi confirmado no contexto recuperado.",
                    severity="high"
                    if classification.requires_strict_corpus_match
                    else "medium",
                )
            )
            validation = validation.model_copy(
                update={
                    "answer_mode": "limited",
                    "sufficient_legal_support": False,
                    "issues": validation.issues,
                }
            )
            answer_draft = legal_composer.fallback_from_validation(
                validation, original_draft=None
            )

        referential_follow_up = self._looks_referential_follow_up(
            normalized_query, classification
        )
        unresolved_anchor = bool(
            referential_follow_up
            and requested_articles
            and not confirmed_articles.intersection(requested_articles)
        )
        if unresolved_anchor and not any(
            issue.code == "followup_anchor_unresolved" for issue in validation.issues
        ):
            validation.issues.append(
                ValidationIssue(
                    code="followup_anchor_unresolved",
                    message="O follow-up refere-se ao mesmo artigo anterior, mas esse artigo ainda não foi confirmado no contexto recuperado.",
                    severity="high",
                )
            )
            validation = validation.model_copy(
                update={
                    "answer_mode": "limited",
                    "sufficient_legal_support": False,
                    "issues": validation.issues,
                }
            )
            answer_draft = legal_composer.fallback_from_validation(
                validation, original_draft=None
            )

        answer_haystack = (answer_draft.rich_content or "").lower()
        if any(
            marker in answer_haystack
            for marker in (
                "não contém",
                "nao contem",
                "não consta",
                "nao consta",
                "não especifica",
                "nao especifica",
                "não é possível",
                "nao e possivel",
                "não foi possível",
                "nao foi possivel",
            )
        ):
            if classification.topic_route not in {"identificacao_civil", "sucessoes"}:
                validation = validation.model_copy(
                    update={
                        "answer_mode": "limited",
                        "sufficient_legal_support": False,
                    }
                )

        if (
            validation.answer_mode == "limited"
            and classification.topic_route in {"identificacao_civil", "sucessoes"}
            and retrieval.official_evidence
        ):
            validation = validation.model_copy(
                update={
                    "answer_mode": "grounded_with_caveat",
                    "sufficient_legal_support": False,
                }
            )
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and answer_draft.rich_content
            and retrieval.official_evidence
            and validation.answer_mode == "limited"
        ):
            validation = validation.model_copy(
                update={
                    "answer_mode": "grounded_with_caveat",
                    "sufficient_legal_support": False,
                }
            )

        confidence = legal_confidence_service.score(
            classification, retrieval, validation, verified_articles
        )
        sources = self._select_sources(retrieval, validation)
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and not sources
            and retrieval.official_evidence
        ):
            sources = _source_items_from_evidence(retrieval.official_evidence)
        # Force user document sources when a document is active
        if active_document_id and not any(
            s.source_scope == "user_upload" for s in sources
        ):
            logger.info(
                "Force-adding user doc sources (non-stream) for %s",
                active_document_id[:8],
            )
            try:
                for ev in retrieval.user_evidence:
                    chunk = ev.chunk
                    sources.append(
                        SourceItem(
                            title=normalize_legal_text(chunk.title),
                            source=normalize_legal_text(chunk.source),
                            link_original=chunk.link_original,
                            deep_link=(
                                f"{chunk.link_original}#page={chunk.page}"
                                if chunk.link_original
                                and chunk.page
                                and "#page=" not in chunk.link_original
                                else chunk.link_original
                            ),
                            page=chunk.page,
                            article_number=normalize_legal_text(chunk.article_number) or None,
                            law_status=chunk.law_status,
                            excerpt=normalize_legal_text(chunk.text[:780]),
                            attribution_text=normalize_legal_text(chunk.text[:300]) if chunk.text else None,
                            source_scope=chunk.source_scope,
                            document_id=chunk.document_id,
                        )
                    )
            except Exception as exc:
                logger.warning("Force-add sources (non-stream) failed: %s", exc)
        answer = legal_composer.compose_answer(
            classification, answer_draft, validation, confidence, sources,
            active_document_id=active_document_id,
        )
        answer = legal_composer.sanitize_answer(answer)
        answer = _normalize_brackets(answer)
        answer = _personalize_pro_answer(answer, query_context)
        answer = _pro_case_source_fallback(answer, query_context, sources)
        _tpp = _time.time() - _tpp
        logger.info("LLM:%.1fs postproc:%.1fs", _tllm, _tpp)

        if answer.startswith("{"):
            import json

            try:
                data = json.loads(answer)
                if isinstance(data, dict):
                    for key in ("rich_content", "answer", "response", "direct_answer"):
                        if key in data and isinstance(data[key], str):
                            answer = data[key]
                            break
            except Exception:
                extracted = legal_composer._extract_rich_content(answer)
                if extracted:
                    answer = extracted
                else:
                    for key in ("rich_content", "direct_answer", "simple_explanation"):
                        m = re.search(
                            rf'"{key}"\s*:\s*"((?:(?:\\.)|[^"\\])*)', answer, re.DOTALL
                        )
                        if m:
                            try:
                                answer = (
                                    m.group(1)
                                    .encode()
                                    .decode("unicode_escape", errors="replace")
                                )
                            except Exception:
                                answer = m.group(1)
                            break
                    if answer.startswith("{") or len(answer) < 50:
                        cleaned = re.sub(
                            r'^\{.*?"rich_content"\s*:\s*"', "", answer, flags=re.DOTALL
                        )
                        cleaned = re.sub(
                            r'",\s*"cited_.*$', "", cleaned, flags=re.DOTALL
                        )
                        cleaned = re.sub(r"\\n", "\n", cleaned)
                        if len(cleaned) > 50:
                            answer = cleaned

        if "```json" in answer:
            answer = re.sub(r"```json\s*\{.*?\}\s*```", "", answer, flags=re.DOTALL)

        answer = _normalize_brackets(answer)
        answer, verification_report = evidence_verifier.verify_and_guard(
            answer,
            normalized_query,
            retrieval.official_evidence + retrieval.user_evidence,
            retrieval.retrieval_notes,
        )
        if verification_report.negative_claim_guarded:
            validation.issues.append(
                ValidationIssue(
                    code="unsupported_negative_conclusion",
                    message=verification_report.unsupported_claims[0],
                    severity="high",
                )
            )
        elif verification_report.unsupported_claims:
            validation.issues.append(
                ValidationIssue(
                    code="claim_support_gap",
                    message=f"{len(verification_report.unsupported_claims)} afirmação(ões) não ficaram directamente suportadas pelas fontes selecionadas.",
                    severity="medium",
                )
            )

        if not current_chat_id:
            current_chat_id = postgres_manager.create_chat(
                title=normalized_query,
                active_document_id=active_document_id,
                user_id=user_id,
            )
        postgres_manager.append_chat_exchange(
            chat_id=current_chat_id,
            question=normalized_query,
            answer=answer,
            provider_used=provider_used,
            sources=[source.model_dump() for source in sources],
            active_document_id=active_document_id,
        )
        postgres_manager.save_query(question=normalized_query, answer=answer)

        primary_basis = (
            validation.confirmed_legal_basis[:1]
            or validation.prudential_legal_basis[:1]
            or validation.jurisprudence_basis[:1]
        )
        primary_slug = (
            self._basis_slug_from_retrieval(primary_basis[0], retrieval)
            if primary_basis
            else (
                REQUESTED_DIPLOMA_SLUGS.get(classification.requested_diplomas[0])
                if classification.requested_diplomas
                else None
            )
        )
        state_article = (
            requested_articles[0]
            if requested_articles
            else (next(iter(confirmed_articles)) if confirmed_articles else None)
        )
        postgres_manager.upsert_conversation_state(
            chat_id=current_chat_id,
            user_id=user_id,
            topic_route=classification.topic_route,
            legal_branch=classification.main_branch,
            diploma_slug=primary_slug,
            active_article=state_article,
            metadata={
                "last_requested_article": requested_articles[0]
                if requested_articles
                else None,
                "last_requested_diploma": classification.requested_diplomas[0]
                if classification.requested_diplomas
                else None,
                "last_requested_diploma_slug": primary_slug,
                "last_answer_mode": validation.answer_mode,
                "last_issue_codes": [issue.code for issue in validation.issues],
                "unresolved_requested_article": (
                    requested_articles[0]
                    if requested_articles
                    and not confirmed_articles.intersection(requested_articles)
                    else None
                ),
                "active_diploma_slug": primary_slug,
                "normative_status": validation.normative_status,
            },
        )

        return ChatResponse(
            answer=answer,
            sources=sources,
            provider_used=provider_used,
            chat_id=current_chat_id,
            active_document_id=active_document_id,
            answer_mode=validation.answer_mode,
            confidence=confidence.model_dump(),
            classification=classification.model_dump(),
            legal_basis=[
                item.model_dump()
                for item in validation.confirmed_legal_basis
                + validation.prudential_legal_basis
                + validation.jurisprudence_basis
            ],
            validation_issues=[issue.model_dump() for issue in validation.issues],
            verified_articles=[asdict(va) for va in verified_articles],
            suggested_actions=legal_composer.get_suggested_actions(
                answer_draft, classification, active_document_id, sources
            ),
        )

    async def preflight_classify(
        self,
        query: str,
        provider: str | None = None,
        conversation_history: list[str] | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
        query_context: str | None = None,
        clarification_context: dict | None = None,
    ) -> dict:
        """Lightweight classification only — no retrieval, no LLM generation.

        Returns {needs_clarification: bool, clarifying_questions: [...]}
        so the frontend can decide whether to show the clarifying UI
        before committing to a full RAG pipeline call.
        """
        normalized_query = query.strip()
        analysis_query = _clarification_enriched_query(
            normalized_query, clarification_context
        )
        effective_query = (
            f"{analysis_query}\n\n{query_context.strip()}"
            if query_context and query_context.strip()
            else analysis_query
        )
        history = conversation_history or []
        chat_state = (
            postgres_manager.get_conversation_state(chat_id, user_id=user_id)
            if chat_id
            else None
        )
        provider_used = provider or get_settings().default_llm_provider

        classification = await self._classify_query(
            effective_query, history, provider_used
        )
        classification = self._hydrate_follow_up_context(
            effective_query, history, classification, chat_state
        )
        classification = _stabilize_follow_up_classification(
            effective_query, history, classification, chat_state
        )
        classification = _apply_deterministic_context_override(
            effective_query, classification
        )
        _apply_progressive_clarification(classification, clarification_context)

        if (
            not classification.needs_clarification
            and not classification.clarifying_questions
        ):
            if _needs_clarification_from_classification(
                classification,
                classification.semantic_confidence,
                has_follow_up_context=_has_follow_up_context(
                    history, classification, chat_state
                ),
            ):
                classification.needs_clarification = True
                classification.clarifying_questions = _default_clarifying_questions(
                    normalized_query, classification, history, chat_state
                )
        elif classification.needs_clarification and _should_refresh_clarifying_questions(
            classification.clarifying_questions
        ):
            classification.clarifying_questions = _default_clarifying_questions(
                normalized_query, classification, history, chat_state
            )

        if classification.needs_clarification:
            _ensure_clarification_prompts(
                normalized_query, classification, history, chat_state
            )
            if _clarification_repeats_previous(classification, clarification_context):
                classification.needs_clarification = False
                classification.clarifying_questions = []
                classification.clarification_prompts = []

        clarification_request = (
            classification.clarification_prompts[0].model_dump()
            if classification.needs_clarification and classification.clarification_prompts
            else None
        )

        return {
            "needs_clarification": classification.needs_clarification,
            "clarifying_questions": classification.clarifying_questions or [],
            "clarification_request": clarification_request,
            "clarifying_message": _clarifying_message(
                normalized_query, history, classification
            )
            if classification.needs_clarification
            else "",
            "main_branch": classification.main_branch,
            "audience": classification.audience,
        }

    async def answer_query_stream_safe(
        self,
        query: str,
        provider: str | None = None,
        conversation_history: list[str] | None = None,
        chat_id: str | None = None,
        active_document_id: str | None = None,
        user_id: str | None = None,
        query_context: str | None = None,
        clarification_context: dict | None = None,
    ):
        """Stream-safe wrapper: checks for vague queries BEFORE retrieval/LLM."""
        import json as _json
        from app.core.logger import get_logger

        log = get_logger(__name__)

        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "classifying",
                    "status": "A enquadrar a questão jurídica.",
                }
            )
            + "\n\n"
        )

        preflight = await self.preflight_classify(
            query,
            provider,
            conversation_history,
            chat_id,
            user_id,
            query_context,
            clarification_context,
        )
        if preflight.get("needs_clarification") and not active_document_id:
            log.info("query is vague, returning clarifying mode: %s", query[:80])
            current_chat_id = chat_id
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=query.strip(),
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            clarification_request = preflight.get("clarification_request") or {}
            clarification_request["original_question"] = (
                str((clarification_context or {}).get("original_question") or "").strip()
                or query.strip()
            )
            answer = preflight.get("clarifying_message", "")
            stored_answer = answer
            if clarification_request.get("question"):
                stored_answer = f"{answer}\n\nAntes de continuar: {clarification_request['question']}"
            postgres_manager.append_chat_exchange(
                chat_id=current_chat_id,
                question=query.strip(),
                answer=stored_answer,
                provider_used=provider or get_settings().default_llm_provider,
                sources=[],
                active_document_id=active_document_id,
                assistant_metadata={
                    "answer_mode": "clarifying",
                    "clarifying_questions": preflight["clarifying_questions"],
                    "clarification_request": clarification_request,
                },
            )
            yield (
                "data: "
                + _json.dumps(
                    {
                        "answer_mode": "clarifying",
                        "clarifying_questions": preflight["clarifying_questions"],
                        "clarification_request": clarification_request,
                        "answer": answer,
                        "chat_id": current_chat_id,
                        "done": True,
                    }
                )
                + "\n\n"
            )
            return

        async for chunk in self.answer_query_stream(
            query,
            provider=provider,
            conversation_history=conversation_history,
            chat_id=chat_id,
            active_document_id=active_document_id,
            user_id=user_id,
            query_context=query_context,
            clarification_context=clarification_context,
        ):
            yield chunk

    async def answer_query_stream(
        self,
        query: str,
        provider: str | None = None,
        conversation_history: list[str] | None = None,
        chat_id: str | None = None,
        active_document_id: str | None = None,
        user_id: str | int | None = None,
        query_context: str | None = None,
        clarification_context: dict | None = None,
    ):
        """Stream answer tokens via SSE, then yields a final `done: true` event.

        Reuses the same classification / retrieval / validation pipeline as
        ``answer_query`` but streams tokens from the LLM so the frontend can
        render progressive output.
        """
        import json as _json

        normalized_query = (query or "").strip()
        analysis_query = _clarification_enriched_query(
            normalized_query, clarification_context
        )
        effective_query = (
            f"{analysis_query}\n\n{query_context.strip()}"
            if query_context and query_context.strip()
            else analysis_query
        )
        history = conversation_history or []
        current_chat_id = chat_id
        provider_used = provider or get_settings().default_llm_provider
        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "classifying",
                    "status": "A interpretar o pedido e o contexto da conversa.",
                }
            )
            + "\n\n"
        )
        chat_state = (
            postgres_manager.get_conversation_state(current_chat_id, user_id=user_id)
            if current_chat_id
            else None
        )
        _conv_diploma_slug = (
            (chat_state or {}).get("diploma_slug")
            or ((chat_state or {}).get("metadata") or {}).get("last_requested_diploma_slug")
            or ((chat_state or {}).get("metadata") or {}).get("active_diploma_slug")
        )
        # Extract diploma names from previous turn's context
        _conv_diploma_names = []
        _prev_diploma = ((chat_state or {}).get("metadata") or {}).get("last_requested_diploma")
        if _prev_diploma:
            _conv_diploma_names.append(str(_prev_diploma))

        # --- Phase 1: classification + follow-up hydration ---
        _t0 = _time.perf_counter()
        classification = await self._classify_query(effective_query, history, provider)
        classification = self._hydrate_follow_up_context(
            effective_query, history, classification, chat_state
        )
        classification = _stabilize_follow_up_classification(
            effective_query, history, classification, chat_state
        )
        classification = _apply_deterministic_context_override(
            effective_query, classification
        )
        _apply_progressive_clarification(classification, clarification_context)
        # Augment conversation context with classification's diploma detection
        if classification.requested_diplomas:
            _conv_diploma_names.extend(classification.requested_diplomas)
            from app.services.legal.retrieval import _requested_diploma_slugs
            _req_slugs = _requested_diploma_slugs(classification)
            if _req_slugs:
                _conv_diploma_slug = _conv_diploma_slug or next(iter(_req_slugs))
        _t1 = _time.perf_counter()

        # Clarification gate — skip when user has an active document loaded
        if active_document_id:
            classification.needs_clarification = False
            classification.clarifying_questions = []
            classification.clarification_prompts = []
        elif (
            not classification.needs_clarification
            and not classification.clarifying_questions
        ):
            if _needs_clarification_from_classification(
                classification,
                classification.semantic_confidence,
                has_follow_up_context=_has_follow_up_context(
                    history, classification, chat_state
                ),
            ):
                classification.needs_clarification = True
                classification.clarifying_questions = _default_clarifying_questions(
                    normalized_query, classification, history, chat_state
                )
        elif classification.needs_clarification and _should_refresh_clarifying_questions(
            classification.clarifying_questions
        ):
            classification.clarifying_questions = _default_clarifying_questions(
                normalized_query, classification, history, chat_state
            )

        if classification.needs_clarification:
            _ensure_clarification_prompts(
                normalized_query, classification, history, chat_state
            )
            if _clarification_repeats_previous(classification, clarification_context):
                classification.needs_clarification = False
                classification.clarifying_questions = []
                classification.clarification_prompts = []

        if classification.needs_clarification and classification.clarifying_questions:
            clarifying_answer = _clarifying_message(
                normalized_query, history, classification
            )
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=normalized_query,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            postgres_manager.save_query(
                question=normalized_query, answer=clarifying_answer
            )
            yield (
                "data: "
                + _json.dumps(
                    {
                        "answer_mode": "clarifying",
                        "clarifying_questions": classification.clarifying_questions,
                        "clarification_request": (
                            classification.clarification_prompts[0].model_dump()
                            if classification.clarification_prompts
                            else None
                        ),
                        "answer": clarifying_answer,
                        "done": True,
                    }
                )
                + "\n\n"
            )
            return

        search_query = self._derive_search_query(
            effective_query, history, classification
        )
        search_query = (
            _compact_pro_search_query(normalized_query, query_context, classification)
            or search_query
        )
        classification.query_text = search_query
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and classification.is_transformation
        ):
            classification = classification.model_copy(
                update={"is_transformation": False, "transformation_type": None}
            )
        # When a document is active, summarize/simplify is a normal query —
        # the LLM will summarise based on the document context naturally.
        if active_document_id and classification.is_transformation:
            classification = classification.model_copy(
                update={"is_transformation": False}
            )
        if classification.is_transformation and not history:
            classification = classification.model_copy(
                update={"is_transformation": False}
            )
        is_transformation = classification.is_transformation

        # --- Phase 2: retrieval ---
        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "retrieving",
                    "status": "A pesquisar legislação e fontes relevantes.",
                }
            )
            + "\n\n"
        )
        _t_retrieval_start = _time.perf_counter()
        if is_transformation:
            retrieval = RetrievalResult(
                classification=classification,
                official_evidence=[],
                user_evidence=[],
                missing_branches=[],
            )
        else:
            expanded_queries = query_expander.expand(search_query, classification)
            results = await asyncio.gather(
                *[
                    legal_retrieval_service.retrieve(
                        qv,
                        classification,
                        conversation_history=history,
                        active_document_id=active_document_id,
                        user_id=user_id,
                        conversation_diploma_slug=_conv_diploma_slug,
                        conversation_diploma_names=_conv_diploma_names if _conv_diploma_names else None,
                    )
                    for qv in expanded_queries[:1]
                ]
            )
            all_evidences: list = []
            all_user_evidences: list = []
            for partial in results:
                all_evidences.extend(partial.official_evidence)
                all_user_evidences.extend(partial.user_evidence)
            if _pro_current_question_needs_direct_retrieval(
                normalized_query, query_context
            ):
                direct_current = await legal_retrieval_service.retrieve(
                    normalized_query,
                    classification,
                    conversation_history=history,
                    active_document_id=active_document_id,
                    user_id=user_id,
                    conversation_diploma_slug=_conv_diploma_slug,
                    conversation_diploma_names=_conv_diploma_names if _conv_diploma_names else None,
                )
                all_evidences.extend(direct_current.official_evidence)
                all_user_evidences.extend(direct_current.user_evidence)
            if all_evidences:
                all_evidences = self._with_jurisprudence_priority(
                    all_evidences, MAX_OFFICIAL_EVIDENCES, classification.main_branch
                )
            combined_evidences = self._with_jurisprudence_priority(
                all_evidences + all_user_evidences,
                MAX_COMBINED_EVIDENCES,
                classification.main_branch,
            )
            if combined_evidences:
                retrieval = RetrievalResult(
                    classification=classification,
                    official_evidence=all_evidences,
                    user_evidence=all_user_evidences,
                    missing_branches=[],
                    retrieved_chunks=[e.chunk for e in combined_evidences],
                )
            else:
                retrieval = await legal_retrieval_service.retrieve(
                    classification.search_query or normalized_query,
                    classification,
                    conversation_history=history,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            retrieval = self._filter_retrieval_by_branch(retrieval, classification)
            retrieval = self._force_requested_article_evidence(retrieval, classification)
        _t_retrieval_done = _time.perf_counter()

        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "evaluating",
                    "status": "A avaliar a relevância e a autoridade das fontes encontradas.",
                }
            )
            + "\n\n"
        )

        # Safety net — force user doc chunks into context when a document is active
        if active_document_id and not retrieval.user_evidence:
            logger.info(
                "User document not found in retrieval — force-fetching chunks for %s",
                active_document_id[:8],
            )
            try:
                from app.services.pdf.document_context import document_context_service
                from app.services.rag.retriever import retriever_service

                force_chunks = document_context_service.get_relevant_chunks(
                    active_document_id,
                    normalized_query or "",
                    user_id=user_id,
                    conversation_history=history,
                )
                if not force_chunks:
                    force_chunks = await retriever_service.retrieve(
                        normalized_query or "",
                        where={"document_id": active_document_id},
                    )
                if force_chunks:
                    from app.services.legal.retrieval import _source_bucket

                    evs = []
                    for ch in force_chunks:
                        evs.append(
                            type(
                                "Ev",
                                (),
                                {
                                    "chunk": ch,
                                    "score": 30.0,
                                    "source_bucket": _source_bucket(ch),
                                    "retrieval_reason": "force_user_doc",
                                },
                            )()
                        )
                    retrieval = replace(retrieval, user_evidence=evs)
                    retrieval.retrieved_chunks = [
                        e.chunk for e in evs
                    ] + retrieval.retrieved_chunks
                    logger.info(
                        "Force-added %d user doc chunks to context", len(force_chunks)
                    )
            except Exception as exc:
                logger.warning("Force-fetch user doc chunks failed: %s", exc)

        if not retrieval.retrieved_chunks and not is_transformation:
            answer_text = "Não encontrei contexto jurídico suficiente no índice actual para responder com segurança. Reformule a pergunta com mais detalhe, indique o diploma pretendido ou peça o artigo exacto a confirmar."
            if not current_chat_id:
                current_chat_id = postgres_manager.create_chat(
                    title=normalized_query,
                    active_document_id=active_document_id,
                    user_id=user_id,
                )
            yield (
                "data: "
                + _json.dumps(
                    {
                        "done": True,
                        "answer": answer_text,
                        "provider_used": provider_used,
                        "chat_id": current_chat_id,
                        "answer_mode": "refused",
                        "sources": [],
                        "validation_issues": [
                            {
                                "code": "no_context",
                                "message": "Sem contexto jurídico suficiente no índice actual.",
                                "severity": "high",
                            }
                        ],
                    }
                )
                + "\n\n"
            )
            return

        ai_preferences = (
            self._coerce_ai_preferences(postgres_manager.get_user_preferences(str(user_id)))
            if user_id
            else {}
        )
        effective_audience = self._effective_audience(
            classification.audience, ai_preferences
        )
        response_max_tokens = self._max_tokens_for_preferences(
            effective_audience, ai_preferences
        )
        professional_context = bool(
            query_context and "Contexto profissional do caso associado:" in query_context
        )
        if self._query_requests_detailed_answer(normalized_query):
            response_max_tokens = max(response_max_tokens, 920)
        if professional_context:
            response_max_tokens = max(response_max_tokens, 980)
        prompt = legal_composer.build_prompt(
            effective_query,
            classification,
            retrieval.retrieved_chunks,
            conversation_history=history,
            ai_preferences=ai_preferences,
            streaming=True,
            professional_context=professional_context,
        )

        # --- Phase 3: LLM streaming ---
        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "composing",
                    "status": "A redigir a resposta fundamentada.",
                }
            )
            + "\n\n"
        )
        _t_llm_start = _time.perf_counter()
        accumulated: list[str] = []
        stream_extractor = legal_composer.create_stream_extractor()
        try:
            async for token in self._stream_llm(
                prompt,
                provider,
                effective_audience,
                max_tokens=response_max_tokens,
            ):
                accumulated.append(token)
                clean_delta = stream_extractor.push(token)
                if clean_delta:
                    yield f"data: {_json.dumps({'token': clean_delta})}\n\n"
        except RuntimeError as exc:
            err_text = str(exc)
            yield (
                "data: "
                + _json.dumps(
                    {"done": True, "answer": err_text, "answer_mode": "refused"}
                )
                + "\n\n"
            )
            return

        raw_answer = "".join(accumulated)
        _t_llm_done = _time.perf_counter()

        # --- Phase 4: validate, compose, persist ---
        yield (
            "data: "
            + _json.dumps(
                {
                    "phase": "verifying",
                    "status": "A verificar os artigos citados e a consistência da resposta.",
                }
            )
            + "\n\n"
        )
        _t_post_start = _time.perf_counter()
        answer_draft = legal_composer.parse_llm_json(raw_answer)
        answer_draft = legal_composer.constrain_draft_to_context(
            answer_draft, retrieval.retrieved_chunks
        )
        validation = legal_validation_service.validate(
            classification, retrieval, answer_draft
        )
        if is_transformation and answer_draft.rich_content:
            validation.sufficient_legal_support = True
            validation.answer_mode = "grounded"
            validation.issues = []
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and answer_draft.rich_content
            and retrieval.official_evidence
            and validation.answer_mode == "limited"
        ):
            validation = validation.model_copy(
                update={
                    "answer_mode": "grounded_with_caveat",
                    "sufficient_legal_support": False,
                }
            )
        verified_articles = []
        to_verify = [
            (
                item.article or "",
                self._basis_slug_from_retrieval(item, retrieval),
                item.page,
            )
            for item in validation.confirmed_legal_basis
            + validation.prudential_legal_basis
            if item.article and self._basis_slug_from_retrieval(item, retrieval)
        ]
        if to_verify:
            try:
                verified_articles = await asyncio.wait_for(
                    article_verifier.verify_batch(to_verify), timeout=1.5
                )
            except asyncio.TimeoutError:
                logger.info("Article verification timed out in stream finalization")
            unverified = [
                item
                for item in verified_articles
                if item.status not in {"confirmed", "confirmed_in_text"}
            ]
            if unverified:
                validation.issues.append(
                    ValidationIssue(
                        code="unverified_article",
                        message=f"{len(unverified)} artigo(s) citados não foram confirmados no corpus indexado.",
                        severity="high",
                    )
                )
                validation = validation.model_copy(
                    update={
                        "answer_mode": "limited",
                        "sufficient_legal_support": False,
                        "issues": validation.issues,
                    }
                )
                answer_draft = legal_composer.fallback_from_validation(
                    validation, original_draft=answer_draft
                )

        confidence = legal_confidence_service.score(
            classification, retrieval, validation, verified_articles
        )
        sources = self._select_sources(retrieval, validation)
        if (
            query_context
            and "Contexto profissional do caso associado:" in query_context
            and not sources
            and retrieval.official_evidence
        ):
            sources = _source_items_from_evidence(retrieval.official_evidence)
        if active_document_id and not any(
            s.source_scope == "user_upload" for s in sources
        ):
            logger.info("Force-adding user doc sources for %s", active_document_id[:8])
            try:
                for ev in retrieval.user_evidence:
                    chunk = ev.chunk
                    sources.append(
                        SourceItem(
                            title=normalize_legal_text(chunk.title),
                            source=normalize_legal_text(chunk.source),
                            link_original=chunk.link_original,
                            deep_link=(
                                f"{chunk.link_original}#page={chunk.page}"
                                if chunk.link_original
                                and chunk.page
                                and "#page=" not in chunk.link_original
                                else chunk.link_original
                            ),
                            page=chunk.page,
                            article_number=normalize_legal_text(chunk.article_number) or None,
                            law_status=chunk.law_status,
                            excerpt=normalize_legal_text(chunk.text[:780]),
                            attribution_text=normalize_legal_text(chunk.text[:300]) if chunk.text else None,
                            source_scope=chunk.source_scope,
                            document_id=chunk.document_id,
                        )
                    )
            except Exception as exc:
                logger.warning("Force-add sources failed: %s", exc)
        answer = legal_composer.compose_answer(
            classification, answer_draft, validation, confidence, sources,
            active_document_id=active_document_id,
        )
        answer = legal_composer.sanitize_answer(answer)
        answer = _normalize_brackets(answer)
        answer = _personalize_pro_answer(answer, query_context)
        answer = _pro_case_source_fallback(answer, query_context, sources)
        answer, verification_report = evidence_verifier.verify_and_guard(
            answer,
            normalized_query,
            retrieval.official_evidence + retrieval.user_evidence,
            retrieval.retrieval_notes,
        )
        if verification_report.negative_claim_guarded:
            validation.issues.append(
                ValidationIssue(
                    code="unsupported_negative_conclusion",
                    message=verification_report.unsupported_claims[0],
                    severity="high",
                )
            )
        elif verification_report.unsupported_claims:
            validation.issues.append(
                ValidationIssue(
                    code="claim_support_gap",
                    message=f"{len(verification_report.unsupported_claims)} afirmação(ões) não ficaram directamente suportadas pelas fontes selecionadas.",
                    severity="medium",
                )
            )

        if not current_chat_id:
            current_chat_id = postgres_manager.create_chat(
                title=normalized_query,
                active_document_id=active_document_id,
                user_id=user_id,
            )
        postgres_manager.append_chat_exchange(
            chat_id=current_chat_id,
            question=normalized_query,
            answer=answer,
            provider_used=provider_used or "deepseek",
            sources=[s.model_dump() for s in sources],
            active_document_id=active_document_id,
            assistant_metadata={"answer_mode": validation.answer_mode},
        )
        postgres_manager.save_query(question=normalized_query, answer=answer)

        # Determine diploma slug for conversation state
        _state_diploma_slug = None
        if validation.confirmed_legal_basis:
            _state_diploma_slug = self._basis_slug_from_retrieval(
                validation.confirmed_legal_basis[0], retrieval
            )
        # Fallback: use the slug of the diploma the user explicitly requested
        if not _state_diploma_slug and classification.requested_diplomas:
            from app.services.legal.retrieval import _requested_diploma_slugs
            _req = _requested_diploma_slugs(classification)
            if _req:
                _state_diploma_slug = next(iter(_req))
        # Final fallback: scan all retrieved chunks for a matching diploma
        if not _state_diploma_slug:
            for ev in retrieval.official_evidence:
                slug = (ev.chunk.metadata or {}).get("diploma_slug")
                if slug:
                    _state_diploma_slug = slug
                    break

        postgres_manager.upsert_conversation_state(
            chat_id=current_chat_id,
            user_id=user_id,
            topic_route=classification.topic_route,
            legal_branch=classification.main_branch,
            diploma_slug=_state_diploma_slug,
            metadata={
                "last_requested_article": (
                    classification.requested_article_numbers[0]
                    if classification.requested_article_numbers
                    else None
                ),
                "last_requested_diploma": (
                    classification.requested_diplomas[0]
                    if classification.requested_diplomas
                    else None
                ),
                "last_answer_mode": validation.answer_mode,
                "last_issue_codes": [i.code for i in validation.issues],
                "normative_status": validation.normative_status,
            },
        )

        _t_post_done = _time.perf_counter()

        # Timing summary
        t_classify = _t1 - _t0
        t_retrieval = _t_retrieval_done - _t_retrieval_start
        t_llm = _t_llm_done - _t_llm_start
        t_post = _t_post_done - _t_post_start
        t_total = _t_post_done - _t0
        logger.info(
            "RAG timing — classify: %(c).1fs | retrieve: %(r).1fs | llm: %(l).1fs | post: %(p).1fs | TOTAL: %(t).1fs",
            {"c": t_classify, "r": t_retrieval, "l": t_llm, "p": t_post, "t": t_total},
        )

        yield (
            "data: "
            + _json.dumps(
                {
                    "done": True,
                    "answer": answer,
                    "provider_used": provider_used or "deepseek",
                    "chat_id": current_chat_id,
                    "active_document_id": active_document_id,
                    "answer_mode": validation.answer_mode,
                    "confidence": confidence.model_dump(),
                    "classification": classification.model_dump(),
                    "sources": [s.model_dump() for s in sources],
                    "validation_issues": [i.model_dump() for i in validation.issues],
                    "legal_basis": [
                        item.model_dump()
                        for item in validation.confirmed_legal_basis
                        + validation.prudential_legal_basis
                    ],
                    "verified_articles": [asdict(item) for item in verified_articles],
                    "timing": {
                        "classify": round(t_classify, 2),
                        "retrieve": round(t_retrieval, 2),
                        "llm": round(t_llm, 2),
                        "post": round(t_post, 2),
                        "total": round(t_total, 2),
                    },
                    "suggested_actions": legal_composer.get_suggested_actions(
                        answer_draft, classification, active_document_id, sources
                    ),
                }
            )
            + "\n\n"
        )

    async def _stream_llm(
        self,
        prompt: str,
        provider: str | None,
        audience: str,
        max_tokens: int | None = None,
    ):
        """Yield tokens from the LLM provider.

        Only DeepSeek supports true token streaming in this codebase.
        All other providers fall back to a non-streaming call and yield
        the full response as a single chunk.
        """
        selected = (
            provider or get_settings().default_llm_provider or "deepseek"
        ).lower()
        max_tokens = max_tokens or (430 if audience == "leigo" else 560)

        if selected == "deepseek":
            stream = deepseek_client.generate_stream(
                prompt, json_mode=False, max_tokens=max_tokens
            )
            while True:
                try:
                    token = await asyncio.wait_for(stream.__anext__(), timeout=25.0)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    logger.warning("DeepSeek stream idle timeout; closing partial response")
                    break
                except asyncio.TimeoutError:
                    logger.warning("DeepSeek stream idle timeout; closing partial response")
                    break
                yield token
            close = getattr(stream, "aclose", None)
            if close:
                await close()
            return

        content, _ = await llm_router.generate(
            prompt, provider=provider, json_mode=False, max_tokens=max_tokens
        )
        yield content

    @staticmethod
    def _filter_retrieval_by_branch(
        retrieval: RetrievalResult, classification
    ) -> RetrievalResult:
        branch = classification.main_branch
        if branch not in {
            "penal",
            "laboral",
            "civil",
            "familia",
            "tributario",
            "comercial",
            "constitucional",
            "administrativo",
            "propriedade",
        }:
            return retrieval

        def _chunk_branch_name(chunk) -> str | None:
            return (chunk.metadata or {}).get("legal_branch")

        official_on_branch = [
            ev
            for ev in retrieval.official_evidence
            if _chunk_branch_name(ev.chunk) == branch
        ]
        if not official_on_branch:
            return retrieval

        official_off_branch = [
            ev
            for ev in retrieval.official_evidence
            if _chunk_branch_name(ev.chunk) != branch
        ]

        from dataclasses import replace

        filtered = replace(retrieval)
        filtered.official_evidence = official_on_branch + [
            ev for ev in official_off_branch if ev.score > 3.0
        ]
        chunks = []
        seen_ids = set()
        for ev in filtered.official_evidence + retrieval.user_evidence:
            cid = id(ev.chunk)
            if cid not in seen_ids:
                seen_ids.add(cid)
                chunks.append(ev.chunk)
        filtered.retrieved_chunks = chunks
        return filtered

    @staticmethod
    def _force_requested_article_evidence(
        retrieval: RetrievalResult, classification
    ) -> RetrievalResult:
        requested_articles = {
            str(article).replace(".", "").strip()
            for article in getattr(classification, "requested_article_numbers", [])
            if str(article).strip()
        }
        if not requested_articles:
            return retrieval
        requested_slugs = _requested_diploma_slugs_from_names(
            getattr(classification, "requested_diplomas", [])
        )
        if requested_slugs and getattr(classification, "requires_strict_corpus_match", False):
            retrieval.official_evidence = [
                evidence
                for evidence in retrieval.official_evidence
                if _chunk_has_requested_slug(evidence.chunk, requested_slugs)
            ]
            retrieval.retrieved_chunks = [
                evidence.chunk
                for evidence in retrieval.official_evidence + retrieval.user_evidence
            ]
        covered_articles: set[str] = set()
        for evidence in retrieval.official_evidence + retrieval.user_evidence:
            if requested_slugs and not _chunk_has_requested_slug(
                evidence.chunk, requested_slugs
            ):
                continue
            for article in requested_articles:
                if RAGPipeline._chunk_matches_preferred_article(
                    evidence.chunk, {article}
                ):
                    covered_articles.add(article)
        if requested_articles.issubset(covered_articles):
            return retrieval

        forced: list[RetrievalEvidence] = []
        try:
            if requested_slugs:
                for slug in requested_slugs:
                    for article in requested_articles:
                        if article in covered_articles:
                            continue
                        chunk = RAGPipeline._best_requested_article_chunk(
                            postgres_manager.find_article_chunks(
                                slug, article, limit=8
                            ),
                            article,
                        )
                        if chunk:
                            forced.append(
                                RetrievalEvidence(
                                    query_used=classification.query_text or "",
                                    chunk=chunk,
                                    score=100.0,
                                    retrieval_reason="requested_article_direct",
                                    source_bucket="official",
                                )
                            )
            else:
                with postgres_manager.connection() as conn, conn.cursor() as cur:
                    for article in requested_articles:
                        if article in covered_articles:
                            continue
                        cur.execute(
                            """
                            SELECT id, source, title, link_original, page, article_number,
                                   law_status, source_scope, document_id, text_content, metadata
                            FROM legal_segments
                            WHERE source_scope = 'official'
                              AND (
                                article_number LIKE %s
                                OR text_content ILIKE %s
                              )
                            ORDER BY page ASC
                            LIMIT 12
                            """,
                            (f"%{article}%", f"%ARTIGO {article}.%"),
                        )
                        candidates: list[RetrievedChunk] = []
                        for row in cur.fetchall():
                            chunk = RetrievedChunk(
                                chunk_id=row["id"],
                                text=row["text_content"] or "",
                                source=row["source"] or "",
                                title=row["title"] or "",
                                link_original=row["link_original"],
                                page=row["page"],
                                article_number=row["article_number"],
                                law_status=row["law_status"] or "Nao verificado",
                                source_scope=row["source_scope"] or "official",
                                document_id=row["document_id"],
                                metadata=row["metadata"] or {},
                            )
                            if not RAGPipeline._chunk_matches_preferred_article(
                                chunk, requested_articles
                            ):
                                continue
                            candidates.append(chunk)
                        chunk = RAGPipeline._best_requested_article_chunk(
                            candidates, article
                        )
                        if chunk:
                            forced.append(
                                RetrievalEvidence(
                                    query_used=classification.query_text or "",
                                    chunk=chunk,
                                    score=80.0,
                                    retrieval_reason="requested_article_direct",
                                    source_bucket="official",
                                )
                            )
        except Exception as exc:
            logger.warning("Direct requested article fetch failed: %s", exc)
            return retrieval

        if not forced and not requested_slugs:
            return retrieval

        seen = {evidence.chunk.chunk_id for evidence in retrieval.official_evidence}
        additions = [
            evidence for evidence in forced if evidence.chunk.chunk_id not in seen
        ]
        existing = retrieval.official_evidence
        if requested_slugs and getattr(classification, "requires_strict_corpus_match", False):
            existing = [
                evidence
                for evidence in existing
                if _chunk_has_requested_slug(evidence.chunk, requested_slugs)
            ]
        if not additions and existing is retrieval.official_evidence:
            return retrieval
        retrieval.official_evidence = additions + existing
        retrieval.retrieved_chunks = [
            evidence.chunk for evidence in retrieval.official_evidence + retrieval.user_evidence
        ]
        return retrieval

    @staticmethod
    def _select_sources(retrieval, validation) -> list[SourceItem]:
        selected: list[SourceItem] = []
        seen: set[tuple[str, int | None, str]] = set()
        preferred_articles = {
            str(item.article).replace(".", "")
            for item in validation.confirmed_legal_basis
            + validation.prudential_legal_basis
            if item.article
        }
        preferred_keys = {
            (item.diploma, item.page, item.source_scope)
            for item in validation.confirmed_legal_basis
            + validation.prudential_legal_basis
        }
        base_evidence = retrieval.official_evidence + retrieval.user_evidence
        classification = retrieval.classification
        requested_slugs = _requested_diploma_slugs_from_names(
            getattr(classification, "requested_diplomas", [])
        )
        if requested_slugs and getattr(classification, "requires_strict_corpus_match", False):
            strict_evidence = [
                evidence
                for evidence in base_evidence
                if _chunk_has_requested_slug(evidence.chunk, requested_slugs)
                or evidence.chunk.source_scope == "user_upload"
            ]
            if strict_evidence:
                base_evidence = strict_evidence
        source_limit = (
            12
            if getattr(classification, "needs_multi_branch_handling", False)
            or getattr(classification, "main_branch", "") == "misto"
            else 8
        )
        if _query_requests_user_document_only(retrieval.classification.query_text):
            user_only_evidence = [
                evidence
                for evidence in retrieval.user_evidence
                if evidence.chunk.source_scope == "user_upload"
            ]
            if user_only_evidence:
                base_evidence = user_only_evidence
        ordered_evidence = sorted(
            base_evidence,
            key=lambda evidence: (
                0
                if (
                    evidence.chunk.title,
                    evidence.chunk.page,
                    evidence.chunk.source_scope,
                )
                in preferred_keys
                else 1,
                0
                if RAGPipeline._chunk_matches_preferred_article(
                    evidence.chunk, preferred_articles
                )
                else 1,
                -evidence.score,
            ),
        )
        jurisprudence_evidence = [
            evidence
            for evidence in ordered_evidence
            if getattr(evidence, "retrieval_reason", "") == "jurisprudence"
            or (evidence.chunk.metadata or {}).get("document_kind") == "jurisprudence"
        ][:2]
        if validation.jurisprudence_basis and jurisprudence_evidence:
            ordered_evidence = jurisprudence_evidence + [
                evidence for evidence in ordered_evidence if evidence not in jurisprudence_evidence
            ]
        for evidence in ordered_evidence:
            chunk = evidence.chunk
            if _source_is_jurisprudence(chunk) and not (
                validation.jurisprudence_basis
                and _jurisprudence_source_is_relevant(
                    retrieval.classification.query_text, chunk
                )
            ):
                continue
            key = (chunk.title, chunk.page, chunk.source_scope, chunk.article_number)
            if key in seen:
                continue
            seen.add(key)
            meta = chunk.metadata or {}
            selected.append(
                SourceItem(
                    title=normalize_legal_text(chunk.title),
                    source=normalize_legal_text(chunk.source),
                    link_original=chunk.link_original,
                    deep_link=(
                        f"{chunk.link_original}#page={chunk.page}"
                        if chunk.link_original
                        and chunk.page
                        and "#page=" not in chunk.link_original
                        else chunk.link_original
                    ),
                    page=chunk.page,
                    article_number=normalize_legal_text(chunk.article_number) or None,
                    law_status=chunk.law_status,
                    excerpt=normalize_legal_text(chunk.text[:780]),
                    attribution_text=normalize_legal_text(chunk.text[:300]) if chunk.text else None,
                    source_scope=chunk.source_scope,
                    source_kind=meta.get("document_kind"),
                    document_id=chunk.document_id,
                )
            )
            if len(selected) >= source_limit:
                break
        return selected

    @staticmethod
    def _best_requested_article_chunk(
        chunks: list[RetrievedChunk], article: str
    ) -> RetrievedChunk | None:
        if not chunks:
            return None
        normalized_article = str(article).replace(".", "").strip()

        def _score(chunk: RetrievedChunk) -> tuple[float, int]:
            metadata = chunk.metadata or {}
            article_main = (
                str(metadata.get("article_main") or "").replace(".", "").strip()
            )
            article_numbers = [
                part.strip().replace(".", "")
                for part in str(chunk.article_number or "").split(",")
                if part.strip()
            ]
            raw_text = chunk.text or ""
            text = normalize_legal_text(raw_text).casefold()
            score = 0.0
            if article_main == normalized_article:
                score += 30.0
            if article_numbers and article_numbers[0] == normalized_article:
                score += 12.0
            if re.search(rf"\bartigo\s+{re.escape(normalized_article)}\b", text):
                score += 10.0
            if (metadata.get("segmentation") or "") == "article_block":
                score += 4.0
            if re.search(r"\.\s*\.\s*\.", raw_text):
                score -= 25.0
            if len(article_numbers) > 2:
                score -= 1.0
            page = int(chunk.page or 9999)
            return (score, -page)

        return max(chunks, key=_score)

    @staticmethod
    def _chunk_matches_preferred_article(chunk, preferred_articles: set[str]) -> bool:
        if not preferred_articles:
            return False
        metadata = chunk.metadata or {}
        refs = [
            str(item).replace(".", "")
            for item in (metadata.get("article_references") or [])
        ]
        if chunk.article_number:
            refs.extend(
                part.strip().replace(".", "")
                for part in chunk.article_number.split(",")
                if part.strip()
            )
        main = metadata.get("article_main")
        if main:
            refs.append(str(main).replace(".", ""))
        return any(ref in preferred_articles for ref in refs)


rag_pipeline = RAGPipeline()
