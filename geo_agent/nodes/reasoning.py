"""ReasoningNode — 领域推理节点。

根据 domain 路由到对应分析器，生成结构化的分析结果和风险假设。

领域路由：
  marine     → 海洋要素阈值规则 + LLM 推理
  stargazing → 观星综合评估
  biology    → 珊瑚/藻华/栖息地评估
  navigation → 航行安全矩阵
  general    → 海洋要素 fallback
"""
from __future__ import annotations

import logging
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState
from geo_agent.tools import astro, biology, navigation
from geo_agent.tools.ocean import get_variable_stats

log = logging.getLogger("geo_agent.nodes.reasoning")


def run(state: GeoAgentState) -> dict[str, Any]:
    domain = state.get("domain", "general")
    ocean_data = state.get("ocean_data", {})
    kept_docs = state.get("kept_docs", [])
    bbox = state.get("bbox", {})
    trace = list(state.get("trace", []))

    analysis: dict[str, Any] = {}
    hypotheses: list[dict[str, Any]] = []

    try:
        if domain == "stargazing":
            analysis, hypotheses = _analyze_stargazing(ocean_data, bbox)
        elif domain == "biology":
            analysis, hypotheses = _analyze_biology(ocean_data)
        elif domain == "navigation":
            analysis, hypotheses = _analyze_navigation(ocean_data)
        else:  # marine / general
            analysis, hypotheses = _analyze_marine(ocean_data)

        # LLM 增强推理（在规则结果基础上补充）
        if llm.configured() and (ocean_data or kept_docs):
            try:
                llm_hyps = _llm_enhance(state, analysis, domain)
                if llm_hyps:
                    hypotheses = llm_hyps
                    analysis["llm_enhanced"] = True
            except Exception as exc:
                log.warning("ReasoningNode LLM enhance failed: %s", exc)

    except Exception as exc:
        log.warning("ReasoningNode domain=%s failed: %s", domain, exc)
        analysis = {"error": str(exc)}

    trace.append({"node": "ReasoningNode", "domain": domain,
                  "hypotheses_count": len(hypotheses)})
    return {
        "domain_analysis": analysis,
        "risk_hypotheses": hypotheses,
        "trace": trace,
    }


# ── 海洋要素分析（marine/general） ────────────────────────────────────────────
def _analyze_marine(ocean_data: dict[str, Any]) -> tuple[dict, list]:
    from ocean_agents_demo.agents import _rule_based_risk
    hypotheses = _rule_based_risk(ocean_data)
    analysis = {
        "method": "threshold_rules",
        "variables_analyzed": [
            v.get("variable") for v in ocean_data.get("variables", []) if not v.get("error")
        ],
    }
    return analysis, hypotheses


# ── 观星分析 ──────────────────────────────────────────────────────────────────
def _analyze_stargazing(ocean_data: dict, bbox: dict) -> tuple[dict, list]:
    lat = (bbox.get("south", 0) + bbox.get("north", 0)) / 2
    lon = (bbox.get("west", 0) + bbox.get("east", 0)) / 2

    cloud = get_variable_stats(ocean_data, "cloud_fraction")
    aod = get_variable_stats(ocean_data, "light_aod")

    cloud_frac = cloud.get("mean") if cloud else None
    aod_val = aod.get("mean") if aod else None

    result = astro.assess_stargazing(
        lat=lat, lon=lon,
        cloud_fraction=cloud_frac,
        aod=aod_val,
    )
    moon = result["moon"]
    bortle = result["bortle"]
    transparency = result["transparency"]

    hypotheses = []
    if result["overall_score"] < 40:
        hypotheses.append({
            "signal": f"观星综合评分 {result['overall_score']}/100",
            "hypothesis": "当前时间/地点不适合深空天文观测",
            "basis": f"月相照明度 {moon['illumination']*100:.0f}%，Bortle {bortle['bortle_scale']} 级，透明度 {transparency['transparency_quality']}",
            "uncertainty": "云量数据可能不精确，实际条件以现场为准",
        })
    if bortle["bortle_scale"] <= 3:
        hypotheses.append({
            "signal": f"Bortle {bortle['bortle_scale']} 级（{bortle['bortle_description'][:30]}）",
            "hypothesis": "该海域光污染极低，适合专业天文观测或暗天空旅游",
            "basis": "距最近城市 {:.0f}km".format(bortle["nearest_city_km"]),
            "uncertainty": "季节性云量变化可能影响实际可观测天数",
        })

    analysis = {
        "method": "astro_assessment",
        "stargazing": result,
    }
    return analysis, hypotheses


# ── 海洋生物分析 ──────────────────────────────────────────────────────────────
def _analyze_biology(ocean_data: dict) -> tuple[dict, list]:
    sst_stats = get_variable_stats(ocean_data, "sst")
    chl_stats = get_variable_stats(ocean_data, "chlorophyll")
    sal_stats = get_variable_stats(ocean_data, "salinity")

    sst_mean = sst_stats.get("mean") if sst_stats else None
    sst_max = sst_stats.get("max") if sst_stats else None
    chl_mean = chl_stats.get("mean") if chl_stats else None
    sal_mean = sal_stats.get("mean") if sal_stats else None

    result = biology.full_marine_biology_assessment(
        sst_mean=sst_mean, sst_max=sst_max,
        chlorophyll_mean=chl_mean, salinity_mean=sal_mean,
    )

    hypotheses = []
    coral = result["coral_bleaching"]
    if coral.get("risk_score", 0) >= 0.3:
        hypotheses.append({
            "signal": "珊瑚白化风险 " + coral["risk_level"],
            "hypothesis": "当前温度/盐度/叶绿素条件对珊瑚礁有压力",
            "basis": "; ".join(coral.get("alerts", [])[:2]),
            "uncertainty": "需结合历史最大月均温（MMM）计算 DHW 确认",
        })
    hab = result["harmful_algal_bloom"]
    if hab.get("risk_score", 0) >= 0.4:
        hypotheses.append({
            "signal": "有害藻华风险 " + hab["risk_level"],
            "hypothesis": "叶绿素与温度条件有利于藻华暴发",
            "basis": "; ".join(hab.get("alerts", [])[:2]),
            "uncertainty": "HAB 物种多样，需现场采样确认种类",
        })

    suitability = result["habitat_suitability"]
    at_risk = suitability.get("at_risk", [])
    if at_risk:
        hypotheses.append({
            "signal": f"{', '.join(at_risk)} 适宜性低",
            "hypothesis": f"当前海况对 {', '.join(at_risk)} 栖息地不适宜",
            "basis": "综合 SST/叶绿素/盐度栖息地模型",
            "uncertainty": "物种分布受多因素影响，启发式模型仅供参考",
        })

    analysis = {"method": "biology_assessment", "biology": result}
    return analysis, hypotheses


# ── 航行安全分析 ──────────────────────────────────────────────────────────────
def _analyze_navigation(ocean_data: dict) -> tuple[dict, list]:
    wave_stats = get_variable_stats(ocean_data, "wave_height")
    period_stats = get_variable_stats(ocean_data, "swell_period")
    wind_stats = get_variable_stats(ocean_data, "wind_speed")
    vis_stats = get_variable_stats(ocean_data, "visibility")

    wave_mean = wave_stats.get("mean") if wave_stats else None
    period_mean = period_stats.get("mean") if period_stats else None
    wind_mean = wind_stats.get("mean") if wind_stats else None
    vis_mean = vis_stats.get("mean") if vis_stats else None

    result = navigation.assess_navigation_safety(
        wave_height_m=wave_mean,
        swell_period_s=period_mean,
        wind_speed_ms=wind_mean,
        visibility_km=vis_mean,
    )

    hypotheses = []
    score = result.get("overall_safety_score", 100)
    if score < 60:
        hypotheses.append({
            "signal": f"航行安全评分 {score}/100",
            "hypothesis": "当前海况对部分船型存在显著安全风险",
            "basis": f"综合浪高/风力/能见度，整体评级：{result.get('overall_verdict','')}",
            "uncertainty": "均值数据不能反映极值波高，建议参考波高分位数",
        })
    danger_vessels = [v["vessel_type"] for v in result.get("vessel_risk", [])
                      if v.get("risk_level") == "危险"]
    if danger_vessels:
        hypotheses.append({
            "signal": f"{', '.join(danger_vessels)} 风险等级：危险",
            "hypothesis": "上述船型在当前海况下存在安全隐患，建议返港或避风",
            "basis": "; ".join(
                f for v in result.get("vessel_risk", [])
                if v.get("risk_level") == "危险"
                for f in v.get("risk_flags", [])
            )[:200],
            "uncertainty": "波高均值可能低估极端情况",
        })
    if result.get("steep_wave_warning"):
        hypotheses.append({
            "signal": result["steep_wave_warning"],
            "hypothesis": "险浪风险：波陡超过破碎临界值，可能出现异常大浪",
            "basis": "波陡 = 波高/波长，超 1/7 时波浪趋于破碎",
            "uncertainty": "需结合实测数据确认",
        })

    analysis = {"method": "navigation_assessment", "navigation": result}
    return analysis, hypotheses


# ── LLM 增强推理 ──────────────────────────────────────────────────────────────
def _llm_enhance(
    state: GeoAgentState,
    analysis: dict[str, Any],
    domain: str,
) -> list[dict[str, Any]]:
    ocean_data = state.get("ocean_data", {})
    kept_docs = state.get("kept_docs", [])

    numbers = _format_ocean_summary(ocean_data)
    evidence = "\n".join(f"- {d.get('title','')}" for d in kept_docs[:6]) or "（无保留证据）"

    domain_hints = {
        "marine":     "海洋热浪、珊瑚白化、富营养化、海平面变化、渔业风险",
        "stargazing": "观星条件、暗天空保护、天文旅游适宜性",
        "biology":    "珊瑚礁健康、鱼群分布、藻华风险、渔业管理",
        "navigation": "航行安全、船只风险、港口管理、气象保障",
        "general":    "海洋环境综合评估",
    }

    messages = [
        {
            "role": "system",
            "content": (
                "你是海洋地理分析助手。结合数值与证据，输出 JSON："
                '{"hypotheses":[{"signal":"触发信号","hypothesis":"风险/机会假设",'
                '"basis":"依据","uncertainty":"不确定性"}]}。'
                f"聚焦领域：{domain_hints.get(domain, '')}。只输出 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题：{state.get('question','')}\n\n"
                f"区域数值：\n{numbers}\n\n"
                f"规则分析已触发假设数：{len(state.get('risk_hypotheses', []))}\n"
                f"文献证据：\n{evidence}"
            ),
        },
    ]
    data = llm.chat_json(messages)
    hyps = data.get("hypotheses") if isinstance(data, dict) else data
    out = []
    for h in hyps or []:
        if isinstance(h, dict):
            out.append({k: str(h.get(k, "")) for k in ("signal", "hypothesis", "basis", "uncertainty")})
    return out


def _format_ocean_summary(ocean_data: dict) -> str:
    rows = []
    for item in ocean_data.get("variables", []):
        if item.get("error"):
            continue
        s = item.get("stats") or {}
        rows.append(
            f"- {item.get('long_name') or item.get('variable')} ({item.get('variable')})："
            f"均值 {s.get('mean')}, 范围 {s.get('min')}~{s.get('max')}"
        )
    return "\n".join(rows) or "（无数值数据）"
