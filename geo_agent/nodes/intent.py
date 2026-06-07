"""IntentNode — 意图解析 + 领域分类。

相比旧版 IntentAgent 新增：
- 领域分类（marine/stargazing/biology/navigation/general）
- 自动选择对应工具集
- 失败时返回结构化降级结果（不崩溃）
"""
from __future__ import annotations

import logging
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState
from ocean_agents_demo import core

log = logging.getLogger("geo_agent.nodes.intent")

DOMAIN_KEYWORDS = {
    "stargazing": ["观星", "看星", "星星", "星空", "天文", "星座", "银河", "月相", "月亮",
                   "能见度", "光污染", "暗天空", "夜空", "天象",
                   "dark sky", "stargazing", "milky way", "astronomy", "telescope"],
    "biology":    ["珊瑚", "鱼群", "藻华", "赤潮", "渔业", "生物多样性", "珊瑚白化",
                   "藻类", "浮游", "鱼类", "生态", "白化",
                   "coral", "fish", "algae", "bloom", "biodiversity", "marine life"],
    "navigation": ["航行", "船只", "渔船", "浪高", "风级", "海况", "航运", "港口",
                   "出海", "行船", "波高", "涌浪", "风浪", "安全出海",
                   "navigation", "vessel", "ship", "wave", "maritime", "sailing"],
    "marine":     ["海温", "sst", "盐度", "叶绿素", "海浪", "海洋热浪", "海平面", "水温",
                   "海洋要素", "海表", "温度异常", "海流", "洋流",
                   "temperature", "salinity", "chlorophyll", "sea level", "ocean heat"],
}

DOMAIN_TOOLS = {
    "marine":     ["ocean", "retrieval"],
    "stargazing": ["astro", "ocean", "retrieval"],
    "biology":    ["biology", "ocean", "retrieval"],
    "navigation": ["navigation", "ocean", "retrieval"],
    "general":    ["ocean", "retrieval"],
}


def classify_domain(question: str) -> str:
    q_lower = question.lower()
    scores: dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw.lower() in q_lower)
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "general"


def run(state: GeoAgentState) -> dict[str, Any]:
    question = state.get("question", "")
    trace = list(state.get("trace", []))

    requested_domain = str(state.get("domain") or "").strip().lower()
    forced_domain = requested_domain in DOMAIN_TOOLS and requested_domain != "auto"

    # 先做轻量级领域分类（确定性，不调 LLM）；前端 Tab/调用方指定领域时优先服从。
    domain = requested_domain if forced_domain else classify_domain(question)

    if llm.configured():
        try:
            intent = _llm_intent(question, domain)
            if forced_domain:
                intent["domain"] = domain
            else:
                domain = intent.get("domain", domain)  # LLM 可能修正领域
            trace.append({
                "node": "IntentNode",
                "mode": "llm",
                "domain": domain,
                "intent_type": intent.get("intent_type"),
                "entities": intent.get("entities", []),
                "hazards": intent.get("hazards", []),
                "variables": intent.get("variables", []),
                "query_variants": len(intent.get("query_variants") or []),
            })
            return {"intent": intent, "domain": domain, "trace": trace}
        except Exception as exc:
            log.warning("IntentNode LLM failed, using heuristic: %s", exc)

    # 降级
    intent = _heuristic_intent(question, domain)
    trace.append({
        "node": "IntentNode",
        "mode": "heuristic",
        "domain": domain,
        "intent_type": intent.get("intent_type"),
        "entities": intent.get("entities", []),
        "hazards": intent.get("hazards", []),
        "variables": intent.get("variables", []),
        "query_variants": len(intent.get("query_variants") or []),
    })
    return {"intent": intent, "domain": domain, "trace": trace}


def _llm_intent(question: str, pre_domain: str) -> dict[str, Any]:
    domain_hint = (
        "stargazing（观星）、biology（海洋生物）、navigation（航行安全）、"
        "marine（海洋要素分析）、general（综合/未知）"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是 GeoAgent 意图解析器。读取用户问题，输出 JSON 字段：\n"
                f"- domain: 问题所属领域，从以下选择：{domain_hint}\n"
                "- intent_type: risk_assessment/planning/observation/general\n"
                "- topics: 主题词数组（中文）\n"
                "- keywords: 本地检索关键词（中文）\n"
                "- queries: 检索 query 数组（至少一中一英）\n"
                "- location_hint: 地理提示（如有，否则留空）\n"
                "只输出 JSON，不要解释。"
            ),
        },
        {"role": "user", "content": question},
    ]
    data = llm.chat_json(messages)
    if not isinstance(data, dict):
        data = {}

    topics = _as_str_list(data.get("topics"))
    keywords = _as_str_list(data.get("keywords")) or topics
    queries = _as_str_list(data.get("queries"))
    if not queries:
        queries = [question]

    intent = core.condense_intent(question)
    intent["domain"] = str(data.get("domain", pre_domain))
    intent["intent_type"] = str(data.get("intent_type") or intent.get("intent_type") or "general")
    intent["intent"] = intent["intent_type"]
    if topics:
        intent["topics"] = topics
    if keywords:
        intent["keywords"] = keywords
    if queries:
        intent["queries"] = list(dict.fromkeys(queries + _as_str_list(intent.get("queries"))))
        intent["retrieval_query"] = " ".join(dict.fromkeys(keywords + queries[:1])).strip() or intent["queries"][0]
    intent["location_hint"] = str(data.get("location_hint", ""))
    intent["mode"] = "llm"
    return intent


def _heuristic_intent(question: str, domain: str) -> dict[str, Any]:
    base = core.condense_intent(question)
    base["domain"] = domain
    base.setdefault("intent_type", "general")
    base.setdefault("intent", base["intent_type"])
    base.setdefault("queries", [base["retrieval_query"]])
    base["mode"] = "heuristic"
    return base


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []
