"""GeoAgent 工具注册表。"""
from __future__ import annotations

from typing import Any

from geo_agent.tools import astro, biology, navigation, ocean

_DOMAIN_DISPATCHERS = {
    "ocean":      ocean.dispatch,
    "astro":      astro.dispatch,
    "biology":    biology.dispatch,
    "navigation": navigation.dispatch,
}

_ALL_TOOL_NAMES: dict[str, str] = {}


def _build_index() -> None:
    for domain, specs in [
        ("ocean",      ocean.TOOL_SPECS),
        ("astro",      astro.TOOL_SPECS),
        ("biology",    biology.TOOL_SPECS),
        ("navigation", navigation.TOOL_SPECS),
    ]:
        for spec in specs:
            _ALL_TOOL_NAMES[spec["name"]] = domain


_build_index()


def tool_specs(domains: list[str] | None = None) -> list[dict[str, Any]]:
    all_specs = (
        ocean.TOOL_SPECS + astro.TOOL_SPECS
        + biology.TOOL_SPECS + navigation.TOOL_SPECS
    )
    if not domains:
        return all_specs
    domain_set = set(domains)
    return [s for s in all_specs if _ALL_TOOL_NAMES.get(s["name"]) in domain_set]


def dispatch(name: str, args: dict[str, Any] | None = None) -> Any:
    args = args or {}
    domain = _ALL_TOOL_NAMES.get(name)
    if domain is None:
        try:
            from ocean_agents_demo.tools import dispatch as legacy_dispatch
            return legacy_dispatch(name, args)
        except Exception:
            raise KeyError(f"unknown tool: {name}")
    return _DOMAIN_DISPATCHERS[domain](name, args)


def domain_for_tool(name: str) -> str | None:
    return _ALL_TOOL_NAMES.get(name)
