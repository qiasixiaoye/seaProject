from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model_name: str
    device: str
    index_path: Path
    metadata_path: Path
    model_cache_dir: Path
    local_files_only: bool
    max_image_bytes: int
    image_ref_ttl_seconds: int
    image_cache_entries: int
    encode_batch_size: int

    @classmethod
    def from_env(cls) -> "Settings":
        index_dir = Path(os.getenv("MULTIMODAL_INDEX_DIR", "/app/index"))
        return cls(
            model_name=os.getenv(
                "MULTIMODAL_MODEL_NAME",
                "OFA-Sys/chinese-clip-vit-base-patch16",
            ),
            device=os.getenv("MULTIMODAL_DEVICE", "cpu").strip().lower() or "cpu",
            index_path=Path(os.getenv("MULTIMODAL_INDEX_PATH", index_dir / "ocean_text.faiss")),
            metadata_path=Path(os.getenv("MULTIMODAL_METADATA_PATH", index_dir / "evidence.jsonl")),
            model_cache_dir=Path(os.getenv("MULTIMODAL_MODEL_CACHE", "/app/models")),
            local_files_only=_bool_env("MULTIMODAL_LOCAL_FILES_ONLY"),
            max_image_bytes=max(1024, int(os.getenv("MULTIMODAL_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))),
            image_ref_ttl_seconds=max(30, int(os.getenv("MULTIMODAL_IMAGE_REF_TTL", "600"))),
            image_cache_entries=max(8, int(os.getenv("MULTIMODAL_IMAGE_CACHE_ENTRIES", "256"))),
            encode_batch_size=max(1, int(os.getenv("MULTIMODAL_ENCODE_BATCH_SIZE", "32"))),
        )

