from __future__ import annotations

import io
import threading
from typing import Any, Iterable

import numpy as np
from PIL import Image, UnidentifiedImageError

from .config import Settings


class ChineseClipEncoder:
    """Lazy-loaded Chinese-CLIP encoder shared by one service process."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._error = ""
        self.dimension = 0

    def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import ChineseCLIPModel, ChineseCLIPProcessor

                cache_dir = str(self.settings.model_cache_dir)
                self.settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
                kwargs = {
                    "cache_dir": cache_dir,
                    "local_files_only": self.settings.local_files_only,
                }
                processor = ChineseCLIPProcessor.from_pretrained(self.settings.model_name, **kwargs)
                model = ChineseCLIPModel.from_pretrained(self.settings.model_name, **kwargs)
                device = self.settings.device
                if device.startswith("cuda") and not torch.cuda.is_available():
                    raise RuntimeError("MULTIMODAL_DEVICE requests CUDA, but torch.cuda.is_available() is false")
                model = model.to(device)
                model.eval()
                self.dimension = int(getattr(model.config, "projection_dim", 0) or 0)
                self._torch = torch
                self._processor = processor
                self._model = model
                self._error = ""
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(f"Chinese-CLIP load failed: {self._error}") from exc

    def encode_image_bytes(self, raw: bytes) -> np.ndarray:
        if not raw:
            raise ValueError("empty image")
        if len(raw) > self.settings.max_image_bytes:
            raise ValueError(f"image exceeds {self.settings.max_image_bytes} bytes")
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(raw)) as opened:
                image = opened.convert("RGB")
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("invalid or unsupported image") from exc

        self.ensure_loaded()
        with self._lock, self._torch.inference_mode():
            inputs = self._processor(images=image, return_tensors="pt")
            inputs = {key: value.to(self.settings.device) for key, value in inputs.items()}
            features = self._model.get_image_features(**inputs)
        return _normalize(features.detach().float().cpu().numpy())

    def encode_texts(self, texts: Iterable[str]) -> np.ndarray:
        values = [str(text or "").strip() for text in texts]
        if not values or any(not text for text in values):
            raise ValueError("text batch contains an empty item")
        self.ensure_loaded()
        with self._lock, self._torch.inference_mode():
            max_length = int(getattr(self._model.config.text_config, "max_position_embeddings", 512))
            inputs = self._processor(
                text=values,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            inputs = {key: value.to(self.settings.device) for key, value in inputs.items()}
            # transformers 4.57 constructs Chinese-CLIP's BERT without a pooler,
            # while get_text_features() still reads pooler_output.  The model's
            # own forward() uses the final CLS token, which is also how the
            # upstream Chinese-CLIP checkpoint was trained.
            text_outputs = self._model.text_model(**inputs, return_dict=True)
            features = self._model.text_projection(text_outputs.last_hidden_state[:, 0, :])
        return _normalize(features.detach().float().cpu().numpy())

    def status(self) -> dict[str, Any]:
        return {
            "model": self.settings.model_name,
            "device": self.settings.device,
            "loaded": self._model is not None,
            "dimension": self.dimension or None,
            "local_files_only": self.settings.local_files_only,
            "error": self._error or None,
        }


def _normalize(values: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype="float32")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise RuntimeError("encoder returned a zero vector")
    return np.ascontiguousarray(matrix / norms, dtype="float32")
