from __future__ import annotations

import re


_SUSPICIOUS_MARKERS = ("Ã", "Â", "â€", "�")
_ORDINAL_RE = re.compile(r"(\d+)\.\s*([º°ª])")
_N_ORDINAL_RE = re.compile(r"\bn\.\s*([º°ª])", re.IGNORECASE)


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in _SUSPICIOUS_MARKERS)


def _try_redecode(text: str, encoding: str) -> str | None:
    try:
        repaired = text.encode(encoding).decode("utf-8")
    except Exception:
        return None
    return repaired


def normalize_legal_text(text: str | None) -> str:
    """Repair common mojibake and legal ordinal artifacts for display.

    The app ingests multiple sources and sometimes receives UTF-8 text that
    was decoded as Latin-1/Windows-1252 somewhere along the path. This helper
    repairs the most common cases without altering already-correct text.
    """

    if not text:
        return ""

    updated = str(text)

    if _mojibake_score(updated) > 0:
        for _ in range(2):
            candidates: list[str] = []
            for encoding in ("latin1", "cp1252"):
                repaired = _try_redecode(updated, encoding)
                if repaired and repaired != updated:
                    candidates.append(repaired)
            if not candidates:
                break
            best = min(candidates, key=_mojibake_score)
            if _mojibake_score(best) >= _mojibake_score(updated):
                break
            updated = best

    updated = (
        updated.replace("Âº", "º")
        .replace("Âª", "ª")
        .replace("Â°", "º")
        .replace("Ã³", "ó")
        .replace("Ã¡", "á")
        .replace("Ã£", "ã")
        .replace("Ã§", "ç")
        .replace("Ãª", "ê")
        .replace("Ã©", "é")
        .replace("Ã¬", "ì")
        .replace("Ãº", "ú")
        .replace("Ãµ", "õ")
    )
    updated = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u241b-\u241f]", "", updated)

    updated = _ORDINAL_RE.sub(
        lambda match: f"{match.group(1)}.{match.group(2) if match.group(2) in {'º', 'ª'} else 'º'}",
        updated,
    )
    updated = _N_ORDINAL_RE.sub("n.º", updated)
    updated = re.sub(r"\bArt(?:igo)?\.\s*(\d+)\.\s*[º°]", r"Art. \1.º", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bArt(?:igo)?\s+(\d+)\.\s*[º°]", r"Art. \1.º", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bArt(?:igo)?\.?\s+(\d{3})([1-9])\b", r"Art. \1.º, n.º \2", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\b(Art\. \d+\.º)(?=(?:da|do|de|das|dos)\b)", r"\1 ", updated, flags=re.IGNORECASE)

    return updated
