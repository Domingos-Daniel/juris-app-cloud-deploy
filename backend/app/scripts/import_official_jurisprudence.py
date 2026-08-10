from __future__ import annotations

import argparse
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

from app.db.postgres import postgres_manager
from app.services.rag.embeddings import embedding_service

TS_BASE_URL = "https://tribunalsupremo.ao"
TS_INDEX_URL = "https://tribunalsupremo.ao/Categoria/jurisprudencia/"
TC_ARCHIVE_URL = "https://www.tribunalconstitucional.ao/pt/jurisprudencia/arquivo/"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    )
}
SESSION = requests.Session()
SESSION.headers.update(REQUEST_HEADERS)

TS_SEED_URLS = (
    "https://tribunalsupremo.ao/Categoria/jurisprudencia/",
    "https://tribunalsupremo.ao/Categoria/jurisprudencia/acordaos/",
    "https://tribunalsupremo.ao/Categoria/jurisprudencia/sumarios-de-acordaos/",
    "https://tribunalsupremo.ao/Categoria/jurisprudencia/sumarios-de-acordaos/camara-do-trabalho-sumarios-de-acordaos/",
    "https://tribunalsupremo.ao/jurisprudencia/acordaos/",
)

TS_DIRECT_URLS = (
    "https://tribunalsupremo.ao/acordaoproc-no5403-21-burla-por-defraudacao/",
    "https://tribunalsupremo.ao/acordaoproc-no4802-20-roubo-e-burla-por-defraudacao/",
    "https://tribunalsupremo.ao/acordaoproc-no4005-19-burla-por-defraudacao-falsificacao-de-documentos/",
    "https://tribunalsupremo.ao/tscc-acordao-proc-no-2018-18-de-27-de-novembro-de-2018-burla-por-defraudacao/",
    "https://tribunalsupremo.ao/proc-no5835-21-burla-por-defraudacao-e-ameaca-votacao-unanimidade-decisao-condenado/",
    "https://tribunalsupremo.ao/apelacao-accao-de-recurso-em-materia-disciplinar/",
)

KEYWORD_GROUPS = {
    "penal": (
        "crime",
        "penal",
        "burla",
        "defraudação",
        "defraudacao",
        "furto",
        "roubo",
        "homicídio",
        "homicidio",
        "corrupção",
        "corrupcao",
        "associação criminosa",
        "associacao criminosa",
        "prisão preventiva",
        "prisao preventiva",
        "habeas corpus",
        "violação",
        "violacao",
        "abuso sexual",
    ),
    "laboral": (
        "laboral",
        "trabalho",
        "trabalhador",
        "despedimento",
        "inss",
        "extra vel ultra petitum",
        "ultra petitum",
        "matéria disciplinar",
        "materia disciplinar",
        "processo laboral",
        "câmara do trabalho",
        "camara do trabalho",
    ),
    "administrativo": (
        "acto administrativo",
        "ato administrativo",
        "contencioso",
        "impugnação administrativa",
        "impugnacao administrativa",
        "agravo administrativo",
    ),
    "tributario": ("tribut", "fiscal", "ipu", "iva", "imposto"),
    "constitucional": (
        "constitucional",
        "inconstitucionalidade",
        "recurso extraordinário de inconstitucionalidade",
        "recurso extraordinario de inconstitucionalidade",
    ),
    "familia": ("divórcio", "divorcio", "família", "familia", "menor", "alimentos"),
    "comercial": (
        "sociedade",
        "accionista",
        "acionista",
        "quotas",
        "providência cautelar",
        "providencia cautelar",
    ),
}

ROUTE_BY_BRANCH = {
    "penal": "penal_substantivo",
    "laboral": "laboral",
    "administrativo": "contencioso_admin",
    "tributario": "tributario",
    "constitucional": "constitucional",
    "familia": "familia",
    "comercial": "sociedades",
}


def _clean(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").casefold())
    return slug.strip("-")[:120] or "item"


def _normalize_supremo_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return urljoin(TS_BASE_URL, url)
    if parsed.netloc in {"localhost", "www.localhost"}:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return urljoin(TS_BASE_URL, path)
    return url


def _get(url: str) -> requests.Response:
    response = SESSION.get(url, timeout=(5, 15))
    response.raise_for_status()
    return response


def _iter_paginated_urls(base_url: str, max_pages: int) -> list[str]:
    base = base_url.rstrip("/") + "/"
    urls = [base]
    urls.extend(f"{base}page/{page}/" for page in range(2, max(2, max_pages) + 1))
    return urls


def _infer_branch_and_route(text: str) -> tuple[str, str]:
    haystack = (text or "").casefold()
    for branch, tokens in KEYWORD_GROUPS.items():
        if any(token in haystack for token in tokens):
            return branch, ROUTE_BY_BRANCH.get(branch, "geral")
    if any(token in haystack for token in ("herança", "heranca", "sucess")):
        return "familia", "sucessoes"
    return "indeterminado", "geral"


def _extract_case_number(text: str) -> str | None:
    patterns = (
        r"\bProc(?:esso)?\.?\s*(?:n[.º°o]\s*)?([A-Za-z0-9./-]+)",
        r"\bAc[oó]rd[aã]o\s*(?:n[.º°o]\s*)?([A-Za-z0-9./-]+)",
        r"\bn[.º°o]\s*([0-9]{1,6}\s*/\s*[0-9]{2,4})",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return re.sub(r"\s+", "", match.group(1)).strip(".,;:")
    return None


def _keywords_from_text(text: str) -> list[str]:
    haystack = (text or "").casefold()
    found: list[str] = []
    for tokens in KEYWORD_GROUPS.values():
        for token in tokens:
            if token in haystack and token not in found:
                found.append(token)
    return found[:12]


def _ts_cases_from_index(html: str, page_url: str) -> list[dict]:
    cases: list[dict] = []
    seen: set[str] = set()
    patterns = (
        r'<h[12][^>]*>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>\s*</h[12]>',
        r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>[^<]*(?:Ac[oó]rd[aã]o|Processo|Proc\.|TSCC)[^<]*)</a>',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
            title = _clean(match.group("title"))
            url = _normalize_supremo_url(urljoin(page_url, match.group("url")))
            if not title or url in seen:
                continue
            if "tribunalsupremo.ao" not in urlparse(url).netloc:
                continue
            if not re.search(r"ac[oó]rd[aã]o|processo|proc\.|tscc|jurisprud", title, re.IGNORECASE):
                continue
            seen.add(url)
            cases.append({"title": title, "url": url})
    return cases


def _extract_meta_description(html: str) -> str | None:
    patterns = (
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            value = _clean(match.group(1))
            if value:
                return value
    return None


def _extract_summary_from_paragraphs(html: str) -> str | None:
    paragraphs = [_clean(item) for item in re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)]
    boilerplate = re.compile(
        r"siga-nos|contactos|link directos|portal oficial|cidade alta|presid[eê]ncia da rep[uú]blica|"
        r"assembleia nacional|tribunal de contas|minist[eé]rio da administra[cç][aã]o p[uú]blica|"
        r"loading|open in new tab|reload document|tamanho m[aá]ximo do ficheiro|"
        r"dimens[oõ]es de imagem sugeridas",
        re.IGNORECASE,
    )
    paragraphs = [item for item in paragraphs if len(item) >= 40 and not boilerplate.search(item)]
    priority = [
        item
        for item in paragraphs
        if re.search(r"resumo|sum[aá]rio|decid|provimento|recurso|arguid|trabalh|burla|defraud", item, re.IGNORECASE)
    ]
    selected = priority or paragraphs
    if not selected:
        return None
    return " ".join(selected[:4])[:1800].strip()


def _ts_detail(detail_html: str) -> tuple[str | None, str | None, str | None]:
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", detail_html, re.IGNORECASE | re.DOTALL)
    title = _clean(title_match.group(1)) if title_match else None

    summary_patterns = (
        r"Resumo do Ac[oó]rd[aã]o:\s*</?strong[^>]*>\s*(.*?)\s*</p>",
        r"Resumo do Ac[oó]rd[aã]o\s*:?(.{40,1800}?)(?:</p>|<h|<div)",
    )
    summary = None
    for pattern in summary_patterns:
        match = re.search(pattern, detail_html, re.IGNORECASE | re.DOTALL)
        if match:
            summary = _clean(match.group(1))
            if summary:
                break
    summary = summary or _extract_meta_description(detail_html) or _extract_summary_from_paragraphs(detail_html)

    pdf_match = re.search(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', detail_html, re.IGNORECASE)
    pdf_url = _normalize_supremo_url(urljoin(TS_INDEX_URL, pdf_match.group(1))) if pdf_match else None
    return title, summary, pdf_url


def _enrich_ts_item(item: dict) -> dict:
    try:
        detail = _get(item["url"])
        title, summary, pdf_url = _ts_detail(detail.text)
    except Exception:
        title, summary, pdf_url = item["title"], None, None
    title_text = title or item["title"]
    branch, route = _infer_branch_and_route(title_text)
    if branch == "indeterminado":
        branch, route = _infer_branch_and_route(f"{title_text} {summary or ''}".strip())
    case_id = "ts:" + hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:16]
    case_number = _extract_case_number(f"{title_text} {summary or ''}")
    keywords = _keywords_from_text(f"{title_text} {summary or ''}")
    return {
        "id": case_id,
        "court": "Tribunal Supremo",
        "chamber": None,
        "case_number": case_number,
        "title": title or item["title"],
        "publication_date": None,
        "url": item["url"],
        "pdf_url": pdf_url,
        "legal_branch": branch,
        "topic_route": route,
        "summary": summary,
        "metadata": {
            "document_kind": "jurisprudence",
            "source_kind": "jurisprudence",
            "court": "Tribunal Supremo",
            "case_number": case_number,
            "keywords": keywords,
        },
    }


def fetch_tribunal_supremo(limit: int = 120, max_pages: int = 16) -> list[dict]:
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    for url in TS_DIRECT_URLS:
        normalized = _normalize_supremo_url(url)
        candidates.append({"title": normalized.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title(), "url": normalized})
        seen_urls.add(normalized)
    for seed_url in TS_SEED_URLS:
        for page_url in _iter_paginated_urls(seed_url, max_pages):
            try:
                response = _get(page_url)
            except Exception:
                if page_url.endswith("/page/2/"):
                    break
                continue
            page_cases = _ts_cases_from_index(response.text, page_url)
            if not page_cases and "/page/" in page_url:
                break
            for item in page_cases:
                if item["url"] in seen_urls:
                    continue
                seen_urls.add(item["url"])
                candidates.append(item)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    enriched: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_enrich_ts_item, item) for item in candidates]
        for future in as_completed(futures):
            enriched.append(future.result())
    return enriched


def fetch_tribunal_constitucional(limit: int = 40) -> list[dict]:
    try:
        response = _get(TC_ARCHIVE_URL)
    except Exception:
        return []
    pattern = re.compile(
        r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>\s*(?P<title>Ac[oó]rd[aã]o[^<]+)\s*</a>',
        re.IGNORECASE,
    )
    items: list[dict] = []
    seen: set[str] = set()
    for match in pattern.finditer(response.text):
        title = _clean(match.group("title"))
        url = urljoin(TC_ARCHIVE_URL, match.group("url"))
        if not title or url in seen:
            continue
        seen.add(url)
        case_id = "tc:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        case_number = _extract_case_number(title)
        items.append(
            {
                "id": case_id,
                "court": "Tribunal Constitucional",
                "chamber": None,
                "case_number": case_number,
                "title": title,
                "publication_date": None,
                "url": url,
                "pdf_url": url if url.lower().endswith(".pdf") else None,
                "legal_branch": "constitucional",
                "topic_route": "constitucional",
                "summary": f"{title}. Jurisprudência oficial do Tribunal Constitucional de Angola.",
                "metadata": {
                    "document_kind": "jurisprudence",
                    "source_kind": "jurisprudence",
                    "court": "Tribunal Constitucional",
                    "case_number": case_number,
                    "keywords": ["constitucional", "inconstitucionalidade"],
                },
            }
        )
        if len(items) >= limit:
            break
    return items


def _segment_text(item: dict) -> str:
    keywords = item.get("metadata", {}).get("keywords") or []
    pieces = [
        f"Tribunal: {item['court']}.",
        f"Título: {item['title']}.",
        f"Processo: {item.get('case_number') or 'não identificado'}.",
        f"Ramo: {item.get('legal_branch') or 'indeterminado'}.",
    ]
    if keywords:
        pieces.append("Palavras-chave: " + ", ".join(keywords) + ".")
    pieces.append(f"Sumário: {item.get('summary') or item['title']}.")
    pieces.append(f"URL oficial: {item['url']}.")
    return " ".join(pieces)


async def _to_segments(cases: list[dict]) -> list[dict]:
    segments: list[dict] = []
    for item in cases:
        text = _segment_text(item)
        embedding = await embedding_service.embed_query(text)
        slug = f"jurisprudencia-{item['court'].casefold().replace(' ', '-')}-{_slugify(item['title'])}"
        metadata = {
            "source": item["court"],
            "title": item["title"],
            "link_original": item["url"],
            "page": None,
            "article_number": None,
            "article_main": None,
            "article_references": [],
            "law_status": "Jurisprudência oficial",
            "source_scope": "official",
            "document_id": item["id"],
            "diploma_slug": slug,
            "legal_branch": item.get("legal_branch") or "indeterminado",
            "topic_route": item.get("topic_route") or "geral",
            "document_kind": "jurisprudence",
            "source_kind": "jurisprudence",
            "court": item["court"],
            "case_number": item.get("case_number"),
            "keywords": item.get("metadata", {}).get("keywords") or [],
        }
        segments.append(
            {
                "id": f"{item['id']}:summary",
                "text": text,
                "embedding": embedding,
                "metadata": metadata,
            }
        )
    return segments


async def main(limit_ts: int, limit_tc: int, max_ts_pages: int) -> None:
    postgres_manager.initialize()
    cases = fetch_tribunal_supremo(limit_ts, max_pages=max_ts_pages) + fetch_tribunal_constitucional(limit_tc)
    postgres_manager.upsert_jurisprudence_cases(cases)
    postgres_manager.upsert_legal_segments(await _to_segments(cases))
    by_court: dict[str, int] = {}
    by_branch: dict[str, int] = {}
    for item in cases:
        by_court[item["court"]] = by_court.get(item["court"], 0) + 1
        branch = item.get("legal_branch") or "indeterminado"
        by_branch[branch] = by_branch.get(branch, 0) + 1
    print(f"Importados {len(cases)} registos de jurisprudência oficial.")
    print(f"Por tribunal: {by_court}")
    print(f"Por ramo: {by_branch}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-ts", type=int, default=120)
    parser.add_argument("--limit-tc", type=int, default=40)
    parser.add_argument("--max-ts-pages", type=int, default=16)
    args = parser.parse_args()

    import asyncio

    asyncio.run(main(args.limit_ts, args.limit_tc, args.max_ts_pages))
