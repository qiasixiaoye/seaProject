"""ScreeningNode — 文献相关性筛选。与旧版基本一致，改为读写 GeoAgentState。"""
from __future__ import annotations

import logging
from typing import Any

from geo_agent import llm
from geo_agent.state import GeoAgentState

log = logging.getLogger("geo_agent.nodes.screening")


def run(state: GeoAgentState) -> dict[str, Any]:
    candidates = state.get("candidates", [])
    intent = state.get("intent", {})
    threshold = state.get("threshold", 0.22)
    trace = list(state.get("trace", []))

    if not candidates:
        trace.append({"node": "ScreeningNode", "mode": "skipped", "reason": "no candidates", "kept": 0, "passed": 0})
        return {"kept_docs": [], "passed_docs": [], "screening_decisions": [], "trace": trace}

    if llm.configured() and candidates:
        try:
            kept, passed, decisions = _llm_screen(intent, candidates)
            trace.append({"node": "ScreeningNode", "mode": "llm",
                          "kept": len(kept), "passed": len(passed)})
            return {"kept_docs": kept, "passed_docs": passed,
                    "screening_decisions": decisions, "trace": trace}
        except Exception as exc:
            log.warning("ScreeningNode LLM failed: %s", exc)

    kept, passed, decisions = _heuristic_screen(intent, candidates, threshold)
    trace.append({"node": "ScreeningNode", "mode": "heuristic",
                  "kept": len(kept), "passed": len(passed)})
    return {"kept_docs": kept, "passed_docs": passed,
            "screening_decisions": decisions, "trace": trace}


def _llm_screen(
    intent: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict], list[dict], list[dict]]:
    catalog = "\n".join(
        f"[{i}] id={doc.get('id')} | 标题：{doc.get('title')} | "
        f"摘要：{str(doc.get('abstract',''))[:250]}"
        for i, doc in enumerate(candidates, 1)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是证据筛选员。判断每份文档是否与用户问题相关。"
                '输出 JSON：{"decisions":[{"id":...,"decision":"keep"|"pass",'
                '"score":0~1,"reason":"..."}]}。只输出 JSON。'
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{intent.get('original_question','')}\n"
                f"主题：{', '.join(intent.get('topics', [])) or '未指定'}\n\n"
                f"候选文档：\n{catalog}"
            ),
        },
    ]
    data = llm.chat_json(messages)
    raw_decisions = (data.get("decisions") if isinstance(data, dict) else data) or []
    by_id = {str(item.get("id")): item for item in raw_decisions if isinstance(item, dict)}

    kept, passed, decisions = [], [], []
    for doc in candidates:
        verdict = by_id.get(str(doc.get("id", "")), {})
        decision = str(verdict.get("decision", "")).lower()
        score = _safe_float(verdict.get("score"), doc.get("score", 0.0))
        reason = str(verdict.get("reason", "")).strip()
        doc = dict(doc, decision_score=round(score, 4), reason=reason)
        if decision == "keep":
            kept.append(doc)
        else:
            decision = "pass"
            passed.append(doc)
        decisions.append({"document_id": doc.get("id"), "decision": decision,
                          "score": score, "reason": reason})

    if not kept and candidates:
        best = max(candidates, key=lambda d: d.get("score", 0))
        kept.append(dict(best, decision_score=best.get("score", 0)))
        passed = [d for d in passed if d.get("id") != best.get("id")]
    return kept, passed, decisions


def _heuristic_screen(
    intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    threshold: float,
) -> tuple[list[dict], list[dict], list[dict]]:
    import math, re
    TOKEN_RE = re.compile(r"[A-Za-z0-9_+-]+|[一-鿿]+")

    def tokenize(text: str) -> set[str]:
        out = set()
        for part in TOKEN_RE.findall(text.lower()):
            if any("一" <= ch <= "鿿" for ch in part):
                for n in (2, 3):
                    out.update(part[i:i+n] for i in range(max(0, len(part)-n+1)))
            elif len(part) > 1:
                out.add(part)
        return out

    q = tokenize(intent.get("retrieval_query", "") + " " + " ".join(intent.get("keywords", [])))
    kept, passed, decisions = [], [], []
    for doc in candidates:
        d = tokenize(doc.get("title", "") + " " + doc.get("abstract", ""))
        overlap = q & d
        semantic = len(overlap) / max(4.0, math.sqrt(max(1, len(q))) * 4)
        base_score = doc.get("score", 0.0)
        combined = round(0.68 * base_score + 0.32 * min(1.0, semantic), 4)
        doc = dict(doc, decision_score=combined)
        (kept if combined >= threshold else passed).append(doc)
        decisions.append({"document_id": doc.get("id"),
                          "decision": "keep" if combined >= threshold else "pass",
                          "score": combined})
    return kept, passed, decisions


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default
