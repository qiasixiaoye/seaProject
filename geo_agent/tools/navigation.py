"""航行安全分析工具。

基于海浪、风速、能见度、洋流等要素评估船只航行安全：
- 道格拉斯海况（Douglas Sea Scale）
- 蒲福风级（Beaufort Scale）
- 综合航行安全评分
- 分船型风险建议
"""
from __future__ import annotations

import math
from typing import Any


# ── 道格拉斯海况等级 ────────────────────────────────────────────────────────
DOUGLAS_SCALE = [
    (0.0,  0,   "平浪",     "无浪"),
    (0.1,  1,   "微浪",     "波纹，无泡沫"),
    (0.5,  2,   "小浪",     "小波，波顶光亮"),
    (1.25, 3,   "轻浪",     "大波，波顶泡沫"),
    (2.5,  4,   "中浪",     "中等波高，白浪普遍"),
    (4.0,  5,   "大浪",     "海浪较高，浪花纷飞"),
    (6.0,  6,   "较大浪",   "大浪，浪峰拉长"),
    (9.0,  7,   "高浪",     "汹涌大浪"),
    (14.0, 8,   "很高浪",   "异常高浪，能见度受影响"),
    (float('inf'), 9, "狂涛", "极端海况，无法航行"),
]


def douglas_sea_state(wave_height_m: float) -> dict[str, Any]:
    """根据有效波高计算道格拉斯海况等级。"""
    for threshold, scale, name, desc in DOUGLAS_SCALE:
        if wave_height_m < threshold:
            # 找到上一级
            idx = DOUGLAS_SCALE.index((threshold, scale, name, desc))
            if idx == 0:
                s = DOUGLAS_SCALE[0]
            else:
                s = DOUGLAS_SCALE[idx - 1]
            return {
                "wave_height_m": round(wave_height_m, 2),
                "douglas_scale": s[1],
                "state_name": s[2],
                "description": s[3],
            }
    last = DOUGLAS_SCALE[-1]
    return {
        "wave_height_m": round(wave_height_m, 2),
        "douglas_scale": last[1],
        "state_name": last[2],
        "description": last[3],
    }


# ── 蒲福风级 ─────────────────────────────────────────────────────────────────
BEAUFORT_SCALE = [
    (0.3,  0, "无风",    "静止"),
    (1.5,  1, "软风",    "烟能飘动"),
    (3.3,  2, "轻风",    "脸感有风"),
    (5.4,  3, "微风",    "旗帜展开"),
    (7.9,  4, "和风",    "沙尘吹起"),
    (10.7, 5, "劲风",    "小树摇摆"),
    (13.8, 6, "强风",    "大树摇摆"),
    (17.1, 7, "疾风",    "迎风步行困难"),
    (20.7, 8, "大风",    "折断树枝"),
    (24.4, 9, "烈风",    "轻微建筑损坏"),
    (28.4, 10, "狂风",   "陆地罕见，连根拔树"),
    (32.6, 11, "暴风",   "大范围破坏"),
    (float('inf'), 12, "飓风", "极端破坏"),
]


def beaufort_wind(wind_speed_ms: float) -> dict[str, Any]:
    """根据风速（m/s）计算蒲福风级。"""
    for threshold, scale, name, desc in BEAUFORT_SCALE:
        if wind_speed_ms < threshold:
            idx = BEAUFORT_SCALE.index((threshold, scale, name, desc))
            s = BEAUFORT_SCALE[max(0, idx - 1)]
            return {
                "wind_speed_ms": round(wind_speed_ms, 1),
                "wind_speed_knots": round(wind_speed_ms * 1.944, 1),
                "beaufort_scale": s[1],
                "beaufort_name": s[2],
                "description": s[3],
            }
    last = BEAUFORT_SCALE[-1]
    return {
        "wind_speed_ms": round(wind_speed_ms, 1),
        "wind_speed_knots": round(wind_speed_ms * 1.944, 1),
        "beaufort_scale": last[1],
        "beaufort_name": last[2],
        "description": last[3],
    }


# ── 能见度评估 ───────────────────────────────────────────────────────────────
def visibility_assessment(visibility_km: float | None) -> dict[str, Any]:
    """评估海上能见度。"""
    if visibility_km is None:
        return {"level": "unknown", "safe_speed_knots": None}
    if visibility_km >= 10:
        level, safe_speed = "优良", 20
    elif visibility_km >= 5:
        level, safe_speed = "良好", 16
    elif visibility_km >= 2:
        level, safe_speed = "一般", 10
    elif visibility_km >= 0.5:
        level, safe_speed = "较差（浓雾预警）", 6
    else:
        level, safe_speed = "极差（雾盲）", 3
    return {
        "visibility_km": round(visibility_km, 1),
        "level": level,
        "safe_speed_knots": safe_speed,
        "requires_fog_signal": visibility_km < 5,
        "requires_radar_watch": visibility_km < 2,
    }


# ── 船型风险矩阵 ─────────────────────────────────────────────────────────────
VESSEL_LIMITS: dict[str, dict[str, Any]] = {
    "小型渔船（<12m）": {
        "max_wave_m": 1.5,
        "max_beaufort": 5,
        "min_visibility_km": 2.0,
        "description": "需立即返港（浪高 >1.5m 或风力 ≥6 级）",
    },
    "中型渔船（12-24m）": {
        "max_wave_m": 3.0,
        "max_beaufort": 7,
        "min_visibility_km": 1.0,
        "description": "应谨慎操作（浪高 >3m 或风力 ≥8 级 应避风）",
    },
    "大型渔船（>24m）": {
        "max_wave_m": 5.0,
        "max_beaufort": 9,
        "min_visibility_km": 0.5,
        "description": "注意货物固定，极端海况返港",
    },
    "集装箱船/货轮": {
        "max_wave_m": 6.0,
        "max_beaufort": 10,
        "min_visibility_km": 0.2,
        "description": "按航运规程操作，注意横摇稳定性",
    },
    "游艇/帆船（<15m）": {
        "max_wave_m": 2.0,
        "max_beaufort": 6,
        "min_visibility_km": 2.0,
        "description": "业余航行者在浪高 >1.5m 时建议靠港",
    },
    "科考船": {
        "max_wave_m": 4.0,
        "max_beaufort": 8,
        "min_visibility_km": 0.5,
        "description": "极端海况暂停采样作业",
    },
}


def vessel_risk_matrix(
    wave_height_m: float | None,
    beaufort_scale: int | None,
    visibility_km: float | None,
) -> list[dict[str, Any]]:
    """针对各类船型评估当前海况的风险等级。"""
    results = []
    for vessel, limits in VESSEL_LIMITS.items():
        risk_flags = []
        overall = "安全"

        if wave_height_m is not None and wave_height_m > limits["max_wave_m"]:
            risk_flags.append(f"浪高 {wave_height_m:.1f}m 超过限制 {limits['max_wave_m']}m")
            overall = "危险"
        elif wave_height_m is not None and wave_height_m > limits["max_wave_m"] * 0.75:
            risk_flags.append(f"浪高 {wave_height_m:.1f}m 接近限制（{limits['max_wave_m']}m），需警惕")
            if overall == "安全":
                overall = "警告"

        if beaufort_scale is not None and beaufort_scale > limits["max_beaufort"]:
            risk_flags.append(f"风力 {beaufort_scale} 级超过限制 {limits['max_beaufort']} 级")
            overall = "危险"
        elif beaufort_scale is not None and beaufort_scale >= limits["max_beaufort"]:
            risk_flags.append(f"风力 {beaufort_scale} 级已达限制")
            if overall == "安全":
                overall = "警告"

        if visibility_km is not None and visibility_km < limits["min_visibility_km"]:
            risk_flags.append(f"能见度 {visibility_km:.1f}km 低于最低要求 {limits['min_visibility_km']}km")
            if overall == "安全":
                overall = "警告"

        results.append({
            "vessel_type": vessel,
            "risk_level": overall,
            "risk_flags": risk_flags,
            "operating_limit": limits["description"],
        })
    return results


# ── 综合航行安全评估 ──────────────────────────────────────────────────────────
def assess_navigation_safety(
    wave_height_m: float | None = None,
    swell_period_s: float | None = None,
    wind_speed_ms: float | None = None,
    visibility_km: float | None = None,
    current_speed_ms: float | None = None,
) -> dict[str, Any]:
    """综合海况评估，返回各船型风险矩阵 + 整体安全评分。"""
    components = {}

    # 海况
    if wave_height_m is not None:
        components["sea_state"] = douglas_sea_state(wave_height_m)
    else:
        components["sea_state"] = None

    # 风级
    if wind_speed_ms is not None:
        components["wind"] = beaufort_wind(wind_speed_ms)
    else:
        components["wind"] = None

    # 能见度
    components["visibility"] = visibility_assessment(visibility_km)

    # 波陡（险浪判断）
    steep_wave_warning = None
    if wave_height_m is not None and swell_period_s is not None and swell_period_s > 0:
        wavelength = 1.56 * swell_period_s ** 2  # 深水近似
        steepness = wave_height_m / wavelength
        if steepness > 1 / 7:
            steep_wave_warning = f"波陡 {steepness:.3f} 超过破碎临界值（1/7），存在险浪"
    components["steep_wave_warning"] = steep_wave_warning

    # 洋流
    if current_speed_ms is not None and current_speed_ms > 2.5:
        components["current_warning"] = f"洋流速度 {current_speed_ms:.1f} m/s 偏强，注意偏流"
    else:
        components["current_warning"] = None

    # 船型风险矩阵
    bf_scale = components["wind"]["beaufort_scale"] if components["wind"] else None
    components["vessel_risk"] = vessel_risk_matrix(wave_height_m, bf_scale, visibility_km)

    # 整体安全评分 0~100
    safety = 100.0
    if wave_height_m is not None:
        safety -= min(50, wave_height_m * 8)
    if wind_speed_ms is not None:
        safety -= min(30, wind_speed_ms * 1.5)
    if visibility_km is not None and visibility_km < 10:
        safety -= max(0, (10 - visibility_km) * 1.5)
    if steep_wave_warning:
        safety -= 15
    safety = max(0.0, round(safety, 1))

    if safety >= 80:
        overall = "优良"
    elif safety >= 60:
        overall = "良好"
    elif safety >= 40:
        overall = "中等（谨慎操作）"
    elif safety >= 20:
        overall = "较差（高风险）"
    else:
        overall = "极差（禁止出海）"

    components["overall_safety_score"] = safety
    components["overall_verdict"] = overall
    return components


# ── Tool Spec ──────────────────────────────────────────────────────────────────
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "assess_navigation_safety",
        "description": (
            "综合评估海上航行安全，包括道格拉斯海况、蒲福风级、能见度、"
            "各类船型风险矩阵及整体安全评分。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "wave_height_m": {"type": "number", "description": "有效波高（米）"},
                "swell_period_s": {"type": "number", "description": "涌浪周期（秒）"},
                "wind_speed_ms": {"type": "number", "description": "风速（m/s）"},
                "visibility_km": {"type": "number", "description": "能见度（公里）"},
                "current_speed_ms": {"type": "number", "description": "洋流速度（m/s），可选"},
            },
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "assess_navigation_safety":
        return assess_navigation_safety(
            wave_height_m=args.get("wave_height_m"),
            swell_period_s=args.get("swell_period_s"),
            wind_speed_ms=args.get("wind_speed_ms"),
            visibility_km=args.get("visibility_km"),
            current_speed_ms=args.get("current_speed_ms"),
        )
    raise KeyError(f"unknown navigation tool: {name}")
