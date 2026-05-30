"""ContextNode — 并行执行的海洋数值上下文采集节点。

在 LangGraph 中与 RetrievalNode 并行运行：
- 读取框选区域的 NetCDF 数值（多变量批量）
- 按 domain 自动追加专有变量（观星加云量，航行加浪高等）
"""
from __future__ import annotations

import logging
from typing import Any

from geo_agent.state import GeoAgentState
from geo_agent.tools import ocean as ocean_tools

log = logging.getLogger("geo_agent.nodes.context")

# 每个领域优先查询的变量
DOMAIN_VARS: dict[str, list[str]] = {
    "marine":     ["sst", "chlorophyll", "salinity", "wave_height"],
    "stargazing": ["cloud_fraction", "light_aod", "sst", "wind_speed"],
    "biology":    ["sst", "chlorophyll", "salinity", "wave_height"],
    "navigation": ["wave_height", "swell_period", "wind_speed", "visibility", "sst"],
    "general":    ["sst", "chlorophyll", "salinity", "wave_height"],
}


def run(state: GeoAgentState) -> dict[str, Any]:
    bbox = state.get("bbox")
    domain = state.get("domain", "general")
    user_vars = state.get("variables", [])
    trace = list(state.get("trace", []))

    if not bbox:
        trace.append({"node": "ContextNode", "mode": "skipped", "reason": "no bbox"})
        return {"ocean_data": {}, "trace": trace}

    # 合并用户指定变量 + 领域默认变量
    vars_to_query = list(dict.fromkeys(
        (user_vars or []) + DOMAIN_VARS.get(domain, DOMAIN_VARS["general"])
    ))[:7]

    try:
        ocean_data = ocean_tools.query_multi_variables(vars_to_query, bbox)
        n_ok = sum(1 for v in ocean_data.get("variables", []) if not v.get("error"))
        missing = [v.get("alias") or v.get("variable") for v in ocean_data.get("variables", []) if v.get("error")]
        trace.append({
            "node": "ContextNode",
            "mode": "nc_query",
            "domain": domain,
            "vars_queried": vars_to_query,
            "vars_ok": n_ok,
            "vars_missing": missing,
        })
        return {"ocean_data": ocean_data, "trace": trace}
    except Exception as exc:
        log.warning("ContextNode query failed: %s", exc)
        trace.append({"node": "ContextNode", "mode": "error", "error": str(exc)})
        return {"ocean_data": {}, "trace": trace}
