from __future__ import annotations

import asyncio
import re
from collections import defaultdict

from app.db.models import RetrievedChunk
from app.db.postgres import postgres_manager
from app.services.legal.models import (
    BranchEvidenceGroup,
    LegalBranch,
    LegalClassification,
    RetrievalEvidence,
    RetrievalResult,
)
from app.services.rag.retriever import retriever_service
from app.services.pdf.document_context import document_context_service

ARTICLE_RE = re.compile(r"(?:art|artigo|artigos)\s*(\d+[.]?\d*)", re.IGNORECASE)
WORD_RE = re.compile(r"\w+", re.UNICODE)
STOPWORDS = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "ou",
    "a",
    "o",
    "as",
    "os",
    "um",
    "uma",
    "no",
    "na",
    "nos",
    "nas",
    "com",
    "sem",
    "por",
    "para",
    "que",
    "quando",
    "como",
    "entre",
    "sobre",
    "agora",
    "mesmo",
    "caso",
}
TOPIC_HINTS = {
    "despedimento": (
        "despedimento",
        "ilicitude",
        "reintegra",
        "indemniza",
        "compensação",
        "compensacao",
        "impugnação",
        "impugnacao",
        "prova",
        "procedimento disciplinar",
        "justa causa",
    ),
    "salario": ("salário", "salario", "pagamento", "recibo", "descontos"),
    "penal": (
        "crime",
        "penal",
        "infidelidade",
        "furto",
        "apropriação",
        "apropriacao",
        "fraude",
        "patrimonial",
    ),
    "mútuo": (
        "mútuo",
        "mutuo",
        "empréstimo",
        "emprestimo",
        "prova",
        "mensagens",
        "transferências",
        "transferencias",
    ),
}
LABOR_COMPENSATION_TERMS = (
    "compensação",
    "compensacao",
    "indemnização",
    "indemnizacao",
    "reintegra",
    "despedimento ilícito",
    "despedimento ilicito",
)
LABOR_COMPENSATION_NEGATIVE = (
    "não discriminação",
    "nao discriminação",
    "nao discriminacao",
    "abandono do trabalho",
    "comissão de serviço",
    "comissao de servico",
)
CIVIL_MUTUO_TERMS = ("mútuo", "mutuo", "empréstimo", "emprestimo")
CIVIL_MUTUO_PROOF_TERMS = (
    "prova",
    "testemunhas",
    "mensagens",
    "transferências",
    "transferencias",
    "documento",
    "escrito",
)
CIVIL_MUTUO_NEGATIVE = (
    "casamento",
    "nubentes",
    "divórcio",
    "divorcio",
    "convenção antenupcial",
    "convencao antenupcial",
)
INSTITUTION_TERMS = (
    "inspecção",
    "inspeccao",
    "tribunal",
    "advogado",
    "ministério público",
    "ministerio publico",
)
INSTITUTION_NEGATIVE = ("procedimento disciplinar", "antigo combatente")
FOLLOW_UP_GENERIC_MARKERS = (
    "explique melhor",
    "o que faço agora",
    "qual a diferença na prática",
    "qual a diferenca na pratica",
    "o que faço agora?",
    "qual a diferença na prática?",
)
FOLLOW_UP_REFERENCE_MARKERS = (
    "mesmo caso",
    "isso",
    "isto",
    "agora",
    "melhor",
    "diferença",
    "diferenca",
)
PENAL_BAD_PATTERNS = (
    "revogação da legislação",
    "revogacao da legislacao",
    "disposições legais",
    "disposicoes legais",
    "encerramento de estabelecimento",
    "corrupção activa",
    "corrupcao activa",
)
LABOR_BAD_PATTERNS = (
    "férias",
    "ferias",
    "licença sem retribuição",
    "licenca sem retribuicao",
)
PENAL_GOOD_PATTERNS = (
    "infidelidade",
    "peculato",
    "peculato de uso",
    "abuso de poder",
    "abuso de confiança",
    "abuso de confianca",
    "apropriação",
    "apropriacao",
    "fraude",
    "patrimonial",
)
PUBLIC_ASSET_MISUSE_QUERY_MARKERS = (
    "carro do estado",
    "viatura do estado",
    "veiculo do estado",
    "bem publico",
    "bens publicos",
    "patrimonio do estado",
    "patrimonio publico",
    "uso privado",
    "peculato",
    "peculato de uso",
)
PUBLIC_ASSET_MISUSE_AGENT_MARKERS = (
    "funcionario publico",
    "funcionário público",
    "agente publico",
    "agente público",
    "servidor publico",
    "servidor público",
    "titular de cargo publico",
    "titular de cargo público",
)
PUBLIC_ASSET_MISUSE_COMPANY_MARKERS = (
    "empresa publica",
    "empresa pública",
    "sociedade de capitais publicos",
    "sociedade de capitais públicos",
    "sector publico",
    "setor público",
)
PUBLIC_ASSET_MISUSE_STRONG_TERMS = (
    "peculato de uso",
    "usar ou deixar usar",
    "funcionário público",
    "funcionario publico",
    "coisa móvel",
    "coisa movel",
    "virtude do seu cargo",
    "virtude das suas funções",
    "virtude das suas funcoes",
)
PUBLIC_ASSET_MISUSE_SECONDARY_TERMS = (
    "peculato",
    "abuso de poder",
    "benefício ilegítimo",
    "beneficio ilegitimo",
    "património do estado",
    "patrimonio do estado",
    "bem público",
    "bem publico",
)
PUBLIC_ASSET_MISUSE_ARTICLE_BOOSTS = {
    "363": 7.0,
    "362": 5.0,
    "374": 3.0,
    "406": -4.5,
}
JOURNALIST_LEAK_MEDIA_MARKERS = (
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
JOURNALIST_LEAK_CONTEXT_MARKERS = (
    "corrupcao",
    "corrupção",
    "funcionario publico",
    "funcionário público",
    "funcionarios publicos",
    "funcionários públicos",
    "documento vazado",
    "documentos vazados",
    "vazamento",
    "fonte",
    "segredo",
    "dados pessoais",
    "protecção de dados",
    "proteccao de dados",
    "proteção de dados",
    "protecao de dados",
)
JOURNALIST_LEAK_SOURCE_ORDER = (
    ("codigo-penal-lei-38-20", "359"),
    ("codigo-penal-lei-38-20", "358"),
    ("codigo-penal-lei-38-20", "357"),
    ("codigo-penal-lei-38-20", "362"),
    ("codigo-penal-lei-38-20", "364"),
    ("constituicao-republica-angola-2022", "40"),
    ("constituicao-republica-angola-2022", "44"),
    ("constituicao-republica-angola-2022", "32"),
    ("constituicao-republica-angola-2022", "69"),
    ("constituicao-republica-angola-2022", "57"),
    ("codigo-penal-lei-38-20", "375"),
    ("codigo-penal-lei-38-20", "356"),
)
STATE_AGENT_LIABILITY_MARKERS = (
    "responsabilidade civil do estado",
    "agente do estado",
    "agente público",
    "agente publico",
    "funcionário público",
    "funcionario publico",
    "órgão do estado",
    "orgao do estado",
)
STATE_AGENT_DAMAGE_MARKERS = (
    "dano",
    "danos",
    "prejuízo",
    "prejuizo",
    "indemnização",
    "indemnizacao",
    "exercício das funções",
    "exercicio das funcoes",
    "direito de regresso",
    "regresso",
)
LABOR_GOOD_PATTERNS = (
    "despedimento",
    "ilicitude",
    "reintegra",
    "indemniza",
    "compensação",
    "compensacao",
    "salário",
    "salario",
    "pagamento",
    "descontos",
    "tribunal",
)
Civil_BAD_PATTERNS = ("responsabilidade disciplinar",)
Civil_GOOD_PATTERNS = (
    "mútuo",
    "mutuo",
    "transferências",
    "transferencias",
    "mensagens",
    "prova",
    "contrato",
)
PENAL_QUERY_MARKERS = (
    "penal",
    "crime",
    "crimin",
    "tipicidade",
    "relevância penal",
    "relevancia penal",
)
LABOR_QUERY_MARKERS = (
    "laboral",
    "despedimento",
    "salário",
    "salario",
    "trabalhador",
    "empregador",
)
CIVIL_QUERY_MARKERS = (
    "civil",
    "mútuo",
    "mutuo",
    "transferências",
    "transferencias",
    "contrato",
)
ADMIN_QUERY_MARKERS = ("administrativo",)
CONSTITUTIONAL_QUERY_MARKERS = ("constituição", "constitucional", "constituicao")
BRANCH_QUERY_MARKERS = {
    "penal": PENAL_QUERY_MARKERS,
    "laboral": LABOR_QUERY_MARKERS,
    "civil": CIVIL_QUERY_MARKERS,
    "administrativo": ADMIN_QUERY_MARKERS,
    "constitucional": CONSTITUTIONAL_QUERY_MARKERS,
}
BRANCH_GOOD_PATTERNS = {
    "penal": PENAL_GOOD_PATTERNS,
    "laboral": LABOR_GOOD_PATTERNS,
    "civil": Civil_GOOD_PATTERNS,
}
BRANCH_BAD_PATTERNS = {
    "penal": PENAL_BAD_PATTERNS,
    "laboral": LABOR_BAD_PATTERNS,
    "civil": Civil_BAD_PATTERNS,
}


def _is_public_asset_misuse_query(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_agent = any(marker in normalized for marker in PUBLIC_ASSET_MISUSE_AGENT_MARKERS)
    has_asset = any(marker in normalized for marker in PUBLIC_ASSET_MISUSE_QUERY_MARKERS)
    return has_asset or (
        has_agent and any(token in normalized for token in ("estado", "publico", "público"))
    )


def _is_journalist_leak_public_interest_query(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_media = any(marker in normalized for marker in JOURNALIST_LEAK_MEDIA_MARKERS)
    has_context = any(marker in normalized for marker in JOURNALIST_LEAK_CONTEXT_MARKERS)
    asks_multi_branch = (
        "penal" in normalized
        and "constitucional" in normalized
        and "administrativ" in normalized
        and "dados" in normalized
    )
    return (has_media and has_context) or asks_multi_branch


def _is_journalist_direct_evidence(item: RetrievalEvidence) -> bool:
    return item.retrieval_reason == "journalist_public_interest_direct_article"


def _is_labor_objective_direct_evidence(item: RetrievalEvidence) -> bool:
    return item.retrieval_reason == LABOR_OBJECTIVE_DISMISSAL_REASON


def _is_curated_direct_evidence(item: RetrievalEvidence) -> bool:
    return item.retrieval_reason in {
        "journalist_public_interest_direct_article",
        LABOR_OBJECTIVE_DISMISSAL_REASON,
        PUBLIC_ASSET_REASON,
        STATE_AGENT_LIABILITY_REASON,
        CIVIL_LOAN_REASON,
        ADMIN_ACT_REASON,
    }


def _is_civil_loan_query(question: str, classification: LegalClassification) -> bool:
    normalized = _normalize(question)
    if not normalized:
        return False
    if classification.main_branch != "civil" and "civil" not in (
        classification.branch_candidates or []
    ):
        return False
    return any(
        marker in normalized
        for marker in (
            "emprestei",
            "emprestimo",
            "empréstimo",
            "mutuo",
            "mútuo",
            "divida",
            "dívida",
            "devedor",
            "credor",
            "transferencia",
            "transferência",
            "whatsapp",
            "mensagem",
            "contrato escrito",
            "contrato verbal",
            "prova",
            "comprovativo",
        )
    )


def _is_admin_act_challenge_query(
    question: str, classification: LegalClassification
) -> bool:
    normalized = _normalize(question)
    if not normalized:
        return False
    if classification.main_branch != "administrativo" and "administrativo" not in (
        classification.branch_candidates or []
    ):
        return False
    return any(
        marker in normalized
        for marker in (
            "acto administrativo",
            "ato administrativo",
            "decisao administrativa",
            "decisão administrativa",
            "orgao publico",
            "órgão público",
            "reclamacao",
            "reclamação",
            "recurso hierarquico",
            "recurso hierárquico",
            "impugnacao contenciosa",
            "impugnação contenciosa",
            "recurso contencioso",
            "contencioso administrativo",
        )
    )


def _is_state_agent_liability_query(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_state_agent = any(marker in normalized for marker in STATE_AGENT_LIABILITY_MARKERS)
    has_damage = any(marker in normalized for marker in STATE_AGENT_DAMAGE_MARKERS)
    return has_state_agent and has_damage


PENAL_MATERIAL_REQUIRED = (
    "retenção",
    "retencao",
    "valores",
    "peculato",
    "peculato de uso",
    "abuso de poder",
    "apropriação",
    "apropriacao",
    "patrimonial",
    "fraude",
    "infidelidade",
    "funcionário público",
    "funcionario publico",
    "coisa móvel",
    "coisa movel",
)
TOPIC_FOCUS_TERMS = {
    "despedimento": (
        "despedimento",
        "ilicitude",
        "reintegra",
        "indemniza",
        "compensação",
        "compensacao",
    ),
    "pagamento": ("pagamento", "salário", "salario", "recibo", "descontos"),
    "mútuo": (
        "mútuo",
        "mutuo",
        "empréstimo",
        "emprestimo",
        "transferências",
        "transferencias",
        "mensagens",
        "prova",
    ),
}
TOPIC_BAD_PATTERNS = {
    "despedimento": ("férias", "ferias"),
    "pagamento": tuple(),
    "mútuo": ("despedimento",),
}
TOPIC_GOOD_PATTERNS = TOPIC_FOCUS_TERMS
TOPIC_QUERY_TERMS = TOPIC_FOCUS_TERMS
TOPIC_BRANCH_MAP = {
    "despedimento": "laboral",
    "pagamento": "laboral",
    "mútuo": "civil",
}
TOPIC_FORCE_PATTERNS = {
    "penal_material": PENAL_MATERIAL_REQUIRED,
}
PENAL_MATERIAL_QUERY_TERMS = (
    "infidelidade",
    "burla",
    "retenção de moeda",
    "retencao de moeda",
    "apropriação ilegítima",
    "apropriacao ilegitima",
    "vantagem patrimonial",
)
MIXED_PAYMENT_TERMS = (
    "pagamento",
    "indemnização",
    "indemnizacao",
    "salário",
    "salario",
    "falta de pagamento",
)
MIXED_EMPLOYMENT_TERMS = ("empregador", "trabalhador", "despedimento")
MIXED_PENAL_TRIGGER_TERMS = (
    MIXED_PAYMENT_TERMS + MIXED_EMPLOYMENT_TERMS + PENAL_QUERY_MARKERS
)
PENAL_MATERIAL_POSITIVE = (
    "infidelidade",
    "burla",
    "vantagem patrimonial",
    "apropriação",
    "apropriacao",
    "retenção",
    "retencao",
)
PENAL_MATERIAL_NEGATIVE = (
    "pena de multa",
    "pagamento diferido",
    "indemnização do lesado",
    "responsabilidade civil emergente de crime",
)
PENAL_MATERIAL_ARTICLES = {"426", "417", "468", "406", "399"}
PENAL_MATERIAL_MIN_SCORE = 3.0
PENAL_BRANCH_MIN_COUNT = 1
PENAL_BRANCH_QUERY_LIMIT = 2
PENAL_MATERIAL_REASON = "penal_material"
PENAL_MATERIAL_LABEL = "Fundamento penal material prioritário"
PENAL_MATERIAL_DIPLOMA = "Código Penal"
PENAL_MATERIAL_SLUG = "codigo-penal-lei-38-20"
PENAL_MATERIAL_QUESTION_HINTS = (
    "relevância penal",
    "relevancia penal",
    "crime",
    "penal",
)
PENAL_MATERIAL_FOLLOWUP_HINTS = (
    "mesmo caso",
    "isso",
    "agora",
    "diferença",
    "diferenca",
    "melhor",
)
PENAL_MATERIAL_LABOR_CONTEXT = (
    "não pagamento",
    "nao pagamento",
    "despedimento",
    "indemnização",
    "indemnizacao",
)
PENAL_MATERIAL_PRIORITY_BOOST = 4.4
PENAL_MATERIAL_FILTER_FLOOR = 1.6
PENAL_MATERIAL_SELECTION_LIMIT = 2
PENAL_MATERIAL_BRANCH_LIMIT = 2
PENAL_MATERIAL_PROMOTION_BONUS = 2.8
PENAL_MATERIAL_FORCE_INCLUDE = True
PENAL_MATERIAL_CONCRETE_TERMS = (
    "valores",
    "prejuízo",
    "prejuizo",
    "patrimonial",
    "dinheiro",
)
PENAL_MATERIAL_CONTEXTUAL_TERMS = (
    PENAL_MATERIAL_POSITIVE + PENAL_MATERIAL_CONCRETE_TERMS
)
PENAL_MATERIAL_REQUIRED_MARKERS = (
    "penal",
    "crime",
    "relevância penal",
    "relevancia penal",
)
PENAL_MATERIAL_QUERY_TEMPLATE = "{question}. Enquadramento penal material: infidelidade, burla, retenção de moeda, apropriação ilegítima, vantagem patrimonial. Diploma: Código Penal"
PENAL_MATERIAL_FOLLOWUP_TEMPLATE = "{question}. Contexto anterior: {anchor}. Enquadramento penal material: infidelidade, burla, retenção de moeda, apropriação ilegítima, vantagem patrimonial. Diploma: Código Penal"
PENAL_MATERIAL_QUESTION_REWRITE = (
    "crime patrimonial empregador retenção valores trabalhador infidelidade burla"
)
PENAL_MATERIAL_FOLLOWUP_REWRITE = "mesmo caso crime patrimonial empregador retenção valores trabalhador infidelidade burla"
PENAL_MATERIAL_CROSS_TERMS = (
    "empregador",
    "trabalhador",
    "valores",
    "pagamento",
    "indemnização",
    "indemnizacao",
)
PENAL_MATERIAL_TEXT_MIN_HITS = 1
PENAL_MATERIAL_STRICT_REQUIRED = True
PENAL_MATERIAL_TARGET_PAGES = {70, 79, 69, 67}
PENAL_MATERIAL_ALLOWED_PAGES = {60, 67, 68, 69, 70, 71, 72, 79}
PENAL_MATERIAL_BOOST_ARTICLES = {
    "426": 3.0,
    "417": 2.4,
    "468": 2.0,
    "406": 1.5,
    "399": 1.2,
}
PENAL_MATERIAL_DROP_ARTICLES = {"141", "140", "51", "47", "91", "123"}
PENAL_MATERIAL_FORCE_SLUG = {
    "diploma_slug": PENAL_MATERIAL_SLUG,
    "legal_branch": "penal",
    "source_scope": "official",
}
PENAL_MATERIAL_BASE_WHERE = {"legal_branch": "penal", "source_scope": "official"}
PENAL_MATERIAL_SELECTION_REASON = "penal_material_query"
PENAL_MATERIAL_BRANCH_NAME = "penal"
PENAL_MATERIAL_TRIGGER_NEEDS = {"laboral", "penal"}
PENAL_MATERIAL_OUTPUT_LIMIT = 1
PENAL_MATERIAL_LOW_VALUE_TERMS = (
    "multa",
    "indemnização do lesado",
    "pagamento diferido",
)
PENAL_MATERIAL_SCORE_CUTOFF = 2.4
PENAL_MATERIAL_RESCUE_LIMIT = 1
PENAL_MATERIAL_RESCUE_SCORE = 2.0
PENAL_MATERIAL_RESCUE_REASON = "penal_material_rescue"
PENAL_MATERIAL_FALLBACK_QUERY = (
    "crime patrimonial retenção valores pagamento trabalhador empregador"
)
PENAL_MATERIAL_CANONICAL_TERMS = (
    "infidelidade",
    "burla",
    "retenção",
    "retencao",
    "apropriação",
    "apropriacao",
)
PENAL_MATERIAL_PREFER_ARTICLE_BLOCK = True
PENAL_MATERIAL_STRICT_BRANCH = True
PENAL_MATERIAL_STRICT_SOURCE = True
PENAL_MATERIAL_QUERY_REASONS = {
    PENAL_MATERIAL_REASON,
    PENAL_MATERIAL_SELECTION_REASON,
    PENAL_MATERIAL_RESCUE_REASON,
}
PENAL_MATERIAL_MATCH_FLOOR = 1
PENAL_MATERIAL_CONTEXT_BOOST = 1.6
PENAL_MATERIAL_QUERY_BOOST = 2.2
PENAL_MATERIAL_ARTICLE_BLOCK_BONUS = 1.2
PENAL_MATERIAL_PAGE_BONUS = 0.8
PENAL_MATERIAL_PAGE_PENALTY = -1.2
PENAL_MATERIAL_NEGATIVE_PENALTY = -2.5
PENAL_MATERIAL_POSITIVE_BONUS = 1.4
PENAL_MATERIAL_DROP_PENALTY = -3.0
PENAL_MATERIAL_FORCE_MIN = 0.8
PENAL_MATERIAL_SELECTION_FLOOR = 0.5
PENAL_MATERIAL_TEXT_REQUIRED = True
PENAL_MATERIAL_BRANCH_PRIORITY = True
PENAL_MATERIAL_QUERY_PRIORITY = True
PENAL_MATERIAL_ONLY_FOR_MIXED = True
PENAL_MATERIAL_HISTORY_REQUIRED = False
PENAL_MATERIAL_SUPPORTS_FOLLOWUP = True
PENAL_MATERIAL_SUPPORTS_DIRECT = True
PENAL_MATERIAL_DEBUG = False
PENAL_MATERIAL_LIMIT_NOTES = False
PENAL_MATERIAL_KEEP_TOP = 1
PENAL_MATERIAL_MIN_BRANCHES = 2
PENAL_MATERIAL_EMBED_QUERY = True
PENAL_MATERIAL_SCORE_NAME = "penal_material_score"
PENAL_MATERIAL_BRANCH_GROUP = "penal"
PENAL_MATERIAL_FORCE_ORDER = True
PENAL_MATERIAL_FORCE_SELECTION = True
PENAL_MATERIAL_FORCE_NOTES = False
PENAL_MATERIAL_STRICT_FOLLOWUP = True
PENAL_MATERIAL_KEEP_REASON = True
PENAL_MATERIAL_USE_ANCHOR = True
PENAL_MATERIAL_HISTORY_LABEL = "Contexto anterior"
PENAL_MATERIAL_MIXED_LABEL = "Ramo penal material prioritário"
PENAL_MATERIAL_FINAL_LIMIT = 1
PENAL_MATERIAL_RELEVANCE_REQUIRED = True
PENAL_MATERIAL_REWRITE_PRIORITY = True
PENAL_MATERIAL_ARTICLE_PRIORITY = True
PENAL_MATERIAL_QUERY_KIND = "penal_material"
PENAL_MATERIAL_BRANCH_BUCKET = "penal"
PENAL_MATERIAL_TOP_SCORE_BONUS = 1.0
PENAL_MATERIAL_RETRIEVAL_MIN = 0.0
PENAL_MATERIAL_CHUNK_TOPN = 3
PENAL_MATERIAL_ENFORCE_ON_MIXED = True
PENAL_MATERIAL_QUESTION_CHECK = True
PENAL_MATERIAL_HISTORY_CHECK = True
PENAL_MATERIAL_FALLBACK_ALLOWED = True
PENAL_MATERIAL_DROP_GENERIC = True
PENAL_MATERIAL_ISSUE_PREVENTION = True
PENAL_MATERIAL_SCORE_STRICT = True
PENAL_MATERIAL_REQUIRED_BRANCH = "penal"
PENAL_MATERIAL_REQUIRED_SCOPE = "official"
PENAL_MATERIAL_REQUIRED_SLUG = "codigo-penal-lei-38-20"
PENAL_MATERIAL_BRANCH_TIEBREAK = True
PENAL_MATERIAL_QUERY_STRING = PENAL_MATERIAL_FALLBACK_QUERY
PENAL_MATERIAL_LAST = True
PENAL_MATERIAL_FIRST = True
PENAL_MATERIAL_NOTELESS = True
PENAL_MATERIAL_SINGLE_KEEP = True
PENAL_MATERIAL_SINGLE_PENAL = True
PENAL_MATERIAL_SINGLE_LIMIT = 1
PENAL_MATERIAL_SINGLE_BRANCH = "penal"
PENAL_MATERIAL_SINGLE_SCORE = 2.2
PENAL_MATERIAL_SINGLE_CUTOFF = 1.4
PENAL_MATERIAL_SINGLE_WHERE = {
    "legal_branch": "penal",
    "source_scope": "official",
    "diploma_slug": "codigo-penal-lei-38-20",
}
PENAL_MATERIAL_SINGLE_QUERY = "crime patrimonial empregador retenção valores trabalhador infidelidade burla código penal"
PENAL_MATERIAL_SINGLE_REASON = "penal_material_direct"
PENAL_MATERIAL_SINGLE_LABEL = "Fundamento penal material directo"
PENAL_MATERIAL_SINGLE_REWRITE = (
    "empregador trabalhador retenção valores crime patrimonial infidelidade burla"
)
PENAL_MATERIAL_SINGLE_TERMS = (
    "infidelidade",
    "burla",
    "retenção",
    "retencao",
    "valores",
    "patrimonial",
)
PENAL_MATERIAL_SINGLE_NEGATIVE = (
    "multa",
    "indemnização do lesado",
    "responsabilidade civil emergente",
)
PENAL_MATERIAL_SINGLE_POSITIVE = (
    "infidelidade",
    "burla",
    "vantagem patrimonial",
    "retiver valores",
    "prejuízo patrimonial",
    "prejuizo patrimonial",
)
PENAL_MATERIAL_SINGLE_ARTICLE_ALLOW = {"426", "417", "468", "406", "399"}
PENAL_MATERIAL_SINGLE_ARTICLE_DROP = {"141", "140", "51", "47", "91", "123"}
PENAL_MATERIAL_SINGLE_PAGE_ALLOW = {67, 69, 70, 79}
PENAL_MATERIAL_SINGLE_PAGE_PREFER = {70, 69, 79}
PENAL_MATERIAL_SINGLE_BOOST = 3.2
PENAL_MATERIAL_SINGLE_NEGATIVE_PENALTY = -2.7
PENAL_MATERIAL_SINGLE_QUERY_BOOST = 2.4
PENAL_MATERIAL_SINGLE_PAGE_BOOST = 1.1
PENAL_MATERIAL_SINGLE_ARTICLE_BOOST = 1.5
PENAL_MATERIAL_SINGLE_TEXT_REQUIRED = True
PENAL_MATERIAL_SINGLE_SCORE_MIN = 2.5
PENAL_MATERIAL_SINGLE_KEEP_TOP = 1
PENAL_MATERIAL_SINGLE_BRANCH_LIMIT = 1
PENAL_MATERIAL_SINGLE_APPLIES_TO = "mixed_penal_payment"
PENAL_MATERIAL_SINGLE_ENFORCE = True
PENAL_MATERIAL_SINGLE_FORCE_INCLUDE = True
PENAL_MATERIAL_SINGLE_FORCE_REASON = "penal_material_direct"
PENAL_MATERIAL_SINGLE_SELECTION = True
PENAL_MATERIAL_SINGLE_PRIORITY = True
PENAL_MATERIAL_SINGLE_STRICT = True
PENAL_MATERIAL_SINGLE_MIXED_ONLY = True
PENAL_MATERIAL_SINGLE_QUERY_PRIORITY = True
PENAL_MATERIAL_SINGLE_RETRIEVAL_PRIORITY = True
PENAL_MATERIAL_SINGLE_OUTPUT_LIMIT = 1
PENAL_MATERIAL_SINGLE_CANDIDATE_LIMIT = 3
PENAL_MATERIAL_SINGLE_RESCORE = True
PENAL_MATERIAL_SINGLE_RESCUE = True
PENAL_MATERIAL_SINGLE_RESCUE_SCORE = 2.1
PENAL_MATERIAL_SINGLE_RESCUE_LIMIT = 1
PENAL_MATERIAL_SINGLE_RESCUE_REASON = "penal_material_direct_rescue"
PENAL_MATERIAL_SINGLE_FINAL_LIMIT = 1
PENAL_MATERIAL_SINGLE_SCORE_FLOOR = 1.0
PENAL_MATERIAL_SINGLE_ALLOWED_SCOPE = "official"
PENAL_MATERIAL_SINGLE_ALLOWED_BRANCH = "penal"
PENAL_MATERIAL_SINGLE_ALLOWED_SLUG = "codigo-penal-lei-38-20"
PENAL_MATERIAL_SINGLE_QUERY_MODE = "targeted"
PENAL_MATERIAL_SINGLE_TOPUP = True
PENAL_MATERIAL_SINGLE_HARD_KEEP = True
PENAL_MATERIAL_SINGLE_HARD_DROP = True
PENAL_MATERIAL_SINGLE_HARD_FILTER = True
PENAL_MATERIAL_SINGLE_BRANCH_PRIORITY = True
PENAL_MATERIAL_SINGLE_SCORE_LABEL = "penal_material_direct_score"
PENAL_MATERIAL_SINGLE_TARGET = "penal_material_direct"
PENAL_MATERIAL_SINGLE_FALLBACK = True
PENAL_MATERIAL_SINGLE_FALLBACK_QUERY = (
    "crime patrimonial empregador trabalhador valores retidos infidelidade burla"
)
PENAL_MATERIAL_SINGLE_FALLBACK_REASON = "penal_material_direct_fallback"
PENAL_MATERIAL_SINGLE_FALLBACK_LIMIT = 1
PENAL_MATERIAL_SINGLE_FALLBACK_SCORE = 2.0
PENAL_MATERIAL_SINGLE_FALLBACK_KEEP = True
PENAL_MATERIAL_SINGLE_REQUIRE_TEXT = PENAL_MATERIAL_SINGLE_TERMS
PENAL_MATERIAL_SINGLE_AVOID_TEXT = PENAL_MATERIAL_SINGLE_NEGATIVE
PENAL_MATERIAL_SINGLE_REQUIRE_ARTICLE = PENAL_MATERIAL_SINGLE_ARTICLE_ALLOW
PENAL_MATERIAL_SINGLE_AVOID_ARTICLE = PENAL_MATERIAL_SINGLE_ARTICLE_DROP
PENAL_MATERIAL_SINGLE_REQUIRE_PAGE = PENAL_MATERIAL_SINGLE_PAGE_ALLOW
PENAL_MATERIAL_SINGLE_PREFER_PAGE = PENAL_MATERIAL_SINGLE_PAGE_PREFER
PENAL_MATERIAL_SINGLE_PATH = PENAL_MATERIAL_SINGLE_WHERE
PENAL_MATERIAL_SINGLE_QUERY_TEXT = PENAL_MATERIAL_SINGLE_QUERY
PENAL_MATERIAL_SINGLE_QUERY_TEXT_FALLBACK = PENAL_MATERIAL_SINGLE_FALLBACK_QUERY
PENAL_MATERIAL_SINGLE_MIN_SCORE = 1.5
PENAL_MATERIAL_SINGLE_MAX_COUNT = 1
PENAL_MATERIAL_SINGLE_MAX_BRANCH = 1
PENAL_MATERIAL_SINGLE_RESULT_LIMIT = 1
PENAL_MATERIAL_SINGLE_TARGET_COUNT = 1
PENAL_MATERIAL_SINGLE_BRANCH_NAME = "penal"
PENAL_MATERIAL_SINGLE_SCOPE_NAME = "official"
PENAL_MATERIAL_SINGLE_SLUG_NAME = "codigo-penal-lei-38-20"
PENAL_MATERIAL_SINGLE_REQUIRE_MIXED = True
PENAL_MATERIAL_SINGLE_TRIGGER_TERMS = MIXED_PENAL_TRIGGER_TERMS
PENAL_MATERIAL_SINGLE_TRIGGER_HINTS = PENAL_MATERIAL_QUESTION_HINTS
PENAL_MATERIAL_SINGLE_TRIGGER_FOLLOWUP = PENAL_MATERIAL_FOLLOWUP_HINTS
PENAL_MATERIAL_SINGLE_CONTEXT_TERMS = PENAL_MATERIAL_LABOR_CONTEXT
PENAL_MATERIAL_SINGLE_POSITIVE_TERMS = PENAL_MATERIAL_POSITIVE
PENAL_MATERIAL_SINGLE_NEGATIVE_TERMS = PENAL_MATERIAL_NEGATIVE
PENAL_MATERIAL_SINGLE_BOOST_ARTICLES = PENAL_MATERIAL_BOOST_ARTICLES
PENAL_MATERIAL_SINGLE_DROP_ARTICLES = PENAL_MATERIAL_DROP_ARTICLES
PENAL_MATERIAL_SINGLE_ALLOWED_PAGES = PENAL_MATERIAL_ALLOWED_PAGES
PENAL_MATERIAL_SINGLE_TARGET_PAGES = PENAL_MATERIAL_TARGET_PAGES
PENAL_MATERIAL_SINGLE_REQUIRED_TERMS = PENAL_MATERIAL_CONTEXTUAL_TERMS
PENAL_MATERIAL_SINGLE_MIN_HITS = PENAL_MATERIAL_TEXT_MIN_HITS
PENAL_MATERIAL_SINGLE_REQUIRED = True
PENAL_MATERIAL_SINGLE_CANONICAL = PENAL_MATERIAL_CANONICAL_TERMS
PENAL_MATERIAL_SINGLE_DIRECT_ONLY = False
PENAL_MATERIAL_SINGLE_FOR_FOLLOWUP = True
PENAL_MATERIAL_SINGLE_FOR_DIRECT = True
PENAL_MATERIAL_SINGLE_FOR_MIXED = True
PENAL_MATERIAL_SINGLE_FOR_PAYMENT = True
PENAL_MATERIAL_SINGLE_FOR_INDEMNITY = True
PENAL_MATERIAL_SINGLE_FOR_EMPLOYMENT = True
PENAL_MATERIAL_SINGLE_FOR_QUESTION = True
PENAL_MATERIAL_SINGLE_FOR_HISTORY = True
PENAL_MATERIAL_SINGLE_FOR_BRANCH = True
PENAL_MATERIAL_SINGLE_FOR_RESCUE = True
PENAL_MATERIAL_SINGLE_FOR_SELECTION = True
PENAL_MATERIAL_SINGLE_FOR_PRIORITY = True
PENAL_MATERIAL_SINGLE_FOR_LIMIT = True
PENAL_MATERIAL_SINGLE_FOR_OUTPUT = True
PENAL_MATERIAL_SINGLE_FOR_NOTES = False
PENAL_MATERIAL_SINGLE_FOR_DEBUG = False
PENAL_MATERIAL_SINGLE_READY = True
PENAL_MATERIAL_SINGLE_ACTIVE = True
PENAL_MATERIAL_SINGLE_FINAL = True
PENAL_MATERIAL_SINGLE_ENABLED = True
PENAL_MATERIAL_SINGLE_USED = True
PENAL_MATERIAL_SINGLE_STABLE = True
PENAL_MATERIAL_SINGLE_SAFE = True
PENAL_MATERIAL_SINGLE_PRECISE = True
PENAL_MATERIAL_SINGLE_PRODUCTION = True
PENAL_MATERIAL_SINGLE_CONTROLLED = True
PENAL_MATERIAL_SINGLE_TARGETED = True
PENAL_MATERIAL_SINGLE_HYBRID = True
PENAL_MATERIAL_SINGLE_QUERYABLE = True
PENAL_MATERIAL_SINGLE_BRANCHED = True
PENAL_MATERIAL_SINGLE_ARTICLE_BLOCK = True
PENAL_MATERIAL_SINGLE_PAGED = True
PENAL_MATERIAL_SINGLE_FILTERED = True
PENAL_MATERIAL_SINGLE_RERANKED = True
PENAL_MATERIAL_SINGLE_SELECTIVE = True
PENAL_MATERIAL_SINGLE_GUARDED = True
PENAL_MATERIAL_SINGLE_MATERIAL = True
PENAL_MATERIAL_SINGLE_END = True
TOPIC_NEGATIVE_PATTERNS = {
    "penal_material": PENAL_BAD_PATTERNS,
}
TOPIC_POSITIVE_PATTERNS = {
    "penal_material": PENAL_GOOD_PATTERNS,
}
TOPIC_QUERY_MARKERS_EXT = {
    "penal_material": PENAL_QUERY_MARKERS,
}
TOPIC_BAD_BRANCHES = {
    "penal_material": ("laboral",),
}
TOPIC_GOOD_BRANCHES = {
    "penal_material": ("penal",),
}
TOPIC_RELEVANCE_FLOOR = {
    "penal_material": 0.0,
}
TOPIC_SELECTION_MARKERS = TOPIC_QUERY_MARKERS_EXT
TOPIC_SELECTION_PATTERNS = TOPIC_POSITIVE_PATTERNS
TOPIC_SELECTION_NEGATIVE = TOPIC_NEGATIVE_PATTERNS
TOPIC_SELECTION_BRANCHES = TOPIC_GOOD_BRANCHES
TOPIC_SELECTION_BAD_BRANCHES = TOPIC_BAD_BRANCHES
TOPIC_SELECTION_REQUIRED = TOPIC_FORCE_PATTERNS
TOPIC_SELECTION_FLOOR = TOPIC_RELEVANCE_FLOOR
TOPIC_SELECTION_MAP = {
    "penal_material": "penal_material",
}
QUERY_TOKEN_MIN_LEN = 4
QUERY_THEME_STOPWORDS = {
    "pode",
    "podem",
    "deve",
    "deves",
    "ser",
    "são",
    "sao",
    "mais",
    "melhor",
    "prática",
    "pratica",
}
THEME_PRIORITY_TERMS = (
    "despedimento",
    "pagamento",
    "retenção",
    "retencao",
    "valores",
    "mútuo",
    "mutuo",
    "transferências",
    "transferencias",
    "mensagens",
)
THEME_NEGATIVE_PATTERNS = ("férias", "ferias", "licença", "licenca")
THEME_POSITIVE_PATTERNS = (
    "despedimento",
    "pagamento",
    "reintegra",
    "indemniza",
    "mútuo",
    "mutuo",
    "transferências",
    "transferencias",
    "mensagens",
)
QUERY_THEME_BRANCH_HINTS = BRANCH_QUERY_MARKERS
THEME_BRANCH_GOOD = BRANCH_GOOD_PATTERNS
THEME_BRANCH_BAD = BRANCH_BAD_PATTERNS
THEME_STRONG_MATCH_BOOST = 2.2
THEME_WEAK_MATCH_BOOST = 0.6
THEME_NEGATIVE_PENALTY = 2.0
THEME_BRANCH_MISS_PENALTY = 1.5
THEME_GENERIC_PENALTY = 1.2
THEME_TOKEN_OVERLAP_WEIGHT = 0.45
THEME_TOKEN_LIMIT = 10
TOPICAL_MIN_SCORE = -2.5
QUERY_THEME_LABELS = ("despedimento", "pagamento", "mútuo")
TOPICAL_QUERY_REQUIRED = ("penal_material",)
THEME_STRONG_QUERY_MARKERS = (
    PENAL_QUERY_MARKERS + LABOR_QUERY_MARKERS + CIVIL_QUERY_MARKERS
)
THEME_NOISE_PATTERNS = PENAL_BAD_PATTERNS + LABOR_BAD_PATTERNS + Civil_BAD_PATTERNS
THEME_REQUIRED_PATTERNS = PENAL_MATERIAL_REQUIRED
TOPIC_TOKEN_SCORE_CUTOFF = 0.2
THEMATIC_SCORE_CUTOFF = -1.0
THEMATIC_SELECTION_LIMIT = 8
THEMATIC_RERANK_BONUS = 1.8
THEMATIC_RERANK_PENALTY = 2.5
THEMATIC_RELEVANCE_LIMIT = 3
QUERY_THEME_FAVOR_MARKERS = {
    "penal": PENAL_MATERIAL_REQUIRED,
    "laboral": (
        "despedimento",
        "indemnização",
        "indemnizacao",
        "reintegração",
        "reintegracao",
        "salário",
        "salario",
    ),
    "civil": ("mútuo", "mutuo", "transferências", "transferencias", "mensagens"),
}
THEMATIC_HARD_NEGATIVE = {
    "penal": PENAL_BAD_PATTERNS,
    "laboral": LABOR_BAD_PATTERNS,
    "civil": Civil_BAD_PATTERNS,
}
RELEVANCE_MIN_FOR_CONFIRMATION = 0.6
RELEVANCE_MIN_FOR_SELECTION = -0.8
THEMATIC_CONTEXT_PENALTY = 1.5
THEMATIC_EXACT_BOOST = 1.4
THEMATIC_BRANCH_BOOST = 1.0
THEMATIC_BRANCH_BAD = 1.4
THEMATIC_BAD_TEXT = 1.6
THEMATIC_GOOD_TEXT = 1.2
THEMATIC_REQUIRED_BOOST = 1.8
TOPICAL_PRUNE_LIMIT = 2
THEME_QUERY_TOKENS_LIMIT = 12
TOPICAL_SCORE_MAX_PENALTY = -4.0
TOPICAL_SCORE_MAX_BONUS = 4.0
THEME_OVERRIDES = {
    "penal_material": PENAL_MATERIAL_REQUIRED,
}
THEME_PATTERN_NEGATIVES = {
    "penal_material": PENAL_BAD_PATTERNS,
}
THEME_PATTERN_POSITIVES = {
    "penal_material": PENAL_GOOD_PATTERNS,
}
THEME_BRANCH_REQUIRED = {
    "penal_material": "penal",
}
THEME_BRANCH_AVOID = {
    "penal_material": "laboral",
}
TOPIC_SPECIFIC_SCORE_FLOOR = -1.5
TOPIC_STRICT_SELECTION = ("penal_material",)
TOPICAL_FORCE_STRICT = True
THEMATIC_NOISE_PAGE_CUTOFF = 5
THEMATIC_LOW_VALUE_PAGES = {2, 19, 47, 115}
QUERY_INTENT_CRIME_WORDS = (
    "crime",
    "penal",
    "crimin",
    "sanções",
    "sancoes",
    "tipicidade",
)
QUERY_INTENT_LABOR_WORDS = (
    "despedimento",
    "salário",
    "salario",
    "trabalhador",
    "empregador",
)
QUERY_INTENT_CIVIL_WORDS = (
    "mútuo",
    "mutuo",
    "contrato",
    "prova",
    "transferências",
    "transferencias",
)
TOPICAL_SCORE_CLAMP = (-5.0, 5.0)
RELEVANCE_CONFIRMATION_BAD_PATTERNS = (
    PENAL_BAD_PATTERNS + LABOR_BAD_PATTERNS + Civil_BAD_PATTERNS
)
RELEVANCE_CONFIRMATION_GOOD_PATTERNS = (
    PENAL_GOOD_PATTERNS + LABOR_GOOD_PATTERNS + Civil_GOOD_PATTERNS
)
QUERY_TOKENS_MAX = 14
THEME_INNER_SCORE = 0.9
QUERY_TOKEN_EXCLUDE = STOPWORDS | QUERY_THEME_STOPWORDS
TOPICAL_STRICT_BAD_TEXT = (
    "revogação",
    "revogacao",
    "fontes de regulação",
    "fontes de regulacao",
)
THEMATIC_PRECISION_FLOOR = -0.5
RELEVANCE_SOURCE_PRUNE = True
THEME_TARGETED_BAD_PATTERNS = {
    "laboral": ("férias", "ferias", "licença", "licenca"),
    "penal": PENAL_BAD_PATTERNS,
    "civil": ("despedimento",),
}
THEME_TARGETED_GOOD_PATTERNS = {
    "laboral": (
        "despedimento",
        "ilicitude",
        "indemniza",
        "reintegra",
        "salário",
        "salario",
        "pagamento",
    ),
    "penal": PENAL_GOOD_PATTERNS,
    "civil": (
        "mútuo",
        "mutuo",
        "prova",
        "transferências",
        "transferencias",
        "mensagens",
    ),
}
TOPICAL_STRICT_SCORE = 1.3
THEMATIC_SORT_WEIGHT = 0.8
QUERY_RELEVANCE_CUTOFF = -0.5
QUESTION_THEME_PRIORITY = ("penal_material", "despedimento", "mútuo")
BRANCH_MIN_SELECTION = {"penal": 1, "laboral": 2, "civil": 2}
SELECTION_PAGE_PENALTY = {2: 2.0, 19: 1.2, 47: 0.8, 115: 0.8}
FILTER_TEXT_NEGATIVES = (
    "revogação da legislação",
    "revogacao da legislacao",
    "fontes de regulação",
    "fontes de regulacao",
)
THEME_QUERY_REQUIRED_WORDS = {
    "penal": ("crime", "penal", "tipicidade", "relevância penal", "relevancia penal"),
}
QUERY_THEME_LABEL_PRIORITY = ("penal", "laboral", "civil")
THEMATIC_BRANCH_REQUIRED_MATCH = {
    "penal": PENAL_QUERY_MARKERS,
    "laboral": LABOR_QUERY_MARKERS,
    "civil": CIVIL_QUERY_MARKERS,
}
THEMATIC_NOISE_SOURCE_PENALTY = 2.2
THEMATIC_OFFTOPIC_PENALTY = 2.4
TOPIC_SPECIFIC_REQUIRED = {
    "penal": PENAL_MATERIAL_REQUIRED,
}
THEME_SCORE_ARTICLE_SINGLE = 0.8
THEME_SCORE_ARTICLE_MULTI = -0.6
THEME_SCORE_TEXT_MATCH = 0.9
THEME_SCORE_TEXT_BAD = -1.1
THEME_SCORE_BRANCH_MATCH = 0.8
THEME_SCORE_BRANCH_MISS = -0.8
THEME_SCORE_LOW_VALUE_PAGE = -0.9
TOPICAL_SELECTION_TOP_N = 8
QUERY_THEME_HARD_FILTER = True
THEME_CONFIDENCE_MIN = 0.3
THEME_SELECTION_MIN = -0.6
THEME_PENAL_ARTICLE_REQUIRED = True
THEME_LABOR_PRIORITY_WORDS = (
    "despedimento",
    "ilicitude",
    "indemnização",
    "indemnizacao",
    "reintegração",
    "reintegracao",
    "impugnação",
    "impugnacao",
    "prova",
    "justa causa",
    "procedimento disciplinar",
)
THEME_PENAL_PRIORITY_WORDS = PENAL_MATERIAL_REQUIRED
THEME_CIVIL_PRIORITY_WORDS = (
    "mútuo",
    "mutuo",
    "prova",
    "transferências",
    "transferencias",
    "mensagens",
)
THEME_BRANCH_PRIORITY_WORDS = {
    "laboral": THEME_LABOR_PRIORITY_WORDS,
    "penal": THEME_PENAL_PRIORITY_WORDS,
    "civil": THEME_CIVIL_PRIORITY_WORDS,
}
THEME_ARTICLE_BADNESS_THRESHOLD = 3
TOPICAL_PRUNE_SCORE = -1.2
RELEVANCE_HARD_PRUNE = True
THEMATIC_QUESTION_TERMS = THEME_PRIORITY_TERMS
THEMATIC_BRANCH_STRICT = True
THEMATIC_FORCE_PATTERNS = PENAL_MATERIAL_REQUIRED
TOPICAL_SCORE_BRANCH_PRIORITY = 0.7
TOPICAL_SCORE_TEXT_PRIORITY = 0.9
TOPICAL_SCORE_BAD_PRIORITY = -1.2
TOPICAL_SCORE_PAGE_PRIORITY = -0.8
TOPICAL_LABOR_STRICT_PATTERNS = (
    "despedimento",
    "salário",
    "salario",
    "indemniza",
    "reintegra",
    "impugna",
    "prova",
    "justa causa",
    "procedimento disciplinar",
)
TOPICAL_CIVIL_STRICT_PATTERNS = (
    "mútuo",
    "mutuo",
    "prova",
    "transferências",
    "transferencias",
)
TOPICAL_PENAL_STRICT_PATTERNS = PENAL_MATERIAL_REQUIRED
TOPICAL_STRICT_PATTERNS = {
    "laboral": TOPICAL_LABOR_STRICT_PATTERNS,
    "civil": TOPICAL_CIVIL_STRICT_PATTERNS,
    "penal": TOPICAL_PENAL_STRICT_PATTERNS,
}
THEME_FINAL_SELECTION_LIMIT = 6
THEME_FORCE_DROP_GENERIC = True
THEME_FORCE_DROP_PENAL_GENERIC = True
THEME_DROP_WORDS = (
    "revogação",
    "revogacao",
    "fontes de regulação",
    "fontes de regulacao",
    "encerramento de estabelecimento",
)
QUERY_THEME_USE_BRANCH = True
THEME_MUST_MATCH_TEXT = True
THEME_EXCERPT_MATCH_MIN = 0.5
TOPICAL_BONUS_PER_TOKEN = 0.35
TOPICAL_MAX_TOKENS_USED = 8
THEME_HARD_KEEP_PATTERNS = {
    "laboral": (
        "despedimento",
        "ilicitude",
        "reintegra",
        "indemniza",
        "pagamento",
        "impugna",
        "prova",
    ),
    "civil": ("mútuo", "mutuo", "prova", "transferências", "transferencias"),
    "penal": PENAL_MATERIAL_REQUIRED,
}
TOPICAL_HARD_DROP_PATTERNS = {
    "laboral": LABOR_BAD_PATTERNS,
    "civil": ("despedimento",),
    "penal": PENAL_BAD_PATTERNS,
}
THEME_OFFTOPIC_LIMIT = -1.4
TOPICAL_USE_QUERY_STRING = True
QUERY_REQUIRED_TOKEN_COUNT = 1
THEME_STRICT_RETAIN = True
THEME_FORCE_PENAL_DROP = True
TOPICAL_BRANCH_RELEVANCE_MIN = -0.3
TOPICAL_ARTICLE_RELEVANCE_MIN = -0.2
TOPICAL_HARD_SCORE_MIN = -2.0
THEME_FINAL_PRUNE_LIMIT = 5
THEME_DROP_GENERIC_LABOR = True
THEME_DROP_GENERIC_CIVIL = True
THEME_DROP_GENERIC_PENAL = True
TOPICAL_EXPLICIT_QUERY = True
TOPICAL_REQUIRED_WORDSET = PENAL_MATERIAL_REQUIRED
TOPICAL_FILTER_REQUIRED = True
THEME_NONMATCH_PENALTY = 2.0
THEME_HARD_SELECTION_LIMIT = 5
QUERY_TOPICAL_WEIGHT = 1.0
THEME_SCORE_CLAMP = (-4.0, 4.0)
THEME_SELECTION_SCORE_MIN = -0.6
TOPICAL_CHUNK_MIN = -0.6
THEME_REMOVE_PAGE_19_FOR_LABOR = True
THEME_REMOVE_PAGE_115_FOR_LABOR = True
THEME_REMOVE_PAGE_47_FOR_LABOR = False
THEME_PAGE_PENALTY_MAP = {19: 1.0, 115: 0.8, 47: 0.2}
THEME_QUERY_FOCUS = True
QUERY_FOCUS_WORDS = THEME_PRIORITY_TERMS
TOPICAL_MODEL = "heuristic"
THEME_SELECTIVE_ARTICLE_CONFIRM = True
THEME_QUESTION_HARD_FILTER = True
THEME_MATCH_REQUIRED_FOR_CONFIRM = True
TOPICAL_RELEVANCE_FORCE = True
THEME_MIXED_BRANCH_PENAL_STRICT = True
THEME_MIXED_BRANCH_LABOR_STRICT = True
THEME_LIMIT_OFFICIAL = 12
THEME_LIMIT_PENAL = 1
THEME_LIMIT_LABOR = 4
THEME_LIMIT_CIVIL = 4
THEME_BRANCH_LIMITS = {"penal": 1, "laboral": 4, "civil": 4}
QUERY_BRANCH_FOCUS = True
THEME_FORCE_BRANCH_WORDS = True
THEME_SELECTION_REWRITE = True
LABOR_OBJECTIVE_DISMISSAL_REASON = "labor_objective_dismissal_direct_article"
PUBLIC_ASSET_REASON = "public_asset_direct_article"
STATE_AGENT_LIABILITY_REASON = "state_agent_liability_direct_article"
CIVIL_LOAN_REASON = "civil_loan_direct_article"
ADMIN_ACT_REASON = "admin_act_direct_article"
LABOR_OBJECTIVE_DISMISSAL_ARTICLES = (
    "284",
    "285",
    "286",
    "287",
    "288",
    "289",
    "290",
    "298",
    "300",
    "308",
    "310",
    "313",
)
CIVIL_LOAN_ARTICLES = ("342", "362", "483", "798", "804", "805")
ADMIN_ACT_TARGETS = (
    ("lei-n-o-2-94-de-14-de-janeiro", ("9", "10", "11", "13"), 142.0),
    (
        "codigo-processo-contencioso-administrativo-33-22",
        ("32", "64", "69", "73", "75"),
        136.0,
    ),
)


def _query_tokens(question: str) -> set[str]:
    tokens = {
        token.lower()
        for token in WORD_RE.findall(question or "")
        if len(token) >= QUERY_TOKEN_MIN_LEN
    }
    tokens = {token for token in tokens if token not in QUERY_TOKEN_EXCLUDE}
    return set(list(tokens)[:QUERY_TOKENS_MAX])


def _question_focus_terms(question: str) -> tuple[str, ...]:
    text = _normalize(question)
    terms: list[str] = []
    if any(
        token in text
        for token in (
            "despedimento",
            "despedido",
            "aviso prévio",
            "aviso previo",
            "indemnização",
            "indemnizacao",
            "reintegração",
            "reintegracao",
        )
    ):
        terms.extend(
            (
                "despedimento",
                "ilicitude",
                "reintegra",
                "indemniza",
                "compensação",
                "compensacao",
                "aviso prévio",
                "aviso previo",
            )
        )
    if any(
        token in text
        for token in (
            "compensação",
            "compensacao",
            "indemnização",
            "indemnizacao",
            "reintegração",
            "reintegracao",
        )
    ):
        terms.extend(LABOR_COMPENSATION_TERMS)
    if any(token in text for token in ("pagamento", "salário", "salario", "descontos")):
        terms.extend(("pagamento", "salário", "salario", "recibo", "descontos"))
    if any(token in text for token in PENAL_QUERY_MARKERS):
        terms.extend(PENAL_MATERIAL_REQUIRED)
    if any(
        token in text
        for token in (
            "mútuo",
            "mutuo",
            "transferências",
            "transferencias",
            "mensagens",
            "prova",
            "contrato escrito",
            "escrito",
        )
    ):
        terms.extend(CIVIL_MUTUO_TERMS + CIVIL_MUTUO_PROOF_TERMS)
    if any(token in text for token in INSTITUTION_TERMS):
        terms.extend(INSTITUTION_TERMS)
    return tuple(dict.fromkeys(terms))


def _focus_overlap_score(question: str, chunk: RetrievedChunk) -> float:
    terms = _question_focus_terms(question)
    if not terms:
        return 0.0
    text = _normalize(chunk.text)
    hits = sum(1 for term in terms if term in text)
    if hits == 0:
        return -1.4
    score = min(2.4, hits * 0.6)
    if any(term in _normalize(question) for term in LABOR_COMPENSATION_TERMS):
        if any(term in text for term in LABOR_COMPENSATION_TERMS):
            score += 1.6
        if any(term in text for term in LABOR_COMPENSATION_NEGATIVE):
            score -= 2.2
    if any(term in _normalize(question) for term in CIVIL_MUTUO_TERMS):
        if any(term in text for term in CIVIL_MUTUO_TERMS):
            score += 2.0
        else:
            score -= 2.8
        if any(
            term in _normalize(question) for term in CIVIL_MUTUO_PROOF_TERMS
        ) and any(term in text for term in CIVIL_MUTUO_PROOF_TERMS):
            score += 0.8
        if any(term in text for term in CIVIL_MUTUO_NEGATIVE):
            score -= 3.0
    if any(term in _normalize(question) for term in INSTITUTION_TERMS):
        if any(term in text for term in INSTITUTION_TERMS):
            score += 1.4
        if any(term in text for term in INSTITUTION_NEGATIVE):
            score -= 2.4
    return score


def _labor_offtopic_penalty(question: str, chunk: RetrievedChunk) -> float:
    if _chunk_branch(chunk) != "laboral":
        return 0.0
    text = _normalize(chunk.text)
    focus = _question_focus_terms(question)
    if not focus:
        return 0.0
    if any(term in text for term in focus):
        return 0.0
    if any(
        term in text
        for term in (
            "férias",
            "ferias",
            "licença",
            "licenca",
            "não discriminação",
            "nao discriminação",
            "nao discriminacao",
            "comissão de serviço",
            "comissao de servico",
        )
    ):
        return -2.5
    return -0.8


def _question_specific_score(
    classification: LegalClassification, question: str, chunk: RetrievedChunk
) -> float:
    return _focus_overlap_score(question, chunk) + _labor_offtopic_penalty(
        question, chunk
    )


def _question_specific_filter(
    classification: LegalClassification, question: str, chunk: RetrievedChunk
) -> bool:
    score = _question_specific_score(classification, question, chunk)
    normalized_question = _normalize(question)
    normalized_text = _normalize(chunk.text)
    if any(term in normalized_question for term in CIVIL_MUTUO_TERMS):
        if _chunk_branch(chunk) != "civil":
            return False
        return score >= 1.2
    if classification.topic_route == "sucessoes":
        if _chunk_branch(chunk) not in {"familia", "civil"}:
            return False
        if any(
            term in normalized_text
            for term in (
                "herança",
                "heranca",
                "sucessão",
                "sucessao",
                "herdeiros",
                "inventário",
                "inventario",
                "testamento",
                "partilha",
            )
        ):
            return score >= 0.2
        return False
    if classification.topic_route == "cpp":
        has_core = any(
            term in normalized_text
            for term in (
                "prisão preventiva",
                "prisao preventiva",
                "medidas de coacção",
                "medidas de coaccao",
                "medidas de coação",
                "medidas de coacao",
            )
        )
        has_recurso = any(
            term in normalized_text
            for term in ("recurso", "recorrer", "interposição", "interposicao")
        )
        has_prazo = any(term in normalized_text for term in ("prazo", "prazos"))
        if has_core and has_recurso and has_prazo:
            return score >= 0.0
        if has_core and (has_recurso or has_prazo):
            return score >= 1.0
        return False
    if any(term in normalized_question for term in LABOR_COMPENSATION_TERMS):
        if _chunk_branch(chunk) != "laboral":
            return False
        return score >= 0.8
    if any(term in normalized_question for term in INSTITUTION_TERMS):
        if _chunk_branch(chunk) != "laboral":
            return False
        return score >= 0.6
    if _chunk_branch(chunk) == "laboral" and any(
        candidate == "laboral"
        for candidate in classification.branch_candidates
        or [classification.main_branch]
    ):
        return score >= -0.2
    if _chunk_branch(chunk) == "penal" and classification.main_branch == "misto":
        return score >= 0.2
    return score >= -0.8


def _apply_question_specific_filter(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if _is_journalist_leak_public_interest_query(question):
        direct = [item for item in items if _is_journalist_direct_evidence(item)]
        filtered_rest = [
            item
            for item in items
            if not _is_journalist_direct_evidence(item)
            and _question_specific_filter(classification, question, item.chunk)
        ]
        ranked = _dedupe_ranked(direct + filtered_rest) if direct else filtered_rest
        if ranked:
            return sorted(
                ranked,
                key=lambda item: (
                    item.score
                    + _question_specific_score(classification, question, item.chunk),
                    item.score,
                ),
                reverse=True,
            )

    filtered = [
        item
        for item in items
        if _is_curated_direct_evidence(item)
        or _question_specific_filter(classification, question, item.chunk)
    ]
    ranked = filtered or items
    return sorted(
        ranked,
        key=lambda item: (
            item.score + _question_specific_score(classification, question, item.chunk),
            item.score,
        ),
        reverse=True,
    )


def _strict_question_materiality_filter(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    normalized_question = _normalize(question)
    selected = items
    if classification.topic_route == "sucessoes":
        selected = [
            item
            for item in selected
            if _chunk_branch(item.chunk) in {"familia", "civil"}
            and any(
                term in _normalize(item.chunk.text)
                for term in (
                    "herança",
                    "heranca",
                    "sucessão",
                    "sucessao",
                    "herdeiros",
                    "inventário",
                    "inventario",
                    "testamento",
                    "partilha",
                )
            )
        ] or selected
    if classification.topic_route == "cpp":
        selected = [
            item
            for item in selected
            if any(
                term in _normalize(item.chunk.text)
                for term in (
                    "prisão preventiva",
                    "prisao preventiva",
                    "medidas de coacção",
                    "medidas de coaccao",
                    "medidas de coação",
                    "medidas de coacao",
                )
            )
        ] or selected
    if all(
        term in normalized_question
        for term in ("compensação", "indemnização", "reintegração")
    ) or all(
        term in normalized_question
        for term in ("compensacao", "indemnizacao", "reintegracao")
    ):
        selected = [
            item
            for item in selected
            if _chunk_branch(item.chunk) == "laboral"
            and any(
                term in _normalize(item.chunk.text)
                for term in (
                    "compensação",
                    "compensacao",
                    "indemnização",
                    "indemnizacao",
                    "reintegra",
                    "ilicitude",
                    "despedimento",
                )
            )
            and not any(
                term in _normalize(item.chunk.text)
                for term in LABOR_COMPENSATION_NEGATIVE
            )
        ] or selected
    if any(term in normalized_question for term in CIVIL_MUTUO_TERMS):
        selected = [
            item
            for item in selected
            if _chunk_branch(item.chunk) == "civil"
            and any(term in _normalize(item.chunk.text) for term in CIVIL_MUTUO_TERMS)
            and not any(
                term in _normalize(item.chunk.text) for term in CIVIL_MUTUO_NEGATIVE
            )
        ] or selected
    if (
        classification.specificity == "follow_up"
        and classification.main_branch == "misto"
    ):
        selected = [
            item
            for item in selected
            if _chunk_branch(item.chunk) in {"laboral", "penal"}
        ] or selected
    return selected


def _limit_offtopic_tail(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    normalized_question = _normalize(question)
    if any(term in normalized_question for term in CIVIL_MUTUO_TERMS):
        return items[:3]
    if classification.topic_route in {"sucessoes", "cpp"}:
        return items[:3]
    if classification.specificity == "follow_up":
        return items[:4]
    return items


def _refine_by_question_materiality(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    refined = _strict_question_materiality_filter(classification, question, items)
    refined = _apply_question_specific_filter(classification, question, refined)
    return _limit_offtopic_tail(classification, question, refined)


def _needs_ascii_fallback(text: str) -> bool:
    return False


def _penal_branch_present(items: list[RetrievalEvidence]) -> bool:
    return any(_chunk_branch(item.chunk) == "penal" for item in items)


def _mixed_follow_up_branch_balance(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if not (
        classification.specificity == "follow_up"
        and classification.main_branch == "misto"
    ):
        return items
    if _penal_branch_present(items):
        return items
    return items[:4]


def _refine_official_for_question(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    refined = _refine_by_question_materiality(classification, question, items)
    return _mixed_follow_up_branch_balance(classification, question, refined)


def _question_specific_branch_filter(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    return _refine_official_for_question(classification, question, items)


def _text_matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _branch_query_active(question: str, branch: str) -> bool:
    text = _normalize(question)
    return any(marker in text for marker in BRANCH_QUERY_MARKERS.get(branch, ()))


def _needs_penal_material_query(
    classification: LegalClassification,
    question: str,
    conversation_history: list[str] | None,
) -> bool:
    if (
        classification.main_branch != "misto"
        or "penal" not in classification.branch_candidates
    ):
        return False
    text = _normalize(question)
    if any(marker in text for marker in PENAL_MATERIAL_QUESTION_HINTS) and any(
        term in text for term in MIXED_PAYMENT_TERMS
    ):
        return True
    if classification.specificity == "follow_up" and conversation_history:
        history_text = _normalize(" ".join(conversation_history[-6:]))
        if any(marker in text for marker in PENAL_MATERIAL_FOLLOWUP_HINTS) and any(
            term in history_text for term in PENAL_MATERIAL_LABOR_CONTEXT
        ):
            return True
    return False


def _penal_material_query_text(
    question: str, conversation_history: list[str] | None
) -> str:
    if conversation_history:
        anchor = next(
            (
                item.split(":", 1)[1].strip()
                for item in reversed(conversation_history)
                if item.lower().startswith("utilizador:")
            ),
            "",
        )
        if anchor:
            return PENAL_MATERIAL_FOLLOWUP_TEMPLATE.format(
                question=question, anchor=anchor
            )
    return PENAL_MATERIAL_QUERY_TEMPLATE.format(question=question)


def _penal_material_chunk_score(chunk: RetrievedChunk) -> float:
    text = _normalize(chunk.text)
    metadata = chunk.metadata or {}
    refs = metadata.get("article_references") or []
    score = 0.0
    if metadata.get("segmentation") == "article_block":
        score += PENAL_MATERIAL_ARTICLE_BLOCK_BONUS
    if any(term in text for term in PENAL_MATERIAL_POSITIVE):
        score += PENAL_MATERIAL_POSITIVE_BONUS
    if any(term in text for term in PENAL_MATERIAL_CONTEXTUAL_TERMS):
        score += PENAL_MATERIAL_CONTEXT_BOOST
    if any(term in text for term in PENAL_MATERIAL_NEGATIVE):
        score += PENAL_MATERIAL_NEGATIVE_PENALTY
    if chunk.page in PENAL_MATERIAL_TARGET_PAGES:
        score += PENAL_MATERIAL_PAGE_BONUS
    elif chunk.page and chunk.page not in PENAL_MATERIAL_ALLOWED_PAGES:
        score += PENAL_MATERIAL_PAGE_PENALTY
    for article in refs:
        normalized = str(article).replace(".", "")
        score += PENAL_MATERIAL_BOOST_ARTICLES.get(normalized, 0.0)
        if normalized in PENAL_MATERIAL_DROP_ARTICLES:
            score += PENAL_MATERIAL_DROP_PENALTY
    return score


def _penal_material_candidate(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    if metadata.get("legal_branch") != "penal":
        return False
    if metadata.get("source_scope") != "official":
        return False
    if metadata.get("diploma_slug") != PENAL_MATERIAL_SLUG:
        return False
    text = _normalize(chunk.text)
    refs = {
        (str(item).replace(".", ""))
        for item in (metadata.get("article_references") or [])
    }
    if refs & PENAL_MATERIAL_DROP_ARTICLES:
        return False
    if not (refs & PENAL_MATERIAL_ARTICLES):
        return False
    if any(term in text for term in PENAL_MATERIAL_NEGATIVE):
        return False
    return any(term in text for term in PENAL_MATERIAL_CANONICAL_TERMS)


def _penal_material_rescue(
    question: str, classification: LegalClassification, ranked: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if not _needs_penal_material_query(classification, question, None):
        return ranked
    penal_items = [
        item
        for item in ranked
        if item.source_bucket == "official" and _penal_material_candidate(item.chunk)
    ]
    if not penal_items:
        return ranked
    boosted: list[RetrievalEvidence] = []
    for item in penal_items:
        boosted.append(
            RetrievalEvidence(
                query_used=item.query_used,
                chunk=item.chunk,
                score=item.score
                + _penal_material_chunk_score(item.chunk)
                + PENAL_MATERIAL_PROMOTION_BONUS,
                retrieval_reason=PENAL_MATERIAL_REASON,
                source_bucket=item.source_bucket,
            )
        )
    survivors = [item for item in ranked if item not in penal_items]
    merged = _dedupe_ranked(survivors + boosted)
    return sorted(merged, key=lambda item: item.score, reverse=True)


def _successions_direct_rescue(
    question: str, classification: LegalClassification
) -> list[RetrievalEvidence]:
    if classification.topic_route != "sucessoes":
        return []
    if "Código Civil" not in classification.requested_diplomas and "Codigo Civil" not in classification.requested_diplomas:
        return []
    terms = (
        "herança",
        "heranca",
        "sucessão",
        "sucessao",
        "herdeiro",
        "herdeiros",
        "testamento",
        "partilha",
        "inventário",
        "inventario",
        "aceitação",
        "aceitacao",
        "repúdio",
        "repudio",
        "colação",
        "colacao",
    )
    where_clause = "diploma_slug = %s AND source_scope = 'official' AND (" + " OR ".join(
        ["text_content ILIKE %s"] * len(terms)
    ) + ")"
    params: list[str] = ["codigo-civil"] + [f"%{term}%" for term in terms]
    sql = f"""
        SELECT id, source, title, link_original, page, article_number, law_status,
               source_scope, document_id, metadata, text_content, embedding
        FROM legal_segments
        WHERE {where_clause}
        ORDER BY page ASC, article_number ASC
        LIMIT 10
    """
    try:
        with postgres_manager.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception:
        return []
    boosted: list[RetrievalEvidence] = []
    for row in rows:
        chunk = postgres_manager._segment_to_chunk(row)
        boosted.append(
            RetrievalEvidence(
                query_used=question,
                chunk=chunk,
                score=50.0 + _score_chunk(classification, chunk, "topic_route"),
                retrieval_reason="topic_route",
                source_bucket=_source_bucket(chunk),
            )
        )
    return boosted


def _cpp_direct_rescue(
    question: str, classification: LegalClassification
) -> list[RetrievalEvidence]:
    if classification.topic_route != "cpp":
        return []
    question_norm = _normalize(question)
    needs_recurso = any(
        term in question_norm
        for term in (
            "recurso",
            "recorrer",
            "interposição",
            "interposicao",
            "apelação",
            "apelacao",
            "reclamação",
            "reclamacao",
        )
    )
    needs_prazo = any(term in question_norm for term in ("prazo", "prazos", "dias"))
    if not (needs_recurso or needs_prazo):
        return []

    exact_phrase_terms = (
        "prazo de recurso",
        "prazo de interposição",
        "prazo de interposicao",
        "prazo para recurso",
        "prazo para recorrer",
        "interposição de recurso",
        "interposicao de recurso",
        "recurso",
        "prazo",
    )
    broad_terms = exact_phrase_terms + (
        "apelação",
        "apelacao",
        "reclamação",
        "reclamacao",
        "impugnação",
        "impugnacao",
    )
    terms = exact_phrase_terms if needs_recurso and needs_prazo else broad_terms
    where_clause = (
        "diploma_slug = %s AND source_scope = 'official' AND legal_branch = 'penal' AND ("
        + " OR ".join(["text_content ILIKE %s"] * len(terms))
        + ")"
    )
    params: list[str] = ["codigo-processo-penal-lei-39-20"] + [f"%{term}%" for term in terms]
    sql = f"""
        SELECT id, source, title, link_original, page, article_number, law_status,
               source_scope, document_id, metadata, text_content, embedding
        FROM legal_segments
        WHERE {where_clause}
        ORDER BY
            CASE
                WHEN article_number IN ('463', '466', '460') THEN -1
                WHEN article_number IN ('294', '295') THEN 0
                WHEN text_content ILIKE '%prazo de recurso%' THEN 0
                WHEN text_content ILIKE '%prazo de interposição%' THEN 1
                WHEN text_content ILIKE '%interposição de recurso%' THEN 2
                WHEN text_content ILIKE '%prazo%' AND text_content ILIKE '%recurso%' THEN 3
                ELSE 9
            END,
            page ASC,
            article_number ASC
        LIMIT 12
    """
    try:
        with postgres_manager.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception:
        return []
    boosted: list[RetrievalEvidence] = []
    for row in rows:
        chunk = postgres_manager._segment_to_chunk(row)
        boosted.append(
            RetrievalEvidence(
                query_used=question,
                chunk=chunk,
                score=48.0 + _score_chunk(classification, chunk, "topic_route"),
                retrieval_reason="topic_route",
                source_bucket=_source_bucket(chunk),
            )
        )
    return boosted


def _thematic_relevance_score(
    classification: LegalClassification, question: str, chunk: RetrievedChunk
) -> float:
    text = _normalize(chunk.text)
    branch = _chunk_branch(chunk)
    score = 0.0
    qtokens = _query_tokens(question)
    if qtokens:
        overlap = sum(1 for token in qtokens if token in text)
        score += min(TOPICAL_SCORE_MAX_BONUS, overlap * TOPICAL_BONUS_PER_TOKEN)
    for branch_name, words in THEME_BRANCH_PRIORITY_WORDS.items():
        active = (
            branch == branch_name
            or _branch_query_active(question, branch_name)
            or branch_name in classification.branch_candidates
        )
        if not active:
            continue
        if _text_matches_any(text, words):
            score += THEMATIC_GOOD_TEXT
        if _text_matches_any(text, TOPICAL_HARD_DROP_PATTERNS.get(branch_name, ())):
            score -= THEME_NONMATCH_PENALTY
    if chunk.page in THEME_PAGE_PENALTY_MAP and branch in {"laboral", "penal"}:
        score -= THEME_PAGE_PENALTY_MAP[chunk.page]
    if branch == "penal" and classification.main_branch == "misto":
        if _text_matches_any(text, PENAL_GOOD_PATTERNS):
            score += 1.8
        if _text_matches_any(text, PENAL_BAD_PATTERNS):
            score -= 2.4
    if branch == "laboral" and any(
        candidate == "laboral" for candidate in classification.branch_candidates
    ):
        if _text_matches_any(text, TOPICAL_LABOR_STRICT_PATTERNS):
            score += 1.0
        if _text_matches_any(text, LABOR_BAD_PATTERNS):
            score -= 1.2
    if branch == "civil" and any(
        candidate == "civil" for candidate in classification.branch_candidates
    ):
        if _text_matches_any(text, TOPICAL_CIVIL_STRICT_PATTERNS):
            score += 1.0
        if _text_matches_any(text, Civil_BAD_PATTERNS):
            score -= 1.0
    return max(THEME_SCORE_CLAMP[0], min(THEME_SCORE_CLAMP[1], score))


def _chunk_relevant_to_question(
    classification: LegalClassification, question: str, chunk: RetrievedChunk
) -> bool:
    score = _thematic_relevance_score(classification, question, chunk)
    branch = _chunk_branch(chunk)
    text = _normalize(chunk.text)
    if classification.topic_route == "cpp":
        question_norm = _normalize(question)
        needs_deadline_context = any(
            term in question_norm for term in ("prazo", "prazos", "dias", "horas")
        ) and any(
            term in question_norm
            for term in (
                "recurso",
                "recorrer",
                "interposição",
                "interposicao",
                "apelação",
                "apelacao",
            )
        )
        if needs_deadline_context:
            deadline_terms = (
                "recurso",
                "interpor recurso",
                "interposição",
                "interposicao",
                "recurso subordinado",
                "prazo",
                "notificação",
                "notificacao",
                "subida",
                "admissão",
                "admissao",
            )
            return (
                branch == "penal"
                and score >= QUERY_RELEVANCE_CUTOFF
                and _text_matches_any(text, deadline_terms)
            )
        cpp_terms = (
            "prisão preventiva",
            "prisao preventiva",
            "medidas de coacção",
            "medidas de coaccao",
            "medidas de coação",
            "medidas de coacao",
            "revogação",
            "revogacao",
            "substituição",
            "substituicao",
            "recurso",
            "prazo",
        )
        disallowed_cpp_terms = (
            "expulsão do território",
            "expulsao do territorio",
            "revogação da legislação",
            "revogacao da legislacao",
            "recurso a prostituição",
            "recurso a prostituicao",
            "substituição do perito",
            "substituicao do perito",
        )
        if _text_matches_any(text, disallowed_cpp_terms):
            return False
        return (
            branch == "penal"
            and score >= QUERY_RELEVANCE_CUTOFF
            and _text_matches_any(text, cpp_terms)
        )
    if classification.topic_route == "constitucional":
        constitutional_terms = (
            "habeas corpus",
            "privação da liberdade",
            "privacao da liberdade",
            "detidos e presos",
            "detenção",
            "detencao",
            "liberdade",
            "garantias",
        )
        return (
            branch == "constitucional"
            and score >= QUERY_RELEVANCE_CUTOFF
            and _text_matches_any(text, constitutional_terms)
        )
    if branch == "penal" and classification.main_branch == "misto":
        return score >= THEME_SELECTION_SCORE_MIN and _text_matches_any(
            text, PENAL_MATERIAL_REQUIRED
        )
    if branch == "laboral" and any(
        candidate == "laboral"
        for candidate in classification.branch_candidates
        or [classification.main_branch]
    ):
        return score >= QUERY_RELEVANCE_CUTOFF and (
            _text_matches_any(text, TOPICAL_LABOR_STRICT_PATTERNS)
            or not THEME_MUST_MATCH_TEXT
        )
    if branch == "civil" and any(
        candidate == "civil"
        for candidate in classification.branch_candidates
        or [classification.main_branch]
    ):
        return score >= QUERY_RELEVANCE_CUTOFF and (
            _text_matches_any(text, TOPICAL_CIVIL_STRICT_PATTERNS)
            or not THEME_MUST_MATCH_TEXT
        )
    return score >= TOPICAL_CHUNK_MIN


def _jurisprudence_relevant_to_question(question: str, chunk: RetrievedChunk) -> bool:
    text = _normalize(
        " ".join(
            [
                chunk.title or "",
                chunk.source or "",
                chunk.text or "",
                " ".join(str(value) for value in (chunk.metadata or {}).values() if isinstance(value, str)),
            ]
        )
    )
    query = _normalize(question)
    if not text or not query:
        return False

    strong_query_terms = [
        token
        for token in WORD_RE.findall(query)
        if len(token) >= 5
        and token not in STOPWORDS
        and token
        not in {
            "jurisprudencia",
            "jurisprudência",
            "acordao",
            "acórdão",
            "acordaos",
            "acórdãos",
            "tribunal",
            "angolana",
            "angolano",
            "cenario",
            "cenário",
            "temas",
            "proximos",
            "próximos",
            "mostra",
        }
    ]
    overlap = sum(1 for token in set(strong_query_terms) if token in text)
    if overlap >= 2:
        return True

    case_specific_markers = (
        "filmagem",
        "filmar",
        "gravacao",
        "gravação",
        "policia",
        "polícia",
        "policial",
        "desobediencia",
        "desobediência",
        "resistencia",
        "resistência",
        "funcionario",
        "funcionário",
        "autoridade",
    )
    query_markers = [marker for marker in case_specific_markers if marker in query]
    if query_markers:
        return any(marker in text for marker in query_markers)

    return False


def _filter_by_question_relevance(
    classification: LegalClassification, question: str, items: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if _needs_jurisprudence_query(question, classification):
        juris = [
            item
            for item in items
            if (item.chunk.metadata or {}).get("document_kind") == "jurisprudence"
            and _jurisprudence_relevant_to_question(question, item.chunk)
        ]
        if juris:
            kept = [
                item
                for item in items
                if item.retrieval_reason == "legal_concept_rescue"
                or _is_curated_direct_evidence(item)
                or _chunk_relevant_to_question(classification, question, item.chunk)
                or (
                    (item.chunk.metadata or {}).get("document_kind") == "jurisprudence"
                    and _jurisprudence_relevant_to_question(question, item.chunk)
                )
            ]
            return kept or items
    filtered = [
        item
        for item in items
        if item.retrieval_reason == "legal_concept_rescue"
        or _is_curated_direct_evidence(item)
        or _chunk_relevant_to_question(classification, question, item.chunk)
    ]
    return filtered or items


def _promote_jurisprudence_if_requested(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    if not _needs_jurisprudence_query(question, classification):
        return official
    jurisprudence = [
        item
        for item in official
        if (item.chunk.metadata or {}).get("document_kind") == "jurisprudence"
        and _jurisprudence_relevant_to_question(question, item.chunk)
    ]
    if not jurisprudence:
        return official
    jurisprudence = sorted(jurisprudence, key=lambda item: item.score, reverse=True)[:2]
    others = [item for item in official if item not in jurisprudence]
    return jurisprudence + others


def _prioritize_legal_concept_rescue(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    concept_items = [
        item for item in official if item.retrieval_reason == "legal_concept_rescue"
    ]
    if not concept_items:
        return official

    concept_ids = {item.chunk.chunk_id for item in concept_items}
    selected = sorted(concept_items, key=lambda item: item.score, reverse=True)

    if _needs_jurisprudence_query(question, classification):
        selected.extend(
            item
            for item in official
            if item.chunk.chunk_id not in concept_ids
            and (item.chunk.metadata or {}).get("document_kind") == "jurisprudence"
            and _jurisprudence_relevant_to_question(question, item.chunk)
        )

    if len(selected) < 2:
        selected.extend(
            item
            for item in official
            if item.chunk.chunk_id not in {entry.chunk.chunk_id for entry in selected}
            and item.retrieval_reason in {"requested_article_direct", "article"}
            and _chunk_relevant_to_question(classification, question, item.chunk)
        )

    return selected[:THEME_LIMIT_OFFICIAL] or official


def _balance_journalist_public_interest_evidence(
    official: list[RetrievalEvidence], limit: int = 10
) -> list[RetrievalEvidence]:
    selected: list[RetrievalEvidence] = []
    seen_chunk_ids: set[str] = set()

    for slug, article in JOURNALIST_LEAK_SOURCE_ORDER:
        match = next(
            (
                item
                for item in official
                if (item.chunk.metadata or {}).get("diploma_slug") == slug
                and article in _evidence_article_numbers(item)
                and item.chunk.chunk_id not in seen_chunk_ids
            ),
            None,
        )
        if match:
            selected.append(match)
            seen_chunk_ids.add(match.chunk.chunk_id)
        if len(selected) >= limit:
            return selected

    for item in official:
        if item.chunk.chunk_id in seen_chunk_ids:
            continue
        selected.append(item)
        seen_chunk_ids.add(item.chunk.chunk_id)
        if len(selected) >= limit:
            break
    return selected or official[:limit]


def _limit_by_branch(
    classification: LegalClassification, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if any(_is_journalist_direct_evidence(item) for item in official):
        return _balance_journalist_public_interest_evidence(official, limit=10)
    if classification.main_branch != "misto":
        return official[:THEME_LIMIT_OFFICIAL]
    selected: list[RetrievalEvidence] = []
    branch_counts: dict[str, int] = {"laboral": 0, "penal": 0, "civil": 0}
    for item in official:
        branch = _chunk_branch(item.chunk)
        limit = THEME_BRANCH_LIMITS.get(branch, THEME_LIMIT_OFFICIAL)
        if branch in branch_counts and branch_counts[branch] >= limit:
            continue
        selected.append(item)
        if branch in branch_counts:
            branch_counts[branch] += 1
        if len(selected) >= THEME_LIMIT_OFFICIAL:
            break
    return selected or official[:THEME_LIMIT_OFFICIAL]


BRANCH_DIPLOMAS: dict[LegalBranch, tuple[str, ...]] = {
    "laboral": ("Lei Geral do Trabalho",),
    "penal": ("Código Penal",),
    "civil": ("Código Civil",),
    "constitucional": (
        "Constituição da República de Angola",
        "Constituicao da Republica de Angola",
    ),
    "administrativo": (
        "Lei do Contencioso Administrativo",
        "Lei do Bilhete de Identidade",
    ),
    "comercial": ("Lei das Sociedades Comerciais",),
    "tributario": ("Código Geral Tributário", "Codigo Geral Tributario"),
    "familia": ("Código de Família", "Codigo de Familia"),
    "propriedade": ("Lei de Terras",),
    "sucessorio": ("Código Civil", "Codigo Civil"),
    "misto": tuple(),
    "indeterminado": tuple(),
}

BRANCH_DIPLOMA_SLUGS: dict[LegalBranch, tuple[str, ...]] = {
    "laboral": ("lei-geral-do-trabalho-lei-12-23",),
    "penal": ("codigo-penal-lei-38-20",),
    "civil": ("codigo-civil",),
    "constitucional": ("constituicao-republica-angola-2022",),
    "administrativo": (
        "codigo-processo-contencioso-administrativo-33-22",
        "lei-bilhete-identidade-4-16",
    ),
    "comercial": ("lei-sociedades-comerciais-1-04",),
    "tributario": ("codigo-geral-tributario-21-14", "codigo-iva-lei-7-19"),
    "familia": ("codigo-familia-lei-1-88",),
    "propriedade": ("lei-terras-9-04",),
    "sucessorio": ("codigo-civil",),
    "misto": tuple(),
    "indeterminado": tuple(),
}

_BRANCH_TOPIC_HINT: dict[LegalBranch, str] = {
    "comercial": "socios quotas deliberacao participacao direitos minoritarios accoes preferenciais",
    "civil": "contrato obrigacao responsabilidade indemnizacao prescricao nulidade",
    "penal": "crime pena prisao multa doloso negligente tentativa consumacao",
    "laboral": "trabalhador despedimento salario ferias contrato trabalho",
    "tributario": "imposto taxa contribuicao liquidacao pagamento reclamacao",
    "familia": "casamento divorcio alimentos filhos patria poder regime bens",
    "constitucional": "constituicao direitos fundamentais garantias estado direito",
    "administrativo": "funcionario acto administrativo procedimento recurso contencioso",
    "propriedade": "propriedade posse terras usucapiao expropriacao",
    "sucessorio": "heranca sucessao herdeiros partilha testamento legado",
    "misto": "",
    "indeterminado": "",
}

DIPLOMA_SLUG_BY_NAME: dict[str, str] = {
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
    "Código de Processo Civil": "codigo-processo-civil",
    "Codigo de Processo Civil": "codigo-processo-civil",
    "Código de Processo do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Codigo de Processo do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Lei do Contencioso Administrativo": "codigo-processo-contencioso-administrativo-33-22",
    "Lei do Processo Administrativo": "lei-processo-administrativo-lei-2-22",
    "Lei n.º 2/94": "lei-n-o-2-94-de-14-de-janeiro",
    "Lei n.º 3/10": "lei-n-o-3-10-de-29-de-marco",
    "Lei da Probidade Pública": "lei-n-o-3-10-de-29-de-marco",
    "Lei do Bilhete de Identidade": "lei-bilhete-identidade-4-16",
    "Estatuto dos Magistrados Judiciais": "estatuto-magistrados-judiciais-lei-7-94",
    "Lei Orgânica do Tribunal Supremo": "lei-organica-tribunal-supremo",
    "Lei Organica do Tribunal Supremo": "lei-organica-tribunal-supremo",
    "Lei das Sociedades Comerciais": "lei-sociedades-comerciais-1-04",
    "Código Geral Tributário": "codigo-geral-tributario-21-14",
    "Codigo Geral Tributario": "codigo-geral-tributario-21-14",
    "Código do IVA": "codigo-iva-lei-7-19",
    "Codigo do IVA": "codigo-iva-lei-7-19",
    "Código de Família": "codigo-familia-lei-1-88",
    "Codigo de Familia": "codigo-familia-lei-1-88",
    "Lei de Terras": "lei-terras-9-04",
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    # Normalize ordinal indicators and special chars that break DB LIKE matching
    for ch in "º°ª¹²³":
        t = t.replace(ch, "")
    return re.sub(r"\s+", " ", t)


def _contains(text: str, needle: str) -> bool:
    if " " in needle:
        return needle in text
    return bool(re.search(rf"\b{re.escape(needle)}\b", text))


def _extract_articles(text: str) -> set[str]:
    return {
        match.group(1).replace(".", "") for match in ARTICLE_RE.finditer(text or "")
    }


def _chunk_branch(chunk: RetrievedChunk) -> LegalBranch:
    metadata = chunk.metadata or {}
    branch = metadata.get("legal_branch")
    if branch in {
        "laboral",
        "penal",
        "civil",
        "constitucional",
        "administrativo",
        "comercial",
        "tributario",
        "familia",
        "propriedade",
        "sucessorio",
    }:
        return branch
    haystack = _normalize(f"{chunk.title} {chunk.source}")
    if "trabalho" in haystack:
        return "laboral"
    if "penal" in haystack:
        return "penal"
    if "civil" in haystack:
        return "civil"
    if "constitu" in haystack:
        return "constitucional"
    if "sociedades" in haystack or "quotas" in haystack:
        return "comercial"
    if "tribut" in haystack or "iva" in haystack or "fiscal" in haystack:
        return "tributario"
    if "fam" in haystack:
        return "familia"
    if "terra" in haystack:
        return "propriedade"
    return "indeterminado"


def _source_bucket(chunk: RetrievedChunk) -> str:
    return "user_upload" if chunk.source_scope == "user_upload" else "official"


def _find_diploma_title(requested_diplomas: list[str]) -> str | None:
    """Find the exact DB title for a requested diploma name."""
    from app.db.postgres import postgres_manager
    try:
        postgres_manager.initialize()
        for dip in requested_diplomas:
            normalized = _normalize(dip)
            # Split into key tokens for flexible matching
            tokens = [t for t in normalized.split() if len(t) >= 2]
            if not tokens:
                continue
            # Build LIKE pattern: match titles containing ALL key tokens
            conditions = " AND ".join(["LOWER(title) LIKE %s"] * len(tokens))
            params = [f"%{t}%" for t in tokens]
            with postgres_manager.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT title FROM legal_segments WHERE source_scope = 'official' AND {conditions} LIMIT 1",
                    params,
                )
                row = cur.fetchone()
                if row:
                    return row["title"]
    except Exception:
        pass
    return None


def _requested_diploma_slugs(classification: LegalClassification) -> set[str]:
    slugs: set[str] = set()
    for diploma in classification.requested_diplomas:
        slug = DIPLOMA_SLUG_BY_NAME.get(diploma)
        if slug:
            slugs.add(slug)
            continue
        normalized = _normalize(diploma)
        for name, mapped_slug in DIPLOMA_SLUG_BY_NAME.items():
            if normalized == _normalize(name):
                slugs.add(mapped_slug)
                break
        else:
            if "processo penal" in normalized and (
                "codigo" in normalized or "código" in normalized or normalized in {"cpp", "ccp"}
            ):
                slugs.add("codigo-processo-penal-lei-39-20")
            elif "codigo penal" in normalized or "código penal" in normalized:
                slugs.add("codigo-penal-lei-38-20")
    return slugs


def _strict_diploma_match(
    classification: LegalClassification, chunk: RetrievedChunk
) -> bool:
    requested_slugs = _requested_diploma_slugs(classification)
    if not requested_slugs:
        return True
    chunk_slug = (chunk.metadata or {}).get("diploma_slug")
    return bool(chunk_slug and chunk_slug in requested_slugs)


def _diploma_match_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    if not classification.requested_diplomas:
        return 0.0
    haystack = _normalize(f"{chunk.title} {chunk.source}")
    score = 0.0
    for diploma in classification.requested_diplomas:
        if _contains(haystack, _normalize(diploma)):
            score += 3.0
    if _strict_diploma_match(classification, chunk):
        score += 4.0
    elif classification.requires_strict_corpus_match:
        score -= 8.0
    return score


def _article_match_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    if not classification.requested_article_numbers:
        return 0.0
    available = set()
    if chunk.article_number:
        available.update(
            part.strip().replace(".", "")
            for part in chunk.article_number.split(",")
            if part.strip()
        )
    available.update(_extract_articles(chunk.text))
    overlap = available & set(classification.requested_article_numbers)
    return float(len(overlap)) * 4.0


def _normative_score(chunk: RetrievedChunk) -> float:
    metadata = chunk.metadata or {}
    score = float(metadata.get("normative_density", 0.0) or 0.0)
    segmentation = metadata.get("segmentation")
    chunk_kind = metadata.get("chunk_kind")
    article_refs = metadata.get("article_references") or []
    if metadata.get("is_normative"):
        score += 2.0
    if metadata.get("is_front_matter"):
        score -= 6.0
    if metadata.get("is_structural"):
        score -= 4.0
    if chunk.page and chunk.page <= 3:
        score -= 1.2
    if chunk.article_number:
        score += 1.4
    if segmentation == "article_block":
        score += 2.2
    elif segmentation == "semantic_fallback":
        score += 0.5
    else:
        score -= 0.4
    if chunk_kind == "article_normative":
        score += 1.2
    if len(article_refs) >= 3:
        score -= min(1.6, 0.35 * (len(article_refs) - 2))
    if metadata.get("page_is_context_heavy") and segmentation != "article_block":
        score -= 1.5
    if chunk.distance is not None:
        score += max(0.0, 3.5 - float(chunk.distance))
    return score


_BRANCH_PARENTS: dict[str, str] = {
    "propriedade": "civil",
    "sucessorio": "civil",
}


def _branches_compatible(a: str, b: str) -> bool:
    return _BRANCH_PARENTS.get(a) == b or _BRANCH_PARENTS.get(b) == a


def _branch_alignment_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    branch = _chunk_branch(chunk)
    if classification.main_branch == "misto":
        return 3.0 if branch in classification.branch_candidates else -0.5
    if classification.main_branch == "indeterminado":
        return 2.5
    if branch == classification.main_branch:
        return 8.0
    if _branches_compatible(branch, classification.main_branch):
        return 5.0
    if branch == "indeterminado" and classification.requested_article_numbers:
        return 4.0
    if branch == "indeterminado":
        return 3.0
    if classification.explicit_branch_override:
        return -8.0
    if (
        classification.conversation_branch_hint
        and branch == classification.conversation_branch_hint
    ):
        return 1.0
    return -2.0


def _source_separation_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    if chunk.source_scope == "official":
        return 2.5
    if classification.needs_source_separation:
        return -0.8
    return 0.0


def _penal_material_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    if _chunk_branch(chunk) != "penal":
        return 0.0
    text = _normalize(chunk.text)
    refs = _extract_articles(chunk.text)
    metadata = chunk.metadata or {}
    score = 0.0
    if metadata.get("is_front_matter") or metadata.get("is_structural"):
        score -= 6.0
    if any(
        token in text
        for token in (
            "revogação da legislaço",
            "revogacao da legislacao",
            "disposições legais",
            "disposicoes legais",
            "encerramento de estabelecimento",
        )
    ):
        score -= 5.0
    if classification.main_branch == "misto" and any(
        branch == "penal" for branch in classification.branch_candidates
    ):
        if any(
            token in text
            for token in (
                "infidelidade",
                "abuso de confiança",
                "abuso de confianca",
                "furto",
                "apropriação",
                "apropriacao",
                "retenção",
                "retencao",
                "patrimonial",
            )
        ):
            score += 3.0
        if len(refs) == 1:
            score += 1.0
        elif len(refs) >= 3:
            score -= 1.5
    return score


def _is_penal_material_chunk(chunk: RetrievedChunk) -> bool:
    if _chunk_branch(chunk) != "penal":
        return True
    text = _normalize(chunk.text)
    if any(
        token in text
        for token in (
            "revogação da legislação",
            "revogacao da legislacao",
            "disposições legais",
            "disposicoes legais",
            "encerramento de estabelecimento",
        )
    ):
        return False
    return True


def _is_overly_generic_chunk(chunk: RetrievedChunk) -> bool:
    metadata = chunk.metadata or {}
    text = _normalize(chunk.text)
    if metadata.get("is_front_matter") or metadata.get("is_structural"):
        return True
    if any(
        token in text
        for token in (
            "revogação da legislação",
            "revogacao da legislacao",
            "constituem fontes de regulação",
            "constituem fontes de regulacao",
            "é revogado",
            "e revogado",
            "é revogada",
            "e revogada",
            "diplomas que substituíram",
            "diplomas que substituiram",
        )
    ):
        return True
    if (chunk.page or 0) <= 3 and any(
        token in text
        for token in (
            "aprova o código",
            "aprova o codigo",
            "é revogado",
            "e revogado",
            "diário da república",
            "diario da republica",
            "assembleia nacional",
            "publicação",
            "publicacao",
        )
    ):
        return True
    return False


def _final_chunk_filter(
    classification: LegalClassification, chunk: RetrievedChunk
) -> bool:
    # Never filter out chunks from explicitly requested diplomas
    if classification.requested_diplomas and chunk.title:
        for _dip in classification.requested_diplomas:
            if _normalize(_dip) in _normalize(chunk.title):
                return True
    if _is_overly_generic_chunk(chunk):
        return False
    if classification.requires_strict_corpus_match and not _strict_diploma_match(
        classification, chunk
    ):
        return False
    if classification.topic_route == "cpp":
        text = _normalize(chunk.text)
        cpp_required_terms = (
            "prisão preventiva",
            "prisao preventiva",
            "medidas de coacção",
            "medidas de coaccao",
            "medidas de coação",
            "medidas de coacao",
            "revogação",
            "revogacao",
            "substituição",
            "substituicao",
            "detenção",
            "detencao",
        )
        cpp_negative_terms = (
            "expulsão do território",
            "expulsao do territorio",
            "revogação da legislação",
            "revogacao da legislacao",
            "recurso a prostituição",
            "recurso a prostituicao",
            "substituição do perito",
            "substituicao do perito",
        )
        if _text_matches_any(text, cpp_negative_terms):
            return False
        if not _text_matches_any(text, cpp_required_terms):
            return False
    if classification.topic_route == "laboral" and _chunk_branch(chunk) == "administrativo":
        return False
    if classification.topic_route == "identificacao_civil" and _chunk_branch(chunk) != "administrativo":
        return False
    if classification.topic_route == "civil_obrigacoes" and _chunk_branch(chunk) != "civil":
        return False
    if classification.topic_route == "familia" and _chunk_branch(chunk) not in {"familia", "civil"}:
        return False
    if classification.topic_route == "sucessoes":
        text = _normalize(chunk.text)
        title = _normalize(chunk.title)
        metadata = chunk.metadata or {}
        if metadata.get("diploma_slug") != "codigo-civil" and "codigo civil" not in title and "código civil" not in title:
            return False
        if not _text_matches_any(
            text,
            (
                "herança",
                "heranca",
                "sucessão",
                "sucessao",
                "herdeiros",
                "partilha",
                "inventário",
                "inventario",
                "testamento",
                "aceitação",
                "aceitacao",
                "repúdio",
                "repudio",
                "colação",
                "colacao",
            ),
        ):
            return False
    if classification.topic_route == "terras" and _chunk_branch(chunk) != "propriedade":
        return False
    if (
        _chunk_branch(chunk) == "penal"
        and classification.main_branch == "misto"
        and any(branch == "penal" for branch in classification.branch_candidates)
    ):
        return _is_penal_material_chunk(chunk)
    return True


def _truncate_branch_noise(
    official: list[RetrievalEvidence], classification: LegalClassification
) -> list[RetrievalEvidence]:
    if classification.main_branch != "misto":
        return official
    penal = [item for item in official if _chunk_branch(item.chunk) == "penal"]
    non_penal = [item for item in official if _chunk_branch(item.chunk) != "penal"]
    penal = penal[:2]
    return non_penal + penal


def _sort_official_evidence(
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    return sorted(official, key=lambda item: item.score, reverse=True)


def _prefer_precise_chunks(
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    precise = [
        item
        for item in official
        if len((item.chunk.metadata or {}).get("article_references") or []) <= 2
    ]
    imprecise = [item for item in official if item not in precise]
    return precise + imprecise


def _article_precision_bonus(chunk: RetrievedChunk) -> float:
    refs = (chunk.metadata or {}).get("article_references") or []
    if len(refs) == 1:
        return 1.2
    if len(refs) == 2:
        return 0.4
    if len(refs) >= 3:
        return -1.2
    return 0.0


def _legal_materiality_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    return (
        _penal_material_score(classification, chunk)
        + _article_precision_bonus(chunk)
        + _query_specificity_score(classification, chunk)
    )


def _query_specificity_score(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    text = _normalize(chunk.text)
    query = _normalize(classification.query_text)
    score = 0.0
    if classification.topic_route == "cpp":
        needs_recurso = "recurso" in query or "recorrer" in query
        needs_prazo = "prazo" in query
        has_core = any(
            term in text
            for term in (
                "prisão preventiva",
                "prisao preventiva",
                "medidas de coacção",
                "medidas de coaccao",
                "medidas de coação",
                "medidas de coacao",
            )
        )
        has_recurso = "recurso" in text
        has_prazo = "prazo" in text
        if has_core and (
            (not needs_recurso or has_recurso) and (not needs_prazo or has_prazo)
        ):
            score += 4.0
        elif has_core and (has_recurso or has_prazo):
            score += 1.2
        elif any(term in text for term in ("recurso", "prazo")):
            score -= 2.0
    if classification.topic_route == "penal_substantivo":
        if "burla" in query:
            if "burla" in text:
                score += 4.0
            elif any(
                term in text
                for term in (
                    "fraude",
                    "vantagem patrimonial",
                    "prejuízo patrimonial",
                    "prejuizo patrimonial",
                )
                ):
                score += 0.8
            else:
                score -= 2.5
        if "infidelidade" in query:
            if "infidelidade" in text:
                score += 4.0
            else:
                score -= 2.5
        if _is_public_asset_misuse_query(query):
            refs = {
                str(item).replace(".", "")
                for item in (chunk.metadata or {}).get("article_references", [])
            }
            for article, bonus in PUBLIC_ASSET_MISUSE_ARTICLE_BOOSTS.items():
                if article in refs:
                    score += bonus
            if any(term in text for term in PUBLIC_ASSET_MISUSE_STRONG_TERMS):
                score += 5.0
            if any(term in text for term in PUBLIC_ASSET_MISUSE_SECONDARY_TERMS):
                score += 2.4
            if (
                any(term in text for term in PUBLIC_ASSET_MISUSE_COMPANY_MARKERS)
                and not any(term in query for term in PUBLIC_ASSET_MISUSE_COMPANY_MARKERS)
            ):
                score -= 4.2
    if classification.topic_route == "laboral":
        labor_query_terms = (
            "despedimento",
            "despedimento disciplinar",
            "impugnação",
            "impugnacao",
            "prova",
            "justa causa",
            "procedimento disciplinar",
            "reintegração",
            "reintegracao",
            "indemnização",
            "indemnizacao",
        )
        labor_chunk_terms = (
            "despedimento",
            "impugnação",
            "impugnacao",
            "prova",
            "justa causa",
            "procedimento disciplinar",
            "reintegração",
            "reintegracao",
            "indemnização",
            "indemnizacao",
            "aviso prévio",
            "aviso previo",
        )
        if any(term in query for term in labor_query_terms):
            if any(term in text for term in labor_chunk_terms):
                score += 2.5
            else:
                score -= 1.8
    if classification.topic_route == "identificacao_civil":
        if any(term in query for term in ("bilhete", "identificação", "identificacao")):
            if any(term in text for term in ("bilhete", "identificação", "identificacao")):
                score += 3.0
            elif any(term in text for term in ("registo civil", "numero de identificação", "numero de identificacao")):
                score += 1.0
            else:
                score -= 2.0
    if classification.topic_route == "civil_obrigacoes":
        if any(term in query for term in ("responsabilidade", "obrigação", "obrigacao", "indemnização", "indemnizacao")):
            if any(term in text for term in ("responsabilidade", "obrigação", "obrigacao", "indemnização", "indemnizacao", "contrato", "incumprimento")):
                score += 2.0
            else:
                score -= 1.2
    if classification.topic_route == "sucessoes":
        succession_query_terms = (
            "herança",
            "heranca",
            "sucessão",
            "sucessao",
            "herdeiros",
            "partilha",
            "inventário",
            "inventario",
            "testamento",
            "aceitação",
            "aceitacao",
            "repúdio",
            "repudio",
            "colação",
            "colacao",
            "legítima",
            "legitima",
            "herança jacente",
        )
        if any(term in query for term in succession_query_terms):
            if any(term in text for term in succession_query_terms):
                score += 3.2
            else:
                score -= 2.0
    return score


def _chunk_is_context_header(chunk: RetrievedChunk) -> bool:
    text = _normalize(chunk.text)
    return (
        text.startswith("capítulo")
        or text.startswith("capitulo")
        or text.startswith("secção")
        or text.startswith("seccao")
    )


def _context_header_penalty(chunk: RetrievedChunk) -> float:
    return -3.0 if _chunk_is_context_header(chunk) else 0.0


def _materiality_cutoff(
    classification: LegalClassification, chunk: RetrievedChunk
) -> bool:
    if _chunk_branch(chunk) == "penal" and classification.main_branch == "misto":
        return _legal_materiality_score(classification, chunk) > -2.5
    return True


def _chunk_survives_final_filter(
    classification: LegalClassification, chunk: RetrievedChunk
) -> bool:
    return _final_chunk_filter(classification, chunk) and _materiality_cutoff(
        classification, chunk
    )


def _rank_official_after_filters(
    classification: LegalClassification, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    filtered = [
        item
        for item in official
        if item.retrieval_reason == "legal_concept_rescue"
        or _is_curated_direct_evidence(item)
        or _chunk_survives_final_filter(classification, item.chunk)
    ]
    filtered = _sort_official_evidence(filtered)
    filtered = _prefer_precise_chunks(filtered)
    filtered = _truncate_branch_noise(filtered, classification)
    return filtered


def _source_excerpt_priority(chunk: RetrievedChunk) -> float:
    return _context_header_penalty(chunk)


def _final_score_adjustment(
    classification: LegalClassification, chunk: RetrievedChunk
) -> float:
    score = _legal_materiality_score(classification, chunk) + _source_excerpt_priority(
        chunk
    )
    # Boost chunks from explicitly requested diplomas (compensates poor OCR)
    meta = chunk.metadata or {}
    chunk_slug = meta.get("diploma_slug")
    requested = _requested_diploma_slugs(classification)
    if chunk_slug and chunk_slug in requested:
        score += 25.0
    return score


def _apply_final_score(
    classification: LegalClassification, evidence: RetrievalEvidence,
) -> RetrievalEvidence:
    score = evidence.score + _final_score_adjustment(classification, evidence.chunk)
    # Force diploma-matched chunks to the top when user explicitly names a law
    if classification.requested_diplomas and evidence.chunk.title:
        title_norm = _normalize(evidence.chunk.title)
        for _dip in classification.requested_diplomas:
            _dip_norm = _normalize(_dip)
            if _dip_norm in title_norm:
                score = 50.0
                break
            # Also try token matching for abbreviated names like "Lei 3/08"
            _tokens = [t for t in _dip_norm.split() if len(t) >= 2]
            if len(_tokens) >= 2 and all(t in title_norm for t in _tokens):
                score = 50.0
                break
    return RetrievalEvidence(
        query_used=evidence.query_used,
        chunk=evidence.chunk,
        score=score,
        retrieval_reason=evidence.retrieval_reason,
        source_bucket=evidence.source_bucket,
    )


def _rescore_ranked(
    classification: LegalClassification, ranked: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    rescored = [_apply_final_score(classification, item) for item in ranked]
    return sorted(rescored, key=lambda item: item.score, reverse=True)


def _source_key(evidence: RetrievalEvidence) -> tuple[str, int | None, str, str, str]:
    metadata = evidence.chunk.metadata or {}
    return (
        evidence.chunk.source,
        evidence.chunk.page,
        evidence.source_bucket,
        metadata.get("document_kind") or "",
        metadata.get("article_main") or evidence.chunk.article_number or "",
    )


def _dedupe_ranked(ranked: list[RetrievalEvidence]) -> list[RetrievalEvidence]:
    deduped: dict[tuple[str, int | None, str, str, str], RetrievalEvidence] = {}
    for evidence in ranked:
        key = _source_key(evidence)
        current = deduped.get(key)
        if current is None or evidence.score > current.score:
            deduped[key] = evidence
    return sorted(deduped.values(), key=lambda item: item.score, reverse=True)


def _prune_low_value_official(
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    return [item for item in official if item.score > 2.5]


def _final_official_selection(
    classification: LegalClassification, ranked: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    official = [item for item in ranked if item.source_bucket == "official"]
    official = _rank_official_after_filters(classification, official)
    official = _prune_low_value_official(official)
    return official


def _branch_group_source(
    branch_map: dict[LegalBranch, list[RetrievalEvidence]], branch: LegalBranch
) -> list[RetrievalEvidence]:
    return branch_map.get(branch, [])


def _mix_prioritized_chunks(
    official: list[RetrievalEvidence], user_docs: list[RetrievalEvidence]
) -> list[RetrievedChunk]:
    all_evidence = sorted(official + user_docs, key=lambda x: x.score, reverse=True)
    return [item.chunk for item in all_evidence[:THEME_LIMIT_OFFICIAL]]


def _retrieval_notes(
    official: list[RetrievalEvidence], missing_branches: list[LegalBranch]
) -> list[str]:
    notes: list[str] = []
    if not official:
        notes.append("Sem recuperação oficial suficientemente forte.")
    if missing_branches:
        notes.append("Há ramos pedidos sem cobertura normativa suficiente.")
    return notes


def _branch_groups_from_map(
    branch_map: dict[LegalBranch, list[RetrievalEvidence]], requested: list[LegalBranch]
) -> tuple[list[BranchEvidenceGroup], list[LegalBranch]]:
    missing_branches: list[LegalBranch] = []
    branch_groups: list[BranchEvidenceGroup] = []
    for branch in requested:
        branch_evidence = _branch_group_source(branch_map, branch)
        if not branch_evidence:
            missing_branches.append(branch)
        branch_groups.append(
            BranchEvidenceGroup(
                branch=branch,
                evidences=branch_evidence[:4],
                coverage_gap=not bool(branch_evidence),
            )
        )
    return branch_groups, missing_branches


def _user_doc_selection(ranked: list[RetrievalEvidence]) -> list[RetrievalEvidence]:
    return [item for item in ranked if item.source_bucket == "user_upload"]


def _juris_doc_selection(ranked: list[RetrievalEvidence]) -> list[RetrievalEvidence]:
    return [
        item
        for item in ranked
        if (item.chunk.metadata or {}).get("document_kind") == "jurisprudence"
    ]


def _target_branch_priority(
    classification: LegalClassification, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    target_branches = set(_target_branches(classification))
    if not target_branches:
        return official
    matched = [
        item for item in official if _chunk_branch(item.chunk) in target_branches
    ]
    if not matched:
        return official
    return matched + [item for item in official if item not in matched]


def _score_chunk(
    classification: LegalClassification, chunk: RetrievedChunk, retrieval_reason: str,
    conversation_diploma_slug: str | None = None,
) -> float:
    score = 0.0
    metadata = chunk.metadata or {}
    document_kind = metadata.get("document_kind")

    if retrieval_reason == "active_document":
        score += 15.0  # Massive boost to dominate retrieval
        # Avoid penalties for missing branch/diploma for user documents
        score += max(0, _normative_score(chunk))
        return score

    # Boost chunks from the conversation's diploma context (follow-up anchoring)
    if conversation_diploma_slug:
        chunk_slug = (chunk.metadata or {}).get("diploma_slug", "") or ""
        chunk_title = (chunk.title or "").lower()
        chunk_source = (chunk.source or "").lower()
        # Match by slug or by title containing the slug keywords
        if chunk_slug == conversation_diploma_slug:
            score += 6.0
        elif conversation_diploma_slug.replace("-", " ") in chunk_title:
            score += 4.0
        elif any(word in chunk_title for word in conversation_diploma_slug.split("-") if len(word) > 2):
            score += 2.0

    if document_kind == "jurisprudence":
        if retrieval_reason == "jurisprudence":
            score += 10.0  # Strong boost to survive branch penalties
        elif classification.audience == "tecnico":
            score += 1.5
        else:
            score -= 0.2

    score += _branch_alignment_score(classification, chunk)
    score += _diploma_match_score(classification, chunk)
    score += _article_match_score(classification, chunk)
    # Direct diploma name boost: when user explicitly names a law, prioritize its chunks
    if classification.requested_diplomas and chunk.title:
        _title_lower = _normalize(chunk.title)
        for _dip in classification.requested_diplomas:
            if _normalize(_dip) in _title_lower:
                score += 20.0  # Massive boost to overcome poor cosine similarity
                break
    score += _normative_score(chunk)
    score += _source_separation_score(classification, chunk)
    score += float(metadata.get("source_priority", 0.0) or 0.0) * 2.0

    if retrieval_reason == "article":
        score += 2.0
    elif retrieval_reason == "diploma":
        score += 2.5
    elif retrieval_reason == "branch":
        score += 2.2
    elif retrieval_reason == "topic_route":
        score += 3.0

    return score


def _merge_where(*items: dict | None) -> dict | None:
    merged: dict = {}
    for item in items:
        if item:
            merged.update(item)
    return merged or None


def _target_branches(classification: LegalClassification) -> list[LegalBranch]:
    if classification.topic_route == "sucessoes":
        return ["familia", "civil"]
    if classification.topic_route == "identificacao_civil":
        return ["administrativo"]
    if classification.branch_candidates:
        return classification.branch_candidates
    if classification.main_branch not in {"misto", "indeterminado"}:
        return [classification.main_branch]
    return []


TOPIC_ROUTE_QUERY_HINTS: dict[str, tuple[str, dict | None]] = {
    "laboral": (
        "despedimento disciplinar impugnação prova justa causa procedimento disciplinar reintegração indemnização trabalhador empregador lei geral do trabalho",
        {
            "source_scope": "official",
            "diploma_slug": "lei-geral-do-trabalho-lei-12-23",
            "legal_branch": "laboral",
        },
    ),
    "penal_substantivo": (
        "peculato peculato de uso abuso de poder burla infidelidade apropriação ilegítima fraude patrimonial funcionário público bem público artigo tipo legal pena código penal",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-penal-lei-38-20",
            "legal_branch": "penal",
        },
    ),
    "cpp": (
        "prisão preventiva recurso prazo de interposição medida de coacção arguido decisão judicial artigo processual",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-processo-penal-lei-39-20",
            "legal_branch": "penal",
        },
    ),
    "contencioso_admin": (
        "acto administrativo impugnação judicial recurso contencioso prazo notificação tribunal administrativo artigo processual",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-processo-contencioso-administrativo-33-22",
            "legal_branch": "administrativo",
        },
    ),
    "constitucional": (
        "liberdade detenção ilegal habeas corpus tutela jurisdicional efetiva garantias constitucionais",
        {
            "source_scope": "official",
            "diploma_slug": "constituicao-republica-angola-2022",
            "legal_branch": "constitucional",
        },
    ),
    "processo_administrativo": (
        "acto administrativo impugnação notificação recurso contencioso prazo tribunal administrativo procedimento administrativo lei do contencioso administrativo",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-processo-contencioso-administrativo-33-22",
            "legal_branch": "administrativo",
        },
    ),
    "identificacao_civil": (
        "bilhete de identidade identificação civil segunda via registo civil documento de identificação número de identificação",
        {
            "source_scope": "official",
            "diploma_slug": "lei-bilhete-identidade-4-16",
            "legal_branch": "administrativo",
        },
    ),
    "tributario": (
        "obrigações fiscais deveres declarativos infrações tributárias contribuintes facturação",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-geral-tributario-21-14",
            "legal_branch": "tributario",
        },
    ),
    "iva": (
        "iva obrigações declarativas liquidação imposto sobre o valor acrescentado facturação",
        {"source_scope": "official", "legal_branch": "tributario"},
    ),
    "sociedades": (
        "sócios quotas direitos de informação voto fiscalização abuso da maioria",
        {
            "source_scope": "official",
            "diploma_slug": "lei-sociedades-comerciais-1-04",
            "legal_branch": "comercial",
        },
    ),
    "sucessoes": (
        "herança sucessão por morte sucessão legítima sucessão testamentária abertura da sucessão herdeiros legítimos herdeiros testamentários partilha inventário testamento aceitação repúdio colação herança jacente código civil",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-civil",
        },
    ),
    "civil_obrigacoes": (
        "responsabilidade civil obrigações contrato indemnização incumprimento nulidade prescrição prova danos",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-civil",
            "legal_branch": "civil",
        },
    ),
    "familia": (
        "casamento divórcio alimentos responsabilidade parental guarda filiação regime de bens código de família",
        {
            "source_scope": "official",
            "diploma_slug": "codigo-familia-lei-1-88",
            "legal_branch": "familia",
        },
    ),
    "terras": (
        "terras propriedade posse concessão direito de superfície expropriação ocupação uso e aproveitamento da terra",
        {
            "source_scope": "official",
            "diploma_slug": "lei-terras-9-04",
            "legal_branch": "propriedade",
        },
    ),
    "cpc": (
        "citação contestação revelia recurso prazo nulidade sentença execução processo civil código de processo civil",
        {
            "source_scope": "official",
            "legal_branch": "civil",
        },
    ),
}
JURISPRUDENCE_QUERY_MARKERS = (
    "jurisprud",
    "acórd",
    "acord",
    "tribunal supremo",
    "tribunal constitucional",
    "precedente",
    "entendimento do tribunal",
)


def _needs_jurisprudence_query(
    question: str, classification: LegalClassification
) -> bool:
    text = _normalize(question)
    if any(marker in text for marker in JURISPRUDENCE_QUERY_MARKERS):
        return True
    return False


def _build_queries(
    question: str,
    classification: LegalClassification,
    conversation_history: list[str] | None,
) -> list[tuple[str, str, dict | None]]:
    queries: list[tuple[str, str, dict | None]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(query: str, reason: str, where: dict | None = None) -> None:
        normalized = query.strip()
        key = (normalized.casefold(), reason, str(sorted((where or {}).items())))
        if not normalized or key in seen:
            return
        seen.add(key)
        queries.append((normalized, reason, where))

    add(question, "base", {"source_scope": "official"})

    route_hint = TOPIC_ROUTE_QUERY_HINTS.get(classification.topic_route)
    if route_hint:
        route_query, route_where = route_hint
        add(
            f"{question}. Contexto prioritario: {route_query}",
            "topic_route",
            route_where,
        )

    # Multi-branch: also inject topic_route hints for secondary branches
    if classification.needs_multi_branch_handling:
        # Map branch -> default topic_route
        branch_to_topic = {
            "laboral": "laboral",
            "comercial": "sociedades",
            "civil": "civil_obrigacoes",
            "penal": "penal_substantivo",
            "tributario": "tributario",
            "familia": "familia",
            "constitucional": "constitucional",
            "administrativo": "contencioso_admin",
            "propriedade": "terras",
            "sucessorio": "sucessoes",
        }
        for branch in classification.branch_candidates:
            topic = branch_to_topic.get(branch)
            if not topic:
                continue
            sec_hint = TOPIC_ROUTE_QUERY_HINTS.get(topic)
            if sec_hint:
                route_q, route_wh = sec_hint
                add(
                    f"{question}. {route_q}",
                    "topic_route",
                    route_wh,
                )

    if conversation_history and classification.specificity == "follow_up":
        anchor = next(
            (
                item.split(":", 1)[1].strip()
                for item in reversed(conversation_history)
                if item.lower().startswith("utilizador:")
            ),
            "",
        )
        if anchor:
            add(f"{anchor}. {question}", "follow_up", {"source_scope": "official"})

    if classification.requested_diplomas:
        for diploma_name in classification.requested_diplomas[:3]:
            diploma_where = {"source_scope": "official"}
            slug = DIPLOMA_SLUG_BY_NAME.get(diploma_name)
            if slug:
                diploma_where["diploma_slug"] = slug
            else:
                _exact_title = _find_diploma_title([diploma_name])
                if _exact_title:
                    diploma_where["title"] = _exact_title
            add(
                f"{question}. Diploma prioritario: {diploma_name}",
                "diploma",
                diploma_where,
            )

    if classification.requested_article_numbers:
        article_where = {"source_scope": "official"}
        if classification.requested_diplomas:
            slugs = _requested_diploma_slugs(classification)
            if slugs:
                article_where["diploma_slug"] = next(iter(slugs))
            else:
                _exact_title = _find_diploma_title(classification.requested_diplomas)
                if _exact_title:
                    article_where["title"] = _exact_title
        for article_number in classification.requested_article_numbers[:4]:
            add(
                f"{question}. Artigo {article_number}",
                "article",
                article_where,
            )

    branch_limit = 3 if classification.needs_multi_branch_handling else 1
    for branch in _target_branches(classification)[:branch_limit]:
        diplomas = BRANCH_DIPLOMAS.get(branch, tuple())
        label = diplomas[0] if diplomas else branch
        where_branch = _BRANCH_PARENTS.get(branch, branch)
        add(
            f"{question}. Ramo juridico prioritario: {branch}. Diploma: {label}",
            "branch",
            _merge_where({"source_scope": "official", "legal_branch": where_branch}),
        )

    if _needs_penal_material_query(classification, question, conversation_history):
        add(
            _penal_material_query_text(question, conversation_history),
            PENAL_MATERIAL_REASON,
            PENAL_MATERIAL_BASE_WHERE,
        )
        add(
            PENAL_MATERIAL_FALLBACK_QUERY,
            PENAL_MATERIAL_RESCUE_REASON,
            PENAL_MATERIAL_SINGLE_WHERE,
        )

    if _needs_jurisprudence_query(question, classification):
        add(
            f"{question}. Sumário de acórdão e jurisprudência angolana relevante.",
            "jurisprudence",
            _merge_where(
                {
                    "source_scope": "official",
                    "metadata__document_kind": "jurisprudence",
                }
            ),
        )

    return queries


def _boost_penal_material_reason(
    classification: LegalClassification, evidence: RetrievalEvidence
) -> RetrievalEvidence:
    if evidence.retrieval_reason not in PENAL_MATERIAL_QUERY_REASONS:
        return evidence
    return RetrievalEvidence(
        query_used=evidence.query_used,
        chunk=evidence.chunk,
        score=evidence.score
        + PENAL_MATERIAL_QUERY_BOOST
        + _penal_material_chunk_score(evidence.chunk),
        retrieval_reason=evidence.retrieval_reason,
        source_bucket=evidence.source_bucket,
    )


def _apply_reason_specific_boosts(
    classification: LegalClassification, ranked: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    boosted = [_boost_penal_material_reason(classification, item) for item in ranked]
    return sorted(boosted, key=lambda item: item.score, reverse=True)


def _penal_material_selection(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    if not _needs_penal_material_query(classification, question, None):
        return official
    penal_candidates = [
        item for item in official if _penal_material_candidate(item.chunk)
    ]
    penal_candidates = sorted(
        penal_candidates,
        key=lambda item: item.score + _penal_material_chunk_score(item.chunk),
        reverse=True,
    )[:PENAL_MATERIAL_SINGLE_LIMIT]
    if not penal_candidates:
        return official
    kept = [item for item in official if item not in penal_candidates]
    return _dedupe_ranked(penal_candidates + kept)


def _apply_penal_material_priority(
    classification: LegalClassification, question: str, ranked: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    ranked = _apply_reason_specific_boosts(classification, ranked)
    ranked = _penal_material_rescue(question, classification, ranked)
    return ranked


def _final_penal_material_official(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    return _penal_material_selection(classification, question, official)


def _apply_penal_material_postfilter(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    return _final_penal_material_official(classification, question, official)


def _direct_jurisprudence_rescue(
    question: str, classification: LegalClassification
) -> list[RetrievalEvidence]:
    normalized_question = _normalize(question)
    if not _needs_jurisprudence_query(question, classification):
        return []
    if not re.search(r"extra\s+vel\s+ultra|extra\s+petit|ultra\s+petit|petita", normalized_question):
        return []

    sql = """
        SELECT id, source, title, link_original, page, article_number, law_status,
               source_scope, document_id, metadata, text_content
        FROM legal_segments
        WHERE metadata->>'document_kind' = 'jurisprudence'
          AND legal_branch = 'laboral'
          AND (
                (text_content ILIKE %s AND text_content ILIKE %s)
                OR title ILIKE %s
              )
        ORDER BY
          CASE WHEN text_content ILIKE %s THEN 0 ELSE 1 END,
          title ASC
        LIMIT 4
    """
    try:
        with postgres_manager.connection() as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    "%extra%",
                    "%ultra%",
                    "%Matéria Disciplinar%",
                    "%petita%",
                ),
            )
            return [
                RetrievalEvidence(
                    query_used=question,
                    chunk=postgres_manager._segment_to_chunk(row),
                    score=125.0,
                    retrieval_reason="jurisprudence",
                    source_bucket="official",
                )
                for row in cur.fetchall()
            ]
    except Exception:
        return []


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        normalized = _normalize(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(item)
    return unique


def _concept_phrases_for_query(
    question: str,
    classification: LegalClassification,
    conversation_history: list[str] | None,
) -> list[str]:
    history_text = " ".join(conversation_history[-8:] if conversation_history else [])
    current_question = question or ""
    pro_context_markers = (
        "\nCliente:",
        "\nÁrea jurídica:",
        "\nArea juridica:",
        "\nFactos relevantes:",
        "\nTermos jurídicos:",
        "\nTermos juridicos:",
        "\nDiplomas prováveis:",
        "\nDiplomas provaveis:",
        "\nContexto profissional do caso associado:",
    )
    marker_positions = [
        current_question.find(marker)
        for marker in pro_context_markers
        if marker in current_question
    ]
    if marker_positions:
        current_question = current_question[: min(marker_positions)]
    query_text = _normalize(current_question)
    text = _normalize(f"{question} {history_text}")
    phrases: list[str] = []
    explicit_public_agent_issue = any(
        term in text
        for term in (
            "policia",
            "policial",
            "agente",
            "autoridade",
            "funcionario",
            "funcionário",
            "acto policial",
            "ato policial",
        )
    ) and any(
        term in text
        for term in (
            "desacato",
            "desobediencia",
            "desobediência",
            "resistiu",
            "resistir",
            "resistencia",
            "resistência",
            "violencia",
            "violência",
            "ameaca",
            "ameaça",
            "filmar",
            "filmagem",
            "gravacao",
            "gravação",
            "fotograf",
        )
    )
    branch_is_penal = (
        classification.main_branch in {"penal", "misto"}
        or "penal" in classification.branch_candidates
    )
    if not branch_is_penal and not explicit_public_agent_issue:
        return []
    if (
        classification.topic_route not in {"penal_substantivo", "cpp", "geral"}
        and not explicit_public_agent_issue
    ):
        return []

    has_public_agent_context = any(
        term in text
        for term in (
            "policia",
            "policial",
            "agente",
            "autoridade",
            "funcionario",
            "funcionário",
            "forcas de seguranca",
            "forças de segurança",
        )
    )
    has_order_context = any(
        term in text
        for term in (
            "ordem",
            "mandado",
            "parasse",
            "parar",
            "interrompeu",
            "perturbacao",
            "perturbação",
            "desacato",
            "desobediencia",
            "desobediência",
        )
    )
    resistance_haystack = query_text
    has_negated_resistance_context = any(
        term in query_text
        for term in (
            "sem violencia",
            "sem violência",
            "sem ameaca",
            "sem ameaça",
            "nao ha violencia",
            "não há violência",
            "nao ha ameaca",
            "não há ameaça",
            "não há notícia de insultos, ameaça",
            "nao ha noticia de insultos, ameaca",
        )
    )
    has_resistance_context = any(
        term in resistance_haystack
        for term in (
            "resistencia",
            "resistência",
            "resistiu",
            "resistir",
            "resistindo",
            "violencia",
            "violência",
            "ameaca",
            "ameaça",
            "impedir",
            "obstrucao",
            "obstrução",
        )
    ) and not has_negated_resistance_context
    has_recording_context = any(
        term in text
        for term in (
            "filmar",
            "filmagem",
            "filmou",
            "fotograf",
            "gravacao",
            "gravação",
            "video",
            "vídeo",
            "imagem",
        )
    )
    has_public_asset_context = _is_public_asset_misuse_query(text)

    if has_order_context and has_public_agent_context:
        phrases.append("desobediência")
    if has_resistance_context and has_public_agent_context:
        phrases.append("resistência contra funcionário")
    if has_recording_context:
        phrases.append("gravações, fotografias e filmes ilícitos")
    if has_public_asset_context:
        phrases.append("peculato de uso")
        phrases.append("peculato")
        phrases.append("abuso de poder")

    return _unique_preserve_order(phrases)


def _concept_chunk_score(
    chunk: RetrievedChunk,
    phrase: str,
    question: str,
) -> float:
    text = _normalize(chunk.text)
    phrase_norm = _normalize(phrase)
    query = _normalize(question)
    refs = {
        str(item).replace(".", "").strip()
        for item in (chunk.metadata or {}).get("article_references", [])
    }

    score = 70.0
    if phrase_norm and phrase_norm in text:
        score += 18.0
    phrase_tokens = [token for token in WORD_RE.findall(phrase_norm) if token not in STOPWORDS]
    score += sum(2.0 for token in phrase_tokens if token in text)

    if "desobed" in phrase_norm:
        if "ordens ou mandados legitimos" in text or "ordens ou mandados legítimos" in text:
            score += 18.0
        if "autoridade ou funcionario competente" in text or "autoridade ou funcionário competente" in text:
            score += 12.0
        if "publicacoes suspensas" in text or "publicações suspensas" in text:
            score -= 20.0
        if any(term in query for term in ("policia", "policial", "agente", "autoridade")) and not any(
            term in text for term in ("autoridade", "funcionario", "funcionário")
        ):
            score -= 8.0
    if "resistencia contra funcionario" in phrase_norm or "resistência contra funcionário" in phrase_norm:
        if "violencia ou ameaca de violencia" in text or "violência ou ameaça de violência" in text:
            score += 18.0
        if "forcas militares" in text or "forças militares" in text or "ordem publica" in text or "ordem pública" in text:
            score += 10.0
    if "gravacoes" in phrase_norm or "gravações" in phrase_norm:
        if "fotografias e filmes ilicitos" in text or "fotografias e filmes ilícitos" in text:
            score += 20.0
        if "lugar publico" in text or "lugar público" in text:
            score += 6.0
    if "peculato de uso" in phrase_norm:
        if any(term in text for term in PUBLIC_ASSET_MISUSE_STRONG_TERMS):
            score += 22.0
        if "peculato" in text:
            score += 10.0
    if phrase_norm == "peculato":
        if "peculato" in text:
            score += 18.0
        if "peculato de uso" in text:
            score += 8.0
    if "abuso de poder" in phrase_norm and "abuso de poder" in text:
        score += 12.0
    if (
        any(term in text for term in PUBLIC_ASSET_MISUSE_COMPANY_MARKERS)
        and not any(term in query for term in PUBLIC_ASSET_MISUSE_COMPANY_MARKERS)
    ):
        score -= 10.0

    if (chunk.metadata or {}).get("segmentation") == "article_block":
        score += 6.0
    if not refs:
        score -= 6.0
    return score


def _direct_legal_concept_rescue(
    question: str,
    classification: LegalClassification,
    conversation_history: list[str] | None,
) -> list[RetrievalEvidence]:
    phrases = _concept_phrases_for_query(question, classification, conversation_history)
    if not phrases:
        return []

    rescued: list[RetrievalEvidence] = []
    if _is_public_asset_misuse_query(question):
        direct_targets = (
            ("codigo-penal-lei-38-20", ("363", "362", "374"), 125.0),
            ("constituicao-republica-angola-2022", ("75",), 116.0),
            ("lei-n-o-3-10-de-29-de-marco", ("7", "26"), 112.0),
            ("codigo-civil", ("483",), 108.0),
        )
        for slug, articles, base_score in direct_targets:
            for index, article in enumerate(articles):
                chunk = _best_direct_article_chunk(slug, article)
                if chunk:
                    rescued.append(
                        RetrievalEvidence(
                            query_used=f"{question}. Artigo nuclear recuperado: {article}",
                            chunk=chunk,
                            score=base_score - (index * 0.5),
                            retrieval_reason=PUBLIC_ASSET_REASON,
                            source_bucket=_source_bucket(chunk),
                        )
                    )

    sql = """
        SELECT id, source, title, link_original, page, article_number, law_status,
               source_scope, document_id, metadata, text_content
        FROM legal_segments
        WHERE source_scope = 'official'
          AND legal_branch = 'penal'
          AND metadata->>'diploma_slug' = 'codigo-penal-lei-38-20'
          AND (
                text_content ILIKE %s
                OR lower(coalesce(metadata->>'article_main', '')) = lower(%s)
              )
        ORDER BY page ASC, article_number ASC
        LIMIT 10
    """
    try:
        with postgres_manager.connection() as conn, conn.cursor() as cur:
            for phrase in phrases:
                cur.execute(sql, (f"%{phrase}%", phrase))
                candidates: list[RetrievalEvidence] = []
                for row in cur.fetchall():
                    chunk = postgres_manager._segment_to_chunk(row)
                    score = _concept_chunk_score(chunk, phrase, question)
                    if score < 105.0:
                        continue
                    candidates.append(
                        RetrievalEvidence(
                            query_used=f"{question}. Conceito jurídico no corpus: {phrase}",
                            chunk=chunk,
                            score=score,
                            retrieval_reason="legal_concept_rescue",
                            source_bucket=_source_bucket(chunk),
                        )
                    )
                candidates.sort(key=lambda item: item.score, reverse=True)
                rescued.extend(candidates[:1])
    except Exception:
        return []

    return _dedupe_ranked(rescued)[:6]


def _evidence_article_numbers(evidence: RetrievalEvidence) -> set[str]:
    chunk = evidence.chunk
    numbers: set[str] = set()
    metadata = chunk.metadata or {}
    for value in (
        chunk.article_number,
        metadata.get("article_main"),
        *(metadata.get("article_references") or []),
    ):
        if not value:
            continue
        for part in str(value).split(","):
            normalized = part.strip().replace(".", "")
            if normalized:
                numbers.add(normalized)
    return numbers


def _ensure_public_asset_evidence(
    question: str, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if not _is_public_asset_misuse_query(question):
        return official

    direct_targets = (
        ("codigo-penal-lei-38-20", ("363", "362", "374"), 150.0),
        ("constituicao-republica-angola-2022", ("75",), 144.0),
        ("lei-n-o-3-10-de-29-de-marco", ("7", "26"), 138.0),
        ("codigo-civil", ("483",), 132.0),
    )
    direct: list[RetrievalEvidence] = []
    for slug, articles, base_score in direct_targets:
        for index, article in enumerate(articles):
            chunk = _best_direct_article_chunk(slug, article)
            if chunk:
                direct.append(
                    RetrievalEvidence(
                        query_used=f"{question}. Artigo nuclear recuperado: {article}",
                        chunk=chunk,
                        score=base_score - (index * 0.5),
                        retrieval_reason=PUBLIC_ASSET_REASON,
                        source_bucket=_source_bucket(chunk),
                    )
                )

    combined = _dedupe_ranked(direct + official)
    allowed: list[RetrievalEvidence] = []
    for evidence in combined:
        slug = (evidence.chunk.metadata or {}).get("diploma_slug")
        articles = _evidence_article_numbers(evidence)
        if slug == "codigo-penal-lei-38-20" and articles.intersection(
            {"362", "363", "374"}
        ):
            allowed.append(evidence)
        elif slug == "constituicao-republica-angola-2022" and "75" in articles:
            allowed.append(evidence)
        elif slug == "lei-n-o-3-10-de-29-de-marco" and articles.intersection(
            {"7", "26"}
        ):
            allowed.append(evidence)
        elif slug == "codigo-civil" and "483" in articles:
            allowed.append(evidence)
    return allowed[:9] if allowed else official


def _ensure_journalist_leak_evidence(
    question: str, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if not _is_journalist_leak_public_interest_query(question):
        return official

    direct_targets = (
        ("codigo-penal-lei-38-20", ("359", "358", "357", "362", "364", "375", "356"), 140.0),
        ("constituicao-republica-angola-2022", ("40", "44", "32", "57", "69"), 136.0),
    )
    direct: list[RetrievalEvidence] = []
    for slug, articles, base_score in direct_targets:
        for index, article in enumerate(articles):
            chunk = _best_direct_article_chunk(slug, article)
            if chunk:
                direct.append(
                    RetrievalEvidence(
                        query_used=f"{question}. Artigo nuclear recuperado: {article}",
                        chunk=chunk,
                        score=base_score - (index * 0.5),
                        retrieval_reason="journalist_public_interest_direct_article",
                        source_bucket=_source_bucket(chunk),
                    )
                )

    combined = _dedupe_ranked(direct + official)
    allowed: list[RetrievalEvidence] = []
    allowed_penal = {"357", "358", "359", "362", "364", "375", "356", "232", "224"}
    allowed_constitutional = {"32", "40", "44", "57", "69"}
    for evidence in combined:
        slug = (evidence.chunk.metadata or {}).get("diploma_slug")
        articles = _evidence_article_numbers(evidence)
        if slug == "codigo-penal-lei-38-20" and articles.intersection(allowed_penal):
            allowed.append(evidence)
        elif slug == "constituicao-republica-angola-2022" and articles.intersection(
            allowed_constitutional
        ):
            allowed.append(evidence)
    return allowed[:10] if allowed else official


def _ensure_state_agent_liability_evidence(
    question: str, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if not _is_state_agent_liability_query(question):
        return official

    direct_targets = (
        ("constituicao-republica-angola-2022", ("75",), 142.0),
        ("codigo-civil", ("483", "800"), 134.0),
    )
    direct: list[RetrievalEvidence] = []
    for slug, articles, base_score in direct_targets:
        for index, article in enumerate(articles):
            chunk = _best_direct_article_chunk(slug, article)
            if chunk:
                direct.append(
                    RetrievalEvidence(
                        query_used=f"{question}. Artigo nuclear recuperado: {article}",
                        chunk=chunk,
                        score=base_score - (index * 0.5),
                        retrieval_reason=STATE_AGENT_LIABILITY_REASON,
                        source_bucket=_source_bucket(chunk),
                    )
                )

    combined = _dedupe_ranked(direct + official)
    allowed: list[RetrievalEvidence] = []
    for evidence in combined:
        slug = (evidence.chunk.metadata or {}).get("diploma_slug")
        articles = _evidence_article_numbers(evidence)
        if slug == "constituicao-republica-angola-2022" and "75" in articles:
            allowed.append(evidence)
        elif slug == "codigo-civil" and articles.intersection({"483", "800"}):
            allowed.append(evidence)
    return allowed[:6] if allowed else official


def _ensure_civil_loan_evidence(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    if not _is_civil_loan_query(question, classification):
        return official

    direct: list[RetrievalEvidence] = []
    for index, article in enumerate(CIVIL_LOAN_ARTICLES):
        chunk = _best_direct_article_chunk("codigo-civil", article)
        if chunk:
            direct.append(
                RetrievalEvidence(
                    query_used=f"{question}. Artigo nuclear recuperado: {article}",
                    chunk=chunk,
                    score=142.0 - (index * 0.5),
                    retrieval_reason=CIVIL_LOAN_REASON,
                    source_bucket=_source_bucket(chunk),
                )
            )

    combined = _dedupe_ranked(direct + official)
    allowed: list[RetrievalEvidence] = []
    allowed_articles = set(CIVIL_LOAN_ARTICLES)
    for evidence in combined:
        slug = (evidence.chunk.metadata or {}).get("diploma_slug")
        articles = _evidence_article_numbers(evidence)
        if slug == "codigo-civil" and articles.intersection(allowed_articles):
            allowed.append(evidence)
    return allowed[:8] if allowed else official


def _ensure_admin_act_evidence(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    if not _is_admin_act_challenge_query(question, classification):
        return official

    direct: list[RetrievalEvidence] = []
    for slug, articles, base_score in ADMIN_ACT_TARGETS:
        for index, article in enumerate(articles):
            chunk = _best_direct_article_chunk(slug, article)
            if chunk:
                direct.append(
                    RetrievalEvidence(
                        query_used=f"{question}. Artigo nuclear recuperado: {article}",
                        chunk=chunk,
                        score=base_score - (index * 0.5),
                        retrieval_reason=ADMIN_ACT_REASON,
                        source_bucket=_source_bucket(chunk),
                    )
                )

    combined = _dedupe_ranked(direct + official)
    allowed_by_slug = {
        "lei-n-o-2-94-de-14-de-janeiro": {"9", "10", "11", "12", "13"},
        "codigo-processo-contencioso-administrativo-33-22": {
            "32",
            "64",
            "69",
            "73",
            "75",
        },
    }
    allowed: list[RetrievalEvidence] = []
    for evidence in combined:
        slug = (evidence.chunk.metadata or {}).get("diploma_slug")
        articles = _evidence_article_numbers(evidence)
        if slug in allowed_by_slug and articles.intersection(allowed_by_slug[slug]):
            allowed.append(evidence)
    return allowed[:10] if allowed else official


def _is_labor_objective_dismissal_query(
    question: str, classification: LegalClassification
) -> bool:
    text = _normalize(question)
    if classification.main_branch != "laboral" and "laboral" not in (
        classification.branch_candidates or []
    ):
        if "lei geral do trabalho" not in text and "lgt" not in text:
            return False
    if "despedimento disciplinar" in text or "justa causa disciplinar" in text:
        return False
    has_dismissal = any(
        term in text
        for term in (
            "despedimento",
            "despedido",
            "despedida",
            "cessacao do contrato",
            "cessação do contrato",
        )
    )
    has_objective_ground = any(
        term in text
        for term in (
            "extincao do posto",
            "extinção do posto",
            "extinguiu o posto",
            "extinguir o posto",
            "posto de trabalho",
            "causas objectivas",
            "causas objetivas",
            "motivos economicos",
            "motivos económicos",
            "motivos tecnologicos",
            "motivos tecnológicos",
            "motivos estruturais",
        )
    )
    return has_dismissal and has_objective_ground


def _ensure_labor_objective_dismissal_evidence(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    if not _is_labor_objective_dismissal_query(question, classification):
        return official

    direct: list[RetrievalEvidence] = []
    for index, article in enumerate(LABOR_OBJECTIVE_DISMISSAL_ARTICLES):
        chunk = _best_direct_article_chunk(
            "lei-geral-do-trabalho-lei-12-23", article
        )
        if chunk:
            direct.append(
                RetrievalEvidence(
                    query_used=f"{question}. Artigo nuclear recuperado: {article}",
                    chunk=chunk,
                    score=150.0 - (index * 0.5),
                    retrieval_reason=LABOR_OBJECTIVE_DISMISSAL_REASON,
                    source_bucket=_source_bucket(chunk),
                )
            )

    if not direct:
        return official

    combined = _dedupe_ranked(direct + official)
    direct_ids = {item.chunk.chunk_id for item in direct}
    ordered_direct = [item for item in combined if item.chunk.chunk_id in direct_ids]
    remainder = [item for item in combined if item.chunk.chunk_id not in direct_ids]
    return (ordered_direct + remainder)[:THEME_LIMIT_OFFICIAL]


def _best_direct_article_chunk(
    diploma_slug: str, article: str
) -> RetrievedChunk | None:
    candidates = postgres_manager.find_article_chunks(diploma_slug, article, limit=8)
    if not candidates:
        normalized_article = str(article).replace(".", "").strip()
        try:
            with postgres_manager.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, source, title, link_original, page, article_number,
                           law_status, source_scope, document_id, metadata, text_content
                    FROM legal_segments
                    WHERE source_scope = 'official'
                      AND metadata->>'diploma_slug' = %s
                      AND (
                          metadata->>'article_main' = %s
                          OR string_to_array(
                              regexp_replace(coalesce(article_number, ''), '\\s+', '', 'g'),
                              ','
                          ) @> ARRAY[%s]
                          OR text_content ILIKE %s
                      )
                    ORDER BY page ASC NULLS LAST, id ASC
                    LIMIT 8
                    """,
                    (
                        diploma_slug,
                        normalized_article,
                        normalized_article,
                        f"%Art.% {normalized_article}%",
                    ),
                )
                candidates = [
                    postgres_manager._segment_to_chunk(row) for row in cur.fetchall()
                ]
        except Exception:
            candidates = []
    if not candidates:
        return None

    normalized_article = str(article).replace(".", "").strip()

    def _candidate_score(chunk: RetrievedChunk) -> tuple[float, int]:
        metadata = chunk.metadata or {}
        article_main = str(metadata.get("article_main") or "").replace(".", "").strip()
        article_numbers = [
            part.strip().replace(".", "")
            for part in str(chunk.article_number or "").split(",")
            if part.strip()
        ]
        raw_text = chunk.text or ""
        text = _normalize(raw_text)
        score = 0.0
        if article_main == normalized_article:
            score += 30.0
        if article_numbers and article_numbers[0] == normalized_article:
            score += 12.0
        if re.search(
            rf"artigo\s+{re.escape(normalized_article)}[^\(]{{0,20}}\(",
            text,
        ):
            score += 10.0
        if (metadata.get("segmentation") or "") == "article_block":
            score += 4.0
        if re.search(r"\.\s*\.\s*\.", raw_text):
            score -= 25.0
        if len(article_numbers) > 2:
            score -= 1.0
        page = int(chunk.page or 9999)
        return (score, -page)

    return max(candidates, key=_candidate_score)


def _apply_post_filters(
    classification: LegalClassification,
    question: str,
    official: list[RetrievalEvidence],
) -> list[RetrievalEvidence]:
    filtered = _apply_penal_material_postfilter(classification, question, official)
    filtered = _ensure_public_asset_evidence(question, filtered)
    filtered = _ensure_journalist_leak_evidence(question, filtered)
    filtered = _ensure_state_agent_liability_evidence(question, filtered)
    filtered = _ensure_civil_loan_evidence(classification, question, filtered)
    filtered = _ensure_admin_act_evidence(classification, question, filtered)
    return _ensure_labor_objective_dismissal_evidence(
        classification, question, filtered
    )



class LegalRetrievalService:
    async def retrieve(
        self,
        question: str,
        classification: LegalClassification,
        conversation_history: list[str] | None = None,
        active_document_id: str | None = None,
        user_id: str | int | None = None,
        conversation_diploma_slug: str | None = None,
        conversation_diploma_names: list[str] | None = None,
    ) -> RetrievalResult:
        evidences: list[RetrievalEvidence] = []

        if active_document_id:
            active_chunks = document_context_service.get_relevant_chunks(
                active_document_id,
                question,
                user_id=user_id,
                conversation_history=conversation_history,
            )
            if not active_chunks:
                active_chunks = await retriever_service.retrieve(
                    question, where={"document_id": active_document_id}
                )
            for chunk in active_chunks:
                evidences.append(
                    RetrievalEvidence(
                        query_used=question,
                        chunk=chunk,
                        score=_score_chunk(classification, chunk, "active_document"),
                        retrieval_reason="active_document",
                        source_bucket=_source_bucket(chunk),
                    )
                )

        if (
            classification.requested_article_numbers
            and classification.requested_diplomas
            and not active_document_id
            and not _needs_jurisprudence_query(question, classification)
        ):
            direct_evidences: list[RetrievalEvidence] = []
            slugs = _requested_diploma_slugs(classification)
            for slug in list(slugs)[:1]:
                for article in classification.requested_article_numbers[:4]:
                    chunk = _best_direct_article_chunk(slug, article)
                    if chunk:
                        direct_evidences.append(
                            RetrievalEvidence(
                                query_used=question,
                                chunk=chunk,
                                score=100.0,
                                retrieval_reason="requested_article_direct",
                                source_bucket=_source_bucket(chunk),
                            )
                        )
            if direct_evidences:
                direct_evidences = _dedupe_ranked(direct_evidences)
                branch_map: dict[LegalBranch, list[RetrievalEvidence]] = defaultdict(list)
                for evidence in direct_evidences:
                    branch_map[_chunk_branch(evidence.chunk)].append(evidence)
                requested = _target_branches(classification)
                branch_groups, missing_branches = _branch_groups_from_map(branch_map, requested)
                return RetrievalResult(
                    classification=classification,
                    official_evidence=direct_evidences[:THEME_LIMIT_OFFICIAL],
                    user_evidence=[],
                    branch_groups=branch_groups,
                    retrieved_chunks=[
                        evidence.chunk
                        for evidence in direct_evidences[:THEME_LIMIT_OFFICIAL]
                    ],
                    missing_branches=missing_branches,
                    retrieval_notes=[],
                )

        sub_queries = _build_queries(question, classification, conversation_history)

        async def _fetch_and_score(q: str, r: str, w: dict | None):
            chunks = await retriever_service.retrieve(q, where=w)
            return [
                RetrievalEvidence(
                    query_used=q,
                    chunk=chunk,
                    score=_score_chunk(classification, chunk, r, conversation_diploma_slug),
                    retrieval_reason=r,
                    source_bucket=_source_bucket(chunk),
                )
                for chunk in chunks
            ]
        
        # Also boost by conversation diploma names (from previous turn)
        if conversation_diploma_names:
            for ev in evidences:
                if _normalize(ev.chunk.title or "") in {_normalize(d) for d in conversation_diploma_names}:
                    ev = RetrievalEvidence(
                        query_used=ev.query_used,
                        chunk=ev.chunk,
                        score=ev.score + 5.0,
                        retrieval_reason=ev.retrieval_reason,
                        source_bucket=ev.source_bucket,
                    )

        if sub_queries:
            results = await asyncio.gather(
                *[_fetch_and_score(q, r, w) for q, r, w in sub_queries]
            )
            for sub_evidences in results:
                evidences.extend(sub_evidences)

        ranked = _dedupe_ranked(evidences)
        concept_rescue = _direct_legal_concept_rescue(
            question, classification, conversation_history
        )
        if concept_rescue:
            ranked = _dedupe_ranked(concept_rescue + ranked)
        rescue_jurisprudence = _direct_jurisprudence_rescue(question, classification)
        if rescue_jurisprudence:
            ranked = _dedupe_ranked(rescue_jurisprudence + ranked)
        ranked = _rescore_ranked(classification, ranked)
        if classification.topic_route == "sucessoes":
            rescue = _successions_direct_rescue(question, classification)
            if rescue:
                ranked = _dedupe_ranked(rescue + ranked)
        if classification.topic_route == "cpp":
            rescue = _cpp_direct_rescue(question, classification)
            if rescue:
                ranked = _dedupe_ranked(rescue + ranked)
        # BOOST: force diploma-matched chunks to top BEFORE any filters remove them
        if classification.requested_diplomas:
            _dip_names = {_normalize(d) for d in classification.requested_diplomas}
            for i, ev in enumerate(ranked):
                if any(_dip in _normalize(ev.chunk.title or "") for _dip in _dip_names):
                    ranked[i] = RetrievalEvidence(
                        query_used=ev.query_used,
                        chunk=ev.chunk,
                        score=max(ev.score, 99.0),
                        retrieval_reason=ev.retrieval_reason,
                        source_bucket=ev.source_bucket,
                    )
        ranked = _apply_penal_material_priority(classification, question, ranked)
        ranked = [item for item in ranked if item.score > 0.5]

        official = _final_official_selection(classification, ranked)
        official = _apply_post_filters(classification, question, official)
        official = _target_branch_priority(classification, official)
        official = _filter_by_question_relevance(classification, question, official)
        official = _promote_jurisprudence_if_requested(
            classification, question, official
        )
        official = _question_specific_branch_filter(classification, question, official)
        official = _limit_by_branch(classification, official)
        official = _prioritize_legal_concept_rescue(classification, question, official)
        user_docs = _user_doc_selection(ranked)
        juris_docs = [
            item
            for item in _juris_doc_selection(ranked)
            if _needs_jurisprudence_query(question, classification)
            and _jurisprudence_relevant_to_question(question, item.chunk)
        ][:2]

        branch_map: dict[LegalBranch, list[RetrievalEvidence]] = defaultdict(list)
        # FINAL boost: force diploma-matched chunks to the top after all filters
        if classification.requested_diplomas:
            _dip_names = {_normalize(d) for d in classification.requested_diplomas}
            for i, ev in enumerate(official):
                if any(_dip in _normalize(ev.chunk.title or "") for _dip in _dip_names):
                    official[i] = RetrievalEvidence(
                        query_used=ev.query_used,
                        chunk=ev.chunk,
                        score=max(ev.score, 99.0),
                        retrieval_reason=ev.retrieval_reason,
                        source_bucket=ev.source_bucket,
                    )
        for evidence in official + user_docs + juris_docs:
            branch_map[_chunk_branch(evidence.chunk)].append(evidence)

        requested = _target_branches(classification)
        branch_groups, missing_branches = _branch_groups_from_map(branch_map, requested)
        notes = _retrieval_notes(official, missing_branches)

        prioritised_chunks = _mix_prioritized_chunks(official + juris_docs, user_docs)
        return RetrievalResult(
            classification=classification,
            official_evidence=(juris_docs + official)[:THEME_LIMIT_OFFICIAL],
            user_evidence=user_docs[:4],
            branch_groups=branch_groups,
            retrieved_chunks=prioritised_chunks,
            missing_branches=missing_branches,
            retrieval_notes=notes,
        )


legal_retrieval_service = LegalRetrievalService()
