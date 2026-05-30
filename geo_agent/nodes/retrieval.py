"""RetrievalNode — RAG 文献检索。

改进：
- 使用 LangChain tool calling（bind_tools）
- 按 domain 选择对应检索词
- 保持旧的多 query 融合降级路径
"""
from __future__ import annotations

import logging
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
                               "queries": info.get("queries", [])})
                return {
                    "candidates": [_doc_to_dict(d) for d in docs],
                    "backend_used": used,
                    "trace": trace,
                }
        except Exception as exc:
            log.warning("RetrievalNode tool loop failed: %s", exc)

    # 多 query 融合降级
    docs, used = _deterministic_retrieve(intent, top_k, backend)
    trace.append({"node": "RetrievalNode", "mode": "multi-query",
                  "count": len(docs), "backend": used})
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
    backend_ref = {"v": "local"}

    def dispatch(name: str, args: dict[str, Any]) -> Any:
        if name == "retrieve_documents":
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
            return {
                "backend": used,
                "count": len(docs),
                "results": [{"id": d.id, "title": d.title, "score": round(d.score, 4)}
                            for d in docs[:3]],
            }
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
    return candidates, backend_ref["v"], {"queries": queries_used}


def _deterministic_retrieve(
    intent: dict[str, Any],
    top_k: int,
    backend: str,
) -> tuple[list[Any], str]:
    from ocean_agents_demo import core

    queries = intent.get("queries") or [intent.get("retrieval_query", "")]
    merged: dict[str, Any] = {}
    used = "local"
    for query in queries:
        sub = dict(intent, retrieval_query=query)
        try:
            docs, used = core.retrieve(sub, top_k, backend)
            for d in docs:
                key = d.id or d.title
                if key not in merged or d.score > merged[key].score:
                    merged[key] = d
        except Exception as exc:
            log.warning("deterministic retrieve query=%s failed: %s", query, exc)
    candidates = sorted(merged.values(), key=lambda d: d.score, reverse=True)[:top_k]
    return candidates, used


def _doc_to_dict(doc: Any) -> dict[str, Any]:
    from dataclasses import asdict
    try:
        return asdict(doc)
    except Exception:
        return doc.__dict__ if hasattr(doc, "__dict__") else dict(doc)
