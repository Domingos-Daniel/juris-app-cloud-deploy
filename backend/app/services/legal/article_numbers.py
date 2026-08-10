from __future__ import annotations

import re

ARTICLE_NUMBER_TOKEN = r"\d+[.]?\d*\s*(?:[º°ª]|\.º|\.°)?"

ARTICLE_LIST_RE = re.compile(
    r"\b(?:art(?:igo|igos)?|arts?\.?)\s*"
    r"(?:n[.º°]*\s*)?"
    rf"({ARTICLE_NUMBER_TOKEN}(?:\s*(?:,|;|/|\be\b|\bou\b)\s*{ARTICLE_NUMBER_TOKEN})*)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"\d+[.]?\d*")


def extract_requested_article_numbers(text: str) -> list[str]:
    articles: list[str] = []
    seen: set[str] = set()
    for match in ARTICLE_LIST_RE.finditer(text or ""):
        for number_match in NUMBER_RE.finditer(match.group(1) or ""):
            article = number_match.group(0).replace(".", "").lstrip("0") or "0"
            if article in seen:
                continue
            seen.add(article)
            articles.append(article)
    return articles
