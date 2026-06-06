"""DataAgent - validates and summarizes NetCDF context for downstream agents."""
from __future__ import annotations

from typing import Any

from geo_agent.state import GeoAgentState


def run(state: GeoAgentState) -> dict[str, Any]:
    ocean_data = state.get("ocean_data", {}) or {}
    trace = list(state.get("trace", []))
    variables = ocean_data.get("variables", []) or []

    ok = [v for v in variables if not v.get("error")]
    missing = [v for v in variables if v.get("error")]
    findings = [_finding(v) for v in ok]

    quality_flags: list[str] = []
    if not state.get("bbox"):
        quality_flags.append("no_bbox")
    if not ok:
        quality_flags.append("no_netcdf_context")
    if missing:
        quality_flags.append("missing_variables")

    data_context = {
        "ok_count": len(ok),
        "missing_count": len(missing),
        "variables": findings,
        "quality_flags": quality_flags,
        "limitations": _limitations(ocean_data, missing),
    }
    trace.append({
        "node": "DataAgent",
        "mode": "netcdf_summary",
        "ok": len(ok),
        "missing": len(missing),
        "quality_flags": quality_flags,
    })
    return {"data_context": data_context, "trace": trace}


def _finding(item: dict[str, Any]) -> dict[str, Any]:
    stats = item.get("stats") or {}
    count = stats.get("count") or 0
    return {
        "variable": item.get("variable"),
        "alias": item.get("alias"),
        "long_name": item.get("long_name"),
        "units": item.get("units"),
        "mean": stats.get("mean"),
        "minimum": stats.get("min"),
        "maximum": stats.get("max"),
        "count": count,
        "land_mask_applied": item.get("land_mask_applied", 0),
        "has_valid_data": bool(count),
    }


def _limitations(ocean_data: dict[str, Any], missing: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    if ocean_data.get("region"):
        out.append("区域统计仅代表当前框选 bbox 和默认时间/深度层。")
    if missing:
        names = ", ".join(str(v.get("alias") or v.get("variable")) for v in missing[:5])
        out.append(f"以下计划变量当前不可用：{names}。")
    if not ocean_data.get("variables"):
        out.append("未取得 NetCDF 数值上下文，报告只能依赖检索证据和明确的数据缺口说明。")
    return out

