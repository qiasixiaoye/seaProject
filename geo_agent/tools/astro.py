"""观星适宜性工具。

不依赖 ephem/astropy 时全部用纯 Python 数学实现：
- 月相计算（新月→满月，基于朔望月周期）
- 月亮高度角（简化版，用于判断月光干扰时段）
- 天空质量估算（Bortle 量表，基于城市距离启发式）
- 大气透明度（基于云量 + 气溶胶光学厚度）
- 最优观测窗口判断
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


# ── 月相 ────────────────────────────────────────────────────────────────────
SYNODIC_MONTH = 29.53058867   # 平均朔望月（天）
KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)  # J2000 附近新月

PHASE_NAMES = {
    0: "新月",
    1: "峨眉月",
    2: "上弦月",
    3: "盈凸月",
    4: "满月",
    5: "亏凸月",
    6: "下弦月",
    7: "残月",
}


def moon_phase(dt: datetime | None = None) -> dict[str, Any]:
    """计算给定时刻的月相。

    Returns:
        phase_name: 月相名称
        illumination: 照明度 0~1
        age_days: 月龄（距上次新月天数）
        is_dark: 是否适合观星（照明 < 0.35）
    """
    if dt is None:
        dt = datetime.now(tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    elapsed = (dt - KNOWN_NEW_MOON).total_seconds() / 86400
    age = elapsed % SYNODIC_MONTH
    phase_frac = age / SYNODIC_MONTH  # 0=新月, 0.5=满月

    # illumination ≈ (1 - cos(2π·phase_frac)) / 2
    illumination = (1 - math.cos(2 * math.pi * phase_frac)) / 2

    # 8 phase name index
    phase_idx = int((phase_frac * 8 + 0.5) % 8)
    phase_name = PHASE_NAMES.get(phase_idx, "未知")

    return {
        "phase_name": phase_name,
        "illumination": round(illumination, 3),
        "age_days": round(age, 1),
        "is_dark_moon": illumination < 0.35,
        "new_moon_in_days": round(SYNODIC_MONTH - age, 1) if age > SYNODIC_MONTH / 2 else round(-age, 1),
    }


# ── 月亮高度角（简化） ────────────────────────────────────────────────────────
def moon_altitude_approx(dt: datetime, lat: float, lon: float) -> float:
    """返回月亮近似高度角（度）。负值表示在地平线以下。

    算法简化：使用月亮黄经黄纬 + 球面天文坐标变换。
    精度约 ±5°，足够判断月光是否干扰观测。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Julian Day Number
    jd = (dt - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400 + 2451545.0

    # 月亮黄经（简化）
    L = (218.316 + 13.176396 * jd) % 360
    M = (134.963 + 13.064993 * jd) % 360  # 近地点平近点角
    F = (93.272 + 13.229350 * jd) % 360   # 月亮纬度参数

    M_r = math.radians(M)
    F_r = math.radians(F)

    lon_moon = L + 6.289 * math.sin(M_r)  # 月亮黄经（度）
    lat_moon = 5.128 * math.sin(F_r)       # 月亮黄纬（度）

    # 转赤道坐标（简化，使用黄赤交角 23.4349°）
    eps = math.radians(23.4349)
    lon_r = math.radians(lon_moon)
    lat_r = math.radians(lat_moon)

    dec = math.degrees(math.asin(
        math.sin(lat_r) * math.cos(eps) + math.cos(lat_r) * math.sin(eps) * math.sin(lon_r)
    ))
    ra_deg = math.degrees(math.atan2(
        math.sin(lon_r) * math.cos(eps) - math.tan(lat_r) * math.sin(eps),
        math.cos(lon_r),
    )) % 360

    # 恒星时 → 时角
    gst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360
    ha = (gst + lon - ra_deg) % 360
    if ha > 180:
        ha -= 360

    # 高度角
    lat_r_obs = math.radians(lat)
    dec_r = math.radians(dec)
    ha_r = math.radians(ha)
    altitude = math.degrees(math.asin(
        math.sin(lat_r_obs) * math.sin(dec_r)
        + math.cos(lat_r_obs) * math.cos(dec_r) * math.cos(ha_r)
    ))
    return round(altitude, 1)


# ── 光污染估算（Bortle 量表） ─────────────────────────────────────────────────
# 主要城市中心坐标（lat, lon, population_millions）
MAJOR_CITIES = [
    (39.9042, 116.4074, 21.5),   # 北京
    (31.2304, 121.4737, 24.2),   # 上海
    (23.1291, 113.2644, 15.3),   # 广州
    (22.5431, 114.0579, 17.6),   # 深圳
    (30.5728, 104.0668, 9.0),    # 成都
    (25.0330, 121.5654, 7.0),    # 台北
    (22.3193, 114.1694, 7.5),    # 香港
    (35.6762, 139.6503, 37.4),   # 东京
    (37.5665, 126.9780, 9.7),    # 首尔
    (1.3521, 103.8198, 5.7),     # 新加坡
    (13.7563, 100.5018, 10.5),   # 曼谷
    (14.0583, 108.2772, 1.0),    # 越南
    (41.0082, 28.9784, 15.0),    # 伊斯坦布尔（偏远对照）
    (-33.8688, 151.2093, 5.3),   # 悉尼
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def estimate_bortle_scale(lat: float, lon: float) -> dict[str, Any]:
    """基于与主要城市距离启发式估算 Bortle 量表 1~9。

    Bortle 1 = 最暗（荒野）, 9 = 市中心。
    """
    min_influence = 0.0
    nearest_city = None
    nearest_dist = 9999.0
    for clat, clon, pop in MAJOR_CITIES:
        dist_km = _haversine_km(lat, lon, clat, clon)
        # 影响强度：随距离衰减（城市规模越大影响范围越远）
        influence = pop / max(1.0, (dist_km / 50) ** 1.8)
        if influence > min_influence:
            min_influence = influence
            nearest_city = (clat, clon, pop)
        if dist_km < nearest_dist:
            nearest_dist = dist_km

    # 换算 Bortle（经验公式）
    if min_influence > 8:
        bortle = 9
    elif min_influence > 4:
        bortle = 8
    elif min_influence > 1.5:
        bortle = 7
    elif min_influence > 0.5:
        bortle = 6
    elif min_influence > 0.15:
        bortle = 5
    elif min_influence > 0.04:
        bortle = 4
    elif min_influence > 0.01:
        bortle = 3
    else:
        bortle = 2

    # 海上或高山区域可以到 1
    if nearest_dist > 300 and bortle <= 2:
        bortle = 1

    bortle_desc = {
        1: "黑暗天空（荒野），银河清晰可见，最佳观测条件",
        2: "真正黑暗，黄道光明显，M33 肉眼可见",
        3: "乡村天空，银河结构可辨，极佳条件",
        4: "乡村/近郊过渡，银河可见但轮廓稍模糊",
        5: "近郊天空，银河主干可见，部分区域受影响",
        6: "明亮近郊，银河仅核心可见，光污染明显",
        7: "郊区/城市过渡，银河极难辨认",
        8: "城市天空，银河完全不可见，只能看到明亮星体",
        9: "市中心，月亮和亮行星可见，深空目标不可及",
    }
    return {
        "bortle_scale": bortle,
        "bortle_description": bortle_desc.get(bortle, ""),
        "light_pollution_level": _bortle_to_level(bortle),
        "nearest_city_km": round(nearest_dist, 0),
        "sky_brightness_mag_arcsec2": round(22.0 - (bortle - 1) * 1.5, 1),  # 近似 SQM
    }


def _bortle_to_level(bortle: int) -> str:
    if bortle <= 2:
        return "极低"
    if bortle <= 4:
        return "低"
    if bortle <= 6:
        return "中等"
    if bortle <= 7:
        return "较高"
    return "高"


# ── 天空透明度 ────────────────────────────────────────────────────────────────
def sky_transparency(
    cloud_fraction: float | None = None,
    aod: float | None = None,
    humidity_pct: float | None = None,
) -> dict[str, Any]:
    """估算大气透明度。

    cloud_fraction: 0~1（来自 NetCDF）
    aod: 气溶胶光学厚度（0.01~1.0 典型范围）
    humidity_pct: 相对湿度（0~100）
    """
    score = 1.0  # 满分
    factors = []

    if cloud_fraction is not None:
        cloud_penalty = cloud_fraction * 0.9
        score -= cloud_penalty
        factors.append(f"云量 {cloud_fraction*100:.0f}%（-{cloud_penalty:.2f}）")

    if aod is not None:
        aod_penalty = min(0.3, aod * 0.5)
        score -= aod_penalty
        factors.append(f"气溶胶 AOD={aod:.3f}（-{aod_penalty:.2f}）")

    if humidity_pct is not None and humidity_pct > 70:
        hum_penalty = (humidity_pct - 70) / 100 * 0.2
        score -= hum_penalty
        factors.append(f"湿度 {humidity_pct:.0f}%（-{hum_penalty:.2f}）")

    score = max(0.0, round(score, 3))

    if score >= 0.85:
        quality = "优秀"
    elif score >= 0.65:
        quality = "良好"
    elif score >= 0.40:
        quality = "一般"
    elif score >= 0.20:
        quality = "较差"
    else:
        quality = "很差"

    return {
        "transparency_score": score,
        "transparency_quality": quality,
        "penalty_factors": factors,
    }


# ── 综合观星评估 ──────────────────────────────────────────────────────────────
def assess_stargazing(
    lat: float,
    lon: float,
    dt: datetime | None = None,
    cloud_fraction: float | None = None,
    aod: float | None = None,
) -> dict[str, Any]:
    """综合评估某地某时刻的观星适宜性。"""
    if dt is None:
        dt = datetime.now(tz=timezone.utc)

    moon_info = moon_phase(dt)
    bortle_info = estimate_bortle_scale(lat, lon)
    transparency_info = sky_transparency(cloud_fraction=cloud_fraction, aod=aod)
    moon_alt = moon_altitude_approx(dt, lat, lon)

    # 综合评分 0~100
    score = 100.0
    # 月相影响
    score -= moon_info["illumination"] * 40
    # 光污染影响
    score -= (bortle_info["bortle_scale"] - 1) * 5
    # 透明度影响
    score -= (1 - transparency_info["transparency_score"]) * 30
    # 月亮在地平线以上额外扣分
    if moon_alt > 0:
        score -= min(15, moon_alt * 0.3)

    score = max(0.0, min(100.0, round(score, 1)))

    if score >= 80:
        verdict = "极佳"
    elif score >= 60:
        verdict = "良好"
    elif score >= 40:
        verdict = "一般"
    elif score >= 20:
        verdict = "较差"
    else:
        verdict = "不宜"

    # 可观测目标推荐
    targets = _recommend_targets(bortle_info["bortle_scale"], moon_info["illumination"])

    return {
        "location": {"lat": lat, "lon": lon},
        "datetime_utc": dt.strftime("%Y-%m-%d %H:%M UTC"),
        "overall_score": score,
        "verdict": verdict,
        "moon": {**moon_info, "altitude_deg": moon_alt},
        "bortle": bortle_info,
        "transparency": transparency_info,
        "recommended_targets": targets,
        "best_viewing_hours": _best_viewing_hint(moon_info),
    }


def _recommend_targets(bortle: int, moon_illumination: float) -> list[str]:
    targets = []
    if bortle <= 3 and moon_illumination < 0.3:
        targets += ["银河核心", "大麦哲伦云", "小麦哲伦云", "M31 仙女座星系", "球状星团 M13"]
    elif bortle <= 5 and moon_illumination < 0.5:
        targets += ["M42 猎户座星云", "M45 昴宿星团", "M44 蜂巢星团", "银河主干"]
    if moon_illumination < 0.7:
        targets += ["M31 仙女座星系（肉眼）", "行星（木星/土星/火星）", "双星"]
    targets += ["月球细节（月相≥30%）", "明亮行星", "人造卫星过境"]
    return list(dict.fromkeys(targets))[:6]


def _best_viewing_hint(moon_info: dict[str, Any]) -> str:
    age = moon_info["age_days"]
    if age < 3 or age > 27:
        return "今晚月光干扰极小，全夜均适合深空观测"
    elif age < 10:
        return f"月亮在 22:00 后落山，建议午夜后观测"
    elif age > 22:
        return f"月亮凌晨 01:00 后升起，建议前半夜观测"
    else:
        return "接近满月，建议避开深空，改观行星或月面细节"


# ── Tool Spec ─────────────────────────────────────────────────────────────────
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "assess_stargazing",
        "description": (
            "评估指定地点的观星适宜性，综合月相、光污染（Bortle 量表）、"
            "大气透明度（云量/气溶胶），返回评分与推荐目标。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "纬度（-90~90）"},
                "lon": {"type": "number", "description": "经度（-180~180）"},
                "cloud_fraction": {"type": "number", "description": "云量 0~1，可由 NetCDF 获取"},
                "aod": {"type": "number", "description": "气溶胶光学厚度，可选"},
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "get_moon_phase",
        "description": "获取当前或指定日期的月相信息（月相名、照明度、月龄）。",
        "parameters": {
            "type": "object",
            "properties": {
                "date_utc": {"type": "string", "description": "ISO 8601 格式日期时间，如 2025-08-15T21:00:00Z，留空用当前时刻"},
            },
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "assess_stargazing":
        return assess_stargazing(
            lat=float(args["lat"]),
            lon=float(args["lon"]),
            cloud_fraction=args.get("cloud_fraction"),
            aod=args.get("aod"),
        )
    if name == "get_moon_phase":
        dt = None
        if args.get("date_utc"):
            try:
                dt = datetime.fromisoformat(args["date_utc"].replace("Z", "+00:00"))
            except Exception:
                pass
        return moon_phase(dt)
    raise KeyError(f"unknown astro tool: {name}")
