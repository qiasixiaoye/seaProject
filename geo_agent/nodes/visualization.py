"""VisualizationAgent - creates rendering guidance from data capabilities."""
from __future__ import annotations

from typing import Any

from geo_agent.state import GeoAgentState


def run(state: GeoAgentState) -> dict[str, Any]:
    ocean_data = state.get("ocean_data", {}) or {}
    trace = list(state.get("trace", []))
    layers = []
    warnings = []

    for item in ocean_data.get("variables", []) or []:
        if item.get("error"):
            continue
        caps = item.get("render_capabilities") or item.get("render_modes") or ["fill", "contour", "points"]
        variable = item.get("variable") or item.get("alias")
        is_vector = bool(item.get("u_grid") and item.get("v_grid")) or "particles" in caps
        modes = ["fill", "contour", "points"]
        if is_vector:
            modes.extend(["particles", "arrows"])
        layer = {
            "variable": variable,
            "title": item.get("long_name") or variable,
            "recommended_mode": "particles" if is_vector else "fill",
            "allowed_modes": list(dict.fromkeys(modes)),
            "land_mask": "backend_hires" if item.get("land_mask_hires") else "grid_or_vector_fallback",
        }
        layers.append(layer)
        if not is_vector:
            warnings.append(f"{variable} 不是完整 u/v 矢量场，前端应禁用粒子流。")

    guidance = {
        "layers": layers,
        "warnings": warnings[:5],
        "basemap": {
            "primary": "GeoServer/OSM labels when available",
            "fallback": "frontend/assets/earth-local.png",
        },
    }
    trace.append({
        "node": "VisualizationAgent",
        "mode": "capability_rules",
        "layers": len(layers),
        "warnings": len(warnings),
    })
    return {"visualization": guidance, "trace": trace}

