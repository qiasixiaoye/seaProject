"""PlannerNode - decomposes a request into agent and tool steps."""
from __future__ import annotations

from typing import Any

from geo_agent.state import GeoAgentState
from geo_agent.tool_registry import list_tools


DOMAIN_VARIABLES = {
    "marine": ["sst", "chlorophyll", "salinity", "wave_height"],
    "biology": ["sst", "chlorophyll", "salinity"],
    "navigation": ["wave_height", "swell_period", "wind_speed", "visibility"],
    "stargazing": ["cloud_fraction", "light_aod", "wind_speed"],
    "general": ["sst", "chlorophyll", "salinity"],
}


def run(state: GeoAgentState) -> dict[str, Any]:
    domain = state.get("domain", "general")
    intent = state.get("intent", {})
    user_vars = state.get("variables", []) or []
    trace = list(state.get("trace", []))

    variables = list(dict.fromkeys(user_vars + DOMAIN_VARIABLES.get(domain, DOMAIN_VARIABLES["general"])))
    queries = intent.get("queries") or [intent.get("retrieval_query") or state.get("question", "")]

    plan = {
        "goal": state.get("question", ""),
        "domain": domain,
        "intent_type": intent.get("intent_type", "general"),
        "agents": [
            "IntentAgent",
            "PlannerAgent",
            "RetrievalAgent",
            "DataAgent",
            "ScreeningAgent",
            "DomainReasoningAgent",
            "VisualizationAgent",
            "ReportAgent",
            "CriticAgent",
            "EvaluatorAgent",
        ],
        "parallel_groups": [["RetrievalAgent", "DataAgent"]],
        "retrieval": {
            "backend": state.get("backend", "auto"),
            "queries": queries[:3],
            "top_k": state.get("top_k", 6),
        },
        "data": {
            "bbox_required": bool(state.get("bbox")),
            "variables": variables[:7],
        },
        "tools": {
            "PlannerAgent": [t["name"] for t in list_tools("PlannerAgent")],
            "RetrievalAgent": [t["name"] for t in list_tools("RetrievalAgent")],
            "DataAgent": [t["name"] for t in list_tools("DataAgent")],
            "VisualizationAgent": [t["name"] for t in list_tools("VisualizationAgent")],
        },
    }

    trace.append({
        "node": "PlannerNode",
        "mode": "deterministic",
        "domain": domain,
        "variables": plan["data"]["variables"],
        "queries": plan["retrieval"]["queries"],
    })
    return {"execution_plan": plan, "planned_variables": plan["data"]["variables"], "trace": trace}

