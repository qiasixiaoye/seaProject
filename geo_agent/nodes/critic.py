"""CriticAgent — 报告质量审查节点。与旧版逻辑一致，适配 GeoAgentState。"""
from __future__ import annotations

import logging
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState

log = logging.getLogger("geo_agent.nodes.critic")


def run(state: GeoAgentState) -> dict[str, Any]:
    trace = list(state.get("trace", []))

    if not llm.configured():
        verdict = _rule_critique(state)
        verdict["mode"] = "rules"
        trace.append({"node": "CriticNode", **verdict})
        return {"critic_result": verdict, "trace": trace}

    try:
        verdict = _llm_critique(state)
        rule_verdict = _rule_critique(state)
        if rule_verdict["issues"]:
            verdict["issues"] = list(dict.fromkeys([*(verdict.get("issues") or []), *rule_verdict["issues"]]))
            verdict["feedback"] = " ".join(
                part for part in [verdict.get("feedback", ""), rule_verdict.get("feedback", "")] if part
            ).strip()
            verdict["passed"] = bool(verdict.get("passed", True)) and rule_verdict["passed"]
    except Exception as exc:
        log.warning("CriticNode failed, accepting report: %s", exc)
        verdict = _rule_critique(state)
        verdict["mode"] = "error_rules"

    trace.append({"node": "CriticNode", "passed": verdict["passed"],
                  "issues_count": len(verdict.get("issues", []))})
    return {"critic_result": verdict, "trace": trace}


def _rule_critique(state: GeoAgentState) -> dict[str, Any]:
    report = state.get("report", "") or ""
    kept_docs = state.get("kept_docs", []) or []
    data_context = state.get("data_context", {}) or {}
    image_mode = bool(str(state.get("image_ref") or "").strip())
    issues: list[str] = []

    strong_markers = ("必然", "一定", "显著", "主要风险", "high risk", "significant risk", "must")
    has_strong_claim = any(marker.lower() in report.lower() for marker in strong_markers)
    has_evidence_ref = _has_evidence_reference(report, kept_docs)
    has_no_evidence_notice = any(marker in report for marker in ("证据不足", "未检索到", "数据局限", "不确定"))

    if kept_docs and not has_evidence_ref:
        issues.append("citation_missing")
    if not kept_docs and has_strong_claim and not has_no_evidence_notice:
        issues.append("unsupported_strong_claim")
    if data_context.get("quality_flags") and not has_no_evidence_notice:
        issues.append("data_limitation_missing")
    if image_mode and not any(marker in report for marker in ("图片仅用于", "跨模态", "没有直接", "未直接", "不能仅凭")):
        issues.append("image_retrieval_boundary_missing")

    feedback_parts = []
    if "citation_missing" in issues:
        feedback_parts.append("核心结论需要引用保留证据的 doc_id/chunk_id 或 [E#] 标签。")
    if "unsupported_strong_claim" in issues:
        feedback_parts.append("没有保留证据时不要给出强结论，应改为风险假设并说明证据不足。")
    if "data_limitation_missing" in issues:
        feedback_parts.append("报告需要说明 bbox、NetCDF 变量缺失或无数值上下文带来的数据局限。")
    if "image_retrieval_boundary_missing" in issues:
        feedback_parts.append("必须说明图片仅用于跨模态召回，报告模型未直接读取图片像素、OCR、色标或数值，不能把召回证据当作原图识别结果。")

    return {
        "passed": not issues,
        "issues": issues,
        "feedback": " ".join(feedback_parts),
    }


def _has_evidence_reference(report: str, kept_docs: list[dict[str, Any]]) -> bool:
    if "[E" in report:
        return True
    for doc in kept_docs:
        values = [
            doc.get("doc_id"),
            doc.get("chunk_id"),
            doc.get("title"),
            doc.get("source"),
        ]
        if any(value and str(value)[:24] in report for value in values):
            return True
    return False


def _llm_critique(state: GeoAgentState) -> dict[str, Any]:
    domain = state.get("domain", "general")
    report = state.get("report", "")
    kept_docs = state.get("kept_docs", [])
    intent = state.get("intent", {})
    image_mode = bool(str(state.get("image_ref") or "").strip())
    has_ocean_numbers = bool((state.get("ocean_data") or {}).get("variables"))

    evidence_list = "\n".join(
        f"- {d.get('title','')}（{d.get('source','')}）"
        for d in kept_docs
    ) or "（无保留证据）"

    domain_checks = {
        "stargazing": "⑥ 是否给出了具体的最佳观测时段建议；",
        "biology":    "⑥ 是否区分了珊瑚白化与藻华两类风险；",
        "navigation": "⑥ 是否针对不同船型分别给出了风险评级；",
        "marine":     "⑥ 是否基于数值阈值而不是泛泛而谈；",
    }.get(domain, "")
    if image_mode:
        domain_checks = (
            "⑥ 是否明确说明图片只用于跨模态文字召回，报告模型未直接读取图片、OCR、色标或像素数值；\n"
            "⑦ 是否避免把召回文献的年份、区域、数值或事件冒充为用户图片本身的信息；\n"
            "⑧ 是否给出图题、图注、变量、单位、基准期和检验方法等必要补充项；\n"
        )
    elif domain == "marine" and not has_ocean_numbers:
        domain_checks = "⑥ 无可用区域数值时，是否避免强行给出数值阈值结论；\n"

    messages = [
        {
            "role": "system",
            "content": (
                "你是报告质量审稿人。只有下列阻断性问题才将 passed 设为 false：捏造事实或引用、核心问题未回答、关键证据与结论矛盾、遗漏会导致误解的能力边界。"
                "措辞、章节详略或建议不够丰富属于非阻断性改进，应写入 issues 但保持 passed=true，避免为风格问题反复改稿。\n"
                "检查以下各项：\n"
                "① 每个核心结论都有证据或数值支撑；\n"
                "② 没有把文件名/元数据当正文引用；\n"
                "③ 证据不足时如实说明，不强行下结论；\n"
                "④ 覆盖了用户问题的核心要素；\n"
                "⑤ 当问题本身需要行动建议且证据足够时，才要求给出可执行建议；\n"
                f"{domain_checks}"
                '输出 JSON：{"passed":true|false,"issues":["..."],'
                '"feedback":"给作者的具体修改意见"}。只输出 JSON。'
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{intent.get('original_question','')}\n\n"
                f"保留证据：\n{evidence_list}\n\n"
                f"待审报告：\n{report[:3000]}"
            ),
        },
    ]
    data = llm.chat_json(messages)
    return {
        "passed": bool(data.get("passed", True)),
        "issues": _as_str_list(data.get("issues")),
        "feedback": str(data.get("feedback", "")).strip(),
        "mode": "llm",
    }


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []
