# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An "ocean digital earth" demo built for project/interview presentation: spatial data
(NetCDF ocean variables) + knowledge retrieval (RAGFlow with local fallback) + multi-agent
LLM reasoning, wired into one demoable pipeline. The browser frontend (Cesium 3D globe +
OpenLayers 2D map) lets a user box-select a region; the backend slices NetCDF by bbox and
runs an agent pipeline that fuses regional statistics, retrieved evidence, and LLM reasoning
into a risk report with a traceable evidence chain.

The README.md is detailed and bilingual (Chinese) — read it for the product narrative,
render rules, data inventory, and interview talking points. This file covers the parts you
only learn by reading multiple source files.

## Commands

```powershell
# Full stack (frontend + both backends + GeoServer) via Docker
docker compose up -d --build          # serves UI at http://127.0.0.1:8000
# In Chinese-path dirs, disable BuildKit first to avoid a gRPC header bug:
$env:DOCKER_BUILDKIT='0'; $env:COMPOSE_DOCKER_CLI_BUILD='1'

# RAGFlow stack (separate compose project)
cd ragflow_stack; docker compose up -d

# Local dev — launches api_server.py detached with health polling + logs in logs/
python scripts/start_server.py --port 8000 --backend auto
python scripts/stop_server.py

# Diagnostics (health of API, agents, Docker, RAGFlow)
powershell -ExecutionPolicy Bypass -File scripts/diagnose.ps1
powershell -ExecutionPolicy Bypass -File scripts/diagnose.ps1 -SkipDocker -SkipRagFlow

# Offline evaluation -> writes eval/report.md + eval/report.json
python eval/run_eval.py --backend local [--top-k 6] [--ndcg]

# Tests: standalone scripts, NOT pytest. Run directly:
python scripts/test_data_ingestion.py   # ingestion + NetCDF round-trip in a temp dir
python scripts/test_mcp_stdio.py         # MCP stdio protocol smoke test

# MCP server (stdio, for Claude Desktop etc.)
python mcp_server.py            # stdio mode
python mcp_server.py --config   # print claude_desktop_config.json snippet
```

There is no central `requirements.txt` at the root; dependencies live in
`backend/requirements.txt` (Flask, netCDF4, numpy, pypdf, langgraph, langchain-openai).

## Environment / graceful degradation

Copy `.env.example` to `.env`. The two key degradation paths are the heart of why the demo
"always runs" — preserve them in any change:

- **No `DEEPSEEK_API_KEY`** → every LLM-driven agent falls back to a deterministic heuristic
  (`ocean_agents_demo/deepseek_client.py` reports unconfigured; agents branch on it).
- **No RAGFlow config** → retrieval falls back to local knowledge in
  `data/ocean_knowledge.json`, `data/knowledge_docs/`, `data/pdf_reports/`. Local retrieval
  uses an offline hash/ngram embedding hybrid-fused with keyword scores
  (`OCEAN_EMBEDDING_BACKEND=hash|keyword|sentence_transformers`).

## Architecture: there are THREE backends and TWO agent systems

This is the single most confusing thing about the repo. They coexist for backward
compatibility; know which you're editing.

### Backends (HTTP entry points)

| File | Server | Role | How it runs |
| --- | --- | --- | --- |
| `backend/app.py` | Flask + gunicorn | **Canonical Docker backend.** `create_app()` → `backend.app:app`. Routes under `/api/*` + `/.well-known/agent-card.json`. Adds data-ingestion endpoints + SQLite metadata DB. | docker-compose `backend` service, port 5000 |
| `api_server.py` | stdlib `http.server` | **Legacy single-file dev server**, embeds the whole HTML UI as a string. | `scripts/start_server.py` launches this for local dev |
| `geo_api.py` | stdlib `http.server` (SSE) | **GeoAgent v2 API** — streaming `/api/geo/*` endpoints, also re-exposes legacy `/api/ask` + `/api/query-ocean`. | docker-compose `geo-api` service, port 5001 |

`backend/app.py` imports from BOTH agent packages: `geo_agent.graph` and
`ocean_agents_demo.core`. nginx (`nginx/default.conf`) routes `/api/` → backend:5000,
`/geo-api/` → geo-api:5001 (buffering off for SSE), `/geoserver/` → geoserver:8080.

### Agent systems

- **`ocean_agents_demo/`** — the original multi-agent RAG report pipeline. `agents.py`
  defines an `Orchestrator` over stages Intent→Retrieval→Screening→Report→Critic (Critic is
  a real feedback loop that can re-trigger Report up to `max_revisions`). `core.py` holds the
  retrieval/ranking/embedding logic and intent schema (`intent.v2`) with entity/hazard/
  variable alias tables. Entry: `core.run_pipeline()`. Used by `api_server.py`, `geo_api.py`'s
  legacy route, `backend/app.py`'s `/api/agents/report`, and `eval/run_eval.py`.

- **`geo_agent/`** — GeoAgent v2, a LangGraph `StateGraph` (`graph.py`) with parallel fan-out
  (retrieval ∥ context) and a critic reflection loop. **Falls back to `run_linear()` manual
  sequencing if langgraph is not installed.** Nodes in `geo_agent/nodes/` (intent, retrieval,
  context, screening, reasoning, report, critic, plus planner/data/evaluator/visualization).
  Supports 4 domains: `marine | stargazing | biology | navigation | general` (see
  `state.py:GeoAgentState`). Tools live in `geo_agent/tools/` and are gated per-agent by an
  explicit allow-list in `geo_agent/tool_registry.py`.

### Shared data layer

- `ocean_agents_demo/nc_data.py` — NetCDF reading: bbox slicing, `step`/`max_points`
  downsampling, per-variable stats, render-mode classification, and ETOPO land mask (ocean
  variables over land return `null`). `dataset_summary()`, `query_grid()`, `list_variables()`.
- `ocean_agents_demo/ingestion.py` — scans/parses `.nc`/`.json`/`.mat`/docs into a SQLite
  metadata table (`SUPPORTED_DATA_EXTS`, `scan_and_ingest`, `parse_asset`). Backs
  `/api/data/*` endpoints in `backend/app.py`.

## Render-rule invariant (don't break this)

Render modes are decided by the backend per variable `category`, not the frontend.
Scalars (SST, salinity, chlorophyll, wave height) get heatmap/contour/points only. Particle
flow is reserved for true `vector` fields (real u/v) and is currently disabled — never fake
it from a scalar gradient. Ocean variables are land-masked via ETOPO; coarse cells touching
land are zeroed whole to keep land uncolored. See README "渲染规则".

## Conventions

- All modules use `from __future__ import annotations` and resolve `ROOT` via
  `Path(__file__).resolve().parents[N]`; scripts insert `ROOT` into `sys.path`.
- Paths default through env vars: `OCEAN_DATA_DIR`, `OCEAN_INSTANCE_DIR` (override `data/` and
  `instance/`).
- Code/comments are bilingual (Chinese + English) — match the surrounding style of the file
  you edit.
- The eval harness is deterministic on `--backend local`; it measures retrieval closure
  against gold labels in `eval/dataset.jsonl` (jsonl with `expected_keywords`, `expect_topics`,
  `relevant_documents`, `relevant_chunks`). Keep it deterministic so cross-commit comparison works.
