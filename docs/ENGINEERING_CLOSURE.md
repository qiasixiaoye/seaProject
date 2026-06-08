# Engineering Closure Map

This document maps the resume-facing claims to concrete implementation points in
the current Ocean Digital Earth RAG multi-agent project.

## Resume Bullet Mapping

| Resume point | Implementation location | Current closure |
| --- | --- | --- |
| Multi-source data parsing and ingestion | `ocean_agents_demo/ingestion.py`, `backend/app.py`, `scripts/test_data_ingestion.py` | NetCDF, JSON and MAT files are parsed into a normalized `dataset_assets` metadata table. The Flask API exposes `/api/data/upload`, `/api/data/sync`, and `/api/data/assets` so demos can show file parsing, standardized metadata, parse status and asset inventory. MAT falls back to header-level metadata when SciPy is unavailable. |
| Multi-Agent orchestration | `ocean_agents_demo/agents.py`, `geo_agent/graph.py`, `geo_agent/nodes/*` | Intent, Planner, Retrieval, Context/Data, Screening, Reasoning, Visualization, Report, Critic, Evaluator are wired. LangGraph is used when installed; otherwise deterministic linear or parallel fallback runs the same state contract. |
| RAG retrieval and evidence chain | `ocean_agents_demo/core.py`, `geo_agent/nodes/retrieval.py`, `geo_agent/nodes/screening.py` | RAGFlow-first retrieval falls back to local JSON/Markdown/PDF metadata. Local retrieval now supports configurable embedding scoring (`OCEAN_EMBEDDING_BACKEND=hash|keyword|sentence_transformers`) and records `keyword_score`, `embedding_score`, `vector_similarity`, route and backend. Retrieved docs are normalized through `EvidenceChunk` fields: `doc_id`, `chunk_id`, `page/pages`, `source`, `source_path`, rank, score and rerank reason. |
| MCP tool service | `mcp_server.py`, `scripts/test_mcp_stdio.py` | MCP stdio exposes `query_ocean_data`, `search_literature`, `run_ocean_report`, and `list_datasets`. Tool errors use `{code,message,details,retryable}`. Smoke tests cover tool listing, compact data query, invalid bbox, literature search and report generation. |
| Offline evaluation | `eval/run_eval.py`, `eval/dataset.jsonl`, `eval/report.json`, `eval/report.md` | Dataset rows include `relevant_documents` and `relevant_chunks`. Evaluation computes Recall@K, Precision@K, MRR and optional nDCG@K, plus citation coverage, trace schema completeness, missed gold and top retrieved evidence. Metadata records backend, top_k, commit hash and run time. |
| Trace observability | `geo_agent/state.py`, `geo_agent/nodes/evaluator.py`, `geo_agent/graph.py` | Final GeoAgent traces normalize every node to `node/mode/status/elapsed_ms/input_summary/output_summary/error`. Evaluator reports trace node coverage, trace schema completeness and tool success rate. |
| Evidence-grounded report loop | `ocean_agents_demo/core.py`, `geo_agent/nodes/report.py`, `geo_agent/nodes/critic.py`, `ocean_agents_demo/agents.py` | Reports receive explicit evidence labels such as `[E1: doc_id/chunk_id]`. Critic checks missing citations, unsupported strong claims and missing data-limitation statements even without an LLM key. |

## Current Completion

- The project now has a runnable offline eval path for local development:
  `python eval/run_eval.py --backend local`.
- MCP stdio has a deterministic smoke test:
  `python scripts/test_mcp_stdio.py`.
- Core files are expected to compile with:
  `python -m py_compile ocean_agents_demo/core.py geo_agent/graph.py geo_agent/state.py mcp_server.py eval/run_eval.py`.
- Local mode does not require `DEEPSEEK_API_KEY` or a running RAGFlow instance.
- Data ingestion has a local smoke test:
  `python scripts/test_data_ingestion.py`.
- Local embedding retrieval defaults to `OCEAN_EMBEDDING_BACKEND=hash`, a
  deterministic n-gram feature hashing model. It is intentionally dependency
  free so the backend container still starts without downloading model weights.
  Setting `OCEAN_EMBEDDING_BACKEND=keyword` disables vector scoring. Setting
  `OCEAN_EMBEDDING_BACKEND=sentence_transformers` and
  `OCEAN_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  enables a real multilingual embedding model when that optional package and
  weights are installed.

## Known Limits

- Local PDF retrieval is mostly metadata-based unless PDF parsing is explicitly enabled or documents are indexed in RAGFlow.
- The metadata store defaults to SQLite for local demos. The table contract is
  intentionally simple and can be moved to Postgres, but this repository does
  not yet include a live Postgres adapter or migration workflow.
- Gold labels in `eval/dataset.jsonl` are lightweight and intended for regression checks, not a full academic benchmark.
- nDCG assumes binary relevance. Graded relevance can be added by extending dataset rows with per-doc gains.
- Tool success rate depends on trace entries exposing tool calls. Deterministic local paths have few tool-loop events unless LLM tool calling is enabled.
- NetCDF evidence and literature evidence are both surfaced, but the report generator still uses template/LLM prompting rather than a formal citation verifier.

## Next Optimization Direction

1. Add graded relevance and negative-control questions to the eval set.
2. Persist per-run traces under `artifacts/traces/` for comparison across commits.
3. Parse selected PDFs into structured chunks by default, then refresh gold chunk ids.
4. Add a citation verifier that checks every final claim sentence against cited evidence ids.
5. Export MCP tool schemas and smoke-test results into CI artifacts.
