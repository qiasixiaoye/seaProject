"""ReportNode — 报告生成节点。

相比旧版新增：
- 按领域选择报告模板前缀（观星/生物/航行/海洋）
- 支持流式输出（yield token）
- 更清晰的引用格式
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Generator

from geo_agent import llm
from geo_agent.state import GeoAgentState

log = logging.getLogger("geo_agent.nodes.report")

DOMAIN_TITLES = {
    "marine":     "海洋要素与风险分析报告",
    "stargazing": "观星适宜性与暗天空评估报告",
    "biology":    "海洋生物状况评估报告",
    "navigation": "航行安全综合评估报告",
    "general":    "海洋地理综合分析报告",
}

DOMAIN_SECTIONS = {
    "stargazing": ["观测地点条件", "月相与月光干扰", "光污染评估", "大气透明度", "推荐观测目标", "最佳观测时段"],
    "biology":    ["珊瑚礁健康评估", "有害藻华风险", "渔业栖息地适宜性", "生态保护建议"],
    "navigation": ["当前海况（道格拉斯等级）", "风级评估（蒲福等级）", "能见度状况", "分船型风险评级", "航行建议"],
    "marine":     ["区域海洋要素", "风险假设", "渔业与生态影响", "监测与规划建议"],
    "general":    ["区域概况", "主要发现", "风险与不确定性", "建议"],
}

MULTIMODAL_TITLE = "图片检索与证据解读报告"
MULTIMODAL_SECTIONS = ["输入与方法边界", "图片召回结果", "可支持的科学解释", "不确定性与下一步"]


def run(state: GeoAgentState, feedback: str | None = None) -> dict[str, Any]:
    domain = state.get("domain", "general")
    intent = state.get("intent", {})
    kept_docs = state.get("kept_docs", [])
    passed_docs = state.get("passed_docs", [])
    ocean_data = state.get("ocean_data", {})
    domain_analysis = state.get("domain_analysis", {})
    risk_hypotheses = state.get("risk_hypotheses", [])
    backend_used = state.get("backend_used", "local")
    image_mode = bool(str(state.get("image_ref") or "").strip())
    retrieval_routes = state.get("retrieval_routes", [])
    revisions = state.get("revisions", 0)
    trace = list(state.get("trace", []))

    if llm.configured():
        try:
            report_text = _llm_report(
                domain=domain,
                intent=intent,
                kept_docs=kept_docs,
                passed_docs=passed_docs,
                ocean_data=ocean_data,
                domain_analysis=domain_analysis,
                risk_hypotheses=risk_hypotheses,
                backend_used=backend_used,
                image_mode=image_mode,
                retrieval_routes=retrieval_routes,
                feedback=feedback,
            )
            mode = "llm"
        except Exception as exc:
            log.warning("ReportNode LLM failed, using template: %s", exc)
            report_text = _template_report(domain, intent, kept_docs, ocean_data, risk_hypotheses, image_mode)
            mode = "template_fallback"
    else:
        report_text = _template_report(domain, intent, kept_docs, ocean_data, risk_hypotheses, image_mode)
        mode = "template"

    trace.append({"node": "ReportNode", "mode": mode, "revised": bool(feedback),
                  "chars": len(report_text), "revisions": revisions})
    return {
        "report": report_text,
        "revisions": revisions + (1 if feedback else 0),
        "trace": trace,
    }


def stream(state: GeoAgentState, feedback: str | None = None) -> Generator[str, None, None]:
    """流式生成报告，逐 token yield。"""
    domain = state.get("domain", "general")
    intent = state.get("intent", {})
    kept_docs = state.get("kept_docs", [])
    ocean_data = state.get("ocean_data", {})
    domain_analysis = state.get("domain_analysis", {})
    risk_hypotheses = state.get("risk_hypotheses", [])
    backend_used = state.get("backend_used", "local")
    image_mode = bool(str(state.get("image_ref") or "").strip())

    if not llm.configured():
        yield _template_report(domain, intent, kept_docs, ocean_data, risk_hypotheses, image_mode)
        return

    messages = _build_messages(
        domain=domain, intent=intent, kept_docs=kept_docs,
        passed_docs=state.get("passed_docs", []),
        ocean_data=ocean_data, domain_analysis=domain_analysis,
        risk_hypotheses=risk_hypotheses, backend_used=backend_used,
        image_mode=image_mode, retrieval_routes=state.get("retrieval_routes", []),
        feedback=feedback,
    )
    for token in llm.chat_stream(messages):
        yield token


# ── 内部辅助 ─────────────────────────────────────────────────────────────────
def _llm_report(
    domain: str,
    intent: dict,
    kept_docs: list,
    passed_docs: list,
    ocean_data: dict,
    domain_analysis: dict,
    risk_hypotheses: list,
    backend_used: str,
    image_mode: bool,
    retrieval_routes: list,
    feedback: str | None,
) -> str:
    messages = _build_messages(
        domain=domain, intent=intent, kept_docs=kept_docs, passed_docs=passed_docs,
        ocean_data=ocean_data, domain_analysis=domain_analysis,
        risk_hypotheses=risk_hypotheses, backend_used=backend_used,
        image_mode=image_mode, retrieval_routes=retrieval_routes, feedback=feedback,
    )
    return llm.chat(messages)


def _build_messages(
    domain: str,
    intent: dict,
    kept_docs: list,
    passed_docs: list,
    ocean_data: dict,
    domain_analysis: dict,
    risk_hypotheses: list,
    backend_used: str,
    image_mode: bool,
    retrieval_routes: list,
    feedback: str | None,
) -> list[dict]:
    title = MULTIMODAL_TITLE if image_mode else DOMAIN_TITLES.get(domain, DOMAIN_TITLES["general"])
    sections = MULTIMODAL_SECTIONS if image_mode else DOMAIN_SECTIONS.get(domain, DOMAIN_SECTIONS["general"])
    section_list = "、".join(sections)

    evidence_n = min(len(kept_docs), 8)
    evidence = "\n\n".join(
        f"{_evidence_label(d, i)} 标题：{d.get('title','')}\n"
        f"来源：{d.get('source','')}\n"
        f"召回路径：{_evidence_route(d)}\n"
        f"相关分：{d.get('decision_score', d.get('score',0)):.3f}\n"
        f"摘要：{str(d.get('abstract',''))[:450]}"
        for i, d in enumerate(kept_docs[:8], 1)
    ) or "当前知识库未找到相关证据，基于数值推理生成报告。"

    numbers = _format_numbers(ocean_data)
    analysis_summary = _format_domain_analysis(domain, domain_analysis)
    hyp_text = _format_hypotheses(risk_hypotheses)
    route_text = _format_retrieval_routes(retrieval_routes)

    user_content = (
        f"用户问题：{intent.get('original_question','')}\n"
        f"检索后端：{backend_used}\n\n"
        f"检索路径：\n{route_text}\n\n"
        f"区域数值：\n{numbers}\n\n"
        f"领域分析：\n{analysis_summary}\n\n"
        f"风险假设：\n{hyp_text}\n\n"
        f"保留证据（{len(kept_docs)} 篇）：\n{evidence}\n\n"
        f"被过滤文档：{', '.join(d.get('title','') for d in passed_docs[:3]) or '无'}"
    )
    if feedback:
        user_content += f"\n\n【审稿意见，请据此修订】\n{feedback}"

    system_prompt = (
        f"今天是 {datetime.now().strftime('%Y年%m月%d日')}，这是本报告的生成日期。"
        "如需在报告中标注日期或落款，一律使用该生成日期；"
        "不得自行编造其它年份或观测时间（证据/数据本身的年份以输入材料为准）。\n"
        f"你是专业的地理与海洋科学分析助手，生成《{title}》。\n"
        f"报告必须包含以下章节：{section_list}。\n"
        + (
            "【图片模式边界】系统只用 Chinese-CLIP 将图片编码后检索文字证据，报告模型没有直接看到原图，也没有执行OCR、色标读取或像素级数值解析。"
            "必须在报告开头明确这一点。只能说明哪些文献被图片向量召回，以及这些证据通常如何解释；"
            "不得把召回文本中的年份、数值、事件或区域冒充为用户图片本身的信息。"
            "若用户同时提供了文字描述，可将其标为用户提供信息，但仍不得声称已从图片直接识别。"
            "结尾给出要获得确定结论还需要的图题、图注、变量、单位、色标含义、时间、基准期和显著性检验方法。\n"
            if image_mode else ""
        )
        + (
            f"【引用规则｜务必遵守】每个核心结论的句末必须用方括号标注所依据的证据编号，"
            f"格式为 [E数字]，可多条如 [E1][E3]；编号只能取自下方“保留证据”列表的 E1 到 E{evidence_n}，"
            f"严禁引用不存在的编号，严禁编造数据。例如：“该海域存在热浪风险[E2]。”\n"
            if evidence_n > 0 else
            "【引用规则】本次未检索到相关证据，请在报告中明确说明“证据不足，以下为基于区域数值的推断”，不得编造文献或数据。\n"
        )
        + "证据不足时明确说明，不得编造数据。\n"
        "输出中文报告，Markdown 格式，含标题层级。"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _template_report(
    domain: str,
    intent: dict,
    kept_docs: list,
    ocean_data: dict,
    risk_hypotheses: list,
    image_mode: bool = False,
) -> str:
    title = MULTIMODAL_TITLE if image_mode else DOMAIN_TITLES.get(domain, "分析报告")
    lines = [
        f"# {title}",
        "",
        f"## 问题",
        intent.get("original_question", ""),
        "",
    ]
    if image_mode:
        lines += [
            "## 输入与方法边界",
            "图片仅用于 Chinese-CLIP 跨模态文字召回；当前报告没有直接读取原图像素、OCR、色标或数值。以下内容是召回证据的解释，不是对原图的确定识别。",
            "",
            "## 图片召回结果",
        ]
        for i, doc in enumerate(kept_docs, 1):
            lines.append(f"- {_evidence_label(doc, i)} {doc.get('title','')}（{_source_with_page(doc)}）")
        if not kept_docs:
            lines.append("- 未检索到足够相关证据")
        lines += [
            "",
            "## 可支持的科学解释",
            "请结合上述证据与原图图题、图注后再作判断。",
            "",
            "## 不确定性与下一步",
            "需要补充变量、单位、时间、异常基准期、色标含义及显著性检验方法。",
            "",
            "## 注",
            "（本报告由模板生成，请配置 DEEPSEEK_API_KEY 启用 LLM 增强报告）",
        ]
        return "\n".join(lines)
    lines += [
        "## 区域数值",
        _format_numbers(ocean_data),
        "",
        "## 风险假设",
        _format_hypotheses(risk_hypotheses),
        "",
        "## 保留证据",
    ]
    for i, doc in enumerate(kept_docs, 1):
        lines.append(f"- {_evidence_label(doc, i)} {doc.get('title','')}（{_source_with_page(doc)}）")
    if not kept_docs:
        lines.append("- 未检索到足够相关证据")
    lines += ["", "## 注", "（本报告由模板生成，请配置 DEEPSEEK_API_KEY 启用 LLM 增强报告）"]
    return "\n".join(lines)


def _evidence_route(doc: dict[str, Any]) -> str:
    routes = doc.get("retrieval_routes") or []
    if routes:
        return "+".join(str(item) for item in routes)
    return str(doc.get("route") or (doc.get("metadata") or {}).get("route") or doc.get("backend") or "unknown")


def _format_retrieval_routes(routes: list) -> str:
    if not routes:
        return "- 未提供检索路径明细"
    lines = []
    for item in routes:
        if not isinstance(item, dict):
            continue
        name = item.get("route") or "unknown"
        status = f"count={item.get('count', 0)}"
        if item.get("weight") is not None:
            status += f"，weight={item.get('weight')}"
        if item.get("error"):
            status += f"，error={item.get('error')}"
        lines.append(f"- {name}：{status}")
    return "\n".join(lines) or "- 未提供检索路径明细"


def _evidence_label(doc: dict[str, Any], index: int) -> str:
    doc_id = doc.get("doc_id") or doc.get("id") or "unknown-doc"
    chunk_id = doc.get("chunk_id") or doc.get("id") or "unknown-chunk"
    page = doc.get("page")
    page_text = f", p.{page}" if page else ""
    return f"[E{index}: {doc_id}/{chunk_id}{page_text}]"


def _source_with_page(doc: dict[str, Any]) -> str:
    page = doc.get("page")
    if page:
        return f"{doc.get('source','')} p.{page}"
    return str(doc.get("source", ""))


def _format_numbers(ocean_data: dict) -> str:
    rows = []
    for item in ocean_data.get("variables", []):
        if item.get("error"):
            label = item.get("alias") or item.get("variable")
            rows.append(f"- {label}：无数据（{item['error']}）")
            continue
        s = item.get("stats") or {}
        rows.append(
            f"- {item.get('long_name') or item.get('variable')}"
            f"（{item.get('units','-')}）：均值 {s.get('mean')}, "
            f"范围 {s.get('min')}~{s.get('max')}, 格点数 {s.get('count')}"
        )
    missing = [
        str(item.get("alias") or item.get("variable"))
        for item in ocean_data.get("missing_variables", [])
        if item.get("alias") or item.get("variable")
    ]
    if missing:
        rows.append(f"- 数据缺口：{', '.join(dict.fromkeys(missing))} 当前未在 NetCDF 数据集中找到，报告不据此下强结论。")
    return "\n".join(rows) or "（无区域数值数据）"


def _format_hypotheses(hypotheses: list) -> str:
    if not hypotheses:
        return "（无风险假设触发）"
    lines = []
    for h in hypotheses:
        lines.append(
            f"- 信号：{h.get('signal','')} → {h.get('hypothesis','')}\n"
            f"  依据：{h.get('basis','')}；不确定性：{h.get('uncertainty','')}"
        )
    return "\n".join(lines)


def _format_domain_analysis(domain: str, analysis: dict) -> str:
    if not analysis:
        return "（无领域分析数据）"

    if domain == "stargazing" and "stargazing" in analysis:
        sg = analysis["stargazing"]
        moon = sg.get("moon", {})
        bortle = sg.get("bortle", {})
        return (
            f"观星综合评分：{sg.get('overall_score')}/100（{sg.get('verdict')}）\n"
            f"月相：{moon.get('phase_name')} 照明度 {moon.get('illumination',0)*100:.0f}%\n"
            f"Bortle 等级：{bortle.get('bortle_scale')} - {bortle.get('bortle_description','')[:40]}\n"
            f"透明度：{sg.get('transparency',{}).get('transparency_quality','')}"
        )

    if domain == "biology" and "biology" in analysis:
        bio = analysis["biology"]
        coral = bio.get("coral_bleaching", {})
        hab = bio.get("harmful_algal_bloom", {})
        most = bio.get("habitat_suitability", {}).get("most_suitable", [])
        return (
            f"珊瑚白化风险：{coral.get('risk_level','未知')}\n"
            f"有害藻华风险：{hab.get('risk_level','未知')}\n"
            f"最适宜物种：{', '.join(most) or '无'}"
        )

    if domain == "navigation" and "navigation" in analysis:
        nav = analysis["navigation"]
        sea = nav.get("sea_state") or {}
        return (
            f"道格拉斯海况：{sea.get('douglas_scale','-')} 级 ({sea.get('state_name','')})\n"
            f"整体安全评分：{nav.get('overall_safety_score','-')}/100"
            f"（{nav.get('overall_verdict','')}）"
        )

    return str(analysis)[:300]
