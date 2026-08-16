from __future__ import annotations

import asyncio
import logging
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
from app.services.legal.query_planner import legal_query_planner
from app.services.legal.reranker import llm_reranker
from app.services.legal.retrieval_quality import retrieval_quality_evaluator
from app.services.rag.retriever import retriever_service
from app.services.pdf.document_context import document_context_service

logger = logging.getLogger(__name__)

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


def _is_curated_direct_evidence(item: RetrievalEvidence) -> bool:
    return item.retrieval_reason in {
        "requested_article_direct",
        "dynamic_cross_reference",
        "legal_concept_rescue",
        "corrective_retrieval",
    }


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


def _limit_by_branch(
    classification: LegalClassification, official: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if classification.main_branch != "misto":
        return official[:THEME_LIMIT_OFFICIAL]
    selected: list[RetrievalEvidence] = []
    target_branches = [
        branch
        for branch in classification.branch_candidates
        if branch not in {"misto", "indeterminado"}
    ]
    per_branch_limit = max(1, THEME_LIMIT_OFFICIAL // max(1, len(target_branches)))
    branch_counts: dict[str, int] = {branch: 0 for branch in target_branches}
    for item in official:
        branch = _chunk_branch(item.chunk)
        if branch in branch_counts and branch_counts[branch] >= per_branch_limit:
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






def _concept_chunk_score(
    chunk: RetrievedChunk,
    phrase: str,
    question: str,
) -> float:
    text = _normalize(chunk.text)
    phrase_norm = _normalize(phrase)
    phrase_stem = _concept_search_stem(phrase_norm)
    refs = {
        str(item).replace(".", "").strip()
        for item in (chunk.metadata or {}).get("article_references", [])
    }

    score = 70.0
    if phrase_norm and phrase_norm in text:
        score += 18.0
    elif phrase_stem and phrase_stem in text[:220]:
        score += 18.0
    phrase_tokens = [token for token in WORD_RE.findall(phrase_norm) if token not in STOPWORDS]
    score += sum(2.0 for token in phrase_tokens if _concept_search_stem(token) in text)

    if (chunk.metadata or {}).get("segmentation") == "article_block":
        score += 6.0
    if len(text) < 180 or text.count(".") > max(8, len(text) // 12):
        score -= 18.0
    if not refs:
        score -= 6.0
    return score


def _concept_search_stem(value: str) -> str:
    normalized = _normalize(value).strip()
    for suffix in (
        "amentos", "imentos", "idades", "mente", "ções", "coes", "amento",
        "imento", "idade", "ção", "cao", "ar", "er", "ir",
    ):
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 5:
            return normalized[: -len(suffix)]
    return normalized[:7] if len(normalized) > 9 else normalized


def _direct_legal_concept_rescue(
    question: str,
    classification: LegalClassification,
    conversation_history: list[str] | None,
) -> list[RetrievalEvidence]:
    phrases = legal_query_planner.plan(question, classification).concepts[:8]
    if not phrases:
        return []

    rescued: list[RetrievalEvidence] = []
    sql = """
        SELECT id, source, title, link_original, page, article_number, law_status,
               source_scope, document_id, metadata, text_content
        FROM legal_segments
        WHERE source_scope = 'official'
          AND (%s::text IS NULL OR legal_branch = %s)
          AND (
                text_content ILIKE %s
                OR title ILIKE %s
                OR lower(coalesce(metadata->>'article_main', '')) = lower(%s)
              )
        ORDER BY page ASC, article_number ASC
        LIMIT 10
    """
    branch_filter = (
        classification.main_branch
        if classification.main_branch not in {"misto", "indeterminado"}
        else None
    )
    try:
        with postgres_manager.connection() as conn, conn.cursor() as cur:
            for phrase in phrases:
                search_term = _concept_search_stem(phrase)
                cur.execute(
                    sql,
                    (
                        branch_filter,
                        branch_filter,
                        f"%{search_term}%",
                        f"%{search_term}%",
                        phrase,
                    ),
                )
                candidates: list[RetrievalEvidence] = []
                for row in cur.fetchall():
                    chunk = postgres_manager._segment_to_chunk(row)
                    score = _concept_chunk_score(chunk, phrase, question)
                    if score < 75.0:
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
    except Exception as exc:
        logger.warning("Dynamic legal concept rescue failed: %s", exc)
        return []

    return _dedupe_ranked(rescued)[:6]




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
    return official


def _expand_dynamic_article_references(
    evidences: list[RetrievalEvidence], limit: int = 8
) -> list[RetrievalEvidence]:
    additions: list[RetrievalEvidence] = []
    seen: set[tuple[str, str]] = set()
    for evidence in evidences[:6]:
        metadata = evidence.chunk.metadata or {}
        slug = metadata.get("diploma_slug")
        if not slug:
            continue
        references = metadata.get("article_references") or []
        for reference in references[:4]:
            article = str(reference).replace(".", "").strip()
            key = (slug, article)
            if not article or key in seen:
                continue
            seen.add(key)
            chunk = _best_direct_article_chunk(slug, article)
            if chunk and chunk.chunk_id != evidence.chunk.chunk_id:
                additions.append(
                    RetrievalEvidence(
                        query_used=evidence.query_used,
                        chunk=chunk,
                        score=max(1.0, evidence.score - 0.75),
                        retrieval_reason="dynamic_cross_reference",
                        source_bucket=_source_bucket(chunk),
                    )
                )
            if len(additions) >= limit:
                return additions
    return additions


async def _rerank_evidences(
    question: str, evidences: list[RetrievalEvidence]
) -> list[RetrievalEvidence]:
    if len(evidences) <= 2:
        return evidences
    scores = await llm_reranker.scores(
        question, [evidence.chunk.text or "" for evidence in evidences]
    )
    rescored = [
        RetrievalEvidence(
            query_used=evidence.query_used,
            chunk=evidence.chunk,
            score=evidence.score + (scores[index] if index < len(scores) else 0.0),
            retrieval_reason=evidence.retrieval_reason,
            source_bucket=evidence.source_bucket,
        )
        for index, evidence in enumerate(evidences)
    ]
    return sorted(rescored, key=lambda item: item.score, reverse=True)



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
        query_plan = legal_query_planner.plan(question, classification)

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
        existing_queries = {query.casefold() for query, _, _ in sub_queries}
        for task in query_plan.tasks:
            if task.query.casefold() in existing_queries:
                continue
            where = {"source_scope": "official"}
            if task.branch:
                where["legal_branch"] = task.branch
            sub_queries.append((task.query, task.purpose, where))
            existing_queries.add(task.query.casefold())

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
        ranked = _rescore_ranked(classification, ranked)
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
        reference_evidence = _expand_dynamic_article_references(official)
        if reference_evidence:
            official = _dedupe_ranked(official + reference_evidence)

        assessment = retrieval_quality_evaluator.assess(
            query_plan, classification, official
        )
        correction_pass = 0
        while not assessment.sufficient and correction_pass < 2:
            correction_pass += 1
            correction_results = await asyncio.gather(
                *[
                    _fetch_and_score(
                        correction_query,
                        "corrective_retrieval",
                        {"source_scope": "official"},
                    )
                    for correction_query in assessment.correction_queries[:4]
                ]
            )
            additions = [item for batch in correction_results for item in batch]
            if not additions:
                break
            official = _dedupe_ranked(official + additions)
            official = _filter_by_question_relevance(
                classification, question, official
            )
            assessment = retrieval_quality_evaluator.assess(
                query_plan, classification, official
            )

        official = await _rerank_evidences(question, official[:30])
        official = official[:12]
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
        notes.append(
            f"retrieval_quality={assessment.status}:{assessment.score:.3f}"
        )
        if correction_pass:
            notes.append(f"corrective_passes={correction_pass}")

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
