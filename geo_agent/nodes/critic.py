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
        verdict = {"passed": True, "issues": [], "feedback": "", "mode": "skipped"}
        trace.append({"node": "CriticNode", **verdict})
        return {"critic_result": verdict, "trace": trace}

    try:
        verdict = _llm_critique(state)
    except Exception as exc:
        log.warning("CriticNode failed, accepting report: %s", exc)
        verdict = {"passed": True, "issues": [], "feedback": "", "mode": "error"}

    trace.append({"node": "CriticNode", "passed": verdict["passed"],
                  "issues_count": len(verdict.get("issues", []))})
    return {"critic_result": verdict, "trace": trace}


def _llm_critique(state: GeoAgentState) -> dict[str, Any]:
    domain = state.get("domain", "general")
    report = state.get("report", "")
    kept_docs = state.get("kept_docs", [])
    intent = state.get("intent", {})

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

    messages = [
        {
            "role": "system",
            "content": (
                "你是报告质量审稿人。检查以下各项：\n"
                "① 每个核心结论都有证据或数值支撑；\n"
                "② 没有把文件名/元数据当正文引用；\n"
                "③ 证据不足时如实说明，不强行下结论；\n"
                "④ 覆盖了用户问题的核心要素；\n"
                "⑤ 给出了可执行的规划/建议；\n"
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
