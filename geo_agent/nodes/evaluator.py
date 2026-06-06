"""EvaluatorAgent - deterministic quality metrics for the finished run."""
from __future__ import annotations

from typing import Any

from geo_agent.state import GeoAgentState


def run(state: GeoAgentState) -> dict[str, Any]:
    report = state.get("report", "") or ""
    kept = state.get("kept_docs", []) or []
    data_context = state.get("data_context", {}) or {}
    trace = list(state.get("trace", []))

    metrics = {
        "citation_coverage": _citation_coverage(report, kept),
        "data_grounding": 1.0 if data_context.get("ok_count", 0) > 0 and "区域数值" in report else 0.0,
        "critic_passed": 1.0 if state.get("critic_result", {}).get("passed", True) else 0.0,
        "trace_completeness": _trace_completeness(trace),
    }
    score = round(sum(metrics.values()) / max(1, len(metrics)), 3)
    result = {"score": score, "metrics": metrics, "grade": _grade(score)}
    trace.append({"node": "EvaluatorAgent", "score": score, "grade": result["grade"]})
    return {"evaluation": result, "trace": trace}


def _citation_coverage(report: str, kept: list[dict[str, Any]]) -> float:
    if not kept:
        return 0.0 if "未检索到" not in report else 0.5
    if "来源" in report:
        return 1.0
    return 1.0 if any(str(d.get("title", ""))[:10] and str(d.get("title", ""))[:10] in report for d in kept) else 0.0


def _trace_completeness(trace: list[dict[str, Any]]) -> float:
    expected = {
        "IntentNode", "PlannerNode", "RetrievalNode", "ContextNode", "DataAgent",
        "ScreeningNode", "ReasoningNode", "VisualizationAgent", "ReportNode", "CriticNode",
    }
    seen = {str(item.get("node")) for item in trace}
    return round(len(expected & seen) / len(expected), 3)


def _grade(score: float) -> str:
    if score >= 0.85:
        return "good"
    if score >= 0.6:
        return "usable"
    return "needs_review"

