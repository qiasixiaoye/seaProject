"""RetrievalNode — RAG 文献检索。

改进：
- 使用 LangChain tool calling（bind_tools）
- 按 domain 选择对应检索词
- 保持旧的多 query 融合降级路径
"""
from __future__ import annotations

import logging
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
    fused = _rrf_fuse(text_candidates, image_candidates, recall_k)
    routes = [
        {
            "route": "text_retrieval",
            "backend": text_used,
            "count": len(text_candidates),
            "queries": text_info.get("queries", []),
        },
        {
            "route": "chinese_clip_image_to_text",
            "backend": "multimodal-service",
            "count": len(image_candidates),
            "model": (image_payload.get("model") or {}).get("model"),
            "index_version": (image_payload.get("index") or {}).get("index_version"),
            "error": multimodal_error or None,
        },
    ]
    used = f"{text_used}+multimodal" if image_candidates else text_used
    return fused, used, {
        "routes": routes,
        "text_count": len(text_candidates),
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
) -> list[dict[str, Any]]:
    routes = [("text", text_candidates, 1.0), ("image", image_candidates, 1.15)]
    active_weight = sum(weight for _name, docs, weight in routes if docs)
    if active_weight <= 0:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for route_name, docs, weight in routes:
        for rank, source_doc in enumerate(docs, 1):
            doc = dict(source_doc)
            key = str(doc.get("chunk_id") or doc.get("id") or doc.get("doc_id") or f"{route_name}-{rank}")
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
            "raw_score": round(entry["rrf"], 8),
        }
        doc["metadata"] = metadata
        doc["score"] = round(score, 6)
        doc["fusion_score"] = round(score, 6)
        doc["retrieval_routes"] = sorted(entry["route_ranks"])
        ranked.append(doc)
    return sorted(ranked, key=lambda item: item.get("score", 0.0), reverse=True)[:limit]


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
