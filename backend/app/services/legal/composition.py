from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from app.db.models import SourceItem
from app.services.legal.models import (
    ConfidenceResult,
    LegalClassification,
    LLMAnswerDraft,
    ValidationResult,
)
from app.services.legal.text_normalization import normalize_legal_text


SECTION_HEADERS = {
    "leigo": {
        "simple": "Em termos simples",
        "steps": "Passos praticos",
        "distinctions": "Distincoes importantes",
        "legal": "Base legal de apoio",
        "prudence": "Nota prudencial",
        "confidence": "Confianca da resposta",
    },
    "misto": {
        "simple": "Explicacao clara",
        "steps": "Passos praticos",
        "distinctions": "Distincoes e nuances",
        "legal": "Base legal de apoio",
        "prudence": "Nota prudencial",
        "confidence": "Confianca da resposta",
    },
    "tecnico": {
        "simple": "Sintese",
        "steps": "Actuacao pratica",
        "distinctions": "Distincoes tecnicas",
        "legal": "Base legal confirmada",
        "prudence": "Limites e validacao adicional",
        "confidence": "Confianca da resposta",
    },
}


def _clean(text: str) -> str:
    updated = normalize_legal_text(text).strip()
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    return updated


def _compact_excerpt(text: str, max_chars: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", normalize_legal_text(text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return f"{cut}..."


def _source_based_answer(
    classification: LegalClassification,
    sources: list[SourceItem],
) -> str:
    official_sources = [
        source
        for source in sources
        if (source.excerpt or "").strip()
        and (source.source_scope or "official") == "official"
    ][:8]
    if len(official_sources) < 3:
        return ""

    diploma = official_sources[0].title or "diploma recuperado"
    lines = [
        "### Resposta",
        "",
        f"Com base nas fontes oficiais recuperadas, a pergunta deve ser analisada principalmente à luz de **{diploma}**.",
        "",
        "### Pontos jurídicos confirmados",
    ]
    for source in official_sources[:6]:
        article = f"Art. {source.article_number}.º" if source.article_number else "Artigo recuperado"
        excerpt = _compact_excerpt(source.excerpt or "")
        lines.append(f"- **{article}**: {excerpt}")

    if classification.audience == "leigo":
        lines.extend(
            [
                "",
                "### Em linguagem simples",
                "A resposta acima resume os artigos encontrados e mostra quais normas devem ser verificadas antes de concluir o caso. Para aplicar ao caso concreto, confirme os factos essenciais, prazos e documentos disponíveis.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "### Nota técnica",
                "A conclusão final depende da articulação entre estes artigos, dos factos provados e de eventual jurisprudência ou regulamentação complementar aplicável.",
            ]
        )
    return "\n".join(lines)


def _extract_prompt_context_value(context: str, label: str) -> str:
    prefix = f"{label}:"
    for line in (context or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _build_pro_case_guidance(question: str) -> str:
    if "Contexto profissional do caso associado:" not in (question or ""):
        return ""

    client = _extract_prompt_context_value(question, "Cliente") or "o cliente"
    title = _extract_prompt_context_value(question, "Título") or "caso associado"
    branch = _extract_prompt_context_value(question, "Área jurídica") or "não definida"
    registered_branch = _extract_prompt_context_value(
        question, "Área jurídica registada pelo profissional"
    )
    opposing_party = _extract_prompt_context_value(question, "Parte contrária")
    summary = _extract_prompt_context_value(question, "Resumo interno")
    deadlines = _extract_prompt_context_value(question, "Prazos registados")
    tasks = _extract_prompt_context_value(question, "Tarefas abertas")
    documents = _extract_prompt_context_value(question, "Documentos ligados")
    gaps = _extract_prompt_context_value(question, "Lacunas do dossiê")

    mismatch = ""
    if registered_branch and registered_branch != branch:
        mismatch = (
            f"- A área registada pelo utilizador é '{registered_branch}', mas os factos indicam '{branch}'. "
            "Usa a área inferida para a pesquisa e alerta discretamente que a classificação do caso deve ser revista.\n"
        )

    return (
        "=== MODO PRO: ANALISE DE CASO CONCRETO ===\n"
        f"Cliente em destaque: {client}\n"
        f"Caso: {title}\n"
        f"Área jurídica efectiva: {branch}\n"
        f"Parte contrária: {opposing_party or 'não definida'}\n"
        f"Resumo factual do profissional: {summary or 'não preenchido'}\n"
        f"Documentos do dossiê: {documents or 'não informados'}\n"
        f"Prazos do dossiê: {deadlines or 'não informados'}\n"
        f"Tarefas abertas: {tasks or 'não informadas'}\n"
        f"Lacunas identificadas: {gaps or 'não informadas'}\n"
        f"{mismatch}"
        "Regras especificas:\n"
        "- Trata a pergunta como trabalho profissional sobre este dossiê, não como pergunta genérica.\n"
        "- Usa o nome do cliente quando falares de estratégia, riscos ou próximos passos.\n"
        "- Se o utilizador perguntar 'como advogado', responde com estratégia jurídica, meios de defesa, prova, riscos e próximos actos.\n"
        "- Se for follow-up, não repitas a resposta anterior; acrescenta análise nova e mais operacional.\n"
        "- Não devolvas texto de aparência template/mock. Cada secção deve depender dos factos do resumo, da área efectiva e das fontes recuperadas.\n"
        "- Quando a pergunta pedir análise/estratégia/parecer, usa uma estrutura profissional curta: **Leitura do caso**, **Questão jurídica**, **Base aplicável**, **Aplicação ao caso**, **Prova/documentos**, **Riscos**, **Próximos actos em Angola**.\n"
        "- Quando a pergunta for directa, responde primeiro em 2-4 linhas e só depois acrescenta os pontos profissionais necessários.\n"
        "- A secção **Prova/documentos** deve dizer o que já existe no dossiê e o que falta pedir ao cliente em Angola.\n"
        "- A secção **Próximos actos em Angola** deve ser operacional: onde agir, que documento preparar, que prazo controlar e que cautela tomar.\n"
        "- Se houver jurisprudência recuperada, cria secção curta **Jurisprudência/entendimento** com a utilidade prática; se não houver, não inventes acórdãos.\n"
        "- Se a resposta puder gerar minuta, checklist ou cálculo, sugere isso como próximo passo, mas não inventes valores sem dados.\n"
        "- Se o caso envolver crime/acusação, separa: tipicidade, ilicitude, prova, garantias processuais e linha de defesa.\n"
        "- Se o resumo mencionar uma qualificação informal do cliente, da polícia ou da acusação, não a trates como tipo penal autónomo salvo se uma fonte recuperada trouxer esse crime. Enquadra tecnicamente a alegação apenas nos tipos legais efectivamente recuperados e assinala quando a palavra usada no caso não aparece como tipo autónomo nas fontes.\n"
        "- Se houver base constitucional conexa, usa-a apenas como argumento complementar quando existir fonte recuperada; não substitui a análise penal/processual.\n"
        "- Se as fontes recuperadas forem insuficientes para um ponto essencial, diz exactamente que ponto falta e que documento/artigo deve ser confirmado.\n"
        "- Nunca cites número de artigo, lei ou acórdão que não esteja confirmado na whitelist/fontes recuperadas. Se conheceres uma lei potencialmente relevante mas ela não estiver no contexto, menciona apenas que deve ser confirmada, sem número de artigo e sem a tratar como fundamento.\n"
        "- Mantém tom angolano profissional: evita prometer resultados, distingue orientação jurídica de acto forense, e recomenda validação antes de protocolar peças.\n"
        "=== FIM DO MODO PRO ===\n\n"
    )


def _is_public_interest_journalism_question(question: str) -> bool:
    normalized = normalize_legal_text(question or "").casefold()
    media_terms = (
        "jornalista",
        "imprensa",
        "noticia",
        "notícia",
        "comunicacao social",
        "comunicação social",
        "publicacao",
        "publicação",
        "denuncia",
        "denúncia",
    )
    context_terms = (
        "corrupcao",
        "corrupção",
        "funcionario publico",
        "funcionário público",
        "funcionarios publicos",
        "funcionários públicos",
        "documentos vazados",
        "documento vazado",
        "vazamento",
        "dados pessoais",
        "protecção de dados",
        "proteccao de dados",
        "proteção de dados",
        "protecao de dados",
        "segredo",
    )
    asks_multi_branch = (
        "penal" in normalized
        and "constitucional" in normalized
        and "administrativ" in normalized
        and "dados" in normalized
    )
    return (
        any(term in normalized for term in media_terms)
        and any(term in normalized for term in context_terms)
    ) or asks_multi_branch


def _build_public_interest_journalism_guidance(
    question: str, classification: LegalClassification
) -> str:
    if not _is_public_interest_journalism_question(question):
        return ""
    return (
        "=== RACIOCINIO PARA JORNALISMO, CORRUPCAO, DOCUMENTOS VAZADOS E DADOS ===\n"
        "Esta classe de caso exige analise multidisciplinar, nao apenas identificacao de crimes.\n"
        "Formato obrigatorio: responde com uma conclusao curta e depois quatro bullets/mini-seccoes: Penal, Constitucional, Administrativo e Proteccao de dados. Acrescenta uma mini-seccao 'Posicao do jornalista' se a pergunta envolver jornalista, fonte ou documentos vazados. Se algum destes blocos faltar, a resposta esta incompleta.\n"
        "Estrutura recomendada quando o contexto permitir:\n"
        "- Penal: distingue crimes possivelmente praticados pelos funcionarios denunciados, pelo autor do vazamento e pelo jornalista. Se a pergunta fala em corrupcao de funcionarios publicos e o contexto tiver corrupcao activa e corrupcao passiva, estes sao os primeiros tipos a testar; recebimento indevido de vantagem e complementar quando ha vantagem sem acto concreto; peculato e participacao economica em negocio so entram se os factos sugerirem desvio, apropriacao, vantagem patrimonial ou negocio publico. Para o jornalista, evita conclusao automatica: verifica se apenas recebeu documentos de fonte, se participou na obtencao ilicita, se instigou o vazamento, se sabia de segredo legal e se havia interesse publico relevante.\n"
        "- Constitucional: pondera liberdade de expressao, liberdade de imprensa e direito de informar contra bom nome, honra, privacidade/intimidade, habeas data/dados e segredos protegidos. Se o contexto tiver artigos constitucionais sobre imprensa, expressao, privacidade/dados ou restricoes, usa-os em conjunto. Aplica necessidade, proporcionalidade e razoabilidade quando houver artigos confirmados. Nao resumas esta parte a uma frase generica.\n"
        "- Administrativo: indica consequencias institucionais possiveis para funcionarios publicos, como averiguacao, processo disciplinar, inspeccao, auditoria, suspensao preventiva ou responsabilizacao financeira, assinalando quando faltar diploma disciplinar especifico no contexto.\n"
        "- Proteccao de dados: distingue dados pessoais comuns (nome, BI, contacto, morada) de dados sensiveis ou de maior risco (saude, biometria, dados bancarios, vida familiar, menores), quando os factos permitirem; pergunta se era necessario divulgar esses dados, se podiam ser anonimizados e se a divulgacao foi proporcional ao interesse publico. Se nao houver lei ordinaria de dados no contexto, diz isso como lacuna sem inventar artigo e sem afirmar violacao de uma lei especifica nao recuperada.\n"
        "Numa pergunta sobre 'sob quais ramos', o objectivo principal e o mapa de enquadramento e a ponderacao juridica; nao transformes a resposta numa lista de crimes apenas.\n"
        "Nao afirmes que o interesse publico 'prevalece geralmente' de forma absoluta; formula como teste de necessidade, proporcionalidade, relevancia publica e minimizacao de dados.\n"
        "Evita citar corrupcao no comercio internacional salvo se os factos mencionarem comercio internacional. Evita tratar todo documento vazado como crime do jornalista.\n"
        "=== FIM DO RACIOCINIO ESPECIFICO ===\n\n"
    )


# ── Suggested Actions — contextual fallback per legal branch ──────────────

_ACTION_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "penal": [
        {"icon": "legal", "label": "Ver Penas Aplicáveis", "prompt_template": "Quais são as penas concretas previstas no Código Penal para este crime, incluindo agravantes e atenuantes?"},
        {"icon": "draft", "label": "Gerar Queixa-Crime", "prompt_template": "Redige o texto de uma queixa-crime para apresentar às autoridades sobre este caso."},
        {"icon": "research", "label": "Jurisprudência Relacionada", "prompt_template": "Mostra decisões recentes dos tribunais angolanos sobre este tipo de crime."},
    ],
    "civil": [
        {"icon": "draft", "label": "Redigir Petição", "prompt_template": "Redige uma petição inicial com base nesta informação para dar entrada no tribunal competente."},
        {"icon": "legal", "label": "Ver Base Legal Completa", "prompt_template": "Mostra os artigos completos do Código Civil que fundamentam esta resposta."},
        {"icon": "calculation", "label": "Prazos e Custas", "prompt_template": "Quais os prazos legais e custas judiciais aplicáveis a este tipo de acção?"},
    ],
    "laboral": [
        {"icon": "draft", "label": "Gerar Comunicação", "prompt_template": "Redige a comunicação formal para o trabalhador com base na Lei Geral do Trabalho."},
        {"icon": "calculation", "label": "Calcular Indemnização", "prompt_template": "Calcula a indemnização devida neste caso com base na LGT."},
        {"icon": "legal", "label": "Ver Base Legal", "prompt_template": "Mostra os artigos específicos da Lei Geral do Trabalho aplicáveis a este caso."},
    ],
    "tributario": [
        {"icon": "calculation", "label": "Calcular Imposto", "prompt_template": "Calcula o valor do imposto devido com base na legislação fiscal angolana."},
        {"icon": "legal", "label": "Ver Prazos Fiscais", "prompt_template": "Quais os prazos de pagamento e entrega de declarações para este imposto?"},
        {"icon": "checklist", "label": "Conformidade Fiscal", "prompt_template": "Verifica a conformidade desta operação com o Código do Imposto Industrial."},
    ],
    "propriedade": [
        {"icon": "legal", "label": "Ver Direitos Reais", "prompt_template": "Mostra os artigos do Código Civil sobre direitos de propriedade."},
        {"icon": "checklist", "label": "Registo Predial", "prompt_template": "Quais os passos para registar esta propriedade na Conservatória?"},
        {"icon": "calculation", "label": "Calcular Impostos", "prompt_template": "Quais os impostos e taxas aplicáveis a esta transacção imobiliária?"},
    ],
    "sucessorio": [
        {"icon": "checklist", "label": "Passos da Habilitação", "prompt_template": "Quais os passos legais para a habilitação de herdeiros em Angola?"},
        {"icon": "calculation", "label": "Calcular Partilha", "prompt_template": "Como se calcula a partilha dos bens entre os herdeiros legítimos?"},
        {"icon": "legal", "label": "Ver Base Legal", "prompt_template": "Mostra os artigos do Código Civil sobre sucessões."},
    ],
    "familia": [
        {"icon": "draft", "label": "Gerar Requerimento", "prompt_template": "Redige o requerimento para dar entrada no Tribunal da Família."},
        {"icon": "calculation", "label": "Simular Pensão", "prompt_template": "Faz uma simulação do valor da pensão de alimentos com base nos rendimentos."},
        {"icon": "checklist", "label": "Lista de Documentos", "prompt_template": "Gera a lista completa de documentos necessários para este processo."},
    ],
    "comercial": [
        {"icon": "draft", "label": "Redigir Aditamento", "prompt_template": "Redige um aditamento contratual para corrigir as cláusulas problemáticas identificadas."},
        {"icon": "legal", "label": "Verificar Conformidade", "prompt_template": "Verifica se estas cláusulas estão em conformidade com a legislação angolana."},
        {"icon": "notice", "label": "Notificação Extrajudicial", "prompt_template": "Gera uma notificação extrajudicial para a contraparte."},
    ],
    "administrativo": [
        {"icon": "draft", "label": "Redigir Recurso", "prompt_template": "Redige um recurso hierárquico ou contencioso com base nesta análise."},
        {"icon": "legal", "label": "Ver Prazos Legais", "prompt_template": "Quais os prazos para impugnar este acto administrativo?"},
        {"icon": "checklist", "label": "Checklist de Requisitos", "prompt_template": "Gera uma checklist dos requisitos legais para este procedimento administrativo."},
    ],
    "constitucional": [
        {"icon": "legal", "label": "Ver Fundamentos", "prompt_template": "Mostra os artigos da Constituição e legislação conexa que fundamentam este direito."},
        {"icon": "draft", "label": "Gerar Petição de Recurso", "prompt_template": "Redige um recurso para o Tribunal Constitucional com base nestes fundamentos."},
        {"icon": "research", "label": "Jurisprudência Constitucional", "prompt_template": "Mostra decisões do Tribunal Constitucional sobre esta matéria."},
    ],
}

_DEFAULT_ACTIONS: list[dict[str, str]] = [
    {"icon": "legal", "label": "Ver Base Legal", "prompt_template": "Mostra os artigos e diplomas que fundamentam esta resposta."},
    {"icon": "research", "label": "Aprofundar o Tema", "prompt_template": "Dá-me mais detalhes e exemplos práticos sobre este assunto."},
]

_ACTION_ICON_ALIASES = {
    "⚖️": "legal",
    "⚖": "legal",
    "📝": "draft",
    "📋": "checklist",
    "📊": "calculation",
    "🔍": "research",
    "📨": "notice",
}


def _normalize_action_icon(value: str | None, label: str = "") -> str:
    cleaned = (value or "").strip()
    if cleaned in {"legal", "draft", "research", "calculation", "checklist", "notice", "document"}:
        return cleaned
    if cleaned in _ACTION_ICON_ALIASES:
        return _ACTION_ICON_ALIASES[cleaned]
    lowered = label.casefold()
    if any(term in lowered for term in ("calcular", "custas", "imposto", "pensão", "partilha")):
        return "calculation"
    if any(term in lowered for term in ("jurisprud", "aprofundar", "relacionad")):
        return "research"
    if any(term in lowered for term in ("redigir", "gerar", "petição", "queixa", "comunicação", "requerimento", "recurso")):
        return "draft"
    if any(term in lowered for term in ("checklist", "lista", "conformidade", "registo")):
        return "checklist"
    if any(term in lowered for term in ("notificação", "extrajudicial")):
        return "notice"
    return "legal"


def _build_fallback_actions(
    branch: str,
    audience: str,
    has_active_document: bool = False,
    has_cited_articles: bool = False,
    has_jurisprudence: bool = False,
) -> list[dict[str, str]]:
    templates = _ACTION_TEMPLATES.get(branch, _DEFAULT_ACTIONS)
    actions = []
    for tpl in templates[:4]:
        prompt = tpl["prompt_template"]
        if audience == "leigo":
            prompt = prompt.replace(
                "Redige o texto de uma queixa-crime para apresentar às autoridades sobre este caso.",
                "Ajuda-me a escrever uma queixa simples para a polícia sobre este caso.",
            )
            prompt = prompt.replace(
                "Redige uma petição inicial com base nesta informação para dar entrada no tribunal competente.",
                "Ajuda-me a escrever o documento para dar entrada no tribunal.",
            )
        actions.append({"icon": _normalize_action_icon(tpl["icon"], tpl["label"]), "label": tpl["label"], "prompt": prompt})
    if has_active_document:
        actions.insert(
            0,
            {"icon": "document", "label": "Agir sobre o Documento", "prompt": "Com base na análise anterior, o que devo corrigir ou melhorar neste documento?"},
        )
    if has_cited_articles and not any(a["label"].startswith("Ver ") for a in actions):
        actions.append(
            {"icon": "legal", "label": "Ver Artigos Citados", "prompt": "Mostra o texto completo dos artigos citados nesta resposta."},
        )
    if has_jurisprudence:
        actions.append(
            {"icon": "research", "label": "Mais Jurisprudência", "prompt": "Há mais decisões judiciais sobre este tema?"},
        )
    return actions[:4]


def _build_ai_prefs_guidance(prefs: dict | None) -> str:
    """Build prompt guidance string from user AI preferences."""
    if isinstance(prefs, str):
        try:
            prefs = json.loads(prefs)
        except Exception:
            prefs = {}
    if not prefs or not any(prefs.values()):
        return ""
    tone_map = {
        "formal": "Usa linguagem formal e juridica.",
        "didatico": "Explica de forma didatica e pedagogica.",
        "simples": "Responde com linguagem simples e direta.",
    }
    detail_map = {
        "breve": "Respostas curtas e objectivas.",
        "normal": "Detalhe equilibrado.",
        "detalhado": "Respostas detalhadas e exaustivas.",
    }
    style_map = {
        "juridico": "Usa terminologia juridica tecnica.",
        "acessivel": "Usa linguagem acessivel para nao-juristas.",
    }
    audience_map = {
        "auto": "Adapta automaticamente ao teor da pergunta.",
        "leigo": "Assume que o utilizador nao tem formacao juridica; explica termos tecnicos.",
        "tecnico": "Assume que o utilizador e jurista/advogado; privilegia rigor tecnico e concisao.",
    }
    format_map = {
        "paragrafos": "Usa paragrafos estruturados.",
        "topicos": "Usa topicos e listas.",
        "auto": "Formato automatico.",
    }

    lines = [
        "PREFERENCIAS DO UTILIZADOR (ajustam tom e formato, mas NAO podem reduzir rigor, cobertura juridica ou regras especificas de seguranca):"
    ]
    t = prefs.get("tone", "formal")
    lines.append(f"- Tom: {t}. {tone_map.get(t, '')}")
    if prefs.get("detail_level"):
        d = prefs["detail_level"]
        lines.append(f"- Nivel de detalhe: {d}. {detail_map.get(d, '')}")
    if prefs.get("audience"):
        a = prefs["audience"]
        lines.append(f"- Audiencia preferida: {a}. {audience_map.get(a, '')}")
    if prefs.get("language_style"):
        s = prefs["language_style"]
        lines.append(f"- Estilo: {s}. {style_map.get(s, '')}")
    f = prefs.get("response_format", "auto")
    lines.append(f"- Formato: {f}. {format_map.get(f, '')}")
    return "\n".join(lines) + "\n\n"


def _build_response_length_guidance(
    audience: str,
    prefs: dict | None,
    professional_context: bool = False,
    detailed_request: bool = False,
) -> str:
    detail_level = (prefs or {}).get("detail_level", "normal")
    response_format = (prefs or {}).get("response_format", "auto")

    if professional_context:
        if detail_level == "breve":
            return (
                "LIMITE DE TAMANHO: Mesmo em contexto profissional, responde de forma executiva: "
                "120 a 180 palavras, ate 4 bullets. Se precisar de estrategia longa, oferece continuar.\n\n"
            )
        return (
            "LIMITE DE TAMANHO: Contexto profissional/caso. Resposta tecnica, mas controlada: "
            "180 a 320 palavras, maximo 5 secoes curtas e ate 8 bullets no total. "
            "Nao escrevas parecer longo salvo se o utilizador pedir expressamente.\n\n"
        )

    if detailed_request:
        return (
            "LIMITE DE TAMANHO: O utilizador pediu detalhe. Responde com profundidade moderada: "
            "260 a 420 palavras, maximo 6 secoes curtas e ate 12 bullets no total. "
            "Cobre todos os pontos perguntados antes de encerrar; nao transformes em aula longa.\n\n"
        )

    if detail_level == "detalhado":
        return (
            "LIMITE DE TAMANHO: Resposta detalhada, mas controlada: maximo 220 palavras, ate 3 secoes e ate 6 bullets no total. "
            "Nao repitas a mesma base legal em paragrafos diferentes.\n\n"
        )

    if detail_level == "breve" or audience == "leigo":
        format_rule = (
            "Usa 3 a 5 bullets curtos."
            if response_format == "topicos"
            else "Usa 2 paragrafos curtos."
        )
        return (
            "LIMITE DE TAMANHO: Resposta muito curta para utilizador final em Angola: 55 a 95 palavras, maximo 2 secoes. "
            f"{format_rule} Inclui so a resposta essencial, 1 base legal se necessaria e uma orientacao pratica.\n\n"
        )

    if audience == "tecnico":
        return (
            "LIMITE DE TAMANHO: Resposta tecnica concisa: 90 a 140 palavras, maximo 3 secoes e ate 4 bullets. "
            "Prioriza requisitos, consequencias juridicas e artigos nucleares; evita explicacoes pedagogicas longas e enumerações exaustivas.\n\n"
        )

    return (
        "LIMITE DE TAMANHO: Resposta simples e directa: 65 a 110 palavras, maximo 2 secoes e ate 4 bullets no total. "
        "Explica o essencial sem alongar; termina oferecendo aprofundar se o utilizador quiser.\n\n"
    )


def _is_user_document_chunk(chunk: Any) -> bool:
    metadata = getattr(chunk, "metadata", None) or {}
    source_scope = getattr(chunk, "source_scope", "")
    return bool(
        source_scope == "user_document"
        or metadata.get("source_scope") == "user_document"
        or metadata.get("document_id")
        or getattr(chunk, "document_id", None)
    )


def _is_document_summary_request(
    question: str, *, professional_context: bool, has_user_document: bool
) -> bool:
    if not has_user_document:
        return False
    normalized = question.casefold()
    has_summary_intent = bool(
        re.search(r"\b(resum|sumariz|pontos?\s+chave|simplif|sintetiz)\b", normalized)
    )
    if not has_summary_intent:
        return False
    has_document_target = bool(
        re.search(
            r"\b(documento|pdf|anexo|ficheiro|arquivo|pe[çc]a|contrato|dossi[eê]|memorial|memorando)\b",
            normalized,
        )
    )
    if professional_context:
        return has_document_target
    return has_document_target or not re.search(
        r"\b(resposta|cliente|linguagem|explicar|em\s+5\s+pontos)\b", normalized
    )


def _source_line(source) -> str:
    article = f", art. {source.article_number}" if source.article_number else ""
    page = f", pag. {source.page}" if source.page else ""
    scope_tag = ""
    if getattr(source, "source_scope", "") == "user_upload":
        scope_tag = " (Documento do Utilizador)"
    elif getattr(source, "source_kind", "") == "jurisprudence":
        scope_tag = " (Jurisprudencia)"
    return f"- {source.title}{article}{page}{scope_tag}"


def _article_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        token.strip().replace(".", "")
        for token in re.split(r"[,;/]", value)
        if token.strip()
    ]


def _context_articles(retrieved_chunks: Iterable) -> set[str]:
    articles: set[str] = set()
    for chunk in retrieved_chunks:
        metadata = chunk.metadata or {}
        article_main = metadata.get("article_main")
        if article_main:
            articles.update(_article_tokens(str(article_main)))
        refs = metadata.get("article_references") or []
        for item in refs:
            articles.update(_article_tokens(str(item)))
        if chunk.article_number:
            articles.update(_article_tokens(chunk.article_number))
        for match in re.finditer(
            r"(?:art|artigo|artigos)\s*(\d+[.]?\d*)", chunk.text or "", re.IGNORECASE
        ):
            articles.add(match.group(1).replace(".", ""))
    return {item for item in articles if item}


def _normalized_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"\w+", text or "", re.UNICODE)
        if len(token) >= 4
    }


def _question_anchor_tokens(question: str) -> set[str]:
    stopwords = {
        "como",
        "qual",
        "quais",
        "para",
        "sobre",
        "entre",
        "agora",
        "mesmo",
        "caso",
        "explica",
        "explique",
        "forma",
        "quero",
        "posso",
        "devo",
        "seria",
        "isso",
        "isto",
        "dessa",
        "desse",
    }
    return {token for token in _normalized_tokens(question) if token not in stopwords}


class LegalComposer:
    def create_stream_extractor(self) -> "RichContentStreamExtractor":
        return RichContentStreamExtractor()

    def build_prompt(
        self,
        question: str,
        classification: LegalClassification,
        retrieved_chunks: list,
        conversation_history: list[str] | None = None,
        ai_preferences: dict | None = None,
        streaming: bool = False,
        professional_context: bool = False,
    ) -> str:
        context_blocks: list[str] = []
        prefs = ai_preferences or {}
        if isinstance(prefs, str):
            try:
                prefs = json.loads(prefs)
            except Exception:
                prefs = {}
        allowed_articles: list[str] = []
        has_user_document_context = any(_is_user_document_chunk(chunk) for chunk in retrieved_chunks)
        is_public_interest_journalism = _is_public_interest_journalism_question(
            question
        )
        document_summary_requested = _is_document_summary_request(
            question,
            professional_context=professional_context,
            has_user_document=has_user_document_context,
        )
        detailed_request = bool(
            re.search(
                r"\b(detalh|aprofund|fundament|parecer|analise tecnica|análise técnica|requisitos|condi[cç][oõ]es|validade|v[aá]lido|direitos|il[ií]cito|l[ií]cito|artigos aplic[aá]veis|requisitos completos|minuta|estrategia|estratégia|quais?\s+ramos|sob\s+quais?\s+ramos|multi[-\s]?disciplinar|protec[çc][aã]o\s+de\s+dados|prote[çc][aã]o\s+de\s+dados)\b",
                question.casefold(),
            )
            or is_public_interest_journalism
        )

        # Separate and order: user docs first, then official
        user_chunks = [c for c in retrieved_chunks if c.source_scope != "official"]
        jurisprudence_chunks = [
            c
            for c in retrieved_chunks
            if c.source_scope == "official"
            and (c.metadata or {}).get("document_kind") == "jurisprudence"
        ]
        official_chunks = [
            c
            for c in retrieved_chunks
            if c.source_scope == "official"
            and (c.metadata or {}).get("document_kind") != "jurisprudence"
        ]
        total_slots = 12 if detailed_request or user_chunks or is_public_interest_journalism else 8
        ordered = (user_chunks + jurisprudence_chunks + official_chunks)[:total_slots]

        # Build inventory of all available articles and diplomas
        inventory_lines: list[str] = []
        seen_articles: set[str] = set()
        for chunk in ordered:
            meta = chunk.metadata or {}
            art_main = meta.get("article_main") or chunk.article_number or ""
            art_ctx = chunk.article_number or ""
            title = (chunk.title or "Desconhecido")[:60]
            # Get first meaningful text snippet as topic hint
            text_snippet = (chunk.text or "")[:80].replace("\n", " ").strip()
            if art_main and art_main != "N/D":
                for art_num in art_main.split(","):
                    art_num = art_num.strip().replace(".", "")
                    if art_num and art_num not in seen_articles:
                        seen_articles.add(art_num)
                        inventory_lines.append(f"  Art. {art_num} ({title}): {text_snippet}")
            elif art_ctx and art_ctx != "N/D":
                for art_num in art_ctx.split(","):
                    art_num = art_num.strip().replace(".", "")
                    if art_num and art_num not in seen_articles:
                        seen_articles.add(art_num)
                        inventory_lines.append(f"  Art. {art_num} ({title}): {text_snippet}")
        inventory_text = "\n".join(inventory_lines) if inventory_lines else "  (nenhum artigo numerado disponivel)"

        for chunk in ordered:
            meta = chunk.metadata or {}
            branch = meta.get("legal_branch", "indeterminado")
            doc_kind = meta.get("document_kind", "")
            is_juris = doc_kind == "jurisprudence"
            source_type = (
                "documento do utilizador"
                if chunk.source_scope == "user_upload"
                else "jurisprudencia"
                if is_juris
                else "fonte oficial"
            )
            article_main = (
                (chunk.metadata or {}).get("article_main")
                or chunk.article_number
                or "N/D"
            )
            if article_main and article_main != "N/D":
                allowed_articles.append(
                    str(article_main).split(",")[0].strip().replace(".", "")
                )
            article_label = f"Art. {article_main}" if article_main and article_main != 'N/D' else f"Ref: {chunk.title[:40]}"
            context_blocks.append(
                f"### [{article_label}] — {chunk.title} (p. {chunk.page or 'N/D'})\n"
                f"Tipo: {source_type} | Ramo: {branch} | Artigos: {chunk.article_number or 'N/D'}"
                f"{' | [PODE CONTER MULTIPLOS ARTIGOS]' if chunk.source_scope == 'user_upload' else ''}\n"
                f"{chunk.text[:1500]}"
            )

        whitelist = sorted({item for item in allowed_articles if item})
        whitelist_text = (
            ", ".join(whitelist)
            if whitelist
            else "nenhum artigo confirmado para citacao especifica"
        )
        history = (
            "\n".join(conversation_history[-6:])
            if conversation_history
            else "Sem historico relevante."
        )

        audience = classification.audience
        question_norm = question.casefold()
        is_public_asset_multi_branch = any(
            marker in question_norm
            for marker in (
                "carro do estado",
                "viatura do estado",
                "veículo do estado",
                "veiculo do estado",
                "bem público",
                "bem publico",
                "património público",
                "patrimonio publico",
                "uso privado",
                "funcionário público",
                "funcionario publico",
            )
        ) and any(
            marker in question_norm
            for marker in (
                "disciplinar",
                "civil",
                "penal",
                "administrativ",
                "responsabilidade",
                "acidente",
            )
        )
        # Override audience from user preferences BEFORE building guidance
        prefs_guidance = _build_ai_prefs_guidance(prefs)
        if prefs.get("audience") in {"leigo", "tecnico"}:
            audience = prefs["audience"]
        elif prefs.get("tone"):
            if prefs["tone"] == "simples":
                audience = "leigo"
            elif prefs["tone"] == "didatico":
                audience = "didatico"
        length_guidance = _build_response_length_guidance(
            audience,
            prefs,
            professional_context=professional_context,
            detailed_request=detailed_request,
        )
        if is_public_interest_journalism:
            length_guidance = (
                "LIMITE DE TAMANHO: Resposta multidisciplinar controlada: "
                "220 a 360 palavras, maximo 5 secoes curtas. "
                "Nao reduzas a analise constitucional, administrativa ou de dados a frases genericas.\n\n"
            )
        elif is_public_asset_multi_branch:
            length_guidance = (
                "LIMITE DE TAMANHO: Resposta multi-responsabilidade controlada: "
                "180 a 320 palavras, maximo 5 secoes curtas. "
                "Mesmo com preferencia breve, cobre penal, civil, administrativo/disciplinar e factos a apurar.\n\n"
            )
        audience_guidance = ""
        if audience == "leigo":
            audience_guidance = (
                "A TUA AUDIENCIA E UM CIDADAO COMUM (LEIGO).\n"
                "- Traduz a lei para linguagem do dia-a-dia, sem juridiques complicado.\n"
                "- Se pratico: diz exactamente o que fazer e a quem se dirigir.\n"
                "- **NUNCA uses abertura 'Caro Cidadão' ou 'Prezado' ou 'Caro utilizador'. Variedade natural.**\n"
                "- Se base legal insuficiente, inclui nota de cautela simples.\n"
                "- Pergunta simples = resposta directa em poucas linhas; pergunta complexa = explica so o essencial e indica proximo passo.\n"
                "- O utilizador angolano comum tende a abandonar respostas longas: evita aulas, histórico, listas grandes e linguagem pesada.\n"
                "- **SE PEDIR 'RESUMO' ou 'RESUMA': condensa a resposta anterior a 1 paragrafo curto com apenas os pontos essenciais, sem repetir exemplos.**\n"
            )
        elif audience == "tecnico":
            audience_guidance = (
                "A TUA AUDIENCIA E UM PROFISSIONAL DO DIREITO (ADVOGADO/JURISTA).\n"
                "- Tom estritamente tecnico, objectivo e rigoroso.\n"
                "- Aborda prazos, requisitos de validade, excepcoes e interpretacao.\n"
                "- Indica meios processuais ou de reaccao legais aplicaveis.\n"
                "- NUNCA uses abertura como 'Caro' ou 'Prezado'. Responde directo ao tema.\n"
                "- Pergunta directa = resposta directa. So detalha se o utilizador pedir expressamente.\n"
                "- SE algum artigo ou diploma nao estiver totalmente confirmado no contexto, menciona essa limitacao tecnicamente.\n"
            )
        else:
            audience_guidance = (
                "A TUA AUDIENCIA E UM PUBLICO MISTO OU ESTUDANTE.\n"
                "- Tom didactico, claro e estruturado.\n"
                "- Combina explicacao facil com distincoes tecnicas necessarias.\n"
                "- NUNCA uses abertura 'Caro Cidadão' ou 'Prezado'. Responde directo.\n"
                "- Simples = breve. So detalha se pedido.\n"
                "- SE a base legal for parcial, inclui nota prudencial adequada.\n"
            )
        follow_up_guidance = ""
        if classification.specificity == "follow_up" and conversation_history:
            follow_up_guidance = (
                "CONTEXTO DE FOLLOW-UP:\n"
                "O utilizador esta a dar seguimento a uma conversa anterior. A pergunta actual e uma continuacao do mesmo tema.\n"
                "NAO mudes de assunto. Aprofunda e complementa a resposta anterior com mais detalhes, exemplos praticos e artigos adicionais do mesmo diploma.\n"
                "Evita repetir exactamente o que ja foi dito no historico. Acrescenta valor novo.\n\n"
            )
        if classification.is_transformation and classification.transformation_type == "simplify":
            follow_up_guidance += (
                "PEDIDO DE SIMPLIFICACAO:\n"
                "O utilizador nao percebeu a resposta anterior. Reescreve para leigo, em poucas linhas, com exemplo pratico.\n"
                "Nao repitas a estrutura tecnica anterior. Comeca por 'Em termos simples:' e termina com uma cautela pratica.\n\n"
            )


        streaming_guidance = (
            "\n\n=== MODO STREAMING PARA INTERFACE ===\n"
            "Responde em Markdown directo, sem JSON, sem bloco de codigo e sem chaves do tipo rich_content.\n"
            "Mantem a mesma estrutura visual: resposta curta inicial, subtitulos ### so quando ajudarem e listas curtas.\n"
            "Nao mostres listas finais de fontes; a interface apresenta as fontes automaticamente.\n"
            "=== FIM DO MODO STREAMING ==="
            if streaming
            else ""
        )
        pro_case_guidance = _build_pro_case_guidance(question)
        journalism_guidance = _build_public_interest_journalism_guidance(
            question, classification
        )
        labor_dismissal_guidance = ""
        if (
            classification.main_branch == "laboral"
            and "desped" in question_norm
            and any(
                marker in question_norm
                for marker in (
                    "requisitos",
                    "válido",
                    "valido",
                    "direitos",
                    "lícito",
                    "licito",
                    "ilícito",
                    "ilicito",
                    "posto de trabalho",
                    "causas objectivas",
                    "causas objetivas",
                )
            )
        ):
            labor_dismissal_guidance = (
                "=== GUIA DE COBERTURA — DESPEDIMENTO LABORAL ===\n"
                "Quando a pergunta pedir requisitos, validade, direitos, despedimento licito/ilicito ou artigos aplicaveis:\n"
                "1. Comeca por enquadrar a modalidade juridica exacta se o contexto permitir.\n"
                "2. Separa requisitos materiais, procedimento formal, direitos no despedimento licito e consequencias do despedimento ilicito.\n"
                "3. Verifica no inventario artigos sobre fundamentos, procedimento, aviso previo, criterios de preferencia, compensacao, nulidade/ilicitude, reintegracao/indemnizacao e prazo de impugnacao.\n"
                "4. Nao omitas condicoes, limites ou excepcoes expressas no artigo. Se um direito so existe em certas circunstancias, menciona essa condicao.\n"
                "5. Nao digas que a compensacao ou um ponto 'nao esta detalhado' se houver artigo confirmado no contexto sobre calculo, compensacao, indemnizacao ou antiguidade.\n"
                "6. Mantem linguagem simples, mas completa; evita resposta minimalista quando o utilizador pediu varios subpontos.\n"
                "=== FIM DO GUIA LABORAL ===\n\n"
            )
        multi_branch_reasoning_guidance = ""
        if (
            classification.main_branch == "misto"
            or classification.needs_multi_branch_handling
            or classification.specificity == "comparacao_multi_ramo"
        ):
            multi_branch_reasoning_guidance = (
                "=== GUIA DE RACIOCINIO MULTI-RAMO ===\n"
                "Quando a pergunta envolver varios ramos, primeiro faz o enquadramento juridico dos factos e so depois escolhe os artigos.\n"
                "Separa a resposta por ramos relevantes: penal, civil, administrativo/disciplinar, constitucional ou dados pessoais, conforme o contexto recuperado.\n"
                "Nao escolhas artigos apenas por coincidencia de palavras. Explica a condicao de aplicacao de cada artigo e afasta o artigo se o facto nao preencher o seu pressuposto.\n"
                "Quando houver direitos fundamentais em conflito, identifica os interesses em tensao e aplica proporcionalidade/interesse publico se o contexto permitir.\n"
                "Se uma responsabilidade depender de factos ainda incertos, formula perguntas de verificacao em vez de concluir de forma absoluta.\n"
                "=== FIM DO GUIA MULTI-RAMO ===\n\n"
            )
        public_asset_guidance = ""
        if is_public_asset_multi_branch:
            public_asset_guidance = (
                "=== GUIA DE COBERTURA — BEM PUBLICO / FUNCIONARIO PUBLICO ===\n"
                "Esta pergunta exige cobertura minima por frentes, mesmo que as preferencias do utilizador pecam resposta breve.\n"
                "Nao respondas apenas com um crime. Se o contexto contiver os artigos correspondentes, distingue:\n"
                "- Penal: peculato de uso quando ha uso temporario de coisa publica para fim diferente; peculato se houver apropriacao; abuso de poder se houver abuso funcional para beneficio/dano. Se os Arts. 363, 362 e 374 estiverem na whitelist, cita-os expressamente e explica a diferenca em frases curtas.\n"
                "- Civil/constitucional: danos ao Estado e a terceiros, incluindo eventual responsabilidade do Estado por actos dos seus agentes e direito de regresso quando sustentado no contexto. Se os Arts. 75 da Constituicao e 483 do Codigo Civil estiverem na whitelist, cita-os expressamente.\n"
                "- Administrativo/disciplinar: averiguacao, processo disciplinar, regras internas de uso do bem e recolha de prova.\n"
                "Obrigatorio: cada frente juridica tratada deve ter pelo menos uma base legal citada, se houver fonte confirmada na whitelist. Nao basta citar apenas o Art. 363.\n"
                "Formula conclusoes condicionais quando faltarem factos: autorizacao de uso, ligacao funcional, culpa no acidente, danos e participacao de terceiros.\n"
                "=== FIM DO GUIA BEM PUBLICO ===\n\n"
            )
        civil_debt_guidance = ""
        if classification.main_branch == "civil" and any(
            marker in question_norm
            for marker in (
                "emprestei",
                "emprestimo",
                "empréstimo",
                "dívida",
                "divida",
                "whatsapp",
                "transferência",
                "transferencia",
                "provar",
                "prova",
            )
        ):
            civil_debt_guidance = (
                "=== GUIA DE COBERTURA — DIVIDA / PROVA CIVIL ===\n"
                "Se a pergunta for sobre provar uma divida ou emprestimo sem contrato escrito:\n"
                "- Art. 342 do Codigo Civil corresponde ao onus da prova: quem invoca o direito deve provar os factos constitutivos.\n"
                "- Art. 362 do Codigo Civil corresponde a prova documental.\n"
                "- Nao atribuas o onus da prova ao Art. 341; esse artigo e sobre funcao das provas.\n"
                "- Explica que transferencias e mensagens podem ajudar como prova documental, mas a força probatoria depende da autenticidade, contexto e contraditorio.\n"
                "=== FIM DO GUIA DIVIDA CIVIL ===\n\n"
            )
        admin_act_guidance = ""
        if classification.main_branch == "administrativo" and any(
            marker in question_norm
            for marker in (
                "reclamação",
                "reclamacao",
                "recurso hierárquico",
                "recurso hierarquico",
                "impugnação contenciosa",
                "impugnacao contenciosa",
                "acto administrativo",
                "ato administrativo",
                "decisão administrativa",
                "decisao administrativa",
            )
        ):
            admin_act_guidance = (
                "=== GUIA DE COBERTURA — IMPUGNACAO ADMINISTRATIVA ===\n"
                "Se a pergunta comparar reclamacao, recurso hierarquico e impugnacao contenciosa:\n"
                "- Se a Lei n.o 2/94 estiver no contexto, NAO digas que reclamacao ou recurso hierarquico nao estao previstos no contexto.\n"
                "- Art. 9 da Lei n.o 2/94: modalidades de impugnacao dos actos administrativos: reclamacao, recurso hierarquico e recurso contencioso.\n"
                "- Art. 11 da Lei n.o 2/94: reclamacao/recurso hierarquico visam revogacao ou alteracao; recurso contencioso visa invalidade/anulacao.\n"
                "- Art. 13 da Lei n.o 2/94: prazos de 30 dias para reclamacao/recurso hierarquico e 60 dias para recurso contencioso, se confirmado no contexto.\n"
                "- Usa o Codigo de Processo do Contencioso Administrativo para legitimidade, forma de processo e inicio/suspensao de prazos judiciais.\n"
                "=== FIM DO GUIA ADMINISTRATIVO ===\n\n"
            )
        compact_chat_guidance = (
            ""
            if professional_context
            or journalism_guidance
            or labor_dismissal_guidance
            or multi_branch_reasoning_guidance
            or public_asset_guidance
            or civil_debt_guidance
            or admin_act_guidance
            else (
                "=== REGRA DE PRODUTO — CHAT COMUM ANGOLANO ===\n"
                "Por padrao, o utilizador quer uma resposta rapida, clara e sem cansar.\n"
                "Estrutura recomendada:\n"
                "### Resposta\n"
                "1 paragrafo curto com a resposta directa.\n"
                "### O que fazer\n"
                "2 ou 3 bullets praticos, se fizer sentido.\n"
                "Base legal: cita no maximo 1 ou 2 artigos essenciais dentro do texto; nao abras uma seccao longa de fundamentacao.\n"
                "Se houver muitos detalhes possiveis, termina com uma frase curta oferecendo aprofundar ou fazer passo a passo.\n"
                "So faz resposta longa se o utilizador pedir expressamente: detalha, aprofunda, parecer, minuta, estratégia, requisitos completos ou análise técnica.\n"
                "=== FIM DA REGRA DE PRODUTO ===\n\n"
            )
        )

        return (
            "Es um advogado angolano senior com mais de 20 anos de pratica, especialista em todos os ramos do Direito Angolano.\n"
            "A tua missao e analisar o Contexto Juridico Recuperado e dar uma resposta juridica profissional, clara e estritamente ancorada nesse contexto.\n"
            "Respondes com rigor e sem inventar normas, artigos, valores, prazos ou procedimentos que nao estejam sustentados no contexto fornecido.\n"
            "\n"
            "=== REGRA FUNDAMENTAL DE VERIFICACAO DE CONTEXTO ===\n"
            "ANTES de afirmares que um artigo, diploma ou informacao 'nao foi encontrado', 'nao consta' ou 'nao esta disponivel', FAZ O SEGUINTE:\n"
            "1. Verifica o INVENTARIO DO CONTEXTO no final do prompt — lista TODOS os artigos e diplomas disponiveis.\n"
            "2. Procura o numero do artigo nos cabecalhos de cada chunk (ex: '### [Art. 417]').\n"
            "3. Le o texto do chunk correspondente antes de concluir que nao existe.\n"
            "4. No Codigo Penal angolano, o crime-base usa apenas o nome (ex: 'Burla' = burla simples, 'Furto' = furto simples, 'Roubo' = roubo simples). A ausencia da palavra 'simples' NAO significa que o artigo nao foi encontrado.\n"
            "5. So digas 'nao encontrado' se, apos verificar TODOS os itens acima, o artigo ou informacao realmente nao estiver presente.\n"
            "=== FIM DA REGRA FUNDAMENTAL ===\n"
            "\n"
            "Responde APENAS com um unico objecto JSON valido.\n"
            "Usa exactamente as seguintes chaves:\n"
            "  - rich_content: resposta em Markdown. OBRIGATORIO usar uma estrutura visual clara:\n"
            "    * Comeca com uma resposta curta e directa, sem introducao cerimonial.\n"
            "    * Usa subtitulos (###) apenas quando melhorarem a leitura.\n"
            "    * Usa listas curtas para passos, requisitos, riscos ou direitos.\n"
            "    * Nunca devolvas bloco unico longo nem lista exaustiva se o utilizador nao pediu detalhe.\n"
            "    * Se houver base legal confirmada, cita-a de forma compacta dentro da resposta ou numa secao curta.\n"
            "  - cited_articles: Lista de artigos citados no texto.\n"
            "  - cited_diplomas: Lista de diplomas citados no texto.\n"
            "\n"
            "REGRAS DE PRIORIDADE DO DOCUMENTO DO UTILIZADOR:\n"
            "Antes de responderes, classifica a intencao do utilizador num destes 3 modos:\n"
            "  MODO EXCLUSIVO: A pergunta pede resposta baseada APENAS no documento do utilizador.\n"
            "    -> Usa SO o documento do utilizador. Leis oficiais sao IGNORADAS.\n"
            "  MODO COMPARATIVO: A pergunta compara, contrasta ou questiona a conformidade do documento face a lei.\n"
            "    -> Explica 1) o que diz o documento, 2) o que diz a lei, 3) as diferencas ou conformidades.\n"
            "  MODO GERAL: A pergunta e generica, sem foco exclusivo no documento anexo.\n"
            "    -> Documento do utilizador como fonte primaria, complementado por leis oficiais.\n"
            'Se o utilizador menciona "artigo X" ou "documento" sem pedir comparacao, assume Modo Exclusivo.\n'
            'Se a pergunta contem palavras como "cumpre", "de acordo", "em conformidade", "legal", "regular", "valido", "lei geral", "codigo", "legislacao" referindo-se a leis externas ao documento, assume MODO COMPARATIVO. Neste modo, USA AMBAS as fontes: primeiro analisa o que diz o documento do utilizador, depois consulta a lei angolana aplicavel, e por fim compara/destaca diferencas.\n'
            "NAO CORRIJAS o documento com leis reais no Modo Exclusivo. No Modo Comparativo, deves apontar divergencias.\n"
            'IMPORTANTE: Os chunks de documentos do utilizador podem conter MULTIPLOS artigos num unico bloco. O metadata "artigo_principal" indica apenas o PRIMEIRO artigo do chunk. Le sempre o TEXTO COMPLETO do chunk.\n'
            'So responde "nao encontrei" se a informacao nao existir nos chunks do documento do utilizador.\n'
            'SE a pergunta for sobre o que "o documento carregado", "o dossier", "o PDF" ou "o anexo" contem, considera APENAS chunks com Tipo: documento do utilizador. Chunks de Tipo: jurisprudencia ou fonte oficial NAO fazem parte do documento carregado, mesmo que aparecam no contexto recuperado.\n'
            'SE o utilizador perguntar se o documento carregado contem acordao, jurisprudencia, decisao judicial ou processo, so podes responder "sim" se essa informacao estiver literalmente num chunk de Tipo: documento do utilizador. Nao atribuas jurisprudencia oficial recuperada ao documento do utilizador.\n'
            'PEDIDOS DE RESUMO/SUMARIZACAO DO DOCUMENTO: So resumes o documento do utilizador se o pedido mencionar claramente documento, PDF, anexo, ficheiro, dossie, contrato, peca ou memorando. Se o pedido for para resumir a resposta, explicar ao cliente, preparar checklist, minuta, perguntas ao cliente, estrategia ou defesa, responde a essa tarefa profissional usando o documento apenas como factos/prova do caso.\n'
            'MODO PRO COM DOCUMENTOS: Quando existir Contexto profissional do caso associado, os documentos ligados sao elementos do dossie. Nao transformes perguntas profissionais em resumo do PDF. Usa o documento para identificar factos, lacunas, prova e riscos, mas responde sempre ao pedido do advogado.\n'
            "\n"
            "REGRAS PARA FONTES JURISPRUDENCIAIS:\n"
            'Se o contexto contiver chunks identificados como "jurisprudencia", trata-os como fontes de autoridade persuasiva que mostram como os tribunais interpretam a lei, mas nao substituem a propria lei.\n'
            "Quando usares jurisprudencia, cita o tribunal, numero do processo e data se disponiveis. Exemplo: 'O Tribunal Supremo, no Processo n.o 894/2019, decidiu que...'\n"
            "Apresenta a decisao do tribunal (provido/negado/anulado) e a razao fundamental (ratio decidendi) de forma clara.\n"
            "Se houver conflito entre a lei e a jurisprudencia, explica ambos os pontos de vista: o que diz a lei e como os tribunais a tem interpretado.\n"
            "Nao inventes nomes de tribunais, numeros de processo ou datas que nao estejam no contexto.\n"
            "\n"
            "REGRAS CRITICAS DE ANALISE JURIDICA:\n"
            "1. USA APENAS O CONTEXTO COMO BASE. Nao completes com conhecimento geral do modelo.\n"
            "2. SE O CONTEXTO NAO CONFIRMAR O PONTO EXACTO, DIZ ISSO EXPRESSAMENTE. Podes explicar o limite do material recuperado, mas nao inventes resposta normativa.\n"
            "3. NUNCA INVENTES artigos, numeros, custos, prazos, orgaos competentes ou formulas do tipo 'em geral'.\n"
            "4. COBERTURA COMPLETA: responde a pergunta com o que o contexto efectivamente permite afirmar. Se houver lacuna, identifica a lacuna.\n"
            "5. CITACOES: Sempre que mencionares um artigo sustentado no contexto, escreve a referencia EXACTAMENTE neste formato (COM colchetes): [[Art. X, Diploma Y, p. Z]]. Exemplo: [[Art. 300.o, Lei Geral do Trabalho, p. 155]]. O sistema converte para apresentacao visual.\n"
            "6. SEM REDUNDANCIA: nao facas listas finais de fontes; o sistema mostra isso na interface.\n"
            "7. RECUPERACAO E APOLOGIA: se for uma CORRECCAO, inicia a rich_content com um pedido de desculpas profissional.\n"
            "8. ESTILO: Portugues de Angola, formal, claro e proporcional a complexidade.\n"
            "9. CITACOES: Usa o formato [[Art. X, Diploma Y, p. Z]]. NUNCA uses parenteses ou outros caracteres no lugar dos colchetes.\n"
            "10. SO PREENCHES cited_articles com artigos confirmados na whitelist abaixo.\n"
            "11. FORMATO PADRAO SUGERIDO: '### Resposta' e, se necessario, '### O que fazer'. Evita secoes redundantes.\n"
            "\n"
            f"{prefs_guidance}"
            f"{length_guidance}"
            f"{audience_guidance}\n"
            f"{pro_case_guidance}"
            f"{compact_chat_guidance}"
            f"{labor_dismissal_guidance}"
            f"{multi_branch_reasoning_guidance}"
            f"{public_asset_guidance}"
            f"{civil_debt_guidance}"
            f"{admin_act_guidance}"
            f"{journalism_guidance}"
            f"{follow_up_guidance}"
            f"ARTIGOS CONFIRMADOS NO CONTEXTO (WHITELIST): {whitelist_text}\n\n"
            f"TIPO DE RESPOSTA: {'CORRECCAO' if classification.is_correction else 'TRANSFORMACAO' if classification.is_transformation else 'RESPOSTA NORMAL'}\n"
            f"OBJECTIVO: {classification.transformation_type if classification.is_transformation else 'Analise Juridica'}\n"
            f"Ramo: {classification.main_branch} | Audiencia efectiva: {audience} | Topico: {classification.topic_route}\n"
            f"Pergunta do utilizador: {question}\n"
            f"Historico resumido: {history}\n\n"
            "--- INVENTARIO DO CONTEXTO RECUPERADO ---\n"
            "Artigos e diplomas disponiveis (verifica esta lista ANTES de dizer que algo nao foi encontrado):\n"
            f"{inventory_text}\n"
            "--- FIM DO INVENTARIO ---\n\n"
            "Contexto juridico recuperado (texto integral):\n"
            + "\n\n---\n\n".join(context_blocks)
            + (
                "\n\n[INSTRUCAO: O utilizador pediu um RESUMO do documento acima. NAO pecas mais informacao. Extrai e lista os pontos principais do documento. Responde diretamente.]"
                if document_summary_requested
                else ""
            )
            + streaming_guidance
        )

    def parse_llm_json(self, raw_answer: str) -> LLMAnswerDraft:
        cleaned_raw = self.sanitize_answer(raw_answer)
        if not cleaned_raw:
            return LLMAnswerDraft()
        payload = None
        try:
            payload = json.loads(cleaned_raw, strict=False)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", cleaned_raw)
            if match:
                try:
                    payload = json.loads(match.group(0), strict=False)
                except Exception:
                    payload = None
        if not isinstance(payload, dict):
            extracted = self._extract_rich_content(cleaned_raw)
            if extracted:
                return LLMAnswerDraft(rich_content=_clean(extracted))
            return LLMAnswerDraft(rich_content=_clean(cleaned_raw))

        if "json_object" in payload and isinstance(payload["json_object"], dict):
            payload = payload["json_object"]
        if "json" in payload and isinstance(payload["json"], dict):
            payload = payload["json"]
        for wrapper in ("rich_content", "answer", "response"):
            if wrapper in payload and isinstance(payload[wrapper], dict):
                payload = payload[wrapper]
                break

        for list_key in (
            "practical_steps",
            "distinctions",
            "prudent_inferences",
            "additional_validation_needed",
            "cited_articles",
            "cited_diplomas",
        ):
            value = payload.get(list_key)
            if value in (None, "", False, True):
                payload[list_key] = (
                    []
                    if value in (None, "", False)
                    else ["Validacao adicional assinalada pelo modelo."]
                )
            elif isinstance(value, str):
                payload[list_key] = [_clean(value)] if _clean(value) else []
            elif isinstance(value, list):
                payload[list_key] = [
                    _clean(str(item)) for item in value if _clean(str(item))
                ]
            else:
                payload[list_key] = [_clean(str(value))] if _clean(str(value)) else []
        # suggested_actions: list of dicts with icon/label/prompt
        raw_actions = payload.pop("suggested_actions", None)
        cleaned_actions: list[dict[str, str]] = []
        if isinstance(raw_actions, list):
            for action in raw_actions:
                if isinstance(action, dict):
                    label = str(action.get("label", "")).strip()
                    prompt = str(action.get("prompt", "")).strip()
                    if label and prompt and 3 <= len(label) <= 70:
                        icon = _normalize_action_icon(str(action.get("icon", "")), label)
                        cleaned_actions.append({"icon": icon, "label": label, "prompt": prompt})
        payload["suggested_actions"] = cleaned_actions
        return LLMAnswerDraft(**payload)

    def constrain_draft_to_context(
        self, draft: LLMAnswerDraft, retrieved_chunks: list
    ) -> LLMAnswerDraft:
        context_articles = _context_articles(retrieved_chunks)
        normalized_articles = []
        for article in draft.cited_articles:
            normalized = str(article).strip().replace(".", "")
            if normalized and normalized in context_articles:
                normalized_articles.append(normalized)
        normalized_diplomas = []
        available_titles = {
            _clean(chunk.title).casefold(): chunk.title
            for chunk in retrieved_chunks
            if _clean(chunk.title)
        }
        for diploma in draft.cited_diplomas:
            cleaned = _clean(str(diploma))
            if not cleaned:
                continue
            if cleaned.casefold() in available_titles:
                normalized_diplomas.append(available_titles[cleaned.casefold()])
                continue
            if any(cleaned.casefold() in title for title in available_titles):
                normalized_diplomas.append(cleaned)
        draft.cited_articles = list(dict.fromkeys(normalized_articles))
        draft.cited_diplomas = list(dict.fromkeys(normalized_diplomas))
        return draft

    def sanitize_answer(self, answer: str) -> str:
        updated = normalize_legal_text(answer).strip()
        updated = re.sub(r"^```(?:json)?\s*", "", updated)
        updated = re.sub(r"\s*```$", "", updated)
        # Strip unit separator / control chars the LLM outputs instead of brackets
        for ch in ("\x1f", "\x1e", "\x1d", "\x1c", "\x1b", "\u241f", "\u241e", "\u241d", "\u241c", "\u241b"):
            updated = updated.replace(ch, "")
        return updated.strip()

    @staticmethod
    def _extract_rich_content(text: str) -> str:
        if not text:
            return ""
        for key in ("rich_content", "answer", "response", "direct_answer", "simple_explanation"):
            pattern = rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                raw_value = match.group(1)
                try:
                    return normalize_legal_text(json.loads(f'"{raw_value}"'))
                except Exception:
                    decoded = (
                        raw_value
                        .replace("\\r\\n", "\n")
                        .replace("\\n", "\n")
                        .replace("\\t", "\t")
                        .replace('\\"', '"')
                        .replace("\\/", "/")
                        .replace("\\\\", "\\")
                    )
                    if decoded.strip():
                        return normalize_legal_text(decoded)

        for key in ("rich_content", "answer", "response", "direct_answer", "simple_explanation"):
            marker = f'"{key}"'
            key_index = text.find(marker)
            if key_index < 0:
                continue
            colon_index = text.find(":", key_index + len(marker))
            if colon_index < 0:
                continue
            quote_index = text.find('"', colon_index + 1)
            if quote_index < 0:
                continue

            raw_chars: list[str] = []
            escaped = False
            closed = False
            index = quote_index + 1
            while index < len(text):
                char = text[index]
                if escaped:
                    raw_chars.append("\\" + char)
                    escaped = False
                    index += 1
                    continue
                if char == "\\":
                    escaped = True
                    index += 1
                    continue
                if char == '"':
                    closed = True
                    break
                raw_chars.append(char)
                index += 1

            raw_value = "".join(raw_chars).strip()
            if not raw_value:
                continue

            if escaped:
                raw_value += "\\\\"

            candidate_literal = f'"{raw_value}"' if closed else None
            try:
                candidate = json.loads(candidate_literal) if candidate_literal else None
            except Exception:
                candidate = (
                    raw_value
                    .replace("\\r\\n", "\n")
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\/", "/")
                    .replace("\\\\", "\\")
                )
            if candidate is None:
                candidate = (
                    raw_value
                    .replace("\\r\\n", "\n")
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace('\\"', '"')
                    .replace("\\/", "/")
                    .replace("\\\\", "\\")
                )

            candidate = normalize_legal_text(candidate).strip()
            if candidate:
                return candidate
        return ""

    def fallback_from_validation(
        self,
        validation: ValidationResult,
        original_draft: LLMAnswerDraft | None = None,
    ) -> LLMAnswerDraft:
        issue_codes = {issue.code for issue in validation.issues}
        prudential_note = (
            "A resposta abaixo baseia-se no contexto recuperado e deve ser lida com prudencia."
        )
        if "followup_anchor_unresolved" in issue_codes:
            return LLMAnswerDraft(
                rich_content=(
                    "Nao confirmei ainda o artigo exacto a que o follow-up se refere.\n\n"
                    "### O que falta confirmar\n"
                    "- o artigo ou diploma exacto do seguimento anterior\n"
                    "- a base normativa correspondente no contexto recuperado\n\n"
                    "### Proximo passo\n"
                    "Envia a pergunta anterior ou identifica o diploma/artigo para eu ancorar a resposta com seguranca."
                )
            )
        if "requested_article_not_recovered" in issue_codes:
            return LLMAnswerDraft(
                rich_content=(
                    "O artigo exacto pedido nao foi confirmado no contexto recuperado.\n\n"
                    "### Leitura segura\n"
                    "Posso indicar o enquadramento geral, mas nao devo afirmar o conteudo desse artigo como confirmado.\n\n"
                    "### Proximo passo\n"
                    "Se quiseres, posso continuar a procurar esse artigo no diploma correcto."
                )
            )
        if "normative_conflict" in issue_codes or "citator_gap" in issue_codes:
            return LLMAnswerDraft(
                rich_content=(
                    "O contexto recuperado indica possivel conflito, alteracao ou revogacao normativa.\n\n"
                    "### O que isso significa\n"
                    "Ainda nao esta confirmado qual e o regime juridico prevalecente para responder sem reserva.\n\n"
                    "### Proximo passo\n"
                    "Posso ajudar a separar os diplomas ou a verificar a vigencia de cada norma."
                )
            )
        if "vigency_unverified" in issue_codes:
            return LLMAnswerDraft(
                rich_content=(
                    "A pergunta exige confirmacao de vigencia normativa, e o contexto actual ainda nao permite afirmar com seguranca se a norma continua em vigor.\n\n"
                    "### Proximo passo\n"
                    "Se me deres o diploma exacto, eu posso enquadrar melhor a questao e assinalar o que ficou por confirmar."
                )
            )

        if original_draft and original_draft.rich_content:
            if validation.answer_mode == "grounded_with_caveat":
                content = original_draft.rich_content.strip()
                lower_content = content.casefold()
                if "cautela" not in lower_content and "reserva" not in lower_content and "limit" not in lower_content:
                    content = f"{prudential_note}\n\n{content}\n\n### Nota pratica\nSe quiseres, eu posso transformar isto em passos concretos ou apontar o artigo exacto a seguir."
                return original_draft.model_copy(update={"rich_content": content})
            return original_draft

        if validation.answer_mode == "refused":
            rich = (
                "Nao foi possivel encontrar informacao juridica suficiente para responder com seguranca.\n\n"
                "### O que podes enviar\n"
                "- o diploma ou artigo em causa\n"
                "- o ramo juridico exacto\n"
                "- o contexto factual essencial"
            )
        elif validation.answer_mode == "grounded_with_caveat":
            rich = (
                f"{prudential_note}\n\n"
                "A resposta que se segue baseia-se no contexto recuperado e aponta a melhor leitura possivel no estado actual da base documental.\n\n"
                "### O que fazer a seguir\n"
                "- confirmar o diploma/artigo exacto, se a questao for muito especifica\n"
                "- verificar se ha prazos ou requisitos processuais adicionais\n"
                "- pedir-me a mesma resposta em formato de passos praticos, se quiseres agir ja"
            )
        else:
            rich = (
                "A informacao disponivel no momento nao permite uma resposta completa.\n\n"
                "### O que falta\n"
                "- confirmacao normativa suficiente\n"
                "- contexto factual mais preciso\n\n"
                "### Proximo passo\n"
                "Reformula com mais detalhes ou indica o diploma/artigo que queres analisar."
            )

        return LLMAnswerDraft(rich_content=rich)

    def answer_tracks_question(
        self, question: str, draft: LLMAnswerDraft, classification: LegalClassification
    ) -> bool:
        question_tokens = _question_anchor_tokens(question)
        if not question_tokens:
            return True
        answer_tokens = _normalized_tokens(draft.rich_content)
        overlap = question_tokens.intersection(answer_tokens)
        return len(overlap) >= 1

    def answer_looks_like_json_artifact(self, answer: str) -> bool:
        stripped = answer.lstrip()
        return stripped.startswith("{") or stripped.startswith("```")

    def get_suggested_actions(
        self,
        draft: LLMAnswerDraft,
        classification,
        active_document_id: str | None = None,
        sources: list[SourceItem] | None = None,
    ) -> list[dict[str, str]]:
        llm_actions = draft.suggested_actions or []
        if llm_actions:
            return llm_actions[:3]
        has_doc = bool(active_document_id)
        has_cited = bool(draft.cited_articles)
        has_juris = any(getattr(s, "source_kind", "") == "jurisprudence" or getattr(s, "source_scope", "") == "jurisprudence" for s in (sources or []))
        return _build_fallback_actions(
            branch=classification.main_branch,
            audience=classification.audience,
            has_active_document=has_doc,
            has_cited_articles=has_cited,
            has_jurisprudence=has_juris,
        )

    def compose_answer(
        self,
        classification: LegalClassification,
        draft: LLMAnswerDraft,
        validation: ValidationResult,
        confidence: ConfidenceResult,
        sources: list[SourceItem],
        active_document_id: str | None = None,
    ) -> str:
        if draft.rich_content:
            text = _clean(draft.rich_content)
            # Strip control chars + unit separator symbols before re-parsing
            for ch in ("\x1f", "\x1e", "\x1d", "\x1c", "\x1b", "\u241f", "\u241e", "\u241d", "\u241c", "\u241b"):
                text = text.replace(ch, "")
            stripped_text = text.lstrip()
            if stripped_text.startswith("{") and '"rich_content"' in stripped_text[:500]:
                try:
                    parsed = json.loads(stripped_text, strict=False)
                    if isinstance(parsed, dict) and parsed.get("rich_content"):
                        text = _clean(parsed["rich_content"])
                except Exception:
                    extracted = self._extract_rich_content(stripped_text)
                    if extracted:
                        text = extracted
            if (
                validation.answer_mode == "limited"
                and confidence.score >= 0.4
                and not validation.issues
                and text.casefold().startswith("a informacao disponivel no momento")
            ):
                sourced = _source_based_answer(classification, sources)
                if sourced:
                    return normalize_legal_text(sourced).strip()
            return normalize_legal_text(text).strip()
        return ""


legal_composer = LegalComposer()


class RichContentStreamExtractor:
    FIELD_KEYS = (
        "rich_content",
        "answer",
        "response",
        "direct_answer",
        "simple_explanation",
    )

    def __init__(self) -> None:
        self._buffer = ""
        self._mode = "search"
        self._cursor = 0
        self._done = False
        self._escape = False
        self._unicode_digits = ""
        self._unicode_active = False
        self._raw_cursor = 0

    def push(self, chunk: str) -> str:
        if not chunk or self._done:
            return ""

        self._buffer += chunk
        output: list[str] = []

        while True:
            if self._mode == "search":
                match = self._find_field_start()
                if match is not None:
                    self._mode = "capture"
                    self._cursor = match
                    continue

                stripped = self._buffer.lstrip()
                if stripped and not stripped.startswith("{") and len(self._buffer) >= 24:
                    self._mode = "raw"
                    output.append(self._buffer[self._raw_cursor :])
                    self._raw_cursor = len(self._buffer)
                break

            if self._mode == "raw":
                if self._raw_cursor < len(self._buffer):
                    output.append(self._buffer[self._raw_cursor :])
                    self._raw_cursor = len(self._buffer)
                break

            made_progress = False
            while self._cursor < len(self._buffer):
                made_progress = True
                char = self._buffer[self._cursor]
                self._cursor += 1

                if self._unicode_active:
                    if re.match(r"[0-9a-fA-F]", char):
                        self._unicode_digits += char
                        if len(self._unicode_digits) == 4:
                            try:
                                output.append(chr(int(self._unicode_digits, 16)))
                            except ValueError:
                                output.append("\\u" + self._unicode_digits)
                            self._unicode_active = False
                            self._unicode_digits = ""
                        continue
                    output.append("\\u" + self._unicode_digits + char)
                    self._unicode_active = False
                    self._unicode_digits = ""
                    continue

                if self._escape:
                    self._escape = False
                    if char == "n":
                        output.append("\n")
                    elif char == "r":
                        continue
                    elif char == "t":
                        output.append("\t")
                    elif char == "b":
                        output.append("\b")
                    elif char == "f":
                        output.append("\f")
                    elif char == "u":
                        self._unicode_active = True
                        self._unicode_digits = ""
                    else:
                        output.append(char)
                    continue

                if char == "\\":
                    self._escape = True
                    continue

                if char == '"':
                    self._done = True
                    break

                output.append(char)

            if not made_progress or self._done:
                break

        text = "".join(output)
        if not text:
            return ""
        return normalize_legal_text(text)

    def _find_field_start(self) -> int | None:
        for key in self.FIELD_KEYS:
            pattern = rf'"{key}"\s*:\s*"'
            match = re.search(pattern, self._buffer)
            if match:
                return match.end()
        return None
