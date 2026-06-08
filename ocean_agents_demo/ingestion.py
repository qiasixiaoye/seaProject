from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("OCEAN_DATA_DIR", ROOT / "data"))
INSTANCE_DIR = Path(os.getenv("OCEAN_INSTANCE_DIR", ROOT / "instance"))
DB_PATH = INSTANCE_DIR / "ocean_demo.sqlite3"

SUPPORTED_DATA_EXTS = {".nc", ".json", ".mat"}
SUPPORTED_KNOWLEDGE_EXTS = {".pdf", ".md", ".txt"}
SUPPORTED_EXTS = SUPPORTED_DATA_EXTS | SUPPORTED_KNOWLEDGE_EXTS


def init_metadata_db(db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dataset_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_key TEXT UNIQUE,
                name TEXT,
                ext TEXT,
                kind TEXT,
                parser TEXT,
                status TEXT,
                path TEXT,
                size INTEGER,
                mtime REAL,
                metadata TEXT,
                created_at REAL,
                updated_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dataset_assets_kind ON dataset_assets(kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dataset_assets_status ON dataset_assets(status)")


def scan_and_ingest(
    roots: list[Path] | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_metadata_db(db_path)
    records: list[dict[str, Any]] = []
    for path in _iter_asset_files(roots or _default_roots()):
        record = parse_asset(path)
        upsert_asset_record(record, db_path)
        records.append(record)
    return records


def parse_asset(path: Path) -> dict[str, Any]:
    path = Path(path)
    ext = path.suffix.lower()
    stat = path.stat()
    metadata: dict[str, Any]
    parser = "unknown"
    status = "parsed"
    kind = _asset_kind(ext)
    try:
        if ext == ".nc":
            parser = "netcdf_metadata"
            metadata = _parse_netcdf(path)
        elif ext == ".json":
            parser = "json_schema_summary"
            metadata = _parse_json(path)
        elif ext == ".mat":
            parser = "mat_metadata"
            metadata = _parse_mat(path)
        elif ext in SUPPORTED_KNOWLEDGE_EXTS:
            parser = "knowledge_file"
            metadata = _parse_knowledge_file(path)
        else:
            parser = "unsupported"
            status = "unsupported"
            metadata = {"error": f"unsupported extension: {ext}"}
    except Exception as exc:
        status = "error"
        metadata = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "asset_key": _asset_key(path),
        "name": path.name,
        "ext": ext,
        "kind": kind,
        "parser": parser,
        "status": status,
        "path": str(path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "metadata": metadata,
    }


def upsert_asset_record(record: dict[str, Any], db_path: Path | None = None) -> None:
    init_metadata_db(db_path)
    now = time.time()
    with sqlite3.connect(db_path or DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO dataset_assets (
                asset_key, name, ext, kind, parser, status, path, size, mtime,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_key) DO UPDATE SET
                name=excluded.name,
                ext=excluded.ext,
                kind=excluded.kind,
                parser=excluded.parser,
                status=excluded.status,
                path=excluded.path,
                size=excluded.size,
                mtime=excluded.mtime,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (
                record["asset_key"],
                record["name"],
                record["ext"],
                record["kind"],
                record["parser"],
                record["status"],
                record["path"],
                int(record["size"]),
                float(record["mtime"]),
                json.dumps(record.get("metadata") or {}, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )


def list_asset_records(db_path: Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_metadata_db(db_path)
    with sqlite3.connect(db_path or DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT asset_key, name, ext, kind, parser, status, path, size, mtime,
                   metadata, created_at, updated_at
            FROM dataset_assets
            ORDER BY updated_at DESC, name ASC
            LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def ingestion_summary(db_path: Path | None = None) -> dict[str, Any]:
    records = list_asset_records(db_path, limit=1000)
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for item in records:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    return {
        "asset_count": len(records),
        "by_kind": by_kind,
        "by_status": by_status,
        "supported_exts": sorted(SUPPORTED_EXTS),
    }


def _default_roots() -> list[Path]:
    return [
        DATA_DIR,
        DATA_DIR / "nc_uploads",
        DATA_DIR / "data_uploads",
        DATA_DIR / "knowledge_docs",
        DATA_DIR / "pdf_reports",
    ]


def _iter_asset_files(roots: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        iterator = root.rglob("*") if root.name in {"pdf_reports", "data_uploads"} else root.glob("*")
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return sorted(files, key=lambda item: str(item).lower())


def _asset_key(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _asset_kind(ext: str) -> str:
    if ext == ".nc":
        return "netcdf"
    if ext == ".mat":
        return "mat"
    if ext == ".json":
        return "json"
    if ext in SUPPORTED_KNOWLEDGE_EXTS:
        return "knowledge"
    return "unknown"


def _parse_netcdf(path: Path) -> dict[str, Any]:
    try:
        from ocean_agents_demo import nc_data
        try:
            summary = nc_data._summary_netcdf4(path)  # type: ignore[attr-defined]
        except Exception:
            summary = nc_data._summary_classic(path)  # type: ignore[attr-defined]
        return {
            "dataset_id": summary.get("id", path.stem),
            "origin": summary.get("origin", ""),
            "variables": _compact_variables(summary.get("variables", [])),
            "variable_count": len(summary.get("variables", [])),
            "source": "netcdf",
        }
    except Exception as exc:
        return {
            "dataset_id": path.stem,
            "source": "netcdf",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _parse_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8", errors="ignore") or "null")
    if isinstance(raw, dict):
        sample = {key: _json_type(value) for key, value in list(raw.items())[:24]}
        return {
            "json_type": "object",
            "top_level_keys": list(raw.keys())[:50],
            "field_types": sample,
        }
    if isinstance(raw, list):
        first = raw[0] if raw else None
        return {
            "json_type": "array",
            "length": len(raw),
            "first_item_type": _json_type(first),
            "first_item_keys": list(first.keys())[:50] if isinstance(first, dict) else [],
        }
    return {"json_type": _json_type(raw)}


def _parse_mat(path: Path) -> dict[str, Any]:
    try:
        from scipy.io import loadmat  # type: ignore
        data = loadmat(path, squeeze_me=False, struct_as_record=False)
        variables = []
        for name, value in data.items():
            if name.startswith("__"):
                continue
            variables.append({
                "name": name,
                "shape": list(getattr(value, "shape", []) or []),
                "dtype": str(getattr(value, "dtype", "")),
            })
        return {
            "mat_format": "scipy_loadmat",
            "variables": variables[:100],
            "variable_count": len(variables),
        }
    except Exception as exc:
        header = path.read_bytes()[:128]
        text = header.decode("latin1", errors="ignore").strip("\x00").strip()
        return {
            "mat_format": "metadata_only",
            "header": text[:120],
            "variables": [],
            "variable_count": 0,
            "parser_note": f"scipy unavailable or file not loadable: {type(exc).__name__}",
        }


def _parse_knowledge_file(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".pdf":
        return {"content_type": "pdf", "parser": "metadata_only", "title": path.stem}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {
        "content_type": path.suffix.lower().lstrip(".") or "text",
        "chars": len(text),
        "first_heading": _first_heading(text),
    }


def _compact_variables(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in variables[:100]:
        out.append({
            "name": item.get("name"),
            "units": item.get("units", ""),
            "long_name": item.get("long_name", ""),
            "shape": item.get("shape", item.get("dims", [])),
            "category": item.get("category", ""),
        })
    return out


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["metadata"] = json.loads(data.get("metadata") or "{}")
    except json.JSONDecodeError:
        data["metadata"] = {}
    return data
