"""Multi-agent orchestration layer for the ocean RAG report pipeline.

Pipeline coordinated by an :class:`Orchestrator`, mapped to the canonical
Plan / Gather / Filter / Synthesize / Reflect agent stages:

    IntentAgent     (Plan)       -> understand the question, plan bilingual queries
    RetrievalAgent  (Gather)     -> autonomous tool loop for documents AND the
                                     NetCDF region statistics (numerical context)
    ScreeningAgent  (Filter)     -> keep / drop each candidate with a reason
    ReportAgent     (Synthesize) -> derive risk hypotheses from numbers + evidence,
                                     then draft an evidence-grounded report
    CriticAgent     (Reflect)    -> review the draft and can send it back for revision

Every LLM-driven agent degrades gracefully to a deterministic heuristic when
``DEEPSEEK_API_KEY`` is absent, so the demo runs end-to-end without any API key.
The Critic forms a true feedback loop: a failed review triggers a ReportAgent
rewrite with the critic's notes, up to ``max_revisions`` times.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ocean_agents_demo import core, deepseek_client, tools
from ocean_agents_demo.core import Doc


log = logging.getLogger("ocean_agents")

Emit = Callable[..., None]


# --------------------------------------------------------------------------- #
# Shared state                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class PipelineState:
    question: str
    top_k: int = 6
    threshold: float = 0.22
    backend: str = "auto"
    region: dict[str, Any] | None = None
    variables: list[str] = field(default_factory=list)
    use_tools: bool = True
    intent: dict[str, Any] = field(default_factory=dict)
    candidates: list[Doc] = field(default_factory=list)
    kept: list[Doc] = field(default_factory=list)
    passed: list[Doc] = field(default_factory=list)
    backend_used: str = "local"
    ocean_context: dict[str, Any] = field(default_factory=dict)
    risk_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    report: str = ""
    critic: dict[str, Any] = field(default_factory=dict)
    revisions: int = 0


# --------------------------------------------------------------------------- #
# Agents                                                                       #
# --------------------------------------------------------------------------- #
class Agent:
    """Base class. Each agent owns one reasoning step and emits a trace event."""

    name: str = "Agent"
    description: str = ""

    def run(self, state: PipelineState, emit: Emit) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


class IntentAgent(Agent):
    name = "IntentAgent"
    description = "凝练用户问题，识别海洋主题，规划中英双语检索 query。"

    def run(self, state: PipelineState, emit: Emit) -> dict[str, Any]:
        if deepseek_client.configured():
            try:
                intent = self._llm_intent(state.question)
                emit(agent=self.name, mode="llm", output=intent)
                return intent
            except Exception as exc:
                log.warning("IntentAgent LLM path failed, falling back: %s", exc)
        intent = self._heuristic_intent(state.question)
        emit(agent=self.name, mode="heuristic", output=intent)
        return intent

    def _llm_intent(self, question: str) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是海洋科学检索规划助手。读取用户问题，输出 JSON，字段："
                    "intent(问题类型，如 risk_assessment/overview/comparison)、"
                    "topics(海洋主题词数组，如 海洋热浪、珊瑚、渔业)、"
                    "keywords(用于本地检索的中文关键词数组)、"
                    "queries(检索 query 数组，至少一条中文、一条英文)。"
                    "只输出 JSON，不要解释。"
                ),
            },
            {"role": "user", "content": question},
        ]
        data = deepseek_client.chat_json(messages)
        topics = _as_str_list(data.get("topics"))
        keywords = _as_str_list(data.get("keywords")) or topics
        queries = _as_str_list(data.get("queries"))
        if not queries:
            queries = [" ".join(keywords + [question]).strip() or question]
        intent = core.condense_intent(question)
        intent["intent"] = str(data.get("intent") or intent.get("intent") or "overview")
        intent["intent_type"] = intent["intent"]
        if topics:
            intent["topics"] = topics
        if keywords:
            intent["keywords"] = keywords
        if queries:
            intent["queries"] = list(dict.fromkeys(queries + _as_str_list(intent.get("queries"))))
            intent["retrieval_query"] = " ".join(dict.fromkeys(keywords + [queries[0]])).strip() or intent["queries"][0]
        intent["mode"] = "llm"
        return intent

    def _heuristic_intent(self, question: str) -> dict[str, Any]:
        intent = core.condense_intent(question)
        intent.setdefault("topics", intent["keywords"])
        intent.setdefault("queries", [intent["retrieval_query"]])
        intent.setdefault("intent", intent.get("intent_type", "overview"))
        intent.setdefault("intent_type", intent["intent"])
        intent["mode"] = "heuristic"
        return intent


class RetrievalAgent(Agent):
    name = "RetrievalAgent"
    description = "Gather 步：工具循环让 LLM 自主决定检索 query（function calling），并按框选区域取 NetCDF 数值上下文；无 Key 时退化为多 query 融合。"

    def run(self, state: PipelineState, emit: Emit) -> tuple[list[Doc], str, dict[str, Any]]:
        # --- gather documents ---
        candidates: list[Doc] = []
        backend_used = "local"
        mode = "multi-query"
        meta: dict[str, Any] = {}
        if deepseek_client.configured() and state.use_tools:
            try:
                candidates, backend_used, info = self._tool_retrieve(state)
                if candidates:
                    mode = "tool-loop"
                    meta = {"queries": info["queries"], "tool_calls": info["calls"]}
            except Exception as exc:
                log.warning("RetrievalAgent tool loop failed, falling back: %s", exc)
        if not candidates:
            candidates, backend_used, det_meta = self._deterministic(state)
            mode = "multi-query"
            meta = det_meta

        # --- gather region numerical context (deterministic NetCDF tool calls) ---
        ocean_context = self._gather_region_context(state)

        emit(
            agent=self.name,
            mode=mode,
            backend=backend_used,
            queries=meta.get("queries", []),
            candidate_count=len(candidates),
            candidates=[d.title for d in candidates],
            tool_calls=meta.get("tool_calls"),
            region_variables=[v.get("variable") for v in (ocean_context.get("variables") or [])],
            region_stats=[
                {"variable": v.get("variable"), "mean": (v.get("stats") or {}).get("mean")}
                for v in (ocean_context.get("variables") or []) if v.get("stats")
            ],
        )
        return candidates, backend_used, ocean_context

    def _gather_region_context(self, state: PipelineState) -> dict[str, Any]:
        if not state.region:
            return {}
        b = state.region
        try:
            west, east = float(b["west"]), float(b["east"])
            south, north = float(b["south"]), float(b["north"])
        except (KeyError, TypeError, ValueError):
            return {}
        variables = state.variables or _default_variables()
        try:
            from geo_agent.tools import ocean as ocean_tools
            return ocean_tools.query_multi_variables(
                variables=variables[:4],
                bbox={"west": west, "east": east, "south": south, "north": north},
                max_vars=4,
            )
        except Exception:
            out: list[dict[str, Any]] = []
            for v in variables[:4]:
                try:
                    out.append(tools.query_ocean_region(variable=v, west=west, east=east, south=south, north=north))
                except Exception as exc:
                    out.append({"variable": v, "alias": v, "error": f"{type(exc).__name__}: {exc}"})
            return {"region": b, "variables": out, "missing_variables": [v for v in out if v.get("error")]}

    def _tool_retrieve(self, state: PipelineState) -> tuple[list[Doc], str, dict[str, Any]]:
        collected: dict[str, Doc] = {}
        queries: list[str] = []
        backend_used = {"v": "local"}

        def dispatch(name: str, args: dict[str, Any]) -> Any:
            if name == "retrieve_documents":
                q = str(args.get("query") or state.question)
                queries.append(q)
                tk = int(args.get("top_k") or state.top_k)
                bk = state.backend if state.backend in {"ragflow", "local"} else str(args.get("backend") or state.backend)
                docs, used = core.retrieve(
                    {"retrieval_query": q, "keywords": [], "original_question": state.question}, tk, bk
                )
                backend_used["v"] = used
                for d in docs:
                    key = d.id or d.title
                    if key not in collected or d.score > collected[key].score:
                        collected[key] = d
                return {
                    "backend": used,
                    "results": [
                        {"id": d.id, "title": d.title, "score": round(d.score, 4), "snippet": (d.abstract or "")[:160]}
                        for d in docs
                    ],
                }
            if name == "list_ocean_variables":
                return tools.list_ocean_variables()
            raise KeyError(f"tool not allowed in retrieval: {name}")

        specs = [s for s in tools.tool_specs() if s["name"] in {"retrieve_documents", "list_ocean_variables"}]
        topics = "、".join(state.intent.get("topics", [])) or "海洋相关主题"
        goal = (
            f"用户问题：{state.question}\n目标主题：{topics}\n"
            "请用 retrieve_documents 检索 1~3 条不同角度的 query（可中英文）来收集证据，足够后结束。"
        )
        loop = deepseek_client.run_tool_loop(
            goal, specs, dispatch,
            system="你是海洋研究检索助手，自主决定检索 query 来收集证据。",
            max_steps=5,
        )
        candidates = sorted(collected.values(), key=lambda d: d.score, reverse=True)[: max(state.top_k, len(collected))]
        calls = sum(1 for s in loop.get("steps", []) if s.get("action") == "call_tool")
        return candidates, backend_used["v"], {"queries": queries, "calls": calls}

    def _deterministic(self, state: PipelineState) -> tuple[list[Doc], str, dict[str, Any]]:
        queries = state.intent.get("queries") or [state.intent.get("retrieval_query", state.question)]
        merged: dict[str, Doc] = {}
        backend_used = "local"
        for query in queries:
            sub_intent = dict(state.intent, retrieval_query=query)
            try:
                docs, backend_used = core.retrieve(sub_intent, state.top_k, state.backend)
            except Exception as exc:
                log.warning("RetrievalAgent query failed (%s): %s", query, exc)
                continue
            for doc in docs:
                key = doc.id or doc.title
                if key not in merged or doc.score > merged[key].score:
                    merged[key] = doc
        candidates = sorted(merged.values(), key=lambda d: d.score, reverse=True)[: max(state.top_k, len(merged))]
        return candidates, backend_used, {"queries": queries}


class ScreeningAgent(Agent):
    name = "ScreeningAgent"
    description = "逐条阅读候选证据，给出 keep/pass 决策、相关分与理由。"

    def run(self, state: PipelineState, emit: Emit) -> tuple[list[Doc], list[Doc], list[dict[str, Any]]]:
        docs = state.candidates
        if deepseek_client.configured() and docs:
            try:
                kept, passed, decisions = self._llm_screen(state.intent, docs)
                emit(agent=self.name, mode="llm", decisions=decisions)
                return kept, passed, decisions
            except Exception as exc:
                log.warning("ScreeningAgent LLM path failed, falling back: %s", exc)
        kept, passed, decisions = core.screen(state.intent, docs, state.threshold)
        emit(agent=self.name, mode="heuristic", decisions=decisions)
        return kept, passed, decisions

    def _llm_screen(
        self, intent: dict[str, Any], docs: list[Doc]
    ) -> tuple[list[Doc], list[Doc], list[dict[str, Any]]]:
        catalog = "\n".join(
            f"[{i}] id={doc.id} | 标题：{doc.title} | 主题：{', '.join(doc.topics) or '-'} | "
            f"摘要：{doc.abstract[:300]}"
            for i, doc in enumerate(docs, 1)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是海洋证据筛选员。针对用户问题，判断每份候选文档是否为相关证据。"
                    "输出 JSON：{\"decisions\":[{\"id\":..., \"decision\":\"keep\"|\"pass\", "
                    "\"score\":0~1, \"evidence_type\":\"direct\"|\"background\"|\"irrelevant\", "
                    "\"reason\":\"...\"}]}。"
                    "标题相似但内容不足、或主题不匹配的，判为 pass。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{intent['original_question']}\n"
                    f"目标主题：{', '.join(intent.get('topics', [])) or '未指定'}\n\n"
                    f"候选文档：\n{catalog}"
                ),
            },
        ]
        data = deepseek_client.chat_json(messages)
        raw_decisions = data.get("decisions") if isinstance(data, dict) else data
        by_id: dict[str, dict[str, Any]] = {}
        for item in raw_decisions or []:
            if isinstance(item, dict) and item.get("id") is not None:
                by_id[str(item["id"])] = item

        kept: list[Doc] = []
        passed: list[Doc] = []
        decisions: list[dict[str, Any]] = []
        for doc in docs:
            verdict = by_id.get(str(doc.id), {})
            decision = str(verdict.get("decision", "")).lower()
            score = _as_float(verdict.get("score"), default=doc.score)
            reason = str(verdict.get("reason", "")).strip()
            doc.decision_score = round(score, 4)
            doc.reason = reason
            if decision == "keep":
                kept.append(doc)
            else:
                decision = "pass"
                passed.append(doc)
            decisions.append(
                {
                    "document_id": doc.id,
                    "decision": decision,
                    "score": doc.decision_score,
                    "evidence_type": verdict.get("evidence_type", ""),
                    "reason": reason,
                }
            )
        if not kept and docs:
            best = max(docs, key=lambda d: d.score)
            if best in passed:
                passed.remove(best)
            kept.append(best)
        return kept, passed, decisions


class ReportAgent(Agent):
    name = "ReportAgent"
    description = "Synthesize 步：先按阈值规则 + LLM 推理把区域数值转成风险假设，再结合证据与数值生成带引用报告，可按审稿意见修订。"

    def run(self, state: PipelineState, emit: Emit, feedback: str | None = None) -> str:
        # Derive risk hypotheses once per request (reuse on Critic-triggered rewrites).
        if not state.risk_hypotheses:
            state.risk_hypotheses, risk_mode = self._derive_risks(state)
        else:
            risk_mode = "cached"
        extra = _format_extra_context(state.ocean_context, state.risk_hypotheses)
        report_text = core.llm_report(
            state.intent, state.kept, state.passed, state.backend_used,
            feedback=feedback, extra_context=extra,
        )
        emit(
            agent=self.name,
            mode="llm" if deepseek_client.configured() else "template",
            revised=bool(feedback),
            chars=len(report_text),
            risk_mode=risk_mode,
            risk_count=len(state.risk_hypotheses),
        )
        return report_text

    def _derive_risks(self, state: PipelineState) -> tuple[list[dict[str, Any]], str]:
        """Threshold rules + optional LLM synthesis (was DomainReasoningAgent)."""
        ctx = state.ocean_context or {}
        hyps = _rule_based_risk(ctx)
        mode = "rules"
        if deepseek_client.configured() and (ctx.get("variables") or state.kept):
            try:
                llm_hyps = self._llm_reason(state, ctx)
                if llm_hyps:
                    hyps = llm_hyps
                    mode = "llm"
            except Exception as exc:
                log.warning("ReportAgent risk LLM failed, keeping rule-based: %s", exc)
        return hyps, mode

    def _llm_reason(self, state: PipelineState, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        numbers = _format_ocean_context(ctx) or "（无区域数值）"
        evidence = "\n".join(f"- {d.title}" for d in state.kept[:6]) or "（无保留证据）"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是海洋风险推理助手。结合区域要素数值与证据，输出 JSON："
                    "{\"hypotheses\":[{\"signal\":\"触发信号\",\"hypothesis\":\"风险假设\","
                    "\"basis\":\"依据\",\"uncertainty\":\"不确定性\"}]}。"
                    "只输出数值/证据可支撑的假设，不要编造。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{state.intent.get('original_question','')}\n\n"
                    f"区域要素数值：\n{numbers}\n\n证据：\n{evidence}"
                ),
            },
        ]
        data = deepseek_client.chat_json(messages)
        hyps = data.get("hypotheses") if isinstance(data, dict) else data
        out: list[dict[str, Any]] = []
        for h in hyps or []:
            if isinstance(h, dict):
                out.append({k: str(h.get(k, "")) for k in ("signal", "hypothesis", "basis", "uncertainty")})
        return out


class CriticAgent(Agent):
    name = "CriticAgent"
    description = "审查报告是否引用证据、是否存在无证据强结论，必要时打回重写。"

    def run(self, state: PipelineState, emit: Emit) -> dict[str, Any]:
        if not deepseek_client.configured():
            verdict = self._rule_critique(state)
            verdict["mode"] = "rules"
            emit(agent=self.name, **verdict)
            return verdict
        try:
            verdict = self._llm_critique(state)
            rule_verdict = self._rule_critique(state)
            if rule_verdict["issues"]:
                verdict["issues"] = list(dict.fromkeys([*(verdict.get("issues") or []), *rule_verdict["issues"]]))
                verdict["feedback"] = " ".join(
                    part for part in [verdict.get("feedback", ""), rule_verdict.get("feedback", "")] if part
                ).strip()
                verdict["passed"] = bool(verdict.get("passed", True)) and rule_verdict["passed"]
        except Exception as exc:
            log.warning("CriticAgent failed, accepting report: %s", exc)
            verdict = self._rule_critique(state)
            verdict["mode"] = "error_rules"
        emit(agent=self.name, **verdict)
        return verdict

    def _rule_critique(self, state: PipelineState) -> dict[str, Any]:
        report_text = state.report or ""
        issues: list[str] = []
        has_evidence_ref = "[E" in report_text or any(
            (d.id and d.id in report_text) or (d.title and d.title[:24] in report_text)
            for d in state.kept
        )
        strong_markers = ("必然", "一定", "显著", "主要风险", "high risk", "significant risk", "must")
        has_strong_claim = any(marker.lower() in report_text.lower() for marker in strong_markers)
        has_limitation = any(marker in report_text for marker in ("证据不足", "未检索到", "数据局限", "不确定"))
        if state.kept and not has_evidence_ref:
            issues.append("citation_missing")
        if not state.kept and has_strong_claim and not has_limitation:
            issues.append("unsupported_strong_claim")
        if state.ocean_context.get("missing_variables") and not has_limitation:
            issues.append("data_limitation_missing")
        feedback = []
        if "citation_missing" in issues:
            feedback.append("核心结论需要引用保留证据的 [E#]、doc_id 或 chunk_id。")
        if "unsupported_strong_claim" in issues:
            feedback.append("无证据时应降低结论强度，并说明证据不足。")
        if "data_limitation_missing" in issues:
            feedback.append("需要说明缺失变量或数值上下文限制。")
        return {"passed": not issues, "issues": issues, "feedback": " ".join(feedback)}

    def _llm_critique(self, state: PipelineState) -> dict[str, Any]:
        evidence = "\n".join(f"- {d.title}（{d.source}）" for d in state.kept) or "（无保留证据）"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是报告质量审稿人。检查报告是否：①每个核心结论都有证据支撑；"
                    "②没有把文件名/元数据当正文证据；③证据不足时如实说明而非强行下结论；"
                    "④覆盖了用户问题的核心要素；⑤给出可执行的规划/监测建议。"
                    "输出 JSON：{\"passed\":true|false, \"issues\":[\"...\"], "
                    "\"feedback\":\"给报告作者的具体修改意见\"}。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{state.intent['original_question']}\n\n"
                    f"保留证据：\n{evidence}\n\n"
                    f"待审报告：\n{state.report}"
                ),
            },
        ]
        data = deepseek_client.chat_json(messages)
        return {
            "passed": bool(data.get("passed", True)),
            "issues": _as_str_list(data.get("issues")),
            "feedback": str(data.get("feedback", "")).strip(),
            "mode": "llm",
        }


# --------------------------------------------------------------------------- #
# Orchestrator                                                                 #
# --------------------------------------------------------------------------- #
class Orchestrator(Agent):
    name = "OrchestratorAgent"
    description = "入口编排器：创建任务、顺序调度各 Agent、运行 Critic 反思回路、收集 trace。"

    def __init__(self, max_revisions: int = 1) -> None:
        self.max_revisions = max(0, max_revisions)
        self.intent_agent = IntentAgent()
        self.retrieval_agent = RetrievalAgent()
        self.screening_agent = ScreeningAgent()
        self.report_agent = ReportAgent()
        self.critic_agent = CriticAgent()

    def run(
        self,
        question: str,
        top_k: int = 6,
        threshold: float = 0.22,
        backend: str = "auto",
        trace: bool = False,
        region: dict[str, Any] | None = None,
        variables: list[str] | None = None,
        use_tools: bool = True,
    ) -> dict[str, Any]:
        started = time.time()
        task_id = f"report_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        events: list[dict[str, Any]] = []

        def emit(**kw: Any) -> None:
            kw.setdefault("t_ms", round((time.time() - started) * 1000, 1))
            events.append(kw)

        state = PipelineState(
            question=question, top_k=top_k, threshold=threshold, backend=backend,
            region=region or None, variables=variables or [], use_tools=use_tools,
        )

        state.intent = self.intent_agent.run(state, emit)
        state.candidates, state.backend_used, state.ocean_context = self.retrieval_agent.run(state, emit)
        state.kept, state.passed, _ = self.screening_agent.run(state, emit)
        # state.risk_hypotheses is populated inside ReportAgent (was DomainReasoningAgent).

        # Draft + Critic review. The critic always runs once; max_revisions
        # only controls whether a failed review triggers a rewrite.
        state.report = self.report_agent.run(state, emit)
        verdict = self.critic_agent.run(state, emit)
        while not verdict.get("passed", True) and state.revisions < self.max_revisions:
            state.revisions += 1
            state.report = self.report_agent.run(state, emit, feedback=verdict.get("feedback"))
            verdict = self.critic_agent.run(state, emit)
        state.critic = verdict

        elapsed_ms = round((time.time() - started) * 1000, 2)
        emit(agent=self.name, status="completed", elapsed_ms=elapsed_ms)

        result: dict[str, Any] = {
            "task_id": task_id,
            "question": question,
            "report": state.report,
            "intent": {
                k: state.intent.get(k) for k in (
                    "schema_version", "intent", "intent_type", "topics", "keywords",
                    "entities", "hazards", "variables", "queries", "query_variants",
                    "retrieval_query", "mode",
                )
            },
            "backend": state.backend_used,
            "ocean_context": state.ocean_context,
            "risk_hypotheses": state.risk_hypotheses,
            "critic": state.critic,
            "revisions": state.revisions,
            "elapsed_ms": elapsed_ms,
            "llm": deepseek_client.status(),
            "kept_documents": [core.doc_to_evidence_dict(d) for d in state.kept],
            "passed_documents": [core.doc_to_evidence_dict(d) for d in state.passed],
        }
        if trace:
            try:
                from geo_agent.state import normalize_trace
                result["trace"] = normalize_trace(events)
            except Exception:
                result["trace"] = events
        return result


# --------------------------------------------------------------------------- #
# Capability advertisement (A2A-style)                                         #
# --------------------------------------------------------------------------- #
def agent_catalog() -> list[dict[str, str]]:
    """Machine-readable list of the agents in the pipeline."""
    agents = [
        IntentAgent, RetrievalAgent, ScreeningAgent, ReportAgent, CriticAgent, Orchestrator,
    ]
    return [{"name": a.name, "description": a.description} for a in agents]


def agent_card(public_url: str = "http://127.0.0.1:8000/api/agents") -> dict[str, Any]:
    """Minimal A2A-style AgentCard describing the report-generation skill."""
    return {
        "name": "OceanReportAgent",
        "description": (
            "Generate evidence-grounded ocean risk and planning reports using "
            "RAGFlow retrieval, gridded NetCDF data and an LLM critic loop."
        ),
        "version": "0.4.0",
        "url": public_url,
        "skills": [
            {"name": "ocean_report", "description": "Generate ocean knowledge reports with citations."},
            {"name": "ocean_risk_assessment", "description": "Assess regional marine risks from RAG and NetCDF data."},
        ],
        "tools": tools.tool_specs(),
        "defaultInputModes": ["text", "application/json"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "pipeline": agent_catalog(),
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _default_variables() -> list[str]:
    try:
        names = [v.get("name") for v in tools.list_ocean_variables().get("variables", []) if v.get("name")]
    except Exception:
        names = []
    priority = ["sst", "chlorophyll", "chlor_a", "salinity", "sss", "wave_height", "swell_period"]
    picked = [n for n in priority if n in names]
    return picked or (names[:2] if names else ["sst"])


def _rule_based_risk(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Threshold rules linking ocean numbers to risk hypotheses.

    Thresholds are rough literature-informed defaults and are meant to be tuned.
    """
    hyps: list[dict[str, Any]] = []
    for item in ctx.get("variables") or []:
        stats = item.get("stats") or {}
        mean = stats.get("mean")
        if mean is None:
            continue
        name = (item.get("variable") or "").lower()
        units = item.get("units") or ""
        if ("sst" in name or "temp" in name) and mean >= 28:
            hyps.append({
                "signal": f"SST 均值 {mean}{units} ≥ 28°C",
                "hypothesis": "海洋热浪 / 珊瑚白化与渔业波动风险升高",
                "basis": "约 28°C 接近热带珊瑚热白化阈值附近",
                "uncertainty": "局部格点切片不代表长期趋势，需结合距平与历史基线",
            })
        if "chl" in name and mean >= 1.0:
            hyps.append({
                "signal": f"叶绿素均值 {mean}{units} ≥ 1.0",
                "hypothesis": "富营养化 / 藻华监测需求上升",
                "basis": "高叶绿素常指示初级生产力旺盛或藻华",
                "uncertainty": "需区分季节、上升流与近岸输入影响",
            })
        if ("wave" in name or "swell" in name or name in {"hs", "vhm0"}) and mean >= 2.5:
            hyps.append({
                "signal": f"浪高均值 {mean}{units} ≥ 2.5m",
                "hypothesis": "近岸 / 航运灾害与岸线侵蚀风险",
                "basis": "较高有效波高增大航行与岸线风险",
                "uncertainty": "极值波高比均值更关键",
            })
        if ("sal" in name or "sss" in name) and (mean < 33 or mean > 37):
            hyps.append({
                "signal": f"盐度均值 {mean}{units} 偏离 33-37",
                "hypothesis": "河口 / 淡水输入或蒸发异常，海气过程不确定",
                "basis": "开阔大洋表层盐度多在 33-37 psu",
                "uncertainty": "近岸受径流强烈影响，需更多采样",
            })
    return hyps


def _format_ocean_context(ctx: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in ctx.get("variables") or []:
        if item.get("error"):
            rows.append(f"- {item.get('alias') or item.get('variable')}：无数据（{item['error']}）")
            continue
        s = item.get("stats") or {}
        rows.append(
            f"- {item.get('long_name') or item.get('variable')}"
            f"（{item.get('variable')}，{item.get('units') or '-'}）："
            f"均值 {s.get('mean')}，范围 {s.get('min')}~{s.get('max')}，有效格点 {s.get('count')}"
        )
    missing = [str(v.get("alias") or v.get("variable")) for v in ctx.get("missing_variables") or [] if v.get("alias") or v.get("variable")]
    if missing:
        rows.append(f"- 数据缺口：{', '.join(dict.fromkeys(missing))} 当前未在 NetCDF 数据集中找到。")
    return "\n".join(rows)


def _format_extra_context(ctx: dict[str, Any], hyps: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    numbers = _format_ocean_context(ctx)
    if numbers:
        parts.append("区域海洋要素数值：\n" + numbers)
    if hyps:
        lines = [
            f"- 信号：{h.get('signal','')} → 假设：{h.get('hypothesis','')}"
            f"（依据：{h.get('basis','')}；不确定性：{h.get('uncertainty','')}）"
            for h in hyps
        ]
        parts.append("数值触发的风险假设（需结合证据判断）：\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
