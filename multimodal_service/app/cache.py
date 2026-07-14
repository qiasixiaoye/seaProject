from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np


class ImageReferenceCache:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._items: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def put(self, vector: np.ndarray, raw: bytes, filename: str) -> dict[str, Any]:
        now = time.time()
        image_ref = f"img_{secrets.token_urlsafe(18)}"
        item = {
            "vector": np.ascontiguousarray(vector, dtype="float32"),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "filename": filename,
            "created_at": now,
            "expires_at": now + self.ttl_seconds,
        }
        with self._lock:
            self._purge(now)
            self._items[image_ref] = item
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)
        return {key: value for key, value in item.items() if key != "vector"} | {"image_ref": image_ref}

    def get(self, image_ref: str) -> dict[str, Any] | None:
        now = time.time()
        with self._lock:
            self._purge(now)
            item = self._items.get(image_ref)
            if item is None:
                return None
            self._items.move_to_end(image_ref)
            return item

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._purge(time.time())
            return {"entries": len(self._items), "ttl_seconds": self.ttl_seconds, "max_entries": self.max_entries}

    def _purge(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if item["expires_at"] <= now]
        for key in expired:
            self._items.pop(key, None)

