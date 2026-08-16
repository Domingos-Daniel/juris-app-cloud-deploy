from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.legal.models import LegalClassification, RetrievalEvidence
from app.services.legal.query_planner import LegalQueryPlan


WORD_RE = re.compile(r"[\wÀ-ÿ-]+", re.UNICODE)
GENERIC_TERMS = {
    "artigo", "artigos", "angola", "angolano", "caso", "codigo", "código", "direito",
    "lei", "legal", "norma", "pode", "qual", "quais", "sobre",
}


@dataclass(slots=True)
class RetrievalAssessment:
    status: str
    score: float
    covered_concepts: list[str] = field(default_factory=list)
    missing_concepts: list[str] = field(default_factory=list)
    correction_queries: list[str] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return self.status == "sufficient"


class RetrievalQualityEvaluator:
    def assess(
        self,
        plan: LegalQueryPlan,
        classification: LegalClassification,
        evidences: list[RetrievalEvidence],
    ) -> RetrievalAssessment:
        if not evidences:
            return RetrievalAssessment(
                status="irrelevant",
                score=0.0,
                missing_concepts=plan.concepts,
                correction_queries=self._correction_queries(plan, [], plan.concepts),
            )

        corpus_text = " ".join(
            f"{item.chunk.title} {item.chunk.text} {(item.chunk.metadata or {}).get('parent_context', '')}"
            for item in evidences[:20]
        ).casefold()
        meaningful = [
            concept for concept in plan.concepts if concept not in GENERIC_TERMS
        ]
        covered = [concept for concept in meaningful if concept in corpus_text]
        missing = [concept for concept in meaningful if concept not in corpus_text]
        concept_score = len(covered) / max(1, len(meaningful))

        requested = {number.replace(".", "") for number in classification.requested_article_numbers}
        recovered = {
            str((item.chunk.metadata or {}).get("article_main") or item.chunk.article_number or "").replace(".", "")
            for item in evidences
        }
        article_score = 1.0 if not requested else float(bool(requested & recovered))

        target_branches = set(classification.branch_candidates)
        if classification.main_branch not in {"misto", "indeterminado"}:
            target_branches.add(classification.main_branch)
        target_branches.discard("misto")
        target_branches.discard("indeterminado")
        recovered_branches = {
            (item.chunk.metadata or {}).get("legal_branch") for item in evidences
        }
        branch_score = (
            1.0
            if not target_branches
            else len(target_branches & recovered_branches) / len(target_branches)
        )
        structural_score = sum(
            1
            for item in evidences[:10]
            if (item.chunk.metadata or {}).get("segmentation") == "article_block"
        ) / max(1, min(10, len(evidences)))

        score = (
            concept_score * 0.45
            + article_score * 0.25
            + branch_score * 0.20
            + structural_score * 0.10
        )
        status = "sufficient" if score >= 0.62 else "partial" if score >= 0.35 else "irrelevant"
        return RetrievalAssessment(
            status=status,
            score=score,
            covered_concepts=covered,
            missing_concepts=missing,
            correction_queries=self._correction_queries(plan, evidences, missing),
        )

    @staticmethod
    def _correction_queries(
        plan: LegalQueryPlan,
        evidences: list[RetrievalEvidence],
        missing: list[str],
    ) -> list[str]:
        queries: list[str] = []
        if missing:
            queries.append(f"{plan.original_query}. Conceitos a localizar: {' '.join(missing[:8])}")
        for issue in plan.issues[:3]:
            queries.append(f"Regra jurídica, competência, requisitos e excepções: {issue}")
        for evidence in evidences[:4]:
            metadata = evidence.chunk.metadata or {}
            parent = metadata.get("parent_context")
            if parent:
                queries.append(f"{plan.original_query}. Contexto sistemático: {parent}")
            references = metadata.get("article_references") or []
            if references:
                queries.append(
                    f"{plan.original_query}. Normas relacionadas mencionadas: {' '.join(map(str, references[:6]))}"
                )
        return list(dict.fromkeys(query for query in queries if query.strip()))[:8]


retrieval_quality_evaluator = RetrievalQualityEvaluator()
