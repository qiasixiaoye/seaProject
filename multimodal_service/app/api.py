from __future__ import annotations

import time
from typing import Any

from flask import Flask, jsonify, request

from .cache import ImageReferenceCache
from .config import Settings
from .diagnostics import score_diagnostics
from .encoder import ChineseClipEncoder
from .store import FaissEvidenceStore


def create_app(settings: Settings | None = None) -> Flask:
    cfg = settings or Settings.from_env()
    encoder = ChineseClipEncoder(cfg)
    store = FaissEvidenceStore(cfg)
    cache = ImageReferenceCache(cfg.image_ref_ttl_seconds, cfg.image_cache_entries)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.max_image_bytes + 1024 * 1024
    app.extensions["multimodal"] = {"settings": cfg, "encoder": encoder, "store": store, "cache": cache}

    @app.get("/healthz")
    def healthz() -> Any:
        return jsonify({"status": "ok", "service": "multimodal-retrieval"})

    @app.get("/readyz")
    def readyz() -> Any:
        try:
            encoder.ensure_loaded()
            store.ensure_loaded()
            return jsonify({"status": "ready", "model": encoder.status(), "index": store.status()})
        except Exception as exc:
            return jsonify({
                "status": "not_ready",
                "error": f"{type(exc).__name__}: {exc}",
                "model": encoder.status(),
                "index": store.status(),
            }), 503

    @app.get("/v1/status")
    def status() -> Any:
        return jsonify({"model": encoder.status(), "index": store.status(), "cache": cache.status()})

    @app.post("/v1/images")
    def upload_image() -> Any:
        file = request.files.get("image")
        if file is None or not file.filename:
            return jsonify({"error": "image file is required"}), 400
        raw = file.read(cfg.max_image_bytes + 1)
        started = time.perf_counter()
        try:
            vector = encoder.encode_image_bytes(raw)
            item = cache.put(vector, raw, file.filename)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 503
        return jsonify({
            "image_ref": item["image_ref"],
            "image_sha256": item["sha256"],
            "expires_in": cfg.image_ref_ttl_seconds,
            "model": cfg.model_name,
            "dimension": encoder.dimension,
            "processing": {
                "operation": "image_embedding",
                "ocr_enabled": False,
                "vision_caption_enabled": False,
                "persisted": False,
            },
            "encode_ms": round((time.perf_counter() - started) * 1000, 2),
        }), 201

    @app.post("/v1/search")
    def search_image() -> Any:
        file = request.files.get("image")
        if file is None or not file.filename:
            return jsonify({"error": "image file is required"}), 400
        raw = file.read(cfg.max_image_bytes + 1)
        try:
            vector = encoder.encode_image_bytes(raw)
            return jsonify(_search_payload(store, vector, request.form.get("top_k", 20), encoder, cache))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 503

    @app.post("/v1/search/by-ref")
    def search_by_ref() -> Any:
        body = request.get_json(silent=True) or {}
        image_ref = str(body.get("image_ref") or "").strip()
        if not image_ref:
            return jsonify({"error": "image_ref is required"}), 400
        item = cache.get(image_ref)
        if item is None:
            return jsonify({"error": "image_ref not found or expired"}), 404
        try:
            payload = _search_payload(store, item["vector"], body.get("top_k", 20), encoder, cache)
            payload["image_ref"] = image_ref
            payload["image_sha256"] = item["sha256"]
            return jsonify(payload)
        except Exception as exc:
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 503

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify({"error": f"image exceeds {cfg.max_image_bytes} bytes"}), 413

    return app


def _search_payload(
    store: FaissEvidenceStore,
    vector,
    top_k: Any,
    encoder: ChineseClipEncoder,
    cache: ImageReferenceCache,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        requested = int(top_k)
    except (TypeError, ValueError):
        requested = 20
    results = store.search(vector, max(1, min(requested, 100)))
    scores = [float(item.get("clip_score", 0.0)) for item in results]
    return {
        "results": results,
        "count": len(results),
        "model": encoder.status(),
        "index": store.status(),
        "cache": cache.status(),
        "diagnostics": score_diagnostics(scores),
        "search_ms": round((time.perf_counter() - started) * 1000, 2),
    }


app = create_app()
