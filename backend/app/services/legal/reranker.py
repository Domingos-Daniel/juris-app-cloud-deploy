from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LocalReranker:
    """
    Reranker local baseado em CrossEncoder (sentence-transformers).

    Substitui o reranker LLM anterior que fazia N chamadas HTTP por chunk.
    Este roda inteiramente na CPU local, sem latência de rede.

    Modelo padrão: cross-encoder/ms-marco-MiniLM-L-6-v2
    - Multilíngue suficiente para Português/Angolano em contexto jurídico
    - Rápido (~20ms por par em CPU)
    - Já incluído no sentence-transformers que está instalado
    """

    def __init__(self) -> None:
        self._model = None
        self.settings = get_settings()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", text or "")
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        return {
            token
            for token in re.findall(r"[a-z0-9]+", normalized.casefold())
            if len(token) > 3
        }

    def _lexical_scores(self, question: str, chunks_text: list[str]) -> list[float]:
        query_tokens = self._tokens(question)
        if not query_tokens:
            return [0.0] * len(chunks_text)
        scores: list[float] = []
        for text in chunks_text:
            chunk_tokens = self._tokens(text)
            overlap = len(query_tokens.intersection(chunk_tokens))
            coverage = overlap / max(1, len(query_tokens))
            specificity = overlap / max(1, len(chunk_tokens) ** 0.5)
            scores.append((coverage * 2.0) + specificity)
        return scores

    @property
    def model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.settings.reranker_model)
                logger.info("LocalReranker: CrossEncoder carregado com sucesso (%s)", self.settings.reranker_model)
            except Exception as exc:
                logger.warning(
                    "LocalReranker: falha ao carregar CrossEncoder (%s). "
                    "Reranking desactivado — todos os chunks aceites. Erro: %s",
                    self.settings.reranker_model, exc,
                )
                self._model = None
        return self._model

    async def rerank(
        self,
        question: str,
        chunks_text: list[str],
        provider: str | None = None,  # ignorado — mantido por compatibilidade de assinatura
    ) -> list[bool]:
        """
        Retorna lista de booleanos indicando se cada chunk é relevante.

        - Se o modelo não estiver disponível, aceita todos os chunks.
        - Trunca cada chunk a 500 chars para eficiência.
        - Garante que pelo menos 1 chunk é aceite (fallback).
        """
        if len(chunks_text) <= 2:
            return [True] * len(chunks_text)

        if not self.settings.reranker_enabled:
            scores = self._lexical_scores(question, chunks_text)
            top_n = min(max(1, self.settings.reranker_top_n), len(scores))
            accepted_indexes = set(
                sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:top_n]
            )
            return [index in accepted_indexes for index in range(len(scores))]

        model = self.model
        if model is None:
            # Fallback seguro: aceitar todos
            return [True] * len(chunks_text)

        pairs = [(question, text[:500]) for text in chunks_text]

        try:
            scores: list[float] = await asyncio.to_thread(model.predict, pairs)
        except Exception as exc:
            logger.warning("LocalReranker: erro ao calcular scores: %s. Aceitando todos.", exc)
            return [True] * len(chunks_text)

        top_n = min(max(1, self.settings.reranker_top_n), len(scores))
        accepted_indexes = set(
            sorted(range(len(scores)), key=lambda index: float(scores[index]), reverse=True)[:top_n]
        )
        results = [index in accepted_indexes for index in range(len(scores))]

        # Garantia: sempre pelo menos 1 chunk aceite
        if not any(results):
            best_idx = max(range(len(scores)), key=lambda i: scores[i])
            results[best_idx] = True

        accepted = sum(results)
        logger.debug(
            "LocalReranker: %d/%d chunks aceites por ranking",
            accepted, len(chunks_text),
        )
        return results

    async def scores(self, question: str, chunks_text: list[str]) -> list[float]:
        if not chunks_text:
            return []
        if not self.settings.reranker_enabled:
            return self._lexical_scores(question, chunks_text)
        if self.model is None:
            return self._lexical_scores(question, chunks_text)
        pairs = [(question, text[:1800]) for text in chunks_text]
        try:
            predicted = await asyncio.to_thread(self.model.predict, pairs)
            return [float(score) for score in predicted]
        except Exception as exc:
            logger.warning("LocalReranker: falha no scoring: %s", exc)
            return self._lexical_scores(question, chunks_text)


llm_reranker = LocalReranker()
