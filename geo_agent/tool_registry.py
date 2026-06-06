"""Central tool registry for GeoAgent.

The registry keeps agent-to-tool permissions explicit. It is intentionally
lightweight so the demo can advertise capabilities and validate tool calls
without requiring a separate orchestration framework.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from geo_agent import tools as geo_tools


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    description: str
    category: str
    allowed_agents: tuple[str, ...]
    timeout_seconds: int
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_agents"] = list(self.allowed_agents)
        return data


def _schema_for(name: str) -> dict[str, Any]:
    for spec in geo_tools.tool_specs():
        if spec.get("name") == name:
            return spec.get("parameters", {})
    return {"type": "object", "properties": {}}


TOOL_REGISTRY: dict[str, ToolRegistration] = {
    "query_ocean_variables": ToolRegistration(
        name="query_ocean_variables",
        description="Query gridded NetCDF variables inside a geographic bbox.",
        category="data",
        allowed_agents=("DataAgent", "ContextAgent", "PlannerAgent"),
        timeout_seconds=30,
        input_schema=_schema_for("query_ocean_variables"),
    ),
    "list_ocean_variables": ToolRegistration(
        name="list_ocean_variables",
        description="List available NetCDF datasets and variables.",
        category="data",
        allowed_agents=("DataAgent", "PlannerAgent"),
        timeout_seconds=10,
        input_schema=_schema_for("list_ocean_variables"),
    ),
    "retrieve_documents": ToolRegistration(
        name="retrieve_documents",
        description="Retrieve knowledge documents from RAGFlow or local fallback.",
        category="retrieval",
        allowed_agents=("RetrievalAgent", "LiteratureAgent", "PlannerAgent"),
        timeout_seconds=20,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 6},
                "backend": {"type": "string", "enum": ["auto", "local", "ragflow"]},
            },
            "required": ["query"],
        },
    ),
    "search_arxiv": ToolRegistration(
        name="search_arxiv",
        description="Fetch recent literature metadata for a domain via arXiv.",
        category="literature",
        allowed_agents=("LiteratureAgent", "RetrievalAgent"),
        timeout_seconds=25,
        input_schema={
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "max": {"type": "integer", "default": 5},
            },
        },
    ),
    "assess_visualization": ToolRegistration(
        name="assess_visualization",
        description="Generate frontend rendering guidance from data capabilities.",
        category="visualization",
        allowed_agents=("VisualizationAgent", "PlannerAgent"),
        timeout_seconds=5,
        input_schema={"type": "object", "properties": {}},
    ),
}


def list_tools(agent: str | None = None) -> list[dict[str, Any]]:
    items = TOOL_REGISTRY.values()
    if agent:
        items = [t for t in items if agent in t.allowed_agents]
    return [t.to_dict() for t in items]


def assert_allowed(tool_name: str, agent: str) -> ToolRegistration:
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        raise KeyError(f"unknown tool: {tool_name}")
    if agent not in tool.allowed_agents:
        raise PermissionError(f"{agent} is not allowed to call {tool_name}")
    return tool


def dispatch(tool_name: str, args: dict[str, Any] | None = None, agent: str = "GeoAgent") -> Any:
    assert_allowed(tool_name, agent)
    args = args or {}
    if tool_name in {"query_ocean_variables", "list_ocean_variables"}:
        return geo_tools.dispatch(tool_name, args)
    if tool_name == "retrieve_documents":
        from ocean_agents_demo import core

        query = str(args.get("query") or "")
        docs, backend = core.retrieve(
            {"retrieval_query": query, "keywords": [], "original_question": query},
            int(args.get("top_k") or 6),
            str(args.get("backend") or "auto"),
        )
        return {"backend": backend, "documents": [core.doc_to_evidence_dict(d) for d in docs]}
    raise KeyError(f"tool has no local dispatcher: {tool_name}")
