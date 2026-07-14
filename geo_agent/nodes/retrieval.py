"""RetrievalNode — RAG 文献检索。

改进：
- 使用 LangChain tool calling（bind_tools）
- 按 domain 选择对应检索词
- 保持旧的多 query 融合降级路径
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState

log = logging.getLogger("geo_agent.nodes.retrieval")


def run(state: GeoAgentState) -> dict[str, Any]:
    intent = state.get("intent", {})
    question = state.get("question", "")
    top_k = state.get("top_k", 6)
    backend = state.get("backend", "auto")
    domain = state.get("domain", "general")
    trace = list(state.get("trace", []))
    image_ref = str(state.get("image_ref") or "").strip()

    if image_ref:
        candidates, used, info, image_candidates = _multimodal_fused_retrieve(
            intent=intent,
            top_k=top_k,
            backend=backend,
            image_ref=image_ref,
        )
        trace.append({
            "node": "RetrievalNode",
            "mode": "multimodal-fusion",
            "count": len(candidates),
            "backend": used,
            "image_count": len(image_candidates),
            "text_count": info.get("text_count", 0),
            "routes": info.get("routes", []),
            "fusion": info.get("fusion", {}),
            "diagnostics": info.get("diagnostics", {}),
            "candidate_preview": info.get("candidate_preview", {}),
            "multimodal_error": info.get("multimodal_error"),
        })
        return {
            "candidates": candidates,
            "multimodal_candidates": image_candidates,
            "retrieval_routes": info.get("routes", []),
            "backend_used": used,
            "trace": trace,
        }

    if llm.configured():
        try:
            docs, used, info = _tool_loop_retrieve(intent, question, top_k, backend, domain)
            if docs:
                trace.append({"node": "RetrievalNode", "mode": "tool-loop",
                               "count": len(docs), "backend": used,
                               "queries": info.get("queries", []),
                               "tool_calls": info.get("tool_calls", [])})
                return {
                    "candidates": [_doc_to_dict(d) for d in docs],
                    "backend_used": used,
                    "trace": trace,
                }
        except Exception as exc:
            log.warning("RetrievalNode tool loop failed: %s", exc)

    # 多 query 融合降级
    docs, used, info = _deterministic_retrieve(intent, top_k, backend)
    trace.append({"node": "RetrievalNode", "mode": "multi-query",
                  "count": len(docs), "backend": used,
                  "queries": info.get("queries", []),
                  "routes": info.get("routes", []),
                  "fusion": info.get("fusion", {})})
    return {
        "candidates": [_doc_to_dict(d) for d in docs],
        "backend_used": used,
        "trace": trace,
    }


def _tool_loop_retrieve(
    intent: dict[str, Any],
    question: str,
    top_k: int,
    backend: str,
    domain: str,
) -> tuple[list[Any], str, dict[str, Any]]:
    from ocean_agents_demo import core

    collected: dict[str, Any] = {}
    queries_used: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    backend_ref = {"v": "local"}

    def dispatch(name: str, args: dict[str, Any]) -> Any:
        started = time.time()
        call = {
            "schema_version": "tool_call.v1",
            "tool_name": name,
            "args_summary": _safe_args(args),
            "success": False,
            "latency_ms": None,
            "result_size": 0,
        }
        if name == "retrieve_documents":
            try:
                q = str(args.get("query") or question)
                queries_used.append(q)
                tk = int(args.get("top_k") or top_k)
                bk = backend if backend in {"ragflow", "local"} else str(args.get("backend") or backend)
                docs, used = core.retrieve(
                    {"retrieval_query": q, "keywords": [], "original_question": question},
                    tk, bk,
                )
                backend_ref["v"] = used
                for d in docs:
                    key = d.id or d.title
                    if key not in collected or d.score > collected[key].score:
                        collected[key] = d
                result = {
                    "backend": used,
                    "count": len(docs),
                    "results": [{"id": d.id, "title": d.title, "score": round(d.score, 4)}
                                for d in docs[:3]],
                }
                call["success"] = True
                call["result_size"] = len(docs)
                return result
            except Exception as exc:
                call["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                call["latency_ms"] = round((time.time() - started) * 1000, 2)
                tool_calls.append(call)
        raise KeyError(f"unknown tool: {name}")

    retrieve_spec = [{
        "name": "retrieve_documents",
        "description": "按 query 检索海洋文献/报告（RAGFlow 向量检索或本地检索）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，可中英文"},
                "top_k": {"type": "integer", "default": top_k},
                "backend": {"type": "string", "enum": ["auto", "local", "ragflow"], "default": "auto"},
            },
            "required": ["query"],
        },
    }]

    topics = "、".join(intent.get("topics", [])) or domain
    goal = (
        f"用户问题：{question}\n"
        f"领域：{domain}，主题：{topics}\n"
        "请用 retrieve_documents 检索 1~3 条不同角度的 query（中英双语）来收集证据，足够后停止。"
    )
    llm.run_tool_loop(
        goal, retrieve_spec, dispatch,
        system="你是海洋领域检索助手，自主选择 query 收集文献证据。",
        max_steps=5,
    )
    candidates = sorted(collected.values(), key=lambda d: d.score, reverse=True)[:top_k]
    return candidates, backend_ref["v"], {"queries": queries_used, "tool_calls": tool_calls}


def _multimodal_fused_retrieve(
    intent: dict[str, Any],
    top_k: int,
    backend: str,
    image_ref: str,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[dict[str, Any]]]:
    """Retrieve image and text evidence in parallel, then fuse by reciprocal rank."""
    from concurrent.futures import ThreadPoolExecutor
    from geo_agent import multimodal_client

    recall_k = max(top_k, min(30, top_k * 3))
    text_docs: list[Any] = []
    text_used = "local"
    text_info: dict[str, Any] = {}
    image_payload: dict[str, Any] = {}
    multimodal_error = ""

    with ThreadPoolExecutor(max_workers=2) as pool:
        text_future = pool.submit(_deterministic_retrieve, intent, recall_k, backend)
        image_future = pool.submit(multimodal_client.search_by_ref, image_ref, recall_k)
        try:
            text_docs, text_used, text_info = text_future.result()
        except Exception as exc:
            log.warning("text retrieval during multimodal query failed: %s", exc)
            text_info = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            image_payload = image_future.result()
        except Exception as exc:
            multimodal_error = f"{type(exc).__name__}: {exc}"
            log.warning("multimodal retrieval degraded to text-only: %s", exc)

    text_candidates = [_doc_to_dict(doc) for doc in text_docs]
    image_candidates = [
        _normalize_multimodal_hit(item, image_payload)
        for item in (image_payload.get("results") or [])
        if isinstance(item, dict)
    ]
    original_counts = {"text": len(text_candidates), "image": len(image_candidates)}
    text_candidates = _limit_per_document(text_candidates, max_per_doc=3)
    image_candidates = _limit_per_document(image_candidates, max_per_doc=3)
    diagnostics = dict(image_payload.get("diagnostics") or {})
    route_weights = {"text": 1.0, "image": _relative_image_weight(diagnostics)}
    fused_pool = _rrf_fuse(
        text_candidates,
        image_candidates,
        min(100, recall_k * 3),
        route_weights=route_weights,
    )
    fused = _limit_per_document(fused_pool, max_per_doc=3)[:recall_k]
    routes = [
        {
            "route": "text_retrieval",
            "backend": text_used,
            "count": len(text_candidates),
            "original_count": original_counts["text"],
            "weight": route_weights["text"],
            "queries": text_info.get("queries", []),
        },
        {
            "route": "chinese_clip_image_to_text",
            "backend": "multimodal-service",
            "count": len(image_candidates),
            "original_count": original_counts["image"],
            "weight": route_weights["image"],
            "model": (image_payload.get("model") or {}).get("model"),
            "index_version": (image_payload.get("index") or {}).get("index_version"),
            "error": multimodal_error or None,
        },
    ]
    used = f"{text_used}+multimodal" if image_candidates else text_used
    return fused, used, {
        "routes": routes,
        "text_count": len(text_candidates),
        "fusion": {
            "method": "confidence_aware_weighted_rrf",
            "rrf_k": 60,
            "weights": route_weights,
            "per_document_cap": 3,
            "absolute_threshold_enabled": False,
        },
        "diagnostics": {
            "image_scores": diagnostics,
            "image_search_ms": image_payload.get("search_ms"),
            "calibration": "unvalidated",
            "message": "图片分数仅用于相对排序；尚未使用标注集校准绝对相关阈值。",
        },
        "candidate_preview": {
            "text": _candidate_preview(text_candidates),
            "image": _candidate_preview(image_candidates),
            "fused": _candidate_preview(fused),
        },
        "multimodal_error": multimodal_error or None,
    }, image_candidates


def _normalize_multimodal_hit(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    doc_id = str(item.get("doc_id") or item.get("evidence_id") or item.get("vector_id"))
    chunk_id = str(item.get("chunk_id") or item.get("evidence_id") or doc_id)
    content = str(item.get("content") or item.get("abstract") or item.get("retrieval_card") or "")
    clip_score = float(item.get("clip_score") or item.get("score") or 0.0)
    pages = item.get("pages") or ([item.get("page")] if item.get("page") else [])
    route = {
        "route": "chinese_clip_image_to_text",
        "backend": "multimodal-service",
        "rank": int(item.get("rank") or 0),
        "score": round(clip_score, 6),
    }
    evidence = {
        "schema_version": "evidence_chunk.v1",
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "document_name": str(item.get("document_name") or item.get("title") or doc_id),
        "title": str(item.get("title") or doc_id),
        "source": str(item.get("source") or ""),
        "source_path": str(item.get("source_path") or item.get("source") or ""),
        "page": item.get("page"),
        "pages": pages,
        "content_type": str(item.get("content_type") or "multimodal_text"),
        "backend": "multimodal-service",
        "route": route["route"],
        "similarity": clip_score,
        "vector_similarity": clip_score,
        "routes": [route],
    }
    metadata = dict(item.get("metadata") or {})
    metadata.update(evidence)
    metadata.update({
        "clip_score": clip_score,
        "model": (payload.get("model") or {}).get("model"),
        "index_version": (payload.get("index") or {}).get("index_version"),
        "evidence": evidence,
    })
    return {
        "id": chunk_id,
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "title": str(item.get("title") or doc_id),
        "kind": "multimodal_text_hit",
        "year": int(item.get("year") or 0),
        "source": str(item.get("source") or ""),
        "topics": list(item.get("topics") or []),
        "abstract": content,
        "score": clip_score,
        "backend": "multimodal-service",
        "reason": "Chinese-CLIP image-to-text recall",
        "page": item.get("page"),
        "pages": pages,
        "route": route["route"],
        "clip_score": clip_score,
        "metadata": metadata,
        "evidence": evidence,
    }


def _rrf_fuse(
    text_candidates: list[dict[str, Any]],
    image_candidates: list[dict[str, Any]],
    limit: int,
    route_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    weights = route_weights or {"text": 1.0, "image": 1.15}
    routes = [
        ("text", text_candidates, float(weights.get("text", 1.0))),
        ("image", image_candidates, float(weights.get("image", 1.0))),
    ]
    active_weight = sum(weight for _name, docs, weight in routes if docs)
    if active_weight <= 0:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for route_name, docs, weight in routes:
        for rank, source_doc in enumerate(docs, 1):
            doc = dict(source_doc)
            key = _fusion_key(doc, fallback=f"{route_name}-{rank}")
            entry = merged.setdefault(key, {"doc": doc, "rrf": 0.0, "route_ranks": {}})
            if route_name == "text" or not entry["doc"].get("abstract"):
                preserved = entry["doc"]
                entry["doc"] = doc
                if preserved.get("clip_score") is not None:
                    entry["doc"]["clip_score"] = preserved["clip_score"]
            elif doc.get("clip_score") is not None:
                entry["doc"]["clip_score"] = doc["clip_score"]
            entry["rrf"] += weight / (60.0 + rank)
            entry["route_ranks"][route_name] = rank

    max_rrf = active_weight / 61.0
    ranked: list[dict[str, Any]] = []
    for entry in merged.values():
        doc = dict(entry["doc"])
        score = min(1.0, entry["rrf"] / max_rrf)
        metadata = dict(doc.get("metadata") or {})
        metadata["fusion"] = {
            "method": "weighted_rrf",
            "route_ranks": entry["route_ranks"],
            "route_weights": {name: weight for name, _docs, weight in routes},
            "raw_score": round(entry["rrf"], 8),
        }
        doc["metadata"] = metadata
        doc["score"] = round(score, 6)
        doc["fusion_score"] = round(score, 6)
        doc["retrieval_routes"] = sorted(entry["route_ranks"])
        ranked.append(doc)
    return sorted(ranked, key=lambda item: item.get("score", 0.0), reverse=True)[:limit]


def _relative_image_weight(diagnostics: dict[str, Any]) -> float:
    """Scale image influence from score separation without claiming calibrated relevance."""
    top = float(diagnostics.get("top_score") or 0.0)
    median_value = diagnostics.get("median_score")
    median = float(top if median_value is None else median_value)
    std = max(0.0, float(diagnostics.get("std_score") or 0.0))
    separation = max(0.0, top - median)
    relative_signal = separation / (separation + 2.0 * std + 1e-8)
    return round(0.9 + 0.3 * min(1.0, relative_signal), 4)


def _limit_per_document(candidates: list[dict[str, Any]], max_per_doc: int) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for item in candidates:
        key = _document_key(item)
        if counts.get(key, 0) >= max_per_doc:
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(item)
    return kept


def _fusion_key(item: dict[str, Any], fallback: str) -> str:
    """Match the same paper page across RAGFlow and local-Faiss identifier schemes."""
    document = _canonical_document_title(item)
    page = _primary_page(item)
    if document and page is not None:
        return f"document-page:{document}:{page}"
    return str(item.get("chunk_id") or item.get("id") or item.get("doc_id") or fallback)


def _document_key(item: dict[str, Any]) -> str:
    document = _canonical_document_title(item)
    if document:
        return f"document:{document}"
    return str(item.get("doc_id") or item.get("source") or item.get("id") or "unknown")


def _canonical_document_title(item: dict[str, Any]) -> str:
    metadata = dict(item.get("metadata") or {})
    evidence = dict(item.get("evidence") or metadata.get("evidence") or {})
    title = str(
        item.get("title")
        or item.get("document_name")
        or evidence.get("document_name")
        or metadata.get("document_name")
        or ""
    )
    title = re.sub(r"\.(?:pdf|docx?|txt|md)$", "", title.strip(), flags=re.I)
    title = re.sub(r"\s*(?:[·/|\-]\s*)?(?:p(?:ages?)?\.?|page|页)\s*[0-9０-９,，、.．…\-–—]+.*$", "", title, flags=re.I)
    return "".join(re.findall(r"[a-z0-9\u3400-\u9fff]+", title.lower()))


def _primary_page(item: dict[str, Any]) -> int | None:
    values = [item.get("page")]
    values.extend(item.get("pages") or [])
    metadata = dict(item.get("metadata") or {})
    values.extend([metadata.get("page")])
    values.extend(metadata.get("pages") or [])
    for value in values:
        try:
            if value is not None and str(value).strip():
                return int(float(value))
        except (TypeError, ValueError):
            continue
    title = str(item.get("title") or "")
    match = re.search(r"(?:p(?:ages?)?\.?|page|页)\s*([0-9０-９]+)", title, flags=re.I)
    if match:
        try:
            return int(match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        except ValueError:
            return None
    return None


def _candidate_preview(candidates: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for rank, item in enumerate(candidates[:limit], 1):
        fusion = dict((item.get("metadata") or {}).get("fusion") or {})
        preview.append({
            "rank": rank,
            "chunk_id": item.get("chunk_id") or item.get("id"),
            "doc_id": item.get("doc_id"),
            "title": item.get("title"),
            "page": item.get("page"),
            "score": round(float(item.get("score") or 0.0), 6),
            "clip_score": item.get("clip_score"),
            "fusion_score": item.get("fusion_score"),
            "route_ranks": fusion.get("route_ranks") or {},
            "routes": item.get("retrieval_routes") or [],
        })
    return preview


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args or {})
    for key in list(out):
        if any(secret in key.lower() for secret in ("key", "token", "secret", "password")):
            out[key] = "***"
    if "query" in out:
        out["query"] = str(out["query"])[:240]
    return out


def _deterministic_retrieve(
    intent: dict[str, Any],
    top_k: int,
    backend: str,
) -> tuple[list[Any], str, dict[str, Any]]:
    from ocean_agents_demo import core

    try:
        docs, used, info = core.retrieve_with_info(intent, top_k, backend)
        return docs, used, info
    except Exception as exc:
        query = intent.get("retrieval_query", "")
        log.warning("deterministic retrieve query=%s failed: %s", query, exc)
        return [], "local", {"queries": [query], "routes": [], "fusion": {"method": "failed"}}


def _doc_to_dict(doc: Any) -> dict[str, Any]:
    from dataclasses import asdict
    from ocean_agents_demo.core import Doc, doc_to_evidence_dict
    if isinstance(doc, Doc):
        return doc_to_evidence_dict(doc)
    try:
        return asdict(doc)
    except Exception:
        return doc.__dict__ if hasattr(doc, "__dict__") else dict(doc)
