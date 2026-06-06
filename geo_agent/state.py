"""GeoAgent 统一状态定义（LangGraph TypedDict）。

所有节点读写同一个 GeoAgentState 实例，字段按流水线阶段分组。
"""
from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


def merge_trace(left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Merge trace updates from sequential or parallel nodes without duplicating prefixes."""
    if not left:
        return list(right or [])
    if not right:
        return list(left)
    merged = list(left)
    if len(right) >= len(left) and right[:len(left)] == left:
        merged.extend(right[len(left):])
        return merged
    for item in right:
        if item not in merged:
            merged.append(item)
    return merged


def normalize_trace(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return trace entries with a stable schema for APIs and UI consumers."""
    out: list[dict[str, Any]] = []
    for index, item in enumerate(trace or []):
        if not isinstance(item, dict):
            item = {"raw": item}
        node = str(item.get("node") or item.get("agent") or f"Step{index + 1}")
        mode = str(item.get("mode") or item.get("status") or "default")
        status = "error" if item.get("error") else str(item.get("status") or "ok")
        normalized: dict[str, Any] = {
            "index": index,
            "node": node,
            "mode": mode,
            "status": status,
            "elapsed_ms": item.get("elapsed_ms"),
            "input_summary": item.get("input_summary", {}),
            "output_summary": item.get("output_summary") or _trace_output_summary(item),
        }
        if item.get("error"):
            normalized["error"] = item.get("error")
        normalized["details"] = {
            k: v for k, v in item.items()
            if k not in {
                "index", "node", "agent", "mode", "status", "elapsed_ms",
                "input_summary", "output_summary", "error",
            }
        }
        out.append(normalized)
    return out


def _trace_output_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "domain", "backend", "count", "queries", "vars_queried", "vars_ok",
        "vars_missing", "kept", "passed", "layers", "warnings", "score",
        "grade", "passed", "revisions", "chars", "quality_flags",
    )
    return {key: item[key] for key in keys if key in item}


class TokenUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class BBox(TypedDict, total=False):
    west: float
    east: float
    south: float
    north: float


class GeoAgentState(TypedDict, total=False):
    # ── 输入 ──────────────────────────────────────────────────
    question: str
    bbox: Optional[BBox]
    variables: list[str]          # 用户指定的 NC 变量名
    backend: str                  # "auto" | "local" | "ragflow"
    top_k: int
    threshold: float
    max_revisions: int
    trace_enabled: bool

    # ── 意图解析（IntentNode） ─────────────────────────────────
    domain: str                   # "marine" | "stargazing" | "biology" | "navigation" | "general"
    intent: dict[str, Any]        # topics / keywords / queries / intent_type
    execution_plan: dict[str, Any]
    planned_variables: list[str]

    # ── 检索层（RetrievalNode + ContextNode，可并行） ──────────
    candidates: list[dict[str, Any]]   # 候选文档（Doc 序列化为 dict）
    backend_used: str
    ocean_data: dict[str, Any]         # NetCDF 区域统计
    data_context: dict[str, Any]       # DataAgent 数值质量摘要

    # ── 筛选层（ScreeningNode） ───────────────────────────────
    kept_docs: list[dict[str, Any]]
    passed_docs: list[dict[str, Any]]
    screening_decisions: list[dict[str, Any]]

    # ── 领域推理（ReasoningNode） ─────────────────────────────
    domain_analysis: dict[str, Any]   # 各领域分析结果
    risk_hypotheses: list[dict[str, Any]]
    visualization: dict[str, Any]     # VisualizationAgent 渲染建议

    # ── 报告生成（ReportNode） ────────────────────────────────
    report: str
    revisions: int

    # ── 质量审查（CriticNode） ────────────────────────────────
    critic_result: dict[str, Any]   # passed / issues / feedback
    evaluation: dict[str, Any]      # EvaluatorAgent 指标

    # ── 元信息 ────────────────────────────────────────────────
    task_id: str
    elapsed_ms: float
    token_usage: TokenUsage
    trace: Annotated[list[dict[str, Any]], merge_trace]
    errors: list[str]
