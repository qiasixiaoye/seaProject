"""EvaluatorAgent - quality metrics for a finished run.

两层评估：
- L1 确定性可验证（citation_accuracy / data_grounding / trace / tools）——代码硬核对，最可信。
- L2 LLM 语义裁判（faithfulness / completeness / relevance / hallucination）——
  带超时 + 硬回退，未配置 LLM 或超时即跳过，绝不拖垮主流程。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState, normalize_trace

log = logging.getLogger("geo_agent.nodes.evaluator")

# 引用编号正则：同时认带括号 [E1]/[E2][E3] 和裸写 E1、E2、E3（模型对方括号时遵守时不遵守，需兜底）。
# 主键是 E 后的编号（对应“保留证据”列表 E1..En），用范围核对抓越界/编造引用。
_CITATION_RE = re.compile(r"\bE(\d{1,2})\b")
_JUDGE_TIMEOUT_S = 8.0


def run(state: GeoAgentState) -> dict[str, Any]:
    report = state.get("report", "") or ""
    candidates = state.get("candidates", []) or []
    kept = state.get("kept_docs", []) or []
    passed = state.get("passed_docs", []) or []
    data_context = state.get("data_context", {}) or {}
    trace = normalize_trace(list(state.get("trace", [])))
    critic = state.get("critic_result", {}) or {}

    citation_coverage = _citation_coverage(report, kept)
    citation_accuracy, citation_stats = _citation_accuracy(report, kept)
    data_grounding = _data_grounding(report, data_context)
    critic_passed = 1.0 if critic.get("passed", True) else 0.0
    trace_completeness = _trace_completeness(trace)
    trace_schema_completeness = _trace_schema_completeness(trace)
    retrieval_signal = _retrieval_signal(candidates, kept)
    tool_metrics = _tool_metrics(trace)
    tool_success = tool_metrics["tool_success_rate"] if tool_metrics["tool_success_rate"] is not None else 1.0

    # ── L2：LLM 语义裁判（带超时 + 硬回退，未配置/失败即为 None）────────────────
    judge = _llm_judge(state, report, kept, data_context)

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
            "citation_accuracy": citation_accuracy,
            "citation_total": citation_stats["total"],
            "citation_valid": citation_stats["valid"],
            "citation_invalid_examples": citation_stats["invalid_examples"],
            "evidence_count": len(kept),
        },
        "answer": {
            "faithfulness": judge.get("faithfulness") if judge else None,
            "completeness": judge.get("completeness") if judge else None,
            "relevance": judge.get("relevance") if judge else None,
            "hallucination_rate": judge.get("hallucination_rate") if judge else None,
            "judge_mode": judge.get("mode") if judge else "skipped",
            "judge_reasoning": judge.get("reasoning") if judge else None,
            "data_grounding": data_grounding,
            "critic_passed": critic_passed,
            "critic_issue_count": len(critic.get("issues", []) or []),
        },
        "tools": tool_metrics,
        "trace": {
            "trace_completeness": trace_completeness,
            "trace_schema_completeness": trace_schema_completeness,
            "node_count": len(trace),
        },
        "system": {
            "elapsed_ms": None,
            "token_usage": None,
        },
        # Backward-compatible scalar keys for older consumers.
        "citation_coverage": citation_coverage,
        "citation_accuracy": citation_accuracy,
        "data_grounding": data_grounding,
        "critic_passed": critic_passed,
        "trace_completeness": trace_completeness,
        "trace_schema_completeness": trace_schema_completeness,
    }

    # ── 三层加权聚合 ──────────────────────────────────────────────────────────
    # 证据层（确定性，可验证）：引用准确率优先，无引用核对时退回覆盖率
    evidence_score = _mean([
        citation_accuracy if citation_accuracy is not None else citation_coverage,
        data_grounding,
        retrieval_signal,
    ])
    # 工程层（链路健康）
    engineering_score = _mean([
        trace_completeness, trace_schema_completeness, tool_success, critic_passed,
    ])
    # 语义层（LLM 裁判，可能缺席）
    semantic_score = None
    if judge:
        hall = judge.get("hallucination_rate")
        semantic_score = _mean([
            judge.get("faithfulness"), judge.get("completeness"), judge.get("relevance"),
            (1.0 - hall) if isinstance(hall, (int, float)) else None,
        ])

    if semantic_score is not None:
        # 语义 0.45 / 证据 0.35 / 工程 0.20：把"答得好不好"顶到最高权重
        score = round(0.45 * semantic_score + 0.35 * evidence_score + 0.20 * engineering_score, 3)
        scoring_mode = "llm_judge_weighted"
    else:
        # 无 LLM 裁判：证据 0.6 / 工程 0.4，保持确定性可复现
        score = round(0.60 * evidence_score + 0.40 * engineering_score, 3)
        scoring_mode = "deterministic"

    unavailable = ["recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy_human"]
    if not judge:
        unavailable += ["faithfulness", "completeness", "relevance", "hallucination_rate"]

    result = {
        "score": score,
        "scoring_mode": scoring_mode,
        "layer_scores": {
            "semantic": semantic_score,
            "evidence": round(evidence_score, 3),
            "engineering": round(engineering_score, 3),
        },
        "metrics": metrics,
        "grade": _grade(score),
        "unavailable_metrics": unavailable,
    }
    trace.append({
        "node": "EvaluatorAgent",
        "score": score,
        "grade": result["grade"],
        "scoring_mode": scoring_mode,
        "metrics": {
            "citation_accuracy": citation_accuracy,
            "citation_valid": f"{citation_stats['valid']}/{citation_stats['total']}",
            "faithfulness": judge.get("faithfulness") if judge else None,
            "completeness": judge.get("completeness") if judge else None,
            "relevance": judge.get("relevance") if judge else None,
            "data_grounding": data_grounding,
        },
    })
    return {"evaluation": result, "trace": trace}


def _mean(values: list[Any]) -> float:
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 4) if nums else 0.0


def _citation_accuracy(report: str, kept: list[dict[str, Any]]) -> tuple[float | None, dict[str, Any]]:
    """确定性核对：报告里的 [E#] 引用是否对应真实保留证据。

    主校验：E 编号必须落在 1..len(kept) 内——引用 [E9] 但只有 6 条证据即编造。
    若引用还带了 doc_id/chunk_id，进一步核对是否匹配该条证据（更强信号）。
    直接抓 LLM 编造/越界的引用，是整套评分里最可信、零成本的一环。
    返回 (准确率, 统计)；无保留证据时为 None（不参与该层）。
    """
    matches = _CITATION_RE.findall(report or "")
    total = len(matches)
    stats = {"total": total, "valid": 0, "invalid_examples": []}
    if not kept:
        return None, stats
    if total == 0:
        # 有证据可引却一处都没引 → 视为 0 分（覆盖率层会一并体现）
        return 0.0, stats

    n = len(kept)
    valid = 0
    for num_str in matches:
        if 1 <= int(num_str) <= n:
            valid += 1
        elif len(stats["invalid_examples"]) < 3:
            stats["invalid_examples"].append(f"E{num_str}")
    stats["valid"] = valid
    return round(valid / total, 3), stats


def _llm_judge(
    state: GeoAgentState,
    report: str,
    kept: list[dict[str, Any]],
    data_context: dict[str, Any],
) -> dict[str, Any] | None:
    """L2 LLM 裁判：参考接地 + 量规锚定 + 结构化输出 + 超时硬回退。

    任何异常/超时/未配置都返回 None，由 run() 退回纯确定性评分。
    """
    if not llm.configured() or not report.strip():
        return None

    intent = state.get("intent", {}) or {}
    question = intent.get("original_question") or intent.get("question") or ""
    evidence = "\n".join(
        f"[E{i}] {d.get('title','')}：{str(d.get('abstract',''))[:200]}"
        for i, d in enumerate(kept[:6], 1)
    ) or "（本次未保留任何证据）"
    # 给裁判与报告同源、同精度的数值（均值/范围/格点数），避免把合法数值误判为编造。
    # 注意：data_context.variables 用扁平 key（见 data.py:_finding），不是 stats 子字典。
    _num_lines = []
    for v in (data_context.get("variables") or [])[:8]:
        if not v.get("has_valid_data") and v.get("mean") is None:
            continue
        name = v.get("long_name") or v.get("alias") or v.get("variable")
        _num_lines.append(
            f"- {name}（{v.get('units','-')}）：均值 {v.get('mean')}，"
            f"范围 {v.get('minimum')}~{v.get('maximum')}，格点数 {v.get('count')}"
        )
    numbers = "\n".join(_num_lines) or "（无区域数值）"

    system_prompt = (
        "你是严格的科学报告质量评审，对报告挑错优先、宁严勿松。"
        "判断结论是否被【给定材料】（区域数值 + 保留证据）支撑；"
        "其中报告引用的区域数值若与【区域数值】一致即视为有支撑。"
        "领域通用标准（如蒲福风级、道格拉斯海况、常见灾害预警阈值）属合理专业常识，不计为幻觉；"
        "只有凭空捏造的事实/数据/文献才算幻觉。\n"
        "按以下量规为每个维度打 0~1 分：\n"
        "faithfulness 忠实度：结论是否被证据/数值支撑（1=全部有支撑，0.6=主要有、个别超纲，0.3=多处无支撑，0=基本编造）\n"
        "completeness 完整度：是否回答了用户问题的全部要点（1=全覆盖，0.5=覆盖一半，0=答非所问）\n"
        "relevance 相关性：内容是否切题、无无关填充（1=高度切题，0=大量跑题）\n"
        "hallucination_rate 幻觉率：无任何证据/数值支撑的断言占比（0=无幻觉，1=几乎全是）\n"
        '只输出 JSON：{"faithfulness":0~1,"completeness":0~1,"relevance":0~1,'
        '"hallucination_rate":0~1,"reasoning":"一句中文依据"}'
    )
    user_content = (
        f"【用户问题】\n{question}\n\n"
        f"【区域数值】\n{numbers}\n\n"
        f"【保留证据】\n{evidence}\n\n"
        f"【待评报告】\n{report[:4000]}"
    )
    try:
        data = llm.chat_json(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_content}],
            temperature=0.0,
            timeout=_JUDGE_TIMEOUT_S,
        )
    except Exception as exc:
        log.warning("LLM judge skipped (fallback to deterministic): %s", exc)
        return None
    if not isinstance(data, dict):
        return None

    def _clip(x: Any) -> float | None:
        try:
            return max(0.0, min(1.0, float(x)))
        except (TypeError, ValueError):
            return None

    out = {
        "faithfulness": _clip(data.get("faithfulness")),
        "completeness": _clip(data.get("completeness")),
        "relevance": _clip(data.get("relevance")),
        "hallucination_rate": _clip(data.get("hallucination_rate")),
        "reasoning": str(data.get("reasoning", ""))[:300],
        "mode": "llm",
    }
    # 三个正向维度全空 → 视为无效裁判
    if all(out[k] is None for k in ("faithfulness", "completeness", "relevance")):
        return None
    return out


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
        details = item.get("details", {}) if isinstance(item.get("details"), dict) else {}
        if details.get("tool_name") or details.get("tool") or item.get("tool_name") or item.get("tool"):
            calls.append({**details, **item})
        for nested in (details.get("tool_calls") or item.get("tool_calls") or []) or []:
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


def _trace_schema_completeness(trace: list[dict[str, Any]]) -> float:
    required = {"node", "mode", "status", "elapsed_ms", "input_summary", "output_summary", "error"}
    if not trace:
        return 0.0
    complete = sum(1 for item in trace if required <= set(item.keys()))
    return round(complete / len(trace), 3)


def _grade(score: float) -> str:
    if score >= 0.85:
        return "good"
    if score >= 0.6:
        return "usable"
    return "needs_review"
