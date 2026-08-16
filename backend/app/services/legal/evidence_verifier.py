from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.legal.models import RetrievalEvidence


NEGATIVE_CLAIM_RE = re.compile(
    r"\b(?:não|nao)\s+(?:sendo\s+[^.!?]{0,40}\s+)?(?:existe|há|ha|pode|possui|tem|compete|pratica|responde|incorre|constitui|configura(?:d[oa])?|é permitido|e permitido|se aplica)\b",
    re.IGNORECASE,
)
BINARY_LEGAL_QUESTION_RE = re.compile(
    r"\b(?:pode|podem|compete|existe|há|ha|tem\s+direito|é\s+permitido|e\s+permitido|"
    r"constitui|configura|aplica-se|aplica\s+se|é\s+válido|e\s+valido)\b",
    re.IGNORECASE,
)
EXPLICIT_NEGATIVE_EVIDENCE = (
    "não pode",
    "nao pode",
    "não é permitido",
    "nao e permitido",
    "é proibido",
    "e proibido",
    "é vedado",
    "e vedado",
    "não compete",
    "nao compete",
    "sem competência",
    "sem competencia",
    "exclusivamente",
    "apenas compete",
)
WORD_RE = re.compile(r"[\wÀ-ÿ-]+", re.UNICODE)
ARTICLE_CITATION_RE = re.compile(
    r"\b(?:art(?:igo|igos|\.)?)\s*(\d+[.]?\d*)", re.IGNORECASE
)
CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+(?=[^-#])")
STOPWORDS = {
    "artigo", "como", "da", "das", "de", "do", "dos", "e", "em", "lei", "na",
    "não", "nao", "no", "o", "os", "ou", "para", "pode", "por", "qual", "que",
    "se", "sobre", "tem", "um", "uma",
}


@dataclass(slots=True)
class ClaimVerificationReport:
    supported: bool
    negative_claim_guarded: bool = False
    unsupported_claims: list[str] = field(default_factory=list)


class EvidenceVerifier:
    def verify_and_guard(
        self,
        answer: str,
        question: str,
        evidences: list[RetrievalEvidence],
        retrieval_notes: list[str] | None = None,
    ) -> tuple[str, ClaimVerificationReport]:
        if not answer.strip():
            return answer, ClaimVerificationReport(supported=False)

        answer, removed_citations = self._remove_unverified_article_claims(
            answer, evidences
        )

        quality_sufficient = any(
            note.startswith("retrieval_quality=sufficient")
            for note in (retrieval_notes or [])
        )
        has_negative_claim = bool(NEGATIVE_CLAIM_RE.search(answer))
        negative_supported = self._negative_supported(question, evidences)
        unsupported_claims = [
            *removed_citations,
            *self._unsupported_claims(answer, evidences),
        ]
        principally_negative = self._is_principally_negative_answer(answer, question)
        if (
            has_negative_claim
            and principally_negative
            and (not quality_sufficient or not negative_supported)
        ):
            guarded = self._guard_negative_answer(evidences)
            return guarded, ClaimVerificationReport(
                supported=False,
                negative_claim_guarded=True,
                unsupported_claims=[
                    "A conclusão negativa não estava apoiada por uma exclusão normativa expressa.",
                    *unsupported_claims,
                ],
            )
        return answer, ClaimVerificationReport(
            supported=not unsupported_claims,
            unsupported_claims=unsupported_claims,
        )

    @staticmethod
    def _is_principally_negative_answer(answer: str, question: str) -> bool:
        plain = re.sub(r"^[#*\s]+", "", answer or "").strip()
        starts_negative = bool(re.match(r"^(?:não|nao)\b", plain, re.IGNORECASE))
        return starts_negative or bool(BINARY_LEGAL_QUESTION_RE.search(question or ""))

    @staticmethod
    def _remove_unverified_article_claims(
        answer: str, evidences: list[RetrievalEvidence]
    ) -> tuple[str, list[str]]:
        available_articles: set[str] = set()
        for evidence in evidences:
            metadata = evidence.chunk.metadata or {}
            values = [
                metadata.get("article_main"),
                evidence.chunk.article_number,
                *(metadata.get("article_references") or []),
            ]
            available_articles.update(
                str(value).replace(".", "").strip()
                for value in values
                if value is not None and str(value).strip()
            )

        removed: list[str] = []
        kept_blocks: list[str] = []
        for block in re.split(r"(\n\s*\n)", answer):
            if not block.strip() or re.fullmatch(r"\n\s*\n", block):
                kept_blocks.append(block)
                continue
            cited = {
                match.group(1).replace(".", "")
                for match in ARTICLE_CITATION_RE.finditer(block)
            }
            missing = cited - available_articles
            if missing:
                removed.append(
                    f"Citação sem evidência recuperada: {', '.join(sorted(missing))}."
                )
                continue
            kept_blocks.append(block)

        cleaned = "".join(kept_blocks).strip()
        if removed:
            cleaned += (
                "\n\n> **Nota de verificação:** Foram omitidas referências numéricas que "
                "não estavam confirmadas nas fontes recuperadas."
            )
        return cleaned, removed

    @staticmethod
    def _unsupported_claims(
        answer: str, evidences: list[RetrievalEvidence]
    ) -> list[str]:
        evidence_by_article: dict[str, str] = {}
        all_context = " ".join(evidence.chunk.text or "" for evidence in evidences).casefold()
        for evidence in evidences:
            metadata = evidence.chunk.metadata or {}
            articles = {
                str(metadata.get("article_main") or "").replace(".", ""),
                *(
                    str(value).replace(".", "")
                    for value in (metadata.get("article_references") or [])
                ),
            }
            for article in articles:
                if article:
                    evidence_by_article[article] = evidence.chunk.text.casefold()

        unsupported: list[str] = []
        for raw_claim in CLAIM_SPLIT_RE.split(answer):
            claim = re.sub(r"^[#*\-\s]+", "", raw_claim).strip()
            if len(claim) < 35 or claim.endswith(":"):
                continue
            citations = {
                match.group(1).replace(".", "")
                for match in ARTICLE_CITATION_RE.finditer(claim)
            }
            claim_terms = {
                token.casefold()
                for token in WORD_RE.findall(claim)
                if len(token) >= 4 and token.casefold() not in STOPWORDS
            }
            if not claim_terms:
                continue
            candidate_context = " ".join(
                evidence_by_article[article]
                for article in citations
                if article in evidence_by_article
            ) or all_context
            overlap = sum(term in candidate_context for term in claim_terms)
            threshold = min(3, max(1, len(claim_terms) // 4))
            if overlap < threshold:
                unsupported.append(claim[:240])
        return unsupported[:6]

    @staticmethod
    def _negative_supported(
        question: str, evidences: list[RetrievalEvidence]
    ) -> bool:
        question_terms = {
            token.casefold()
            for token in WORD_RE.findall(question)
            if len(token) >= 4 and token.casefold() not in STOPWORDS
        }
        for evidence in evidences[:12]:
            text = (evidence.chunk.text or "").casefold()
            if not any(marker in text for marker in EXPLICIT_NEGATIVE_EVIDENCE):
                continue
            overlap = sum(term in text for term in question_terms)
            if overlap >= min(3, max(1, len(question_terms) // 3)):
                return True
        return False

    @staticmethod
    def _guard_negative_answer(evidences: list[RetrievalEvidence]) -> str:
        bases: list[str] = []
        for evidence in evidences[:5]:
            article = str(
                (evidence.chunk.metadata or {}).get("article_main")
                or evidence.chunk.article_number
                or ""
            ).strip()
            title = (evidence.chunk.title or evidence.chunk.source or "Fonte jurídica").strip()
            label = f"Art. {article} — {title}" if article else title
            if label not in bases:
                bases.append(label)
        guarded = (
            "### Resposta limitada pelas fontes\n\n"
            "As fontes recuperadas ainda não permitem confirmar nem negar a conclusão com segurança. "
            "Por isso, não apresento como certa uma afirmação positiva ou negativa que não esteja expressamente demonstrada no corpus.\n\n"
            "### O que falta verificar\n\n"
            "É necessário localizar a norma específica que atribua, condicione ou exclua expressamente a consequência jurídica perguntada."
        )
        if bases:
            guarded += "\n\n### Fontes analisadas sem confirmação conclusiva\n\n" + "\n".join(
                f"- {base}" for base in bases
            )
        return guarded


evidence_verifier = EvidenceVerifier()
