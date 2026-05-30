from __future__ import annotations

import os
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS

from ocean_agents_demo import deepseek_client
from ocean_agents_demo.agents import Orchestrator, agent_card, agent_catalog
from ocean_agents_demo.core import clear_doc_cache, rag_status, run_pipeline
from ocean_agents_demo.nc_data import dataset_summary, query_grid


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("OCEAN_DATA_DIR", ROOT / "data"))
NC_DIR = DATA_DIR / "nc_uploads"
PDF_DIR = DATA_DIR / "pdf_reports"
INSTANCE_DIR = Path(os.getenv("OCEAN_INSTANCE_DIR", ROOT / "instance"))
DB_PATH = INSTANCE_DIR / "ocean_demo.sqlite3"


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    init_db()

    @app.get("/api/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "service": "ocean-flask-api",
                "llm": deepseek_client.status(),
                "data_dir": str(DATA_DIR),
                "database": str(DB_PATH),
                "geoserver": {
                    "public_url": os.getenv("GEOSERVER_PUBLIC_URL", "/geoserver"),
                    "internal_url": os.getenv("GEOSERVER_INTERNAL_URL", "http://geoserver:8080/geoserver"),
                },
            }
        )

    @app.get("/api/project/status")
    def project_status() -> Any:
        """Portfolio-grade status endpoint for the whole demo stack."""
        rag = rag_status()
        ocean = dataset_summary()
        return jsonify(
            {
                "service": "ocean-digital-earth-rag",
                "status": "ok",
                "stack": {
                    "frontend": "nginx static site",
                    "backend": "flask + gunicorn",
                    "database": "sqlite",
                    "earth": "cesium + openlayers",
                    "geoserver": os.getenv("GEOSERVER_PUBLIC_URL", "/geoserver"),
                    "ragflow": rag["ragflow"],
                    "llm": rag["llm"],
                    "agents": agent_catalog(),
                },
                "data": {
                    "netcdf_datasets": len(ocean.get("datasets", [])),
                    "knowledge_documents": rag["local"]["document_count"],
                    "data_dir": str(DATA_DIR),
                    "database": str(DB_PATH),
                },
            }
        )

    @app.get("/api/datasets")
    def datasets() -> Any:
        scanned = sync_files()
        return jsonify({"files": scanned, "netcdf": dataset_summary()})

    @app.get("/api/ocean/datasets")
    def ocean_datasets() -> Any:
        return jsonify(dataset_summary())

    @app.post("/api/ocean/query")
    def ocean_query() -> Any:
        try:
            payload = request.get_json(silent=True) or {}
            result = query_grid(payload)
            log_query("ocean", {"payload": payload, "dataset": result.get("dataset"), "variable": result.get("variable")})
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    @app.post("/api/rag/ask")
    def ask() -> Any:
        try:
            payload = request.get_json(silent=True) or {}
            result = run_pipeline(
                question=str(payload.get("question", "")).strip(),
                top_k=int(payload.get("top_k", 6)),
                threshold=float(payload.get("threshold", 0.22)),
                backend=str(payload.get("backend", "auto")),
                trace=bool(payload.get("trace", False)),
                region=payload.get("region"),
                variables=payload.get("variables"),
            )
            log_query("rag", {"payload": _safe_payload(payload), "task_id": result.get("task_id"), "backend": result.get("backend")})
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    @app.get("/api/rag/status")
    def rag_info() -> Any:
        return jsonify(rag_status())

    @app.get("/api/agents")
    def agents_list() -> Any:
        """Capability listing of the multi-agent pipeline."""
        return jsonify({"agents": agent_catalog()})

    @app.get("/.well-known/agent-card.json")
    def agent_card_route() -> Any:
        """A2A-style AgentCard so other agents can discover this service."""
        return jsonify(agent_card())

    @app.post("/api/agents/report")
    def agents_report() -> Any:
        """Run the multi-agent report pipeline with full trace + critic loop."""
        try:
            payload = request.get_json(silent=True) or {}
            orchestrator = Orchestrator(max_revisions=int(payload.get("max_revisions", 1)))
            result = orchestrator.run(
                question=str(payload.get("question", "")).strip(),
                top_k=int(payload.get("top_k", 6)),
                threshold=float(payload.get("threshold", 0.22)),
                backend=str(payload.get("backend", "auto")),
                trace=bool(payload.get("trace", True)),
                region=payload.get("region"),
                variables=payload.get("variables"),
            )
            log_query("agent_report", {"payload": _safe_payload(payload), "task_id": result.get("task_id"), "backend": result.get("backend")})
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    @app.post("/api/sync")
    def sync() -> Any:
        clear_doc_cache()
        return jsonify({"files": sync_files()})

    return app


def init_db() -> None:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    NC_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            create table if not exists files (
                id integer primary key autoincrement,
                kind text not null,
                name text not null,
                path text not null unique,
                size integer not null,
                mtime real not null,
                created_at real not null
            )
            """
        )
        conn.execute(
            """
            create table if not exists queries (
                id integer primary key autoincrement,
                kind text not null,
                payload text not null,
                created_at real not null
            )
            """
        )


def log_query(kind: str, payload: dict[str, Any]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "insert into queries(kind, payload, created_at) values(?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), time.time()),
        )


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    for key in list(clean):
        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            clean[key] = "***"
    question = str(clean.get("question", ""))
    if len(question) > 1200:
        clean["question"] = question[:1200] + "..."
    return clean


def sync_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    patterns = [(DATA_DIR, "*.nc", "netcdf"), (NC_DIR, "**/*.nc", "netcdf"), (PDF_DIR, "**/*.pdf", "pdf")]
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        for directory, pattern, kind in patterns:
            directory.mkdir(parents=True, exist_ok=True)
            for path in sorted(directory.glob(pattern)):
                stat = path.stat()
                conn.execute(
                    """
                    insert into files(kind, name, path, size, mtime, created_at)
                    values(?,?,?,?,?,?)
                    on conflict(path) do update set size=excluded.size, mtime=excluded.mtime
                    """,
                    (kind, path.name, str(path), stat.st_size, stat.st_mtime, now),
                )
                rows.append({"kind": kind, "name": path.name, "path": str(path), "size": stat.st_size})
    return rows


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
