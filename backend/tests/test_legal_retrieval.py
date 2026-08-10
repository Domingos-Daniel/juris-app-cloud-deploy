import pytest

from app.services.legal.models import LegalClassification
from app.services.legal.retrieval import _successions_direct_rescue


pytestmark = pytest.mark.asyncio


async def test_successions_direct_rescue_returns_code_civil_chunks():
    classification = LegalClassification(
        query_text="Como funciona herança em Angola?",
        main_branch="familia",
        branch_candidates=["familia", "civil"],
        request_type="explicacao_simples",
        specificity="geral",
        audience="leigo",
        topic_route="sucessoes",
        search_query="Como funciona herança em Angola?",
        norm_type_needed="misto",
        requested_diplomas=["Código Civil"],
        needs_source_separation=True,
    )

    rescue = _successions_direct_rescue(
        "Como funciona herança em Angola?", classification
    )

    assert rescue
    assert all(
        item.chunk.metadata and item.chunk.metadata.get("diploma_slug") == "codigo-civil"
        for item in rescue
    )
