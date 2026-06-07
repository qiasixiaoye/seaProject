"""Offline evaluation harness for the ocean multi-agent RAG pipeline.

The evaluator is intentionally deterministic on the ``local`` backend. It
measures retrieval closure against dataset gold labels and records enough run
metadata to compare changes across commits.

Usage:
    python eval/run_eval.py --backend local [--top-k 6] [--ndcg]

Outputs:
    eval/report.json
    eval/report.md
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocean_agents_demo import deepseek_client  # noqa: E402
from ocean_agents_demo.core import run_pipeline  # noqa: E402

EVAL_DIR = ROOT / "eval"
DATASET = EVAL_DIR / "dataset.jsonl"


def load_dataset() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(DATASET.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        row.setdefault("relevant_documents", [])
        row.setdefault("relevant_chunks", [])
        if not row["relevant_documents"] and not row["relevant_chunks"]:
            raise ValueError(f"{DATASET}:{line_no} missing relevant_documents/relevant_chunks")
        rows.append(row)
    return rows


def doc_text(doc: dict[str, Any]) -> str:
    parts = [
        str(doc.get("title", "")),
        " ".join(str(t) for t in doc.get("topics", []) or []),
        str(doc.get("abstract", "")),
    ]
    return " ".join(parts).lower()


def retrieval_hit(kept: list[dict[str, Any]], keywords: list[str]) -> bool:
    blob = " ".join(doc_text(d) for d in kept)
    return any(str(k).lower() in blob for k in keywords)


def topic_recall(kept: list[dict[str, Any]], topics: list[str]) -> float:
    if not topics:
        return 1.0
    blob = " ".join(doc_text(d) for d in kept)
    hit = sum(1 for t in topics if str(t).lower() in blob)
    return round(hit / len(topics), 3)


def citation_coverage(report: str, kept: list[dict[str, Any]]) -> float:
    if not kept:
        return 0.0
    report_lower = report.lower()
    has_section = any(marker in report_lower for marker in ("reference", "source", "citation"))
    has_section = has_section or any(marker in report for marker in ("参考", "来源", "证据"))
    mentioned = any(d.get("title") and str(d["title"])[:12] in report for d in kept)
    return 1.0 if (has_section or mentioned) else 0.0


def llm_groundedness(report: str, kept: list[dict[str, Any]]) -> float | None:
    if not deepseek_client.configured():
        return None
    evidence = "\n".join(
        f"- {d.get('title')}: {str(d.get('abstract') or '')[:200]}" for d in kept
    ) or "(no evidence)"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a groundedness judge. Score whether the report's claims "
                "are supported by the supplied evidence. Return only JSON: "
                "{\"groundedness\":0-1,\"note\":\"...\"}."
            ),
        },
        {"role": "user", "content": f"Evidence:\n{evidence}\n\nReport:\n{report[:3000]}"},
    ]
    try:
        data = deepseek_client.chat_json(messages)
        return round(float(data.get("groundedness", 0)), 3)
    except Exception:
        return None


def _ranked_documents(out: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    docs = list(out.get("kept_documents", []) or []) + list(out.get("passed_documents", []) or [])

    def rank_key(doc: dict[str, Any]) -> tuple[float, float, float]:
        rank_after = _num(doc.get("rank_after"), default=9999)
        rank_before = _num(doc.get("rank_before"), default=9999)
        score = _num(doc.get("rerank_score"), default=_num(doc.get("score"), default=0.0))
        return (rank_after, rank_before, -score)

    deduped: dict[str, dict[str, Any]] = {}
    for doc in sorted(docs, key=rank_key):
        key = str(doc.get("chunk_id") or doc.get("id") or doc.get("title"))
        deduped.setdefault(key, doc)
    return list(deduped.values())[:top_k]


def _ids(doc: dict[str, Any]) -> tuple[str, str]:
    doc_id = str(doc.get("doc_id") or doc.get("id") or "")
    chunk_id = str(doc.get("chunk_id") or doc.get("id") or "")
    return doc_id, chunk_id


def retrieval_metrics(
    retrieved: list[dict[str, Any]],
    gold_docs: list[str],
    gold_chunks: list[str],
    k: int,
    include_ndcg: bool,
) -> dict[str, Any]:
    gold_doc_set = {str(x) for x in gold_docs if str(x)}
    gold_chunk_set = {str(x) for x in gold_chunks if str(x)}
    gold_units = gold_chunk_set or gold_doc_set
    if not gold_units:
        return {"recall_at_k": None, "precision_at_k": None, "mrr": None, "ndcg_at_k": None}

    hits: list[int] = []
    top = retrieved[:k]
    for doc in top:
        doc_id, chunk_id = _ids(doc)
        hit = (chunk_id in gold_chunk_set) or (doc_id in gold_doc_set)
        hits.append(1 if hit else 0)

    hit_count = sum(hits)
    first_hit = next((i + 1 for i, hit in enumerate(hits) if hit), None)
    metrics = {
        "recall_at_k": round(hit_count / len(gold_units), 4),
        "precision_at_k": round(hit_count / max(1, len(top)), 4),
        "mrr": round(1 / first_hit, 4) if first_hit else 0.0,
        "ndcg_at_k": None,
    }
    if include_ndcg:
        dcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(hits))
        ideal_hits = [1] * min(len(gold_units), k)
        idcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(ideal_hits))
        metrics["ndcg_at_k"] = round(dcg / idcg, 4) if idcg else 0.0
    return metrics


def missed_gold(retrieved: list[dict[str, Any]], gold_docs: list[str], gold_chunks: list[str]) -> dict[str, list[str]]:
    retrieved_docs: set[str] = set()
    retrieved_chunks: set[str] = set()
    for doc in retrieved:
        doc_id, chunk_id = _ids(doc)
        if doc_id:
            retrieved_docs.add(doc_id)
        if chunk_id:
            retrieved_chunks.add(chunk_id)
    return {
        "documents": [doc for doc in gold_docs if doc not in retrieved_docs],
        "chunks": [chunk for chunk in gold_chunks if chunk not in retrieved_chunks],
    }


def trace_metrics(trace: list[dict[str, Any]]) -> dict[str, Any]:
    required = {"node", "mode", "status", "elapsed_ms", "input_summary", "output_summary", "error"}
    if not trace:
        return {"trace_completeness": 0.0, "trace_event_count": 0}
    complete = sum(1 for item in trace if required <= set(item.keys()))
    return {
        "trace_completeness": round(complete / len(trace), 3),
        "trace_event_count": len(trace),
    }


def run(backend: str, limit: int | None, top_k: int, include_ndcg: bool) -> dict[str, Any]:
    started = time.time()
    rows = load_dataset()
    if limit:
        rows = rows[:limit]
    results: list[dict[str, Any]] = []
    for row in rows:
        t0 = time.time()
        out = run_pipeline(row["question"], top_k=top_k, backend=backend, trace=True)
        elapsed = round((time.time() - t0) * 1000, 1)
        kept = out.get("kept_documents", []) or []
        retrieved = _ranked_documents(out, top_k)
        metric = retrieval_metrics(
            retrieved,
            row.get("relevant_documents", []),
            row.get("relevant_chunks", []),
            top_k,
            include_ndcg,
        )
        rec = {
            "id": row["id"],
            "question": row["question"],
            "gold": {
                "relevant_documents": row.get("relevant_documents", []),
                "relevant_chunks": row.get("relevant_chunks", []),
            },
            "metrics": {
                **metric,
                "legacy_retrieval_hit": int(retrieval_hit(kept, row.get("expected_keywords", []))),
                "topic_recall": topic_recall(kept, row.get("expect_topics", [])),
                "citation_coverage": citation_coverage(out.get("report", ""), kept),
                **trace_metrics(out.get("trace", []) or []),
            },
            "counts": {
                "kept": len(kept),
                "retrieved": len(retrieved),
                "passed": len(out.get("passed_documents", []) or []),
            },
            "missed_gold": missed_gold(
                retrieved,
                row.get("relevant_documents", []),
                row.get("relevant_chunks", []),
            ),
            "top_retrieved": [_retrieved_summary(doc, rank) for rank, doc in enumerate(retrieved, 1)],
            "revisions": out.get("revisions", 0),
            "latency_ms": elapsed,
            "groundedness": llm_groundedness(out.get("report", ""), kept),
        }
        results.append(rec)
        print(
            f"[{rec['id']}] R@{top_k}={metric['recall_at_k']} "
            f"P@{top_k}={metric['precision_at_k']} MRR={metric['mrr']} "
            f"kept={rec['counts']['kept']} {elapsed}ms"
        )

    summary = summarize(results, backend, top_k, include_ndcg, started)
    return {"metadata": summary.pop("metadata"), "summary": summary, "results": results}


def _retrieved_summary(doc: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "doc_id": doc.get("doc_id") or doc.get("id"),
        "chunk_id": doc.get("chunk_id") or doc.get("id"),
        "title": doc.get("title"),
        "page": doc.get("page"),
        "source": doc.get("source"),
        "score": doc.get("score"),
        "rerank_score": doc.get("rerank_score"),
        "rerank_reason": doc.get("rerank_reason"),
    }


def summarize(
    results: list[dict[str, Any]],
    backend: str,
    top_k: int,
    include_ndcg: bool,
    started: float,
) -> dict[str, Any]:
    def mean_metric(key: str) -> float | None:
        vals = [
            r["metrics"][key]
            for r in results
            if isinstance(r.get("metrics", {}).get(key), (int, float))
        ]
        return round(statistics.mean(vals), 4) if vals else None

    grounded_vals = [r["groundedness"] for r in results if r["groundedness"] is not None]
    elapsed_ms = round((time.time() - started) * 1000, 1)
    return {
        "n": len(results),
        "backend": backend,
        "top_k": top_k,
        "llm_configured": deepseek_client.configured(),
        "recall_at_k": mean_metric("recall_at_k"),
        "precision_at_k": mean_metric("precision_at_k"),
        "mrr": mean_metric("mrr"),
        "ndcg_at_k": mean_metric("ndcg_at_k") if include_ndcg else None,
        "legacy_retrieval_hit_rate": mean_metric("legacy_retrieval_hit"),
        "avg_topic_recall": mean_metric("topic_recall"),
        "avg_citation_coverage": mean_metric("citation_coverage"),
        "avg_trace_completeness": mean_metric("trace_completeness"),
        "avg_latency_ms": round(statistics.mean([r["latency_ms"] for r in results]), 3) if results else 0.0,
        "avg_groundedness": round(statistics.mean(grounded_vals), 3) if grounded_vals else None,
        "metadata": {
            "backend": backend,
            "top_k": top_k,
            "commit_hash": commit_hash(),
            "started_at_unix": round(started, 3),
            "elapsed_ms": elapsed_ms,
            "dataset": str(DATASET.relative_to(ROOT)),
            "report_json": str((EVAL_DIR / "report.json").relative_to(ROOT)),
            "report_md": str((EVAL_DIR / "report.md").relative_to(ROOT)),
        },
    }


def commit_hash() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def write_reports(report: dict[str, Any]) -> None:
    (EVAL_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    s = report["summary"]
    m = report["metadata"]
    lines = [
        "# Ocean Multi-Agent RAG Evaluation Report",
        "",
        "## Summary",
        "",
        f"- backend: `{s['backend']}`",
        f"- top_k: `{s['top_k']}`",
        f"- commit: `{m.get('commit_hash')}`",
        f"- samples: `{s['n']}`",
        f"- Recall@K: **{s['recall_at_k']}**",
        f"- Precision@K: **{s['precision_at_k']}**",
        f"- MRR: **{s['mrr']}**",
        f"- nDCG@K: **{s['ndcg_at_k'] if s['ndcg_at_k'] is not None else 'N/A'}**",
        f"- citation coverage: **{s['avg_citation_coverage']}**",
        f"- trace completeness: **{s['avg_trace_completeness']}**",
        f"- avg latency: **{s['avg_latency_ms']} ms**",
        "",
        "## Per-Question Details",
        "",
    ]
    for r in report["results"]:
        metrics = r["metrics"]
        lines.extend([
            f"### {r['id']}",
            "",
            f"Question: {r['question']}",
            "",
            (
                f"- Recall@K={metrics['recall_at_k']} | Precision@K={metrics['precision_at_k']} | "
                f"MRR={metrics['mrr']} | nDCG@K={metrics['ndcg_at_k']}"
            ),
            f"- missed gold documents: {', '.join(r['missed_gold']['documents']) or 'none'}",
            f"- missed gold chunks: {', '.join(r['missed_gold']['chunks']) or 'none'}",
            "",
            "| rank | doc_id | chunk_id | score | source |",
            "|---|---|---|---|---|",
        ])
        for item in r["top_retrieved"]:
            source = str(item.get("source") or "").replace("|", "/")[:80]
            lines.append(
                f"| {item['rank']} | {item.get('doc_id')} | {item.get('chunk_id')} | "
                f"{item.get('rerank_score') or item.get('score')} | {source} |"
            )
        lines.append("")
    (EVAL_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="local", choices=["local", "auto", "ragflow"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--ndcg", action="store_true", help="compute nDCG@K")
    args = parser.parse_args()
    report = run(args.backend, args.limit, args.top_k, args.ndcg)
    write_reports(report)
    print("\n=== SUMMARY ===")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"\nReports written to {EVAL_DIR / 'report.md'} and {EVAL_DIR / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
