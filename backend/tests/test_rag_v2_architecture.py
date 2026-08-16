from __future__ import annotations

from app.db.models import RetrievedChunk
from app.db.postgres import PostgresManager
from app.services.legal.evidence_verifier import evidence_verifier
from app.services.legal.models import LegalClassification, RetrievalEvidence
from app.services.legal.query_planner import legal_query_planner
from app.services.legal.retrieval import (
    _build_queries,
    _concept_search_stem,
    _generic_official_selection,
)
from app.services.legal.retrieval_quality import retrieval_quality_evaluator
from app.services.legal.pre_classifier import apply_pre_classification
from app.services.llm.cloudflare_embeddings import CloudflareEmbeddingClient
from app.services.rag.pipeline import RAGPipeline
from app.services.pdf.chunker import legal_semantic_chunks


def _classification(**updates) -> LegalClassification:
    values = {
        "query_text": "A Assembleia Nacional pode destituir o Presidente da República?",
        "main_branch": "constitucional",
        "branch_candidates": ["constitucional"],
        "request_type": "competencia_institucional",
        "specificity": "factual",
        "audience": "misto",
        "topic_route": "constitucional",
        "search_query": "destituição Presidente Assembleia Nacional",
    }
    values.update(updates)
    return LegalClassification(**values)


def _row(chunk_id: str, article: str, text: str, distance=None) -> dict:
    return {
        "id": chunk_id,
        "source": "cra.pdf",
        "title": "Constituição da República de Angola",
        "link_original": None,
        "page": 70,
        "article_number": article,
        "law_status": "Em vigor",
        "source_scope": "official",
        "document_id": None,
        "metadata": {
            "article_main": article,
            "legal_branch": "constitucional",
            "segmentation": "article_block",
        },
        "text_content": text,
        "distance": distance,
    }


def test_rrf_preserves_strong_exact_lexical_result():
    exact = _row("129", "129", "Destituição do Presidente da República", 0.35)
    generic = _row("164", "164", "Competência legislativa da Assembleia", 0.10)
    fused = PostgresManager._reciprocal_rank_fusion(
        [exact, generic], [generic, exact], limit=2
    )
    assert fused[0]["id"] == "129"


def test_chunker_preserves_legislative_hierarchy():
    chunks = legal_semantic_chunks(
        "TÍTULO III\nOrganização do Estado\nCAPÍTULO II\nPresidente da República\n"
        "Artigo 129.º\n(Destituição)\nO Presidente pode ser destituído nas situações previstas."
    )
    assert chunks[0]["article_main"] == "129"
    assert "Presidente da República" in chunks[0]["parent_context"]


def test_query_planner_decomposes_multibranch_question():
    classification = _classification(
        main_branch="misto",
        branch_candidates=["penal", "constitucional", "administrativo"],
        needs_multi_branch_handling=True,
    )
    plan = legal_query_planner.plan(
        "Há responsabilidade penal; e ainda responsabilidade administrativa e constitucional?",
        classification,
    )
    assert len(plan.issues) >= 2
    assert {task.branch for task in plan.tasks} >= {
        "penal",
        "constitucional",
        "administrativo",
    }


def test_query_planner_keeps_facts_in_branch_specific_tasks():
    question = (
        "Um funcionário público usa carro do Estado para fins privados e causa acidente. "
        "Como avaliar responsabilidade disciplinar, civil, penal e administrativa?"
    )
    classification = _classification(
        query_text=question,
        main_branch="misto",
        branch_candidates=["administrativo", "civil", "penal"],
        needs_multi_branch_handling=True,
    )
    plan = legal_query_planner.plan(question, classification)
    penal_tasks = [task.query for task in plan.tasks if task.branch == "penal"]
    assert any("carro do Estado" in query for query in penal_tasks)
    assert any("Ramo jurídico: penal" in query for query in penal_tasks)


def test_retrieval_uses_original_facts_and_semantic_plan():
    question = "Um funcionário usa uma viatura pública para fins privados."
    classification = _classification(
        query_text=question,
        main_branch="penal",
        branch_candidates=["penal"],
        search_query="peculato de uso abuso de funções património público",
    )
    queries = _build_queries(question, classification, None)
    by_reason = {reason: query for query, reason, _ in queries}
    assert by_reason["base"] == question
    assert by_reason["semantic_plan"] == classification.search_query


def test_retrieval_quality_requests_correction_for_irrelevant_article():
    classification = _classification()
    plan = legal_query_planner.plan(classification.query_text, classification)
    chunk = RetrievedChunk(
        chunk_id="164",
        text="Artigo 164.º Reserva absoluta de competência legislativa.",
        source="cra.pdf",
        title="Constituição",
        link_original=None,
        page=87,
        article_number="164",
        law_status="Em vigor",
        source_scope="official",
        metadata={"article_main": "164", "legal_branch": "constitucional"},
    )
    assessment = retrieval_quality_evaluator.assess(
        plan,
        classification,
        [RetrievalEvidence(classification.query_text, chunk, 1.0, "base", "official")],
    )
    assert not assessment.sufficient
    assert assessment.correction_queries


def test_negative_answer_is_guarded_without_explicit_exclusion():
    chunk = RetrievedChunk(
        chunk_id="164",
        text="Artigo 164.º A Assembleia Nacional legisla sobre estas matérias.",
        source="cra.pdf",
        title="Constituição",
        link_original=None,
        page=87,
        article_number="164",
        law_status="Em vigor",
        source_scope="official",
        metadata={"article_main": "164", "legal_branch": "constitucional"},
    )
    answer, report = evidence_verifier.verify_and_guard(
        "### Resposta\n\nNão, a Assembleia Nacional não tem competência para destituir o Presidente.",
        "A Assembleia Nacional pode destituir o Presidente?",
        [RetrievalEvidence("q", chunk, 1.0, "base", "official")],
        ["retrieval_quality=partial:0.400"],
    )
    assert report.negative_claim_guarded
    assert "não permitem confirmar nem negar" in answer
    assert "não tem competência" not in answer


def test_unverified_article_citation_is_removed_from_answer():
    chunk = RetrievedChunk(
        chunk_id="363",
        text="Artigo 363.º Peculato de uso.",
        source="cp.pdf",
        title="Código Penal",
        link_original=None,
        page=60,
        article_number="363",
        law_status="Em vigor",
        source_scope="official",
        metadata={"article_main": "363", "legal_branch": "penal"},
    )
    answer, report = evidence_verifier.verify_and_guard(
        "### Resposta\n\nO Art. 400.º pune esta conduta.\n\nO Art. 363.º deve ser analisado.",
        "Qual é a responsabilidade penal?",
        [RetrievalEvidence("q", chunk, 1.0, "base", "official")],
        ["retrieval_quality=sufficient:0.900"],
    )
    assert "Art. 400" not in answer
    assert "Art. 363" in answer
    assert report.unsupported_claims


def test_concept_stemming_matches_inflected_legal_headings():
    assert _concept_search_stem("destituir") == "destitu"
    assert _concept_search_stem("destituição") == "destitui"


def test_generic_selection_preserves_multibranch_legal_evidence():
    classification = _classification(
        main_branch="misto",
        branch_candidates=["administrativo", "civil", "penal"],
        needs_multi_branch_handling=True,
    )

    def evidence(
        chunk_id: str, branch: str, score: float, text: str | None = None
    ) -> RetrievalEvidence:
        chunk = RetrievedChunk(
            chunk_id=chunk_id,
            text=text or f"Artigo {chunk_id}.º Enquadramento jurídico relevante.",
            source=f"{branch}.pdf",
            title=f"Diploma de {branch}",
            link_original=None,
            page=10,
            article_number=chunk_id,
            law_status="Em vigor",
            source_scope="official",
            metadata={
                "article_main": chunk_id,
                "legal_branch": branch,
                "segmentation": "article_block",
            },
        )
        return RetrievalEvidence("q", chunk, score, "original", "official")

    selected = _generic_official_selection(
        classification,
        [
            evidence(
                "363",
                "penal",
                27.0,
                "O funcionário usa a coisa móvel para fins diferentes dos devidos.",
            ),
            evidence("406", "penal", 30.0),
            evidence("75", "civil", 29.0),
            evidence("7", "administrativo", 28.0),
        ],
        question="O funcionário usa uma viatura para fins privados.",
    )
    branches = {item.chunk.metadata["legal_branch"] for item in selected}
    assert branches == {"administrativo", "civil", "penal"}
    assert {
        item.chunk.metadata["legal_branch"] for item in selected[:3]
    } == {"administrativo", "civil", "penal"}
    articles = [item.chunk.article_number for item in selected]
    assert articles.index("363") < articles.index("406")


def test_inferred_branch_does_not_invent_requested_diploma():
    classified = apply_pre_classification(
        {
            "main_branch": "misto",
            "topic_route": "geral",
            "requested_diplomas": ["Código Penal"],
        },
        "Um funcionário público usou uma viatura do Estado para fins privados.",
    )
    assert classified["requested_diplomas"] == []


def test_explicit_diploma_alias_is_preserved():
    classified = apply_pre_classification(
        {"main_branch": "penal", "topic_route": "cpp", "requested_diplomas": []},
        "Explique os artigos 10 e 137 do CPP.",
    )
    assert classified["requested_diplomas"] == ["Código do Processo Penal"]


def test_full_cpp_name_with_de_is_preserved_and_negated_penal_code_is_ignored():
    classified = apply_pre_classification(
        {"main_branch": "penal", "topic_route": "cpp", "requested_diplomas": []},
        "Explique os artigos 10 e 137 do Código de Processo Penal. "
        "Não confunda com o Código Penal.",
    )
    assert classified["requested_diplomas"] == ["Código do Processo Penal"]


def test_negated_diploma_is_not_treated_as_requested():
    classified = apply_pre_classification(
        {"main_branch": "penal", "topic_route": "cpp", "requested_diplomas": []},
        "Explique os artigos 10 e 137 do CPP. Não confunda com o Código Penal.",
    )
    assert classified["requested_diplomas"] == ["Código do Processo Penal"]


def test_cloudflare_embedding_client_splits_payload_too_large():
    assert CloudflareEmbeddingClient._should_split_batch(413, 20)
    assert not CloudflareEmbeddingClient._should_split_batch(413, 1)


def test_pipeline_merge_preserves_requested_branch_coverage():
    classification = _classification(
        main_branch="misto",
        branch_candidates=["administrativo", "civil", "penal"],
        needs_multi_branch_handling=True,
    )

    def evidence(chunk_id: str, branch: str, score: float) -> RetrievalEvidence:
        chunk = RetrievedChunk(
            chunk_id=chunk_id,
            text="Norma relevante.",
            source=f"{branch}.pdf",
            title=branch,
            link_original=None,
            page=int(chunk_id),
            article_number=chunk_id,
            law_status="Em vigor",
            source_scope="official",
            metadata={"article_main": chunk_id, "legal_branch": branch},
        )
        return RetrievalEvidence("q", chunk, score, "base", "official")

    merged = RAGPipeline._with_branch_coverage(
        [
            evidence("1", "civil", 30),
            evidence("2", "civil", 29),
            evidence("3", "administrativo", 28),
            evidence("4", "penal", 20),
        ],
        4,
        classification,
    )
    assert {(item.chunk.metadata or {}).get("legal_branch") for item in merged} == {
        "administrativo",
        "civil",
        "penal",
    }
