"""Capability advertisement for GeoAgent and A2A-style discovery."""
from __future__ import annotations

from typing import Any

from geo_agent.tool_registry import list_tools


AGENT_CATALOG: list[dict[str, Any]] = [
    {
        "name": "IntentAgent",
        "role": "intent",
        "description": "识别领域、任务类型、主题词和中英检索 query。",
    },
    {
        "name": "PlannerAgent",
        "role": "planner",
        "description": "把复杂问题拆成检索、数据、推理、可视化和质检步骤。",
    },
    {
        "name": "RetrievalAgent",
        "role": "retrieval",
        "description": "优先调用 RAGFlow，失败时回退本地知识库，并保留候选证据。",
    },
    {
        "name": "DataAgent",
        "role": "data",
        "description": "查询并审查 NetCDF 区域统计、缺失变量和数据局限。",
    },
    {
        "name": "ScreeningAgent",
        "role": "screening",
        "description": "筛选候选证据，输出 keep/pass、相关分和理由。",
    },
    {
        "name": "DomainReasoningAgent",
        "role": "reasoning",
        "description": "按海洋、生物、航行、观星等领域规则生成风险假设。",
    },
    {
        "name": "VisualizationAgent",
        "role": "visualization",
        "description": "根据变量类型输出填色、等值线、粒子流和底图渲染建议。",
    },
    {
        "name": "ReportAgent",
        "role": "report",
        "description": "基于数值上下文和证据链生成中文 Markdown 报告。",
    },
    {
        "name": "CriticAgent",
        "role": "critic",
        "description": "审查证据支撑、数据局限、建议可执行性，必要时触发修订。",
    },
    {
        "name": "EvaluatorAgent",
        "role": "evaluation",
        "description": "计算引用覆盖、数据支撑、critic 结果和 trace 完整度指标。",
    },
]


def agent_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in AGENT_CATALOG]


def agent_card(public_url: str = "http://127.0.0.1:8000/api/agents") -> dict[str, Any]:
    return {
        "name": "OceanGeoAgent",
        "description": (
            "Ocean geospatial analysis agent with NetCDF data grounding, "
            "RAGFlow/local retrieval, visualization guidance and critic/evaluator loops."
        ),
        "version": "0.5.0",
        "url": public_url,
        "capabilities": {
            "streaming": True,
            "stateTransitionHistory": True,
            "pushNotifications": False,
            "deterministicFallback": True,
        },
        "skills": [
            {
                "id": "marine-risk-analysis",
                "name": "海洋风险分析",
                "description": "基于 NetCDF 区域统计和 RAG 证据生成风险与规划报告。",
                "inputModes": ["text", "application/json", "bbox"],
                "outputModes": ["text/markdown", "application/json"],
            },
            {
                "id": "ocean-rendering-guidance",
                "name": "海洋要素渲染建议",
                "description": "根据变量类型判断填色、等值线、点图、粒子流等渲染能力。",
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
            },
            {
                "id": "stargazing-assessment",
                "name": "观星适宜性评估",
                "description": "综合月相、光污染、大气透明度和海况给出观测建议。",
                "inputModes": ["text", "bbox"],
                "outputModes": ["text/markdown", "application/json"],
            },
            {
                "id": "navigation-safety",
                "name": "航行安全评估",
                "description": "结合浪高、涌浪、风速和能见度评估不同船型风险。",
                "inputModes": ["text", "bbox"],
                "outputModes": ["text/markdown", "application/json"],
            },
        ],
        "agents": agent_catalog(),
        "tools": list_tools(),
        "defaultInputModes": ["text", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
    }

