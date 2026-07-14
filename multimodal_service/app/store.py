from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings


class FaissEvidenceStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._index: Any = None
        self._records: dict[int, dict[str, Any]] = {}
        self._error = ""
        self._version = ""

    def ensure_loaded(self) -> None:
        if self._index is not None:
            return
        self.reload()

    def reload(self) -> None:
        with self._lock:
            try:
                import faiss

                if not self.settings.index_path.exists():
                    raise FileNotFoundError(f"index not found: {self.settings.index_path}")
                if not self.settings.metadata_path.exists():
                    raise FileNotFoundError(f"metadata not found: {self.settings.metadata_path}")
                records: dict[int, dict[str, Any]] = {}
                with self.settings.metadata_path.open("r", encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        vector_id = int(item.get("vector_id"))
                        if vector_id in records:
                            raise ValueError(f"duplicate vector_id={vector_id} at line {line_no}")
                        records[vector_id] = item
                index = faiss.read_index(str(self.settings.index_path))
                if int(index.ntotal) != len(records):
                    raise ValueError(f"index count {index.ntotal} != metadata count {len(records)}")
                self._index = index
                self._records = records
                self._version = _read_manifest_version(self.settings.metadata_path.parent)
                self._error = ""
            except Exception as exc:
                self._index = None
                self._records = {}
                self._error = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(f"Faiss evidence store load failed: {self._error}") from exc

    def search(self, vector: np.ndarray, top_k: int) -> list[dict[str, Any]]:
        self.ensure_loaded()
        k = max(1, min(int(top_k), 100, len(self._records)))
        query = np.ascontiguousarray(vector, dtype="float32")
        if query.ndim != 2 or query.shape[0] != 1:
            raise ValueError("query vector must have shape (1, dimension)")
        with self._lock:
            scores, ids = self._index.search(query, k)
        results: list[dict[str, Any]] = []
        for rank, (score, vector_id) in enumerate(zip(scores[0], ids[0]), 1):
            if int(vector_id) < 0:
                continue
            record = dict(self._records.get(int(vector_id), {}))
            if not record:
                continue
            record.update({
                "vector_id": int(vector_id),
                "rank": rank,
                "clip_score": round(float(score), 6),
                "score": round(float(score), 6),
                "backend": "multimodal-service",
                "route": "chinese_clip_image_to_text",
            })
            results.append(record)
        return results

    def status(self) -> dict[str, Any]:
        return {
            "loaded": self._index is not None,
            "count": len(self._records),
            "dimension": int(getattr(self._index, "d", 0) or 0) if self._index is not None else None,
            "index_path": str(self.settings.index_path),
            "metadata_path": str(self.settings.metadata_path),
            "index_version": self._version or None,
            "error": self._error or None,
        }


def _read_manifest_version(directory: Path) -> str:
    path = directory / "manifest.json"
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("index_version") or "")
    except Exception:
        return ""

