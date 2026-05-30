"""海洋生物分析工具。

基于海洋要素数值（SST、叶绿素、盐度）推断：
- 珊瑚白化风险（Degree Heating Weeks 简化版）
- 鱼群栖息地适宜性（多变量索引）
- 有害藻华（HAB）风险
- 海洋生物多样性指数（启发式）
"""
from __future__ import annotations

from typing import Any


# ── 珊瑚白化 ─────────────────────────────────────────────────────────────────
# NOAA Coral Reef Watch 阈值：热带珊瑚 bleaching threshold 一般在 Bleaching Alert 1 = 1°C above MMM
# 简化：用 SST 均值直接与阈值比较

CORAL_BLEACHING_THRESHOLD = 28.5  # °C，超过即有风险（热带区域约值）
CORAL_SEVERE_THRESHOLD = 30.0     # °C，严重白化

CORAL_SUITABLE_TEMP = (24.0, 28.5)    # 珊瑚适宜温度范围
CORAL_SUITABLE_SAL = (34.0, 36.5)     # 珊瑚适宜盐度范围


def assess_coral_bleaching(
    sst_mean: float | None,
    sst_max: float | None = None,
    salinity_mean: float | None = None,
    chlorophyll_mean: float | None = None,
) -> dict[str, Any]:
    """评估珊瑚白化风险。"""
    if sst_mean is None:
        return {"risk": "unknown", "reason": "缺少 SST 数据"}

    risk_level = "低"
    alerts = []
    risk_score = 0.0

    # 温度分析
    if sst_mean >= CORAL_SEVERE_THRESHOLD:
        risk_level = "极高"
        risk_score += 0.9
        alerts.append(f"SST 均值 {sst_mean:.1f}°C 超过严重白化阈值 {CORAL_SEVERE_THRESHOLD}°C")
    elif sst_mean >= CORAL_BLEACHING_THRESHOLD:
        risk_level = "高"
        risk_score += 0.6
        alerts.append(f"SST 均值 {sst_mean:.1f}°C 超过白化阈值 {CORAL_BLEACHING_THRESHOLD}°C")
    elif sst_mean >= CORAL_BLEACHING_THRESHOLD - 1:
        risk_level = "中等"
        risk_score += 0.3
        alerts.append(f"SST 均值 {sst_mean:.1f}°C 接近白化阈值（{CORAL_BLEACHING_THRESHOLD}°C）")

    if sst_max is not None and sst_max >= CORAL_SEVERE_THRESHOLD:
        risk_score = min(1.0, risk_score + 0.2)
        alerts.append(f"局部最高温 {sst_max:.1f}°C 超过严重白化阈值")

    # 盐度分析
    if salinity_mean is not None:
        if salinity_mean < CORAL_SUITABLE_SAL[0] or salinity_mean > CORAL_SUITABLE_SAL[1]:
            risk_score = min(1.0, risk_score + 0.15)
            alerts.append(f"盐度 {salinity_mean:.1f} psu 偏离珊瑚适宜范围 {CORAL_SUITABLE_SAL}")

    # 叶绿素（高叶绿素→富营养化→藻类竞争→珊瑚压力）
    if chlorophyll_mean is not None and chlorophyll_mean > 0.5:
        risk_score = min(1.0, risk_score + 0.1)
        alerts.append(f"叶绿素 {chlorophyll_mean:.2f} mg/m³ 偏高，藻类竞争压力增加")

    # 最终风险等级
    if risk_score >= 0.8:
        risk_level = "极高"
    elif risk_score >= 0.55:
        risk_level = "高"
    elif risk_score >= 0.3:
        risk_level = "中等"
    elif risk_score > 0:
        risk_level = "低"
    else:
        risk_level = "极低"

    recovery_hint = ""
    if risk_score >= 0.6:
        recovery_hint = "建议实施珊瑚礁监测，减少潜水扰动，限制渔业活动"
    elif risk_score >= 0.3:
        recovery_hint = "建议加强水质监测，关注 SST 距平变化"

    return {
        "risk_level": risk_level,
        "risk_score": round(risk_score, 3),
        "alerts": alerts,
        "recommendation": recovery_hint,
        "inputs": {
            "sst_mean": sst_mean,
            "sst_max": sst_max,
            "salinity_mean": salinity_mean,
            "chlorophyll_mean": chlorophyll_mean,
        },
    }


# ── 有害藻华（HAB）风险 ────────────────────────────────────────────────────────
HAB_CHLOROPHYLL_THRESHOLD = 1.0   # mg/m³，超过即关注
HAB_HIGH_THRESHOLD = 5.0          # mg/m³，高风险

HAB_FAVORABLE_TEMP = (20.0, 30.0)  # HAB 最适温度

def assess_harmful_algal_bloom(
    chlorophyll_mean: float | None,
    sst_mean: float | None = None,
    salinity_mean: float | None = None,
) -> dict[str, Any]:
    """评估有害藻华（HAB）风险。"""
    if chlorophyll_mean is None:
        return {"risk": "unknown", "reason": "缺少叶绿素数据"}

    risk_score = 0.0
    alerts = []

    if chlorophyll_mean >= HAB_HIGH_THRESHOLD:
        risk_score += 0.8
        alerts.append(f"叶绿素 {chlorophyll_mean:.2f} mg/m³ 极高，极有可能已发生藻华")
    elif chlorophyll_mean >= HAB_CHLOROPHYLL_THRESHOLD:
        risk_score += 0.45
        alerts.append(f"叶绿素 {chlorophyll_mean:.2f} mg/m³ 超过藻华关注阈值 {HAB_CHLOROPHYLL_THRESHOLD}")
    elif chlorophyll_mean >= 0.5:
        risk_score += 0.2
        alerts.append(f"叶绿素 {chlorophyll_mean:.2f} mg/m³ 偏高，需持续监测")

    if sst_mean is not None and HAB_FAVORABLE_TEMP[0] <= sst_mean <= HAB_FAVORABLE_TEMP[1]:
        risk_score = min(1.0, risk_score + 0.15)
        alerts.append(f"SST {sst_mean:.1f}°C 处于藻华最适温度范围")

    if salinity_mean is not None and salinity_mean < 32:
        risk_score = min(1.0, risk_score + 0.1)
        alerts.append(f"盐度偏低（{salinity_mean:.1f}），可能受淡水/径流输入影响，促进藻华")

    risk_score = round(risk_score, 3)
    if risk_score >= 0.7:
        level = "高"
    elif risk_score >= 0.4:
        level = "中等"
    elif risk_score > 0:
        level = "低"
    else:
        level = "极低"

    return {
        "risk_level": level,
        "risk_score": risk_score,
        "alerts": alerts,
        "impacts": _hab_impacts(risk_score),
    }


def _hab_impacts(score: float) -> list[str]:
    if score >= 0.7:
        return ["水产养殖大规模受损风险", "贝类毒素蓄积风险", "鱼类死亡事件可能", "饮用水安全威胁（近岸湖库）"]
    if score >= 0.4:
        return ["近海渔业产量下降", "旅游水域关闭风险", "需实施水质预警"]
    return ["日常监测即可"]


# ── 鱼群栖息地适宜性 ──────────────────────────────────────────────────────────
SPECIES_PROFILES: dict[str, dict[str, Any]] = {
    "金枪鱼": {"temp": (20, 30), "sal": (33, 37), "chl_max": 2.0},
    "沙丁鱼": {"temp": (14, 22), "sal": (33, 36), "chl_min": 0.3},
    "鲭鱼":   {"temp": (10, 24), "sal": (32, 36), "chl_min": 0.2},
    "珊瑚鱼": {"temp": (24, 29), "sal": (34, 37), "chl_max": 0.5},
    "虾蟹类": {"temp": (18, 28), "sal": (20, 35), "chl_min": 0.1},
    "海豚/鲸鱼": {"temp": (10, 28), "sal": (32, 37), "chl_min": 0.15},
}


def assess_habitat_suitability(
    sst_mean: float | None,
    chlorophyll_mean: float | None = None,
    salinity_mean: float | None = None,
) -> dict[str, Any]:
    """评估各类海洋生物的栖息地适宜性。"""
    results = []
    for species, profile in SPECIES_PROFILES.items():
        score = 1.0
        reasons = []

        if sst_mean is not None:
            tmin, tmax = profile["temp"]
            if sst_mean < tmin:
                delta = tmin - sst_mean
                score -= min(0.5, delta * 0.08)
                reasons.append(f"水温偏低（{sst_mean:.1f}°C < {tmin}°C）")
            elif sst_mean > tmax:
                delta = sst_mean - tmax
                score -= min(0.5, delta * 0.08)
                reasons.append(f"水温偏高（{sst_mean:.1f}°C > {tmax}°C）")

        if salinity_mean is not None:
            smin, smax = profile["sal"]
            if salinity_mean < smin or salinity_mean > smax:
                score -= 0.2
                reasons.append(f"盐度 {salinity_mean:.1f} 超出适宜范围 {smin}~{smax}")

        if chlorophyll_mean is not None:
            if "chl_min" in profile and chlorophyll_mean < profile["chl_min"]:
                score -= 0.15
                reasons.append(f"叶绿素偏低（初级生产力不足）")
            if "chl_max" in profile and chlorophyll_mean > profile["chl_max"]:
                score -= 0.2
                reasons.append(f"叶绿素过高（水质富营养化）")

        score = max(0.0, round(score, 3))
        results.append({
            "species": species,
            "suitability_score": score,
            "suitability_level": _score_to_level(score),
            "issues": reasons,
        })

    results.sort(key=lambda x: x["suitability_score"], reverse=True)
    return {
        "species_suitability": results,
        "most_suitable": [r["species"] for r in results if r["suitability_score"] >= 0.7],
        "at_risk": [r["species"] for r in results if r["suitability_score"] < 0.4],
    }


def _score_to_level(score: float) -> str:
    if score >= 0.8:
        return "非常适宜"
    if score >= 0.6:
        return "适宜"
    if score >= 0.4:
        return "一般"
    if score >= 0.2:
        return "不适宜"
    return "极不适宜"


# ── 综合生物评估 ──────────────────────────────────────────────────────────────
def full_marine_biology_assessment(
    sst_mean: float | None = None,
    sst_max: float | None = None,
    chlorophyll_mean: float | None = None,
    salinity_mean: float | None = None,
) -> dict[str, Any]:
    """一次性调用三个子评估，返回综合报告。"""
    return {
        "coral_bleaching": assess_coral_bleaching(sst_mean, sst_max, salinity_mean, chlorophyll_mean),
        "harmful_algal_bloom": assess_harmful_algal_bloom(chlorophyll_mean, sst_mean, salinity_mean),
        "habitat_suitability": assess_habitat_suitability(sst_mean, chlorophyll_mean, salinity_mean),
    }


# ── Tool Spec ──────────────────────────────────────────────────────────────────
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "marine_biology_assessment",
        "description": (
            "综合评估海洋生物状况：珊瑚白化风险、有害藻华（HAB）风险、"
            "各鱼种栖息地适宜性。需提供 SST/叶绿素/盐度均值。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sst_mean": {"type": "number", "description": "海表温度均值（°C）"},
                "sst_max": {"type": "number", "description": "海表温度最大值（°C），可选"},
                "chlorophyll_mean": {"type": "number", "description": "叶绿素均值（mg/m³）"},
                "salinity_mean": {"type": "number", "description": "盐度均值（psu）"},
            },
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "marine_biology_assessment":
        return full_marine_biology_assessment(
            sst_mean=args.get("sst_mean"),
            sst_max=args.get("sst_max"),
            chlorophyll_mean=args.get("chlorophyll_mean"),
            salinity_mean=args.get("salinity_mean"),
        )
    raise KeyError(f"unknown biology tool: {name}")
