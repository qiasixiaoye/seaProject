"""Internal HTTP client for the decoupled multimodal retrieval service."""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MultimodalServiceError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("MULTIMODAL_BASE_URL", "").strip())


def upload_image(raw: bytes, filename: str, content_type: str = "") -> dict[str, Any]:
    if not configured():
        raise MultimodalServiceError("MULTIMODAL_BASE_URL is not configured")
    boundary = f"----ocean-{secrets.token_hex(16)}"
    mime = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    disposition = f'Content-Disposition: form-data; name="image"; filename="{_safe_filename(filename)}"\r\n'
    body = (
        f"--{boundary}\r\n".encode()
        + disposition.encode("utf-8")
        + f"Content-Type: {mime}\r\n\r\n".encode()
        + raw
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return _request_json(
        "/v1/images",
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def search_by_ref(image_ref: str, top_k: int = 20) -> dict[str, Any]:
    payload = json.dumps({"image_ref": image_ref, "top_k": top_k}).encode("utf-8")
    return _request_json("/v1/search/by-ref", payload, {"Content-Type": "application/json"})


def service_status() -> dict[str, Any]:
    if not configured():
        return {"configured": False, "available": False, "reason": "MULTIMODAL_BASE_URL is not configured"}
    try:
        data = _request_json("/v1/status", None, {})
        return {"configured": True, "available": True, **data}
    except Exception as exc:
        return {"configured": True, "available": False, "reason": str(exc)}


def _request_json(path: str, data: bytes | None, headers: dict[str, str]) -> dict[str, Any]:
    base = os.getenv("MULTIMODAL_BASE_URL", "").rstrip("/")
    if not base:
        raise MultimodalServiceError("MULTIMODAL_BASE_URL is not configured")
    method = "POST" if data is not None else "GET"
    request = Request(base + path, data=data, headers=headers, method=method)
    timeout = max(1.0, float(os.getenv("MULTIMODAL_TIMEOUT_SECONDS", "30")))
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}").get("error")
        except Exception:
            detail = ""
        raise MultimodalServiceError(f"multimodal HTTP {exc.code}: {detail or exc.reason}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise MultimodalServiceError(f"multimodal service unavailable: {exc}") from exc
    if not isinstance(payload, dict):
        raise MultimodalServiceError("multimodal service returned a non-object response")
    if payload.get("error"):
        raise MultimodalServiceError(str(payload["error"]))
    return payload


def _safe_filename(value: str) -> str:
    return "".join(ch for ch in str(value or "image") if ch.isalnum() or ch in "._-")[:120] or "image"

