"""海洋要素工具 — 封装 NetCDF 区域统计与变量查询。

在旧工具基础上增加：
- 云量 (cloud_fraction) 查询
- 能见度 (visibility) 估算
- 多变量批量查询
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("geo_agent.tools.ocean")

# ── 变量别名映射（用户可用直觉名称） ───────────────────────────────────────
ALIAS: dict[str, list[str]] = {
    "sst":            ["sst", "sea_surface_temperature", "analysed_sst", "thetao"],
    "chlorophyll":    ["chlor_a", "chlorophyll", "chl", "chla", "chlor"],
    "salinity":       ["sss", "salinity", "so", "psal", "sal"],
    "wave_height":    ["wave_height", "Thgt", "thgt", "significant_height_of_wind_and_swell_waves",
                       "vhm0", "hs", "swh"],
    "swell_period":   ["swell_period", "sper", "tm02", "t02", "mwp"],
    "wind_speed":     ["wind_speed", "wind", "u10", "v10", "ws"],
    "cloud_fraction": ["cloud_fraction", "cloud_cover", "clt", "tcc"],
    "visibility":     ["vis", "visibility", "visib"],
    "light_aod":      ["aod", "aerosol_optical_depth", "aod550"],
}

PRIORITY_VARS = ["sst", "chlorophyll", "salinity", "wave_height", "swell_period",
                 "wind_speed", "cloud_fraction"]


def resolve_variable_name(alias: str) -> str:
    """将用户别名解析为可用的 NC 变量名。"""
    try:
        from ocean_agents_demo.nc_data import list_variables
        available = {v["name"] for v in list_variables().get("variables", [])}
        by_lower = {str(name).lower(): str(name) for name in available}
    except Exception:
        available = set()
        by_lower = {}

    # 先直接尝试
    if alias in available:
        return alias
    if alias.lower() in by_lower:
        return by_lower[alias.lower()]
    # 再走别名表
    candidates = ALIAS.get(alias, [alias])
    for c in candidates:
        if c in available:
            return c
        if c.lower() in by_lower:
            return by_lower[c.lower()]
    return alias   # 原样返回，让 nc_data 自己报错


def query_variable(
    variable: str,
    bbox: dict[str, float],
    dataset: str = "",
) -> dict[str, Any]:
    """查询单个变量的区域统计。"""
    from ocean_agents_demo.tools import query_ocean_region
    resolved = resolve_variable_name(variable)
    try:
        result = query_ocean_region(
            variable=resolved,
            west=float(bbox.get("west", -180)),
            east=float(bbox.get("east", 180)),
            south=float(bbox.get("south", -90)),
            north=float(bbox.get("north", 90)),
            dataset=dataset,
        )
        result["alias"] = variable
        return result
    except Exception as exc:
        log.warning("query_variable %s failed: %s", variable, exc)
        return {"variable": resolved, "alias": variable, "error": str(exc)}


def query_multi_variables(
    variables: list[str],
    bbox: dict[str, float],
    max_vars: int = 7,
) -> dict[str, Any]:
    """批量查询多个变量，返回汇总结果。"""
    if not variables:
        variables = PRIORITY_VARS
    results = []
    for v in variables[:max_vars]:
        results.append(query_variable(v, bbox))
    missing = [
        {
            "alias": item.get("alias") or item.get("variable"),
            "variable": item.get("variable"),
            "error": item.get("error"),
        }
        for item in results
        if item.get("error")
    ]
    return {
        "region": bbox,
        "variables": results,
        "queried_count": len(results),
        "ok_count": sum(1 for item in results if not item.get("error")),
        "missing_variables": missing,
    }


def list_available_variables() -> list[dict[str, Any]]:
    """列出当前所有可用变量。"""
    try:
        from ocean_agents_demo.tools import list_ocean_variables
        return list_ocean_variables().get("variables", [])
    except Exception:
        return []


def get_variable_stats(data: dict[str, Any], variable_alias: str) -> dict[str, Any] | None:
    """从 query_multi_variables 返回值中提取指定变量的统计。"""
    for item in data.get("variables", []):
        if item.get("alias") == variable_alias or item.get("variable") == variable_alias:
            return item.get("stats")
    return None


# ── 工具 spec（供 LLM tool calling） ──────────────────────────────────────
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "query_ocean_variables",
        "description": (
            "查询框选海域的多个海洋要素统计（SST、叶绿素、盐度、浪高、云量等）。"
            "返回 min/max/mean/count 数值统计。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询的变量列表，如 ['sst','chlorophyll','wave_height']",
                },
                "bbox": {
                    "type": "object",
                    "description": "经纬度范围 {west, east, south, north}",
                    "properties": {
                        "west": {"type": "number"},
                        "east": {"type": "number"},
                        "south": {"type": "number"},
                        "north": {"type": "number"},
                    },
                    "required": ["west", "east", "south", "north"],
                },
            },
            "required": ["bbox"],
        },
    },
    {
        "name": "list_ocean_variables",
        "description": "列出当前系统中所有可用的海洋 NetCDF 数据集与变量名称。",
        "parameters": {"type": "object", "properties": {}},
    },
]


def dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "query_ocean_variables":
        return query_multi_variables(
            variables=args.get("variables", []),
            bbox=args.get("bbox", {}),
        )
    if name == "list_ocean_variables":
        return {"variables": list_available_variables()}
    raise KeyError(f"unknown ocean tool: {name}")
