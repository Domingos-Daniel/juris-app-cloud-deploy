from __future__ import annotations

import re

ARTICLE_RE = re.compile(
    r"(?:art|artigo|artigos|art\.?º?)\s*(\d+[.]?\d*)", re.IGNORECASE
)


def extract_article_references(text: str) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for match in ARTICLE_RE.finditer(text or ""):
        article = match.group(1).rstrip(".").replace(".", "")
        if article in seen:
            continue
        seen.add(article)
        items.append(article)
    return items


def primary_article_number(text: str) -> str | None:
    refs = extract_article_references(text)
    return refs[0] if refs else None


def article_reference_summary(text: str, limit: int = 4) -> str | None:
    refs = extract_article_references(text)
    if not refs:
        return None
    return ", ".join(refs[:limit])
