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
