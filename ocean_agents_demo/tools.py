"""Tool registry for the ocean agents.

Each tool is a plain Python callable plus an OpenAI/JSON-Schema-style spec so an
LLM can decide *which* tool to call and *with what arguments* (see
``deepseek_client.run_tool_loop``). The same callables are also used directly by
the deterministic agents, so tools are the single source of truth for "things the
system can do".
"""

from __future__ import annotations

from typing import Any, Callable

from ocean_agents_demo import core, nc_data


# --------------------------------------------------------------------------- #
# Tool implementations                                                         #
# --------------------------------------------------------------------------- #
def query_ocean_region(
    variable: str = "sst",
    west: float = -180.0,
    east: float = 180.0,
    south: float = -90.0,
    north: float = 90.0,
    dataset: str = "",
) -> dict[str, Any]:
    """Return min/max/mean/count statistics for one variable over a lat/lon box."""
    payload: dict[str, Any] = {
        "variable": variable,
        "bounds": {"west": west, "east": east, "south": south, "north": north},
        "max_points": 4000,
    }
    if not dataset:
        dataset = _resolve_dataset_for_variable(variable)
    if dataset:
        payload["dataset"] = dataset
    res = nc_data.query_grid(payload)
    return {
        "dataset": res.get("dataset"),
        "variable": res.get("variable"),
        "units": res.get("units"),
        "long_name": res.get("long_name"),
        "bounds": res.get("bounds"),
        "stats": res.get("stats"),
    }


def retrieve_documents(query: str, top_k: int = 6, backend: str = "auto") -> dict[str, Any]:
    """Retrieve candidate documents from RAGFlow (vector) or the local index."""
    intent = {"retrieval_query": query, "keywords": [], "original_question": query}
    docs, used = core.retrieve(intent, int(top_k), str(backend))
    return {
        "backend": used,
        "results": [
            {
                "id": d.id,
                "title": d.title,
                "score": round(d.score, 4),
                "source": d.source,
                "snippet": (d.abstract or "")[:200],
            }
            for d in docs
        ],
    }


def list_ocean_variables() -> dict[str, Any]:
    """List the variables available across the loaded NetCDF datasets."""
    data = nc_data.list_variables()
    return {
        "variables": [
            {
                "dataset": v.get("dataset"),
                "name": v.get("name"),
                "long_name": v.get("long_name"),
                "units": v.get("units"),
            }
            for v in data.get("variables", [])
        ]
    }


def _resolve_dataset_for_variable(variable: str) -> str:
    """Find a dataset id that actually contains the requested variable."""
    try:
        for v in nc_data.list_variables().get("variables", []):
            if v.get("name") == variable and v.get("dataset"):
                return str(v["dataset"])
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------- #
# Registry + specs                                                             #
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, Callable[..., Any]] = {
    "query_ocean_region": query_ocean_region,
    "retrieve_documents": retrieve_documents,
    "list_ocean_variables": list_ocean_variables,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "retrieve_documents",
        "description": "按 query 检索海洋文献/报告证据（RAGFlow 向量检索或本地检索）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，可中文或英文"},
                "top_k": {"type": "integer", "description": "返回条数", "default": 6},
                "backend": {"type": "string", "enum": ["auto", "local", "ragflow"], "default": "auto"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "query_ocean_region",
        "description": "查询某海域某要素(如 sst/chlorophyll/salinity)的 min/max/mean/count 统计。",
        "parameters": {
            "type": "object",
            "properties": {
                "variable": {"type": "string", "description": "变量名，如 sst"},
                "west": {"type": "number"},
                "east": {"type": "number"},
                "south": {"type": "number"},
                "north": {"type": "number"},
                "dataset": {"type": "string", "description": "可选数据集 id，留空用默认"},
            },
            "required": ["variable", "west", "east", "south", "north"],
        },
    },
    {
        "name": "list_ocean_variables",
        "description": "列出当前可用的海洋数据集与变量。",
        "parameters": {"type": "object", "properties": {}},
    },
]


def tool_specs() -> list[dict[str, Any]]:
    return TOOL_SPECS


def dispatch(name: str, args: dict[str, Any] | None) -> Any:
    fn = REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"unknown tool: {name}")
    return fn(**(args or {}))
