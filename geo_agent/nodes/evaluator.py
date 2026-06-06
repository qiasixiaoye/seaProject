"""EvaluatorAgent - deterministic quality metrics for a finished run."""
from __future__ import annotations

from typing import Any

from geo_agent.state import GeoAgentState


def run(state: GeoAgentState) -> dict[str, Any]:
    report = state.get("report", "") or ""
    candidates = state.get("candidates", []) or []
    kept = state.get("kept_docs", []) or []
    passed = state.get("passed_docs", []) or []
    data_context = state.get("data_context", {}) or {}
    trace = list(state.get("trace", []))
    critic = state.get("critic_result", {}) or {}

    citation_coverage = _citation_coverage(report, kept)
    data_grounding = _data_grounding(report, data_context)
    critic_passed = 1.0 if critic.get("passed", True) else 0.0
    trace_completeness = _trace_completeness(trace)
    retrieval_signal = _retrieval_signal(candidates, kept)
    tool_metrics = _tool_metrics(trace)

    metrics = {
        "retrieval": {
            "backend": state.get("backend_used", state.get("backend", "local")),
            "candidate_count": len(candidates),
            "kept_count": len(kept),
            "passed_count": len(passed),
            "avg_candidate_score": _avg_score(candidates, "score"),
            "avg_kept_score": _avg_score(kept, "decision_score", fallback_key="score"),
            "top_score": _top_score(candidates),
            "avg_rerank_score": _avg_score(candidates, "rerank_score"),
            "top_rerank_score": _top_score_by_key(candidates, "rerank_score"),
            "recall_at_k": None,
            "precision_at_k": None,
            "mrr": None,
            "ndcg_at_k": None,
        },
        "evidence": {
            "citation_coverage": citation_coverage,
            "citation_accuracy": None,
            "evidence_count": len(kept),
        },
        "answer": {
            "faithfulness": None,
            "completeness": None,
            "hallucination_rate": None,
            "data_grounding": data_grounding,
            "critic_passed": critic_passed,
            "critic_issue_count": len(critic.get("issues", []) or []),
        },
        "tools": tool_metrics,
        "trace": {
            "trace_completeness": trace_completeness,
            "node_count": len(trace),
        },
        "system": {
            "elapsed_ms": None,
            "token_usage": None,
        },
        # Backward-compatible scalar keys for older consumers.
        "citation_coverage": citation_coverage,
        "data_grounding": data_grounding,
        "critic_passed": critic_passed,
        "trace_completeness": trace_completeness,
    }

    components = [
        citation_coverage,
        data_grounding,
        critic_passed,
        trace_completeness,
        retrieval_signal,
        tool_metrics["tool_success_rate"] if tool_metrics["tool_success_rate"] is not None else 1.0,
    ]
    score = round(sum(components) / len(components), 3)
    result = {
        "score": score,
        "metrics": metrics,
        "grade": _grade(score),
        "unavailable_metrics": [
            "recall_at_k",
            "precision_at_k",
            "mrr",
            "ndcg_at_k",
            "citation_accuracy",
            "faithfulness",
            "completeness",
            "hallucination_rate",
        ],
    }
    trace.append({
        "node": "EvaluatorAgent",
        "score": score,
        "grade": result["grade"],
        "metrics": {
            "citation_coverage": citation_coverage,
            "data_grounding": data_grounding,
            "critic_passed": critic_passed,
            "trace_completeness": trace_completeness,
        },
    })
    return {"evaluation": result, "trace": trace}


def _citation_coverage(report: str, kept: list[dict[str, Any]]) -> float:
    if not kept:
        no_docs_markers = ("未检索到", "无证据", "no evidence", "no retrieved")
        return 0.5 if any(marker in report.lower() for marker in no_docs_markers) else 0.0
    if any(marker in report for marker in ("来源", "参考", "证据", "source", "citation")):
        return 1.0
    return 1.0 if any(
        str(d.get("title", ""))[:10] and str(d.get("title", ""))[:10] in report
        for d in kept
    ) else 0.0


def _data_grounding(report: str, data_context: dict[str, Any]) -> float:
    ok_count = int(data_context.get("ok_count", 0) or 0)
    if ok_count <= 0:
        return 0.0
    variables = data_context.get("variables", []) or []
    names = [
        str(v.get("alias") or v.get("variable") or "")
        for v in variables
        if v.get("alias") or v.get("variable")
    ]
    if not names:
        return 0.5
    report_lower = report.lower()
    hits = sum(1 for name in names if name and name.lower() in report_lower)
    return round(min(1.0, max(0.5, hits / max(1, len(names)))), 3)


def _avg_score(items: list[dict[str, Any]], key: str, fallback_key: str | None = None) -> float | None:
    vals: list[float] = []
    for item in items:
        raw = item.get(key)
        if raw is None and fallback_key:
            raw = item.get(fallback_key)
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _top_score(items: list[dict[str, Any]]) -> float | None:
    return _top_score_by_key(items, "score")


def _top_score_by_key(items: list[dict[str, Any]], key: str) -> float | None:
    vals: list[float] = []
    for item in items:
        try:
            vals.append(float(item.get(key)))
        except (TypeError, ValueError):
            continue
    return round(max(vals), 4) if vals else None


def _retrieval_signal(candidates: list[dict[str, Any]], kept: list[dict[str, Any]]) -> float:
    if not candidates:
        return 0.0
    kept_ratio = len(kept) / max(1, len(candidates))
    top_score = _top_score(candidates)
    score_part = min(1.0, max(0.0, top_score if top_score is not None else 0.0))
    return round(0.55 * min(1.0, kept_ratio) + 0.45 * score_part, 3)


def _tool_metrics(trace: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for item in trace:
        if item.get("tool_name") or item.get("tool"):
            calls.append(item)
        for nested in item.get("tool_calls", []) or []:
            if isinstance(nested, dict):
                calls.append(nested)
    if not calls:
        return {
            "tool_call_count": 0,
            "tool_success_rate": None,
            "failed_tool_count": 0,
        }
    failed = 0
    for call in calls:
        if call.get("success") is False or call.get("ok") is False or call.get("error"):
            failed += 1
    return {
        "tool_call_count": len(calls),
        "tool_success_rate": round((len(calls) - failed) / len(calls), 3),
        "failed_tool_count": failed,
    }


def _trace_completeness(trace: list[dict[str, Any]]) -> float:
    expected = {
        "IntentNode", "PlannerNode", "RetrievalNode", "ContextNode", "DataAgent",
        "ScreeningNode", "ReasoningNode", "VisualizationAgent", "ReportNode",
        "CriticNode",
    }
    seen = {str(item.get("node")) for item in trace}
    return round(len(expected & seen) / len(expected), 3)


def _grade(score: float) -> str:
    if score >= 0.85:
        return "good"
    if score >= 0.6:
        return "usable"
    return "needs_review"
