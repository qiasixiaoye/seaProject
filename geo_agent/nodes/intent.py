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


# 问候 / 闲聊 / 求助类输入（非分析）。命中后直接给引导回复，不跑检索与报告。
GREETING_PATTERNS = [
    "你好", "您好", "哈喽", "你好呀", "嗨", "在吗", "在不在", "有人吗",
    "你是谁", "你叫什么", "你是什么", "你能做什么", "你能干什么", "你会什么", "你可以做什么",
    "怎么用", "如何使用", "使用说明", "帮助", "介绍一下", "自我介绍", "功能",
    "谢谢", "多谢", "感谢", "早上好", "中午好", "下午好", "晚上好", "晚安",
    "hi", "hello", "hey", "help", "thanks", "thank you", "who are you", "what can you do",
]

_ANALYSIS_MARKERS = [
    "评估", "分析", "风险", "报告", "预测", "建议", "适合", "如何", "怎样", "是否",
    "海", "洋", "鱼", "船", "星", "浪", "温", "盐", "藻",
    "ocean", "sea", "marine", "risk", "assess", "temperature", "wave",
]


def is_smalltalk(question: str) -> bool:
    """判断是否为问候/闲聊/求助类非分析输入。

    保守策略：只要命中任一领域关键词或分析意图词，一律放行走完整流程，
    避免误伤真问题。"""
    q = (question or "").strip().lower()
    if not q:
        return True
    for keywords in DOMAIN_KEYWORDS.values():
        if any(kw.lower() in q for kw in keywords):
            return False
    has_analysis = any(m in q for m in _ANALYSIS_MARKERS)
    hit_greeting = any(g in q for g in GREETING_PATTERNS)
    if hit_greeting and (len(q) <= 16 or not has_analysis):
        return True
    if len(q) <= 4 and not has_analysis:
        return True
    return False


def smalltalk_reply(question: str) -> str:
    """问候/闲聊的固定引导回复（不消耗 LLM token）。"""
    return (
        "👋 你好！我是**海洋数字地球智能助手**。\n"
        "我会基于你**框选海域**的实测数据 + 知识库证据，生成可追溯的分析报告"
        "（每一步都在右侧「执行审计」里可查）。\n\n"
        "**我能做：**\n"
        "- 🌊 海洋要素：海温 / 盐度 / 叶绿素 / 海浪异常与风险\n"
        "- 🐠 海洋生物：珊瑚白化、赤潮、栖息地适宜性\n"
        "- ⚓ 航行安全：浪高、风级、分船型风险\n"
        "- 🌟 观星适宜性：月相、光污染、云量\n\n"
        "**怎么用：**\n"
        "1. 在地球上点「框选」拖出一片海域；\n"
        "2. 选上方领域 Tab（或保持「自动」）；\n"
        "3. 输入问题，例如\"评估该海域珊瑚礁与渔业风险\"。\n\n"
        "也可以直接点下方的快捷示例试试 🙌"
    )


def run(state: GeoAgentState) -> dict[str, Any]:
    question = state.get("question", "")
    trace = list(state.get("trace", []))

    # 非分析类输入（问候/闲聊/求助）：不调 LLM、不跑后续，直接给引导回复
    if is_smalltalk(question):
        intent = {
            "intent_type": "chitchat",
            "domain": "general",
            "original_question": question,
            "topics": [], "keywords": [], "query_variants": [],
            "smalltalk_reply": smalltalk_reply(question),
        }
        trace.append({"node": "IntentNode", "mode": "smalltalk",
                      "domain": "general", "intent_type": "chitchat"})
        return {"intent": intent, "domain": "general", "trace": trace}

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
