from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, asdict, field
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ocean_agents_demo import deepseek_client


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
KNOWLEDGE_JSON = DATA_DIR / "ocean_knowledge.json"
KNOWLEDGE_DOCS_DIR = DATA_DIR / "knowledge_docs"
PDF_REPORTS_DIR = DATA_DIR / "pdf_reports"
TOKEN_RE = re.compile(r"[A-Za-z0-9_+-]+|[\u4e00-\u9fff]+")
MARINE_TERMS = ["海洋酸化", "酸化", "贝类", "养殖", "海洋热浪", "热浪", "珊瑚", "渔业", "海平面", "监测", "治理", "规划", "风险"]
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass
class Doc:
    id: str
    title: str
    kind: str
    year: int
    source: str
    topics: list[str]
    abstract: str
    score: float = 0.0
    decision_score: float = 0.0
    backend: str = "local"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


EVIDENCE_SCHEMA_VERSION = "evidence_chunk.v1"


def enrich_evidence_metadata(
    docs: list[Doc],
    route: str,
    start_rank: int = 1,
) -> list[Doc]:
    """Attach a normalized EvidenceChunk contract to Doc.metadata."""
    for offset, doc in enumerate(docs):
        rank = start_rank + offset
        meta = dict(doc.metadata or {})
        pages = _metadata_pages(meta)
        content_type = str(meta.get("content_type") or _infer_content_type(doc))
        effective_route = str(meta.get("route") or route)
        evidence = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "doc_id": str(meta.get("doc_id") or meta.get("document_id") or doc.id),
            "chunk_id": str(meta.get("chunk_id") or doc.id),
            "dataset_id": str(meta.get("dataset_id") or ""),
            "document_name": str(meta.get("document_name") or doc.title),
            "title": doc.title,
            "source": doc.source,
            "source_path": str(meta.get("source_path") or doc.source),
            "section": str(meta.get("section") or meta.get("section_path") or ""),
            "section_path": meta.get("section_path") or [],
            "page": pages[0] if pages else None,
            "pages": pages,
            "bbox": meta.get("bbox"),
            "content_type": content_type,
            "year": doc.year or None,
            "backend": doc.backend,
            "route": effective_route,
            "retrieval_query": str(meta.get("retrieval_query") or ""),
            "query_variant": str(meta.get("query_variant") or ""),
            "routes": meta.get("routes") or [],
            "rank_before": int(meta.get("rank_before") or rank),
            "rank_after": int(meta.get("rank_after") or rank),
            "similarity": _as_float(meta.get("similarity", doc.score), default=doc.score),
            "vector_similarity": _nullable_float(meta.get("vector_similarity")),
            "term_similarity": _nullable_float(meta.get("term_similarity")),
            "rerank_score": _nullable_float(meta.get("rerank_score")),
            "rerank_reason": str(meta.get("rerank_reason") or "not_reranked"),
        }
        meta.update(evidence)
        meta["evidence"] = evidence
        doc.metadata = meta
    return docs


def doc_to_evidence_dict(doc: Doc) -> dict[str, Any]:
    """Serialize a Doc with a stable evidence block and convenience fields."""
    data = asdict(doc)
    evidence = dict((doc.metadata or {}).get("evidence") or {})
    if not evidence:
        enrich_evidence_metadata([doc], doc.backend)
        evidence = dict((doc.metadata or {}).get("evidence") or {})
        data = asdict(doc)
    data["evidence"] = evidence
    for key in (
        "doc_id", "chunk_id", "dataset_id", "document_name", "page", "pages",
        "section", "section_path", "content_type", "rank_before", "rank_after",
        "rerank_score", "rerank_reason", "similarity", "vector_similarity",
        "term_similarity", "retrieval_query", "query_variant", "routes",
    ):
        data[key] = evidence.get(key)
    return data


def _metadata_pages(meta: dict[str, Any]) -> list[int]:
    raw = meta.get("pages")
    if raw is None and meta.get("page") is not None:
        raw = [meta.get("page")]
    pages: list[int] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if page not in pages:
                pages.append(page)
    return pages


def _infer_content_type(doc: Doc) -> str:
    if doc.kind == "ragflow_chunk":
        return "paragraph"
    if doc.kind == "local_pdf_metadata":
        return "document_metadata"
    if doc.kind == "local_pdf":
        return "pdf_text"
    if doc.kind == "local_markdown":
        return "markdown_section"
    return doc.kind or "text"


def _nullable_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for part in TOKEN_RE.findall(text.lower()):
        if any("\u4e00" <= ch <= "\u9fff" for ch in part):
            for n in (2, 3, 4):
                out.update(part[i : i + n] for i in range(max(0, len(part) - n + 1)))
        elif len(part) > 1:
            out.add(part)
    return out


def condense_intent(question: str) -> dict[str, Any]:
    keywords = [term for term in MARINE_TERMS if term in question]
    if not keywords:
        keywords = sorted(tokenize(question), key=len, reverse=True)[:8]
    return {
        "original_question": question,
        "refined_question": question[:90],
        "retrieval_query": " ".join(dict.fromkeys(keywords + [question])),
        "keywords": keywords,
    }


def load_docs() -> list[Doc]:
    return [Doc(**asdict(doc)) for doc in _load_docs_cached()]


@lru_cache(maxsize=1)
def _load_docs_cached() -> tuple[Doc, ...]:
    docs: list[Doc] = []
    if KNOWLEDGE_JSON.exists():
        raw = json.loads(KNOWLEDGE_JSON.read_text(encoding="utf-8"))
        docs.extend(Doc(**item) for item in raw)
    docs.extend(load_markdown_docs(KNOWLEDGE_DOCS_DIR))
    docs.extend(load_pdf_summaries(PDF_REPORTS_DIR))
    return tuple(_dedupe_docs(docs))


def clear_doc_cache() -> None:
    _load_docs_cached.cache_clear()
    ragflow_catalog.cache_clear()


def load_markdown_docs(directory: Path) -> list[Doc]:
    if not directory.exists():
        return []
    docs: list[Doc] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        title = _first_heading(text) or path.stem
        topics = _field_list(text, "主题")
        abstract = _field_text(text, "摘要") or _compact_text(text, 900)
        docs.append(
            Doc(
                id=f"md-{path.stem}",
                title=title,
                kind=_field_text(text, "类型") or "local_markdown",
                year=_field_year(text) or 0,
                source=str(path),
                topics=topics,
                abstract=abstract,
            )
        )
    return docs


def load_pdf_summaries(directory: Path) -> list[Doc]:
    if not directory.exists():
        return []
    docs: list[Doc] = []
    for path in sorted(directory.rglob("*.pdf")):
        text = _extract_pdf_text(path) if os.getenv("OCEAN_PARSE_PDF_ON_LOAD", "").lower() == "true" else ""
        title = _clean_pdf_title(path.stem)
        topics = [term for term in MARINE_TERMS if term in title or term in text]
        abstract = _compact_text(text, 1200) if text else _metadata_abstract(path, title, topics)
        docs.append(
            Doc(
                id=f"pdf-{path.stem}",
                title=title,
                kind="local_pdf" if text else "local_pdf_metadata",
                year=_field_year(text) or 0,
                source=str(path),
                topics=topics,
                abstract=abstract,
            )
        )
    return docs


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            return ""
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages[:12]:
            pages.append(page.extract_text() or "")
        return "\n".join(pages).strip()
    except Exception:
        return ""


def _clean_pdf_title(stem: str) -> str:
    return re.sub(r"_[0-9a-f]{8}$", "", stem, flags=re.IGNORECASE).replace("_", " ").strip()


def _metadata_abstract(path: Path, title: str, topics: list[str]) -> str:
    lang = "中文" if "cn" in {part.lower() for part in path.parts} else "英文"
    inferred = topics or [term for term in MARINE_TERMS if term.lower() in title.lower()]
    if not inferred:
        inferred = _infer_topics_from_title(title)
    topic_text = "、".join(inferred) if inferred else "海洋环境、海洋监测或海洋治理"
    return f"{lang}本地 PDF 文档《{title}》，主题可由标题推断为：{topic_text}。在线接口默认使用文件名和元数据参与检索；完整正文建议上传到 RAGFlow 知识库解析。"


def _infer_topics_from_title(title: str) -> list[str]:
    mapping = {
        "sst": "海表温度",
        "temperature": "海温",
        "salinity": "盐度",
        "salt": "盐度",
        "wave": "海浪",
        "waves": "海浪",
        "sea level": "海平面",
        "carbon": "碳循环",
        "bgc": "生物地球化学",
        "biomass": "生物量",
        "heat content": "海洋热含量",
        "reanalysis": "再分析",
        "forecast": "预报",
        "海洋生态": "海洋生态",
        "海洋灾害": "海洋灾害",
        "海平面": "海平面",
        "近岸海域": "近岸海域",
        "环境质量": "环境质量",
    }
    lower = title.lower()
    out = []
    for key, value in mapping.items():
        if key in lower or key in title:
            out.append(value)
    return list(dict.fromkeys(out))


def _dedupe_docs(docs: list[Doc]) -> list[Doc]:
    seen: set[str] = set()
    out: list[Doc] = []
    for doc in docs:
        key = doc.id or doc.title
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _field_text(text: str, name: str) -> str:
    pattern = re.compile(rf"^{re.escape(name)}[：:]\s*(.+)$", re.MULTILINE)
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _field_list(text: str, name: str) -> list[str]:
    value = _field_text(text, name)
    if not value:
        return []
    return [item.strip() for item in re.split(r"[、,，;；]", value) if item.strip()]


def _field_year(text: str) -> int:
    match = re.search(r"(19|20)\d{2}", text)
    return int(match.group(0)) if match else 0


def _compact_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def local_retrieve(query: str, top_k: int) -> list[Doc]:
    q = tokenize(query)
    docs = []
    for doc in load_docs():
        d = tokenize(doc.title + " " + " ".join(doc.topics) + " " + doc.abstract)
        overlap = q & d
        doc.score = min(1.0, len(overlap) / max(1.0, math.sqrt(len(q)) * 5))
        if doc.score:
            docs.append(doc)
    ranked = sorted(docs, key=lambda d: d.score, reverse=True)[:top_k]
    return enrich_evidence_metadata(ranked, route="local_keyword")


def rag_status() -> dict[str, Any]:
    docs = load_docs()
    ragflow = {
        "base_url": os.getenv("RAGFLOW_BASE_URL", ""),
        "configured": bool(os.getenv("RAGFLOW_API_KEY") and os.getenv("RAGFLOW_DATASET_IDS")),
        "dataset_ids": [x.strip() for x in os.getenv("RAGFLOW_DATASET_IDS", "").split(",") if x.strip()],
    }
    if ragflow["configured"]:
        try:
            catalog = ragflow_catalog()
            ragflow["datasets"] = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "language": item.get("language"),
                    "document_count": item.get("document_count"),
                    "chunk_count": item.get("chunk_count"),
                    "embedding_model": item.get("embedding_model"),
                }
                for item in catalog.get("datasets", [])
            ]
        except Exception as exc:
            ragflow["catalog_error"] = f"{type(exc).__name__}: {exc}"
    return {
        "llm": deepseek_client.status(),
        "local": {
            "document_count": len(docs),
            "knowledge_json": str(KNOWLEDGE_JSON),
            "knowledge_docs_dir": str(KNOWLEDGE_DOCS_DIR),
            "pdf_reports_dir": str(PDF_REPORTS_DIR),
            "documents": [{"id": doc.id, "title": doc.title, "kind": doc.kind, "source": doc.source} for doc in docs],
        },
        "ragflow": ragflow,
    }


def ragflow_retrieve(query: str, top_k: int) -> list[Doc]:
    base = os.getenv("RAGFLOW_BASE_URL", "").rstrip("/")
    api_key = os.getenv("RAGFLOW_API_KEY", "")
    dataset_ids = [x.strip() for x in os.getenv("RAGFLOW_DATASET_IDS", "").split(",") if x.strip()]
    if not base or not api_key or not dataset_ids:
        raise RuntimeError("RAGFlow is not configured")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    payload = {
        "question": query,
        "dataset_ids": dataset_ids,
        "page": 1,
        "page_size": top_k,
        "top_k": max(top_k, 10),
        "similarity_threshold": 0.15,
        "vector_similarity_weight": 0.75,
        "highlight": False,
    }
    data = _ragflow_json(base + "/retrieval", api_key, payload, method="POST")
    chunks = data.get("data", {}).get("chunks", [])
    catalog = ragflow_catalog()
    docs = []
    for i, chunk in enumerate(chunks[:top_k], 1):
        doc = _ragflow_doc_from_chunk(chunk, i, catalog)
        docs.append(doc)
    return enrich_evidence_metadata(docs, route="ragflow_vector")


@lru_cache(maxsize=1)
def ragflow_catalog() -> dict[str, Any]:
    base = os.getenv("RAGFLOW_BASE_URL", "").rstrip("/")
    api_key = os.getenv("RAGFLOW_API_KEY", "")
    dataset_ids = [x.strip() for x in os.getenv("RAGFLOW_DATASET_IDS", "").split(",") if x.strip()]
    if not base or not api_key or not dataset_ids:
        return {"datasets": [], "documents": {}}
    if not base.endswith("/api/v1"):
        base += "/api/v1"

    datasets_raw = _ragflow_json(base + "/datasets", api_key, method="GET")
    dataset_items = datasets_raw.get("data") or []
    dataset_by_id = {
        str(item.get("id")): item for item in dataset_items if str(item.get("id")) in set(dataset_ids)
    }
    document_by_id: dict[str, dict[str, Any]] = {}
    for dataset_id in dataset_ids:
        docs_raw = _ragflow_json(base + f"/datasets/{dataset_id}/documents", api_key, method="GET")
        for doc in docs_raw.get("data", {}).get("docs", []) or []:
            doc = dict(doc)
            dataset = dataset_by_id.get(dataset_id, {})
            doc["dataset_name"] = dataset.get("name") or dataset_id
            doc["dataset_language"] = dataset.get("language") or ""
            document_by_id[str(doc.get("id"))] = doc
    return {"datasets": list(dataset_by_id.values()), "documents": document_by_id}


def _ragflow_json(url: str, api_key: str, payload: dict[str, Any] | None = None, method: str = "GET") -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {api_key}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method, headers=headers)
    with urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


def _ragflow_doc_from_chunk(chunk: dict[str, Any], index: int, catalog: dict[str, Any]) -> Doc:
    documents = catalog.get("documents", {})
    document_id = str(chunk.get("document_id") or "")
    chunk_id = str(chunk.get("id") or chunk.get("chunk_id") or f"ragflow-{index}")
    meta = documents.get(document_id, {})
    dataset_id = str(chunk.get("dataset_id") or meta.get("dataset_id") or "")
    dataset_name = str(meta.get("dataset_name") or dataset_id or "ragflow")
    dataset_language = str(meta.get("dataset_language") or "")
    document_name = (
        str(chunk.get("document_name") or chunk.get("docnm_kwd") or meta.get("name") or meta.get("location") or "")
        .strip()
    )
    pages = _ragflow_pages(chunk.get("positions"))
    title = _ragflow_title(document_name, dataset_name, document_id, pages, index)
    content = _clean_ragflow_content(str(chunk.get("content") or chunk.get("text") or ""))
    source = _ragflow_source(dataset_name, document_name, pages)
    score = _as_float(chunk.get("similarity", chunk.get("score", 0.0)), default=0.0)
    return Doc(
        id=chunk_id,
        title=title,
        kind="ragflow_chunk",
        year=_field_year(document_name + " " + content),
        source=source,
        topics=[x for x in [dataset_language, dataset_name] if x],
        abstract=content[:1200],
        score=score,
        backend="ragflow",
        metadata={
            "chunk_id": chunk_id,
            "document_id": document_id,
            "document_name": document_name,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "dataset_language": dataset_language,
            "pages": pages,
            "positions": chunk.get("positions") or [],
            "similarity": score,
            "vector_similarity": _as_float(chunk.get("vector_similarity"), default=0.0),
            "term_similarity": _as_float(chunk.get("term_similarity"), default=0.0),
            "chunk_count": meta.get("chunk_count"),
            "run": meta.get("run"),
            "progress": meta.get("progress"),
        },
    )


def _ragflow_pages(positions: Any) -> list[int]:
    pages: list[int] = []
    if isinstance(positions, list):
        for pos in positions:
            if isinstance(pos, list) and pos:
                try:
                    page = int(pos[0])
                except (TypeError, ValueError):
                    continue
                if page not in pages:
                    pages.append(page)
    return pages[:6]


def _ragflow_title(document_name: str, dataset_name: str, document_id: str, pages: list[int], index: int) -> str:
    base = _clean_pdf_title(Path(document_name).stem) if document_name else ""
    if not base:
        base = f"{dataset_name} / chunk {document_id[:8] or index}"
    if pages:
        page_text = ",".join(str(page) for page in pages[:3])
        if len(pages) > 3:
            page_text += "..."
        return f"{base} · p.{page_text}"
    return base


def _ragflow_source(dataset_name: str, document_name: str, pages: list[int]) -> str:
    parts = [f"RAGFlow dataset={dataset_name}"]
    if document_name:
        parts.append(f"document={document_name}")
    if pages:
        parts.append("pages=" + ",".join(str(page) for page in pages))
    return "; ".join(parts)


def _clean_ragflow_content(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


DOMAIN_QUERY_ALIASES: tuple[tuple[str, ...], ...] = (
    ("sst", "sea surface temperature", "surface temperature", "\u6d77\u8868\u6e29\u5ea6", "\u6d77\u6e29"),
    ("sss", "sea surface salinity", "salinity", "\u76d0\u5ea6", "\u6d77\u8868\u76d0\u5ea6"),
    ("chlorophyll", "chlorophyll-a", "chl-a", "chla", "\u53f6\u7eff\u7d20"),
    ("swh", "significant wave height", "wave height", "wave", "\u6709\u6548\u6ce2\u9ad8", "\u6d77\u6d6a"),
    ("marine heatwave", "mhw", "\u6d77\u6d0b\u70ed\u6d6a"),
    ("coral bleaching", "coral reef", "\u73ca\u745a\u767d\u5316", "\u73ca\u745a\u7901"),
    ("fishery", "fisheries", "fishing", "\u6e14\u4e1a", "\u6e14\u573a"),
    ("sea level", "sea-level rise", "sla", "\u6d77\u5e73\u9762", "\u6d77\u5e73\u9762\u4e0a\u5347"),
    ("ocean acidification", "acidification", "ph", "\u6d77\u6d0b\u9178\u5316", "\u9178\u5316"),
    ("carbon", "bgc", "biogeochemical", "carbon cycle", "\u78b3\u5faa\u73af", "\u751f\u7269\u5730\u7403\u5316\u5b66"),
    ("enso", "el nino", "la nina", "\u5384\u5c14\u5c3c\u8bfa", "\u62c9\u5c3c\u5a1c"),
    ("upwelling", "coastal upwelling", "\u4e0a\u5347\u6d41", "\u8fd1\u5cb8\u4e0a\u5347\u6d41"),
)


def expand_retrieval_queries(intent: dict[str, Any], max_queries: int = 4) -> list[dict[str, Any]]:
    """Create deterministic domain query variants for bilingual ocean retrieval."""
    raw_queries = _as_text_list(intent.get("queries"))
    base_query = str(intent.get("retrieval_query") or "").strip()
    if base_query:
        raw_queries.insert(0, base_query)
    original_question = str(intent.get("original_question") or "").strip()
    if original_question:
        raw_queries.append(original_question)

    variants: list[dict[str, Any]] = []
    for query in raw_queries:
        _add_query_variant(variants, query, "original", [])

    context = " ".join(
        raw_queries
        + _as_text_list(intent.get("keywords"))
        + _as_text_list(intent.get("topics"))
    )
    matched_aliases = _matched_domain_aliases(context)
    if matched_aliases:
        alias_terms = list(dict.fromkeys(term for group in matched_aliases for term in group))
        _add_query_variant(
            variants,
            " ".join([base_query or original_question, *alias_terms[:8]]),
            "domain_aliases",
            alias_terms[:8],
        )
        english_terms = [term for term in alias_terms if term.isascii()]
        if english_terms:
            _add_query_variant(variants, " ".join(english_terms[:10]), "english_aliases", english_terms[:10])
        non_ascii_terms = [term for term in alias_terms if not term.isascii()]
        if non_ascii_terms:
            _add_query_variant(variants, " ".join(non_ascii_terms[:10]), "chinese_aliases", non_ascii_terms[:10])
    else:
        keyword_query = " ".join(_as_text_list(intent.get("keywords"))[:8])
        if keyword_query:
            _add_query_variant(variants, keyword_query, "keywords", [])

    return variants[:max(1, max_queries)]


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _add_query_variant(
    variants: list[dict[str, Any]],
    query: str,
    source: str,
    aliases: list[str],
) -> None:
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query:
        return
    key = query.lower()
    if any(item["query"].lower() == key for item in variants):
        return
    variants.append({"query": query[:500], "source": source, "aliases": aliases})


def _matched_domain_aliases(text: str) -> list[tuple[str, ...]]:
    lower = str(text or "").lower()
    tokens = tokenize(lower)
    matched: list[tuple[str, ...]] = []
    for group in DOMAIN_QUERY_ALIASES:
        for alias in group:
            alias_lower = alias.lower()
            alias_tokens = tokenize(alias_lower)
            if alias_lower in lower or (alias_tokens and alias_tokens <= tokens):
                matched.append(group)
                break
    return matched


def retrieve(intent: dict[str, Any], top_k: int, backend: str) -> tuple[list[Doc], str]:
    docs, used, _info = retrieve_with_info(intent, top_k, backend)
    return docs, used


def retrieve_with_info(intent: dict[str, Any], top_k: int, backend: str) -> tuple[list[Doc], str, dict[str, Any]]:
    recall_k = max(top_k, min(30, top_k * 3))
    variants = expand_retrieval_queries(intent)
    merged: dict[str, Doc] = {}
    routes: list[dict[str, Any]] = []
    backend_used = "local"

    def add_docs(docs: list[Doc], used: str, variant: dict[str, Any], route: str) -> None:
        for rank, doc in enumerate(docs, 1):
            meta = dict(doc.metadata or {})
            route_hit = {
                "query": variant["query"],
                "query_variant": variant["source"],
                "route": route,
                "backend": used,
                "rank": rank,
                "score": round(float(doc.score or 0.0), 4),
            }
            meta["retrieval_query"] = variant["query"]
            meta["query_variant"] = variant["source"]
            meta["route"] = route
            meta["routes"] = [*list(meta.get("routes") or []), route_hit]
            doc.metadata = meta
            key = doc.id or doc.title
            if key not in merged:
                merged[key] = doc
                continue
            existing = merged[key]
            existing_meta = dict(existing.metadata or {})
            combined_routes = [*list(existing_meta.get("routes") or []), route_hit]
            if doc.score > existing.score:
                meta["routes"] = combined_routes
                doc.metadata = meta
                merged[key] = doc
            else:
                existing_meta["routes"] = combined_routes
                existing.metadata = existing_meta

    if backend in {"auto", "ragflow"}:
        try:
            backend_used = "ragflow"
            for variant in variants:
                docs = ragflow_retrieve(variant["query"], recall_k)
                routes.append({
                    "query": variant["query"],
                    "query_variant": variant["source"],
                    "route": "ragflow_vector",
                    "backend": "ragflow",
                    "count": len(docs),
                })
                add_docs(docs, "ragflow", variant, "ragflow_vector")
        except Exception as exc:
            routes.append({
                "query": variants[0]["query"] if variants else str(intent.get("retrieval_query") or ""),
                "route": "ragflow_vector",
                "backend": "ragflow",
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}",
            })
            if backend == "ragflow":
                raise

    if not merged:
        for variant in variants:
            try:
                docs = local_retrieve(variant["query"], recall_k)
                routes.append({
                    "query": variant["query"],
                    "query_variant": variant["source"],
                    "route": "local_keyword",
                    "backend": "local",
                    "count": len(docs),
                })
                add_docs(docs, "local", variant, "local_keyword")
            except Exception as exc:
                routes.append({
                    "query": variant["query"],
                    "query_variant": variant["source"],
                    "route": "local_keyword",
                    "backend": "local",
                    "count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        backend_used = "local"

    candidates = list(merged.values())
    reranked = rerank_evidence_docs(intent, candidates, top_k)
    info = {
        "queries": [variant["query"] for variant in variants],
        "query_variants": variants,
        "routes": routes,
        "fusion": {
            "method": "best_score_then_heuristic_rerank",
            "input_count": len(candidates),
            "output_count": len(reranked),
        },
    }
    return reranked, backend_used, info


def rerank_evidence_docs(intent: dict[str, Any], docs: list[Doc], top_k: int) -> list[Doc]:
    """Deterministic reranker used until an external rerank model is configured."""
    if not docs:
        return []
    query_text = " ".join([
        str(intent.get("retrieval_query") or ""),
        " ".join(str(x) for x in intent.get("keywords", []) or []),
        str(intent.get("original_question") or ""),
    ])
    q = {t for t in tokenize(query_text) if len(t) >= 2}
    scored: list[tuple[float, int, Doc, str]] = []
    for rank_before, doc in enumerate(docs, 1):
        text = " ".join([doc.title, " ".join(doc.topics), doc.abstract])
        d_tokens = {t for t in tokenize(text) if len(t) >= 2}
        overlap = q & d_tokens
        semantic = len(overlap) / max(4.0, math.sqrt(max(1, len(q))) * 4)
        metadata_bonus = _rerank_metadata_bonus(doc)
        rerank_score = round(
            0.62 * min(1.0, max(0.0, doc.score))
            + 0.30 * min(1.0, semantic)
            + 0.08 * metadata_bonus,
            4,
        )
        reason = _rerank_reason(doc, overlap, metadata_bonus)
        meta = dict(doc.metadata or {})
        meta["rank_before"] = rank_before
        meta["rerank_score"] = rerank_score
        meta["rerank_reason"] = reason
        doc.metadata = meta
        scored.append((rerank_score, -rank_before, doc, reason))

    reranked = [item[2] for item in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)]
    selected = reranked[:top_k]
    route = str((selected[0].metadata or {}).get("route") or selected[0].backend)
    for rank_after, doc in enumerate(selected, 1):
        meta = dict(doc.metadata or {})
        meta["rank_after"] = rank_after
        doc.metadata = meta
    return enrich_evidence_metadata(selected, route=route)


def _rerank_metadata_bonus(doc: Doc) -> float:
    meta = doc.metadata or {}
    content_type = str(meta.get("content_type") or _infer_content_type(doc))
    bonus = 0.0
    if meta.get("pages"):
        bonus += 0.25
    if content_type in {"paragraph", "pdf_text", "markdown_section"}:
        bonus += 0.25
    if doc.year:
        bonus += 0.15
    if doc.source:
        bonus += 0.10
    return min(1.0, bonus)


def _rerank_reason(doc: Doc, overlap: set[str], metadata_bonus: float) -> str:
    parts = []
    if overlap:
        preview = ", ".join(sorted(overlap, key=len, reverse=True)[:5])
        parts.append(f"term_overlap={preview}")
    else:
        parts.append("term_overlap=none")
    if metadata_bonus:
        parts.append(f"metadata_bonus={metadata_bonus:.2f}")
    parts.append(f"base_similarity={doc.score:.4f}")
    return "; ".join(parts)


def screen(intent: dict[str, Any], docs: list[Doc], threshold: float) -> tuple[list[Doc], list[Doc], list[dict[str, Any]]]:
    q = {t for t in tokenize(intent["retrieval_query"]) if len(t) >= 2}
    kept, passed, decisions = [], [], []
    requested = " ".join(intent["keywords"]) + " " + intent["original_question"]
    for doc in docs:
        d = {t for t in tokenize(doc.title + " " + " ".join(doc.topics) + " " + doc.abstract) if len(t) >= 2}
        semantic = len(q & d) / max(4, math.sqrt(max(1, len(q))) * 4)
        penalty = topic_penalty(requested, doc)
        doc.decision_score = round((0.68 * doc.score + 0.32 * min(1.0, semantic)) * penalty, 4)
        target = kept if doc.decision_score >= threshold else passed
        target.append(doc)
        decisions.append({"document_id": doc.id, "decision": "keep" if target is kept else "pass", "score": doc.decision_score})
    return kept, passed, decisions


def topic_penalty(requested: str, doc: Doc) -> float:
    text = (doc.title + " " + " ".join(doc.topics) + " " + doc.abstract).lower()
    groups = [["海洋酸化", "酸化"], ["海洋热浪", "热浪"], ["海平面"]]
    wanted = [g for g in groups if any(x in requested for x in g)]
    if not wanted:
        return 1.0
    return 1.0 if any(any(x.lower() in text for x in g) for g in wanted) else 0.55


def report(intent: dict[str, Any], kept: list[Doc], passed: list[Doc], backend: str, extra_context: str = "") -> str:
    lines = [
        "# 海洋领域 RAG 多 Agent 分析报告",
        "",
        "## 问题凝练",
        intent["original_question"],
        "",
        f"检索后端：{backend}。关键词：{', '.join(intent['keywords']) or '未显式命中'}。",
        "",
        "## 核心结论",
    ]
    all_text = " ".join(d.abstract for d in kept)
    if "酸化" in all_text:
        lines.append("- 海洋酸化会削弱钙化生物壳体/骨骼形成能力，贝类养殖与珊瑚生态是重点风险对象。")
    if "热浪" in all_text:
        lines.append("- 海洋热浪会放大珊瑚白化、物种迁移、食物网扰动和渔业波动风险。")
    if not kept:
        lines.append("- 当前知识库未检索到足够相关材料，需要补充文档或降低阈值。")
    lines += ["", "## 证据筛选", "| 文档 | 类型 | 年份 | 分数 |", "|---|---|---|---|"]
    lines += [f"| {d.title} | {d.kind} | {d.year or '-'} | {d.decision_score:.3f} |" for d in kept]
    lines += ["", "## 规划建议"]
    lines += [
        "1. 建立主题化知识库：海洋酸化、海洋热浪、海平面、渔业治理分别建库或打标签。",
        "2. IntentAgent 先凝练问题，再交给 RetrievalAgent 检索 RAGFlow 或本地知识库。",
        "3. ScreeningAgent 阅读摘要和关键片段，保留高相关证据，过滤低相关文档。",
        "4. ReportAgent 输出结论、证据表、风险判断、监测指标和治理路径，并保留引用来源。",
    ]
    lines += ["", "## 被过滤文档"]
    lines += [f"- {d.title} ({d.decision_score:.3f})" for d in passed] or ["- 无"]
    lines += ["", "## 参考来源"]
    lines += [f"- {d.title}: {d.source}" for d in kept]
    if extra_context:
        lines += ["", "## 区域数值与风险假设", extra_context]
    return "\n".join(lines)


def llm_report(
    intent: dict[str, Any],
    kept: list[Doc],
    passed: list[Doc],
    backend: str,
    feedback: str | None = None,
    extra_context: str = "",
) -> str:
    if not deepseek_client.configured():
        return report(intent, kept, passed, backend, extra_context=extra_context)
    context = "\n\n".join(
        f"[{i}] 标题：{doc.title}\n类型：{doc.kind}\n年份：{doc.year or '-'}\n来源：{doc.source}\n相关分：{doc.decision_score}\n摘要：{doc.abstract}"
        for i, doc in enumerate(kept[:8], 1)
    )
    if not context:
        context = "当前没有通过相关性筛选的文档。"
    user_content = (
        f"原始问题：{intent['original_question']}\n"
        f"凝练检索词：{intent.get('retrieval_query', '')}\n"
        f"检索后端：{backend}\n\n"
        f"通过筛选的证据：\n{context}\n\n"
        f"被过滤文档：{', '.join(d.title for d in passed) or '无'}\n\n"
        "请生成一份海洋相关中文报告，保留文献/报告来源线索。"
    )
    if extra_context:
        # 区域数值上下文 + DomainReasoningAgent 的风险假设。
        user_content += "\n\n" + extra_context
    if feedback:
        # CriticAgent 把上一版报告打回时，带着具体修改意见重写。
        user_content += (
            "\n\n这是对你上一版报告的审稿意见，请据此修订后重新输出完整报告：\n"
            f"{feedback}"
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是海洋科学与海洋治理方向的中文研究助手。"
                "必须基于给定证据回答，不能编造文献；证据不足时直接说明。"
                "输出结构包含：问题凝练、证据判断、核心结论、风险与不确定性、规划建议、参考来源。"
            ),
        },
        {"role": "user", "content": user_content},
    ]
    try:
        return deepseek_client.chat(messages)
    except Exception as exc:
        fallback = report(intent, kept, passed, backend)
        return fallback + f"\n\n## LLM 调用状态\n- DeepSeek 调用失败，已回退本地模板：{type(exc).__name__}: {exc}"


def run_pipeline(question: str, top_k: int = 6, threshold: float = 0.22, backend: str = "auto", trace: bool = False, region: dict[str, Any] | None = None, variables: list[str] | None = None) -> dict[str, Any]:
    """Public entry point. Delegates to the multi-agent Orchestrator.

    Falls back to the legacy linear pipeline if the agent layer cannot be
    imported, so the API keeps working even if the agents module is broken.
    """
    try:
        from ocean_agents_demo.agents import Orchestrator
    except Exception:
        return _linear_pipeline(question, top_k, threshold, backend, trace)
    return Orchestrator().run(
        question=question, top_k=top_k, threshold=threshold, backend=backend, trace=trace,
        region=region, variables=variables,
    )


def _linear_pipeline(question: str, top_k: int = 6, threshold: float = 0.22, backend: str = "auto", trace: bool = False) -> dict[str, Any]:
    """Legacy single-pass pipeline (no LLM-driven agents, no critic loop)."""
    started = time.time()
    events: list[dict[str, Any]] = []
    intent = condense_intent(question)
    events.append({"agent": "IntentAgent", "output": intent})
    docs, used_backend = retrieve(intent, top_k, backend)
    events.append({"agent": "RetrievalAgent", "backend": used_backend, "candidates": [d.title for d in docs]})
    kept, passed, decisions = screen(intent, docs, threshold)
    events.append({"agent": "ScreeningAgent", "decisions": decisions})
    final_report = llm_report(intent, kept, passed, used_backend)
    events.append({"agent": "ReportAgent", "elapsed_ms": round((time.time() - started) * 1000, 2)})
    result = {
        "question": question,
        "report": final_report,
        "llm": deepseek_client.status(),
        "kept_documents": [doc_to_evidence_dict(d) for d in kept],
        "passed_documents": [doc_to_evidence_dict(d) for d in passed],
    }
    if trace:
        result["trace"] = events
    return result
