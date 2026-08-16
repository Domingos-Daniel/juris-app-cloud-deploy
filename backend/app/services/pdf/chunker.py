from __future__ import annotations

import re

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover - fallback for environments not yet updated
    RecursiveCharacterTextSplitter = None

from app.core.config import get_settings

ARTICLE_BLOCK_RE = re.compile(
    r"(?is)(art(?:\.|igo)?\s*\d+[.ºo°]*\s*[-–—:]?.*?)(?=(?:\n\s*art(?:\.|igo)?\s*\d+[.ºo°]*\s*[-–—:]?)|\Z)"
)
ARTICLE_HEADER_RE = re.compile(r"art(?:\.|igo)?\s*(\d+[.]?\d*)", re.IGNORECASE)
SECTION_MARKER_RE = re.compile(
    r"(?im)^\s*(?P<kind>livro|parte|t[íi]tulo|cap[íi]tulo|sec[cç][aã]o|subsec[cç][aã]o|divis[aã]o|subdivis[aã]o)\b(?P<label>.*)$"
)

HIERARCHY_KEYS = {
    "livro": "book",
    "parte": "part",
    "titulo": "title",
    "capitulo": "chapter",
    "seccao": "section",
    "subseccao": "subsection",
    "divisao": "division",
    "subdivisao": "subdivision",
}


def _ascii_key(value: str) -> str:
    import unicodedata

    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def update_hierarchy(text: str, hierarchy: dict[str, str] | None = None) -> dict[str, str]:
    current = dict(hierarchy or {})
    for match in SECTION_MARKER_RE.finditer(text or ""):
        key = HIERARCHY_KEYS.get(_ascii_key(match.group("kind")))
        if key:
            label = match.group("label").strip()
            if not label or re.fullmatch(r"[IVXLCDM\d.º°-]+", label, re.IGNORECASE):
                remainder = (text or "")[match.end() :]
                next_line = next(
                    (line.strip() for line in remainder.splitlines() if line.strip()),
                    "",
                )
                if next_line and not ARTICLE_HEADER_RE.search(next_line):
                    label = f"{label} {next_line}".strip()
            current[key] = f"{match.group('kind').strip()} {label}".strip()
    return current


def chunk_text(text: str, min_words: int | None = None, max_words: int | None = None) -> list[str]:
    settings = get_settings()
    min_words = min_words or settings.chunk_min_words
    max_words = max_words or settings.chunk_max_words

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        if end < len(words) and (end - start) < min_words:
            end = min(start + min_words, len(words))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end == len(words):
            break
        start = max(end - 60, start + 1)
    return chunks


def semantic_chunk_text(text: str) -> list[str]:
    settings = get_settings()
    if RecursiveCharacterTextSplitter is None:
        return chunk_text(text, min_words=settings.chunk_min_words, max_words=settings.chunk_max_words)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=[
            "\nArtigo ",
            "\nARTIGO ",
            "\nCapítulo ",
            "\nCAPÍTULO ",
            "\nSecção ",
            "\nSECÇÃO ",
            "\n\n",
            "\n",
            " ",
            "",
        ],
        keep_separator=True,
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]


def legal_semantic_chunks(
    text: str, inherited_hierarchy: dict[str, str] | None = None
) -> list[dict]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    article_blocks = [match.group(1).strip() for match in ARTICLE_BLOCK_RE.finditer(cleaned) if match.group(1).strip()]
    if article_blocks:
        chunks: list[dict] = []
        matches = [match for match in ARTICLE_BLOCK_RE.finditer(cleaned) if match.group(1).strip()]
        for match, block in zip(matches, article_blocks, strict=False):
            header = ARTICLE_HEADER_RE.search(block)
            article_main = header.group(1).replace(".", "") if header else None
            hierarchy = update_hierarchy(cleaned[: match.start()], inherited_hierarchy)
            chunks.append(
                {
                    "text": block,
                    "article_main": article_main,
                    "segmentation": "article_block",
                    "hierarchy": hierarchy,
                    "parent_context": " > ".join(hierarchy.values()),
                }
            )
        return chunks

    section_lines = list(SECTION_MARKER_RE.finditer(cleaned))
    if section_lines:
        return [
            {
                "text": chunk,
                "article_main": ARTICLE_HEADER_RE.search(chunk).group(1).replace(".", "") if ARTICLE_HEADER_RE.search(chunk) else None,
                "segmentation": "semantic_fallback",
                "hierarchy": update_hierarchy(cleaned, inherited_hierarchy),
            }
            for chunk in semantic_chunk_text(cleaned)
        ]

    return [
        {
            "text": chunk,
            "article_main": ARTICLE_HEADER_RE.search(chunk).group(1).replace(".", "") if ARTICLE_HEADER_RE.search(chunk) else None,
            "segmentation": "plain_fallback",
            "hierarchy": dict(inherited_hierarchy or {}),
        }
        for chunk in semantic_chunk_text(cleaned)
    ]
