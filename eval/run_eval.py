"""Offline evaluation harness for the ocean multi-agent RAG pipeline.

Runs a fixed question set through the pipeline and reports retrieval and report
quality metrics. The deterministic metrics (retrieval hit-rate, topic recall,
citation coverage, latency, critic revisions) run WITHOUT any API key on the
``local`` backend. If ``DEEPSEEK_API_KEY`` is set, an extra LLM-as-judge
"groundedness" score is computed per question.

Usage:
    python eval/run_eval.py [--backend local|auto|ragflow] [--limit N]

Outputs eval/report.md and eval/report.json.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocean_agents_demo import deepseek_client  # noqa: E402
from ocean_agents_demo.core import run_pipeline  # noqa: E402

EVAL_DIR = ROOT / "eval"
DATASET = EVAL_DIR / "dataset.jsonl"


def load_dataset() -> list[dict]:
    rows = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def doc_text(doc: dict) -> str:
    parts = [doc.get("title", ""), " ".join(doc.get("topics", []) or []), doc.get("abstract", "")]
    return " ".join(parts).lower()


def retrieval_hit(kept: list[dict], keywords: list[str]) -> bool:
    blob = " ".join(doc_text(d) for d in kept)
    return any(k.lower() in blob for k in keywords)


def topic_recall(kept: list[dict], topics: list[str]) -> float:
    if not topics:
        return 1.0
    blob = " ".join(doc_text(d) for d in kept)
    hit = sum(1 for t in topics if t.lower() in blob)
    return round(hit / len(topics), 3)


def citation_coverage(report: str, kept: list[dict]) -> float:
    if not kept:
        return 0.0
    has_section = "参考来源" in report or "来源" in report
    mentioned = any(d.get("title") and d["title"][:12] in report for d in kept)
    return 1.0 if (has_section or mentioned) else 0.0


def llm_groundedness(report: str, kept: list[dict]) -> float | None:
    if not deepseek_client.configured():
        return None
    evidence = "\n".join(f"- {d.get('title')}: {(d.get('abstract') or '')[:200]}" for d in kept) or "（无证据）"
    messages = [
        {"role": "system", "content": (
            "你是报告忠实度评审。判断报告中的结论是否都能由给定证据支撑，"
            "输出 JSON：{\"groundedness\":0~1, \"note\":\"...\"}。只输出 JSON。")},
        {"role": "user", "content": f"证据：\n{evidence}\n\n报告：\n{report[:3000]}"},
    ]
    try:
        data = deepseek_client.chat_json(messages)
        return round(float(data.get("groundedness", 0)), 3)
    except Exception:
        return None


def run(backend: str, limit: int | None) -> dict:
    rows = load_dataset()
    if limit:
        rows = rows[:limit]
    results = []
    for row in rows:
        t0 = time.time()
        out = run_pipeline(row["question"], backend=backend, trace=False)
        elapsed = round((time.time() - t0) * 1000, 1)
        kept = out.get("kept_documents", [])
        rec = {
            "id": row["id"],
            "question": row["question"],
            "kept": len(kept),
            "candidates": len(kept) + len(out.get("passed_documents", [])),
            "retrieval_hit": int(retrieval_hit(kept, row.get("expected_keywords", []))),
            "topic_recall": topic_recall(kept, row.get("expect_topics", [])),
            "citation_coverage": citation_coverage(out.get("report", ""), kept),
            "revisions": out.get("revisions", 0),
            "latency_ms": elapsed,
            "groundedness": llm_groundedness(out.get("report", ""), kept),
        }
        results.append(rec)
        print(f"[{rec['id']}] hit={rec['retrieval_hit']} topic_recall={rec['topic_recall']} "
              f"cite={rec['citation_coverage']} kept={rec['kept']} {rec['latency_ms']}ms")

    def mean(key: str) -> float:
        vals = [r[key] for r in results if isinstance(r.get(key), (int, float))]
        return round(statistics.mean(vals), 3) if vals else 0.0

    grounded_vals = [r["groundedness"] for r in results if r["groundedness"] is not None]
    summary = {
        "n": len(results),
        "backend": backend,
        "llm_configured": deepseek_client.configured(),
        "retrieval_hit_rate": mean("retrieval_hit"),
        "avg_topic_recall": mean("topic_recall"),
        "avg_citation_coverage": mean("citation_coverage"),
        "avg_revisions": mean("revisions"),
        "avg_latency_ms": mean("latency_ms"),
        "avg_groundedness": round(statistics.mean(grounded_vals), 3) if grounded_vals else None,
    }
    return {"summary": summary, "results": results}


def write_reports(report: dict) -> None:
    (EVAL_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s = report["summary"]
    lines = [
        "# 海洋多 Agent RAG 评测报告",
        "",
        f"- 样本数：{s['n']}　|　检索后端：`{s['backend']}`　|　LLM：{'已配置' if s['llm_configured'] else '未配置(确定性指标)'}",
        f"- 检索命中率 retrieval_hit_rate：**{s['retrieval_hit_rate']}**",
        f"- 平均主题召回 avg_topic_recall：**{s['avg_topic_recall']}**",
        f"- 平均引用覆盖 avg_citation_coverage：**{s['avg_citation_coverage']}**",
        f"- 平均 Critic 修订轮次 avg_revisions：{s['avg_revisions']}",
        f"- 平均端到端耗时 avg_latency_ms：{s['avg_latency_ms']} ms",
        f"- 平均忠实度 avg_groundedness：{s['avg_groundedness'] if s['avg_groundedness'] is not None else 'N/A（需配置 LLM）'}",
        "",
        "## 逐题明细",
        "",
        "| id | 命中 | 主题召回 | 引用覆盖 | 保留 | 候选 | 修订 | 耗时(ms) | 忠实度 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        g = r["groundedness"] if r["groundedness"] is not None else "-"
        lines.append(
            f"| {r['id']} | {r['retrieval_hit']} | {r['topic_recall']} | {r['citation_coverage']} | "
            f"{r['kept']} | {r['candidates']} | {r['revisions']} | {r['latency_ms']} | {g} |"
        )
    (EVAL_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="local", choices=["local", "auto", "ragflow"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = run(args.backend, args.limit)
    write_reports(report)
    print("\n=== SUMMARY ===")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"\nReports written to {EVAL_DIR/'report.md'} and {EVAL_DIR/'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
