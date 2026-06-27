"""GeoAgent LangGraph StateGraph。

拓扑结构：
    START
      ↓
    intent_node
      ↓      ↓  (并行 fan-out)
  retrieval  context
      ↓      ↓  (fan-in 合并)
    screening_node
      ↓
    reasoning_node
      ↓
    report_node
      ↓
    critic_node
      ↓ [条件边]
    ┌── passed=True  → END
    └── passed=False, revisions < max → report_node (反思重写循环)

兼容性说明：
  - 若 langgraph 未安装，fallback 到 run_linear() 手工顺序执行
  - 并行分支用 asyncio.gather 实现（在支持 async 的环境下）
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState, merge_trace, normalize_trace
from geo_agent.nodes import context, critic, data, evaluator, intent, planner, reasoning, report, retrieval, screening, visualization

log = logging.getLogger("geo_agent.graph")


# ── LangGraph 图构建 ────────────────────────────────────────────────────────
def build_graph():
    """构建并编译 LangGraph StateGraph。

    Returns:
        CompiledStateGraph | None: 若 langgraph 未安装返回 None。
    """
    try:
        from langgraph.graph import StateGraph, START, END  # type: ignore
    except ImportError:
        log.info("langgraph not installed, will use linear fallback")
        return None

    def intent_node(state: GeoAgentState) -> dict[str, Any]:
        return intent.run(state)

    def retrieval_node(state: GeoAgentState) -> dict[str, Any]:
        return retrieval.run(state)

    def planner_node(state: GeoAgentState) -> dict[str, Any]:
        return planner.run(state)

    def context_node(state: GeoAgentState) -> dict[str, Any]:
        return context.run(state)

    def data_node(state: GeoAgentState) -> dict[str, Any]:
        return data.run(state)

    def screening_node(state: GeoAgentState) -> dict[str, Any]:
        return screening.run(state)

    def reasoning_node(state: GeoAgentState) -> dict[str, Any]:
        return reasoning.run(state)

    def report_node(state: GeoAgentState) -> dict[str, Any]:
        critic_result = state.get("critic_result", {})
        feedback = critic_result.get("feedback") if critic_result and not critic_result.get("passed", True) else None
        return report.run(state, feedback=feedback)

    def critic_node(state: GeoAgentState) -> dict[str, Any]:
        return critic.run(state)

    def visualization_node(state: GeoAgentState) -> dict[str, Any]:
        return visualization.run(state)

    def evaluator_node(state: GeoAgentState) -> dict[str, Any]:
        return evaluator.run(state)

    def should_revise(state: GeoAgentState) -> str:
        """条件边：是否需要修订报告。"""
        cr = state.get("critic_result", {})
        revisions = state.get("revisions", 0)
        max_rev = state.get("max_revisions", 1)
        if not cr.get("passed", True) and revisions < max_rev:
            return "revise"
        return "evaluate"

    graph = StateGraph(GeoAgentState)

    # 添加节点
    graph.add_node("intent", intent_node)
    graph.add_node("planner", planner_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("context", context_node)
    graph.add_node("data", data_node)
    graph.add_node("screening", screening_node)
    graph.add_node("reasoning", reasoning_node)
    graph.add_node("visualization", visualization_node)
    graph.add_node("report", report_node)
    graph.add_node("critic", critic_node)
    graph.add_node("evaluator", evaluator_node)

    # 连接边
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "planner")

    # 并行 fan-out: planner → retrieval + context
    graph.add_edge("planner", "retrieval")
    graph.add_edge("planner", "context")

    # fan-in: retrieval + context → data → screening
    graph.add_edge("retrieval", "data")
    graph.add_edge("context", "data")

    graph.add_edge("data", "screening")
    graph.add_edge("screening", "reasoning")
    graph.add_edge("reasoning", "visualization")
    graph.add_edge("visualization", "report")
    graph.add_edge("report", "critic")

    # 条件边：Critic 反思循环
    graph.add_conditional_edges(
        "critic",
        should_revise,
        {"revise": "report", "evaluate": "evaluator"},
    )
    graph.add_edge("evaluator", END)

    return graph.compile()


# ── 线性降级执行（无 LangGraph） ──────────────────────────────────────────────
def run_linear(state: GeoAgentState) -> GeoAgentState:
    """顺序执行所有节点，无并行。用于 langgraph 不可用时的降级。"""
    state = dict(state)  # 复制，避免原地修改

    def merge(updates: dict[str, Any]) -> None:
        for k, v in updates.items():
            if k == "trace" and isinstance(v, list) and isinstance(state.get("trace"), list):
                state["trace"] = merge_trace(state.get("trace", []), v)
            else:
                state[k] = v

    merge(intent.run(state))
    merge(planner.run(state))

    # 伪并行：顺序但都跑
    merge(retrieval.run(state))
    merge(context.run(state))

    merge(data.run(state))
    merge(screening.run(state))
    merge(reasoning.run(state))
    merge(visualization.run(state))
    merge(report.run(state))
    merge(critic.run(state))

    # Critic 反思循环
    max_rev = state.get("max_revisions", 1)
    while not state.get("critic_result", {}).get("passed", True) and state.get("revisions", 0) < max_rev:
        feedback = state["critic_result"].get("feedback", "")
        merge(report.run(state, feedback=feedback))
        merge(critic.run(state))

    merge(evaluator.run(state))
    return state


# ── 并行辅助（asyncio） ───────────────────────────────────────────────────────
async def _run_parallel(state: GeoAgentState) -> tuple[dict, dict]:
    """异步并行运行 retrieval + context。"""
    loop = asyncio.get_event_loop()
    ret_task = loop.run_in_executor(None, retrieval.run, state)
    ctx_task = loop.run_in_executor(None, context.run, state)
    return await asyncio.gather(ret_task, ctx_task)


def run_parallel_linear(state: GeoAgentState) -> GeoAgentState:
    """用 asyncio 并行跑 retrieval+context，其余顺序执行。"""
    state = dict(state)

    def merge(updates: dict[str, Any]) -> None:
        for k, v in updates.items():
            if k == "trace" and isinstance(v, list) and isinstance(state.get("trace"), list):
                state["trace"] = merge_trace(state.get("trace", []), v)
            else:
                state[k] = v

    merge(intent.run(state))
    merge(planner.run(state))

    # 并行
    try:
        ret_result, ctx_result = asyncio.run(_run_parallel(state))
    except RuntimeError:
        # 已有 event loop（如在 Jupyter/Flask 中）
        loop = asyncio.new_event_loop()
        ret_result, ctx_result = loop.run_until_complete(_run_parallel(state))
        loop.close()

    merge(ret_result)
    merge(ctx_result)

    merge(data.run(state))
    merge(screening.run(state))
    merge(reasoning.run(state))
    merge(visualization.run(state))
    merge(report.run(state))
    merge(critic.run(state))

    max_rev = state.get("max_revisions", 1)
    while not state.get("critic_result", {}).get("passed", True) and state.get("revisions", 0) < max_rev:
        feedback = state["critic_result"].get("feedback", "")
        merge(report.run(state, feedback=feedback))
        merge(critic.run(state))

    merge(evaluator.run(state))
    return state


# ── 公开入口 ──────────────────────────────────────────────────────────────────
_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run(
    question: str,
    bbox: dict[str, float] | None = None,
    domain: str | None = None,
    variables: list[str] | None = None,
    backend: str = "auto",
    top_k: int = 6,
    threshold: float = 0.22,
    max_revisions: int = 1,
    trace_enabled: bool = False,
    use_parallel: bool = True,
) -> dict[str, Any]:
    """GeoAgent 主入口。

    Args:
        question: 用户问题
        bbox: 框选区域 {west, east, south, north}
        domain: 强制指定领域（留空自动检测）
        variables: 用户指定的 NC 变量名
        backend: "auto" | "local" | "ragflow"
        top_k: 检索文档数
        threshold: 筛选阈值
        max_revisions: Critic 最大修订次数
        trace_enabled: 是否返回完整 trace
        use_parallel: 是否并行执行 retrieval+context

    Returns:
        dict: 包含 report, domain_analysis, risk_hypotheses, critic_result, token_usage 等
    """
    started = time.time()
    task_id = f"geo_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    llm.reset_usage()

    # 非分析类输入（问候/闲聊/求助）：直接返回引导回复，不跑整个图
    if intent.is_smalltalk(question):
        return {
            "task_id": task_id,
            "question": question,
            "domain": "general",
            "report": intent.smalltalk_reply(question),
            "intent": {"intent_type": "chitchat", "domain": "general"},
            "execution_plan": {},
            "critic_result": {},
            "evaluation": {},
            "revisions": 0,
            "kept_documents": [],
            "passed_documents": [],
            "elapsed_ms": round((time.time() - started) * 1000, 2),
            "token_usage": llm.get_usage(),
            "llm_configured": llm.configured(),
            "chitchat": True,
        }

    initial_state: GeoAgentState = {
        "question": question,
        "bbox": bbox or None,
        "domain": domain or "",
        "variables": variables or [],
        "backend": backend,
        "top_k": top_k,
        "threshold": threshold,
        "max_revisions": max_revisions,
        "trace_enabled": trace_enabled,
        "revisions": 0,
        "trace": [],
        "errors": [],
        "token_usage": {},
    }

    graph = get_graph()
    if graph is not None:
        # LangGraph 执行
        try:
            final_state = graph.invoke(initial_state)
        except Exception as exc:
            log.warning("LangGraph invoke failed, falling back to linear: %s", exc)
            final_state = run_linear(initial_state)
    elif use_parallel:
        final_state = run_parallel_linear(initial_state)
    else:
        final_state = run_linear(initial_state)

    elapsed_ms = round((time.time() - started) * 1000, 2)
    token_usage = llm.get_usage()
    trace = normalize_trace(final_state.get("trace", []))
    evaluation = dict(final_state.get("evaluation", {}) or {})
    if evaluation:
        metrics = dict(evaluation.get("metrics", {}) or {})
        metrics["system"] = {
            "elapsed_ms": elapsed_ms,
            "token_usage": token_usage,
        }
        evaluation["metrics"] = metrics

    result: dict[str, Any] = {
        "task_id": task_id,
        "question": question,
        "domain": final_state.get("domain", ""),
        "report": final_state.get("report", ""),
        "intent": final_state.get("intent", {}),
        "execution_plan": final_state.get("execution_plan", {}),
        "ocean_data": final_state.get("ocean_data", {}),
        "data_context": final_state.get("data_context", {}),
        "domain_analysis": final_state.get("domain_analysis", {}),
        "risk_hypotheses": final_state.get("risk_hypotheses", []),
        "visualization": final_state.get("visualization", {}),
        "critic_result": final_state.get("critic_result", {}),
        "evaluation": evaluation,
        "revisions": final_state.get("revisions", 0),
        "kept_documents": final_state.get("kept_docs", []),
        "passed_documents": final_state.get("passed_docs", []),
        "backend_used": final_state.get("backend_used", "local"),
        "elapsed_ms": elapsed_ms,
        "token_usage": token_usage,
        "llm_configured": llm.configured(),
    }
    if trace_enabled:
        result["trace"] = trace

    return result
