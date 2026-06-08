from __future__ import annotations

import os
import json
import sqlite3
import time
import math as _math
from pathlib import Path
from typing import Any
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, request
from flask_cors import CORS

from geo_agent import graph as geo_graph
from geo_agent.catalog import agent_card, agent_catalog
from ocean_agents_demo import deepseek_client
from ocean_agents_demo.core import clear_doc_cache, rag_status, run_pipeline
from ocean_agents_demo.ingestion import (
    SUPPORTED_DATA_EXTS,
    ingestion_summary,
    init_metadata_db,
    list_asset_records,
    parse_asset,
    scan_and_ingest,
    upsert_asset_record,
)
from ocean_agents_demo.nc_data import dataset_summary, query_grid


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("OCEAN_DATA_DIR", ROOT / "data"))
NC_DIR = DATA_DIR / "nc_uploads"
DATA_UPLOAD_DIR = DATA_DIR / "data_uploads"
PDF_DIR = DATA_DIR / "pdf_reports"
KNOWLEDGE_DIR = DATA_DIR / "knowledge_docs"
INSTANCE_DIR = Path(os.getenv("OCEAN_INSTANCE_DIR", ROOT / "instance"))
DB_PATH = INSTANCE_DIR / "ocean_demo.sqlite3"

ALLOWED_UPLOAD_EXTS = {".pdf", ".md", ".txt", ".json"}
ALLOWED_DATA_UPLOAD_EXTS = SUPPORTED_DATA_EXTS
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    init_db()

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({
            "status": "ok",
            "service": "ocean-flask-api",
            "llm": deepseek_client.status(),
            "data_dir": str(DATA_DIR),
            "database": str(DB_PATH),
            "geoserver": {
                "public_url": os.getenv("GEOSERVER_PUBLIC_URL", "/geoserver"),
                "internal_url": os.getenv("GEOSERVER_INTERNAL_URL", "http://geoserver:8080/geoserver"),
            },
        })

    @app.get("/api/project/status")
    def project_status() -> Any:
        rag = rag_status()
        ocean = dataset_summary()
        ingestion = ingestion_summary(DB_PATH)
        return jsonify({
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
                "ingested_assets": ingestion["asset_count"],
                "ingested_by_kind": ingestion["by_kind"],
                "knowledge_documents": rag["local"]["document_count"],
                "data_dir": str(DATA_DIR),
                "database": str(DB_PATH),
            },
        })

    @app.get("/api/datasets")
    def datasets() -> Any:
        scanned = sync_files()
        return jsonify({
            "files": scanned,
            "netcdf": dataset_summary(),
            "ingestion": ingestion_summary(DB_PATH),
        })

    @app.get("/api/ocean/datasets")
    def ocean_datasets() -> Any:
        return jsonify(dataset_summary())

    @app.get("/api/data/assets")
    def data_assets() -> Any:
        limit = int(request.args.get("limit", 200))
        return jsonify({
            "summary": ingestion_summary(DB_PATH),
            "assets": list_asset_records(DB_PATH, limit=limit),
        })

    @app.post("/api/data/sync")
    def data_sync() -> Any:
        records = scan_and_ingest(db_path=DB_PATH)
        log_query("data_sync", {"count": len(records)})
        return jsonify({
            "success": True,
            "count": len(records),
            "summary": ingestion_summary(DB_PATH),
            "assets": records,
        })

    @app.post("/api/data/upload")
    def data_upload() -> Any:
        """Upload scientific data files and immediately register parse metadata."""
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        filename = secure_filename(f.filename)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_DATA_UPLOAD_EXTS:
            return jsonify({
                "error": f"File type '{ext}' not allowed. Supported: {sorted(ALLOWED_DATA_UPLOAD_EXTS)}"
            }), 400

        dest_dir = NC_DIR if ext == ".nc" else DATA_UPLOAD_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        save_path = _unique_path(dest_dir / filename)

        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            return jsonify({"error": f"File too large ({size} bytes, max {MAX_UPLOAD_BYTES})"}), 413

        f.save(str(save_path))
        record = parse_asset(save_path)
        upsert_asset_record(record, DB_PATH)
        log_query("data_upload", {"filename": filename, "dest": str(save_path), "size": size, "status": record["status"]})
        return jsonify({
            "success": True,
            "asset": record,
            "summary": ingestion_summary(DB_PATH),
        })

    @app.post("/api/ocean/query")
    def ocean_query() -> Any:
        try:
            payload = request.get_json(silent=True) or {}
            result = query_grid(payload)
            log_query("ocean", {"payload": payload, "dataset": result.get("dataset"), "variable": result.get("variable")})
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    @app.post("/api/ocean/vector-field")
    def ocean_vector_field() -> Any:
        """联合查询真实 u/v 矢量场，返回 u_grid / v_grid / speed_grid。"""
        try:
            payload = request.get_json(silent=True) or {}
            bounds      = payload.get("bounds") or payload
            west        = float(bounds.get("west", 117))
            east        = float(bounds.get("east", 127))
            south       = float(bounds.get("south", 18))
            north       = float(bounds.get("north", 26))
            max_pts     = max(100, min(50000, int(payload.get("max_points", 4000))))
            time_index  = max(0, int(payload.get("time_index") or 0))
            depth_index = max(0, int(payload.get("depth_index") or 0))
            step        = max(0, int(payload.get("step") or 0))
            dataset     = str(payload.get("dataset") or "")
            u_dataset   = str(payload.get("u_dataset") or dataset)
            v_dataset   = str(payload.get("v_dataset") or dataset)
            u_variable  = str(payload.get("u_variable") or "water_u")
            v_variable  = str(payload.get("v_variable") or "water_v")

            u_result = query_grid({
                "dataset": u_dataset, "variable": u_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "max_points": max_pts, "step": step,
                "time_index": time_index, "depth_index": depth_index,
            })
            if "u_grid" in u_result:
                return jsonify(u_result)

            v_result = query_grid({
                "dataset": v_dataset, "variable": v_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "max_points": max_pts, "step": step,
                "time_index": time_index, "depth_index": depth_index,
            })
            u_grid = u_result.get("values", [])
            v_grid = v_result.get("values", [])
            lats   = u_result.get("lats", [])
            lons   = u_result.get("lons", [])

            speed_grid: list[list[float | None]] = []
            flat_speed: list[float] = []
            for i, u_row in enumerate(u_grid):
                s_row: list[float | None] = []
                for j, u_val in enumerate(u_row):
                    v_val = v_grid[i][j] if i < len(v_grid) and j < len(v_grid[i]) else None
                    if u_val is None or v_val is None:
                        s_row.append(None)
                    else:
                        spd = round(_math.hypot(float(u_val), float(v_val)), 4)
                        s_row.append(spd)
                        flat_speed.append(spd)
                speed_grid.append(s_row)

            if not flat_speed:
                return jsonify({"error": "ValueError", "message": "no valid vector data in selected region"}), 400

            log_query("vector_field", {
                "u_dataset": u_dataset, "v_dataset": v_dataset,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
            })
            return jsonify({
                "u_dataset": u_dataset, "v_dataset": v_dataset,
                "u_variable": u_variable, "v_variable": v_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "lats": lats, "lons": lons,
                "u_grid": u_grid, "v_grid": v_grid, "speed_grid": speed_grid,
                "time_index": time_index, "depth_index": depth_index,
                "category": "vector",
                "render_modes": ["heatmap", "particles", "contour", "points"],
                "particle_ready": True,
                "shape": {"lat": len(lats), "lon": len(lons)},
                "stats": {
                    "min": round(min(flat_speed), 4),
                    "max": round(max(flat_speed), 4),
                    "mean": round(sum(flat_speed) / len(flat_speed), 4),
                    "count": len(flat_speed),
                },
                "render_time_ms": (u_result.get("render_time_ms") or 0) + (v_result.get("render_time_ms") or 0),
            })
        except Exception as exc:
            return jsonify({"error": type(exc).__name__, "message": str(exc)}), 500

    # ── RAGFlow 知识库管理 ────────────────────────────────────────────────────

    @app.get("/api/rag/status")
    def rag_info() -> Any:
        return jsonify(rag_status())

    @app.get("/api/rag/documents")
    def rag_documents() -> Any:
        """返回本地知识库文档列表 + RAGFlow 文档列表。"""
        status = rag_status()
        local_docs = status["local"]["documents"]
        ragflow_info = status["ragflow"]
        ragflow_docs: list[dict] = []
        if ragflow_info.get("configured"):
            try:
                from ocean_agents_demo.core import ragflow_catalog
                catalog = ragflow_catalog()
                for doc in catalog.get("documents", {}).values():
                    ragflow_docs.append({
                        "id": doc.get("id"),
                        "name": doc.get("name") or doc.get("id"),
                        "type": doc.get("type", ""),
                        "size": doc.get("size", 0),
                        "status": doc.get("run", ""),
                        "dataset_name": doc.get("dataset_name", ""),
                        "source": "ragflow",
                    })
            except Exception as exc:
                ragflow_info["doc_list_error"] = str(exc)
        return jsonify({
            "local": {
                "count": len(local_docs),
                "documents": [
                    {"id": d["id"], "title": d["title"], "kind": d["kind"], "source": "local"}
                    for d in local_docs
                ],
            },
            "ragflow": {
                "configured": ragflow_info.get("configured", False),
                "count": len(ragflow_docs),
                "documents": ragflow_docs,
            },
        })

    @app.post("/api/rag/upload")
    def rag_upload() -> Any:
        """上传知识文档到本地知识库。支持 .pdf / .md / .txt / .json。
        multipart/form-data: file=<file>  [dest=knowledge_docs|pdf_reports]
        """
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400

        filename = secure_filename(f.filename)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            return jsonify({
                "error": f"File type '{ext}' not allowed. Supported: {sorted(ALLOWED_UPLOAD_EXTS)}"
            }), 400

        dest_name = request.form.get("dest", "")
        if ext == ".pdf":
            dest_dir = PDF_DIR / "cn"
            dest_dir.mkdir(parents=True, exist_ok=True)
        else:
            dest_dir = KNOWLEDGE_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)

        save_path = dest_dir / filename
        # 防止覆盖：加 _1 _2 等后缀
        stem, suffix = save_path.stem, save_path.suffix
        counter = 1
        while save_path.exists():
            save_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        # 大小检查
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            return jsonify({"error": f"File too large ({size} bytes, max {MAX_UPLOAD_BYTES})"}), 413

        f.save(str(save_path))
        clear_doc_cache()  # 强制下次查询时重新加载知识库

        log_query("rag_upload", {"filename": filename, "dest": str(save_path), "size": size})
        return jsonify({
            "success": True,
            "filename": save_path.name,
            "path": str(save_path),
            "size": size,
            "message": f"文件已保存至 {save_path.relative_to(ROOT)}，知识库缓存已清除。",
        })

    @app.post("/api/rag/sync-local")
    def rag_sync_local() -> Any:
        """重新扫描本地知识库目录并刷新缓存。"""
        clear_doc_cache()
        status = rag_status()
        return jsonify({
            "success": True,
            "local_count": status["local"]["document_count"],
            "message": "本地知识库已重新扫描。",
            "documents": status["local"]["documents"],
        })

    # ── Agents ───────────────────────────────────────────────────────────────

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

    @app.get("/api/agents")
    def agents_list() -> Any:
        return jsonify({"agents": agent_catalog()})

    @app.get("/.well-known/agent-card.json")
    def agent_card_route() -> Any:
        return jsonify(agent_card())

    @app.post("/api/agents/report")
    def agents_report() -> Any:
        try:
            payload = request.get_json(silent=True) or {}
            result = geo_graph.run(
                question=str(payload.get("question", "")).strip(),
                bbox=payload.get("bbox") or payload.get("region"),
                domain=payload.get("domain"),
                variables=payload.get("variables") or [],
                backend=str(payload.get("backend", "auto")),
                top_k=int(payload.get("top_k", 6)),
                threshold=float(payload.get("threshold", 0.22)),
                max_revisions=int(payload.get("max_revisions", 1)),
                trace_enabled=bool(payload.get("trace", True)),
                use_parallel=bool(payload.get("parallel", True)),
            )
            log_query("agent_report", {
                "payload": _safe_payload(payload),
                "task_id": result.get("task_id"),
                "backend": result.get("backend_used"),
            })
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
    DATA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    init_metadata_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            type TEXT,
            payload TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_query(qtype: str, data: dict) -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO query_log(ts, type, payload) VALUES (?, ?, ?)",
            (time.time(), qtype, json.dumps(data, ensure_ascii=False, default=str)[:4096]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def sync_files() -> list[dict]:
    results = []
    for directory in (DATA_DIR, NC_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for f in sorted(directory.iterdir()):
            results.append({
                "name": f.name,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "dir": str(directory),
            })
    return results


def _unique_path(path: Path) -> Path:
    stem, suffix = path.stem, path.suffix
    out = path
    counter = 1
    while out.exists():
        out = path.parent / f"{stem}_{counter}{suffix}"
        counter += 1
    return out


def _safe_payload(payload: dict) -> dict:
    safe = dict(payload)
    safe.pop("api_key", None)
    safe.pop("password", None)
    return safe


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
