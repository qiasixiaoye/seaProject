from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from multimodal_service.app.config import Settings
from multimodal_service.app.encoder import ChineseClipEncoder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Chinese-CLIP text Faiss index")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--index-version", default="")
    args = parser.parse_args()

    records = _read_records(args.input)
    if not records:
        raise SystemExit("no evidence records found")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings.from_env()
    encoder = ChineseClipEncoder(settings)
    vectors: list[np.ndarray] = []
    batch_size = max(1, args.batch_size)
    print(f"encoding {len(records)} evidence records in batches of {batch_size}", flush=True)
    for start in range(0, len(records), batch_size):
        texts = [item["retrieval_card"] for item in records[start : start + batch_size]]
        vectors.append(encoder.encode_texts(texts))
        encoded = min(start + batch_size, len(records))
        print(f"encoded {encoded}/{len(records)}", flush=True)
    matrix = np.ascontiguousarray(np.vstack(vectors), dtype="float32")

    import faiss

    base = faiss.IndexFlatIP(matrix.shape[1])
    index = faiss.IndexIDMap2(base)
    ids = np.asarray([item["vector_id"] for item in records], dtype="int64")
    index.add_with_ids(matrix, ids)

    index_tmp = output_dir / "ocean_text.faiss.tmp"
    metadata_tmp = output_dir / "evidence.jsonl.tmp"
    faiss.write_index(index, str(index_tmp))
    with metadata_tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for item in records:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    index_path = output_dir / "ocean_text.faiss"
    metadata_path = output_dir / "evidence.jsonl"
    os.replace(index_tmp, index_path)
    os.replace(metadata_tmp, metadata_path)
    version = args.index_version or time.strftime("ocean-%Y%m%d-%H%M%S")
    manifest = {
        "index_version": version,
        "model": settings.model_name,
        "dimension": int(matrix.shape[1]),
        "normalized": True,
        "metric": "inner_product",
        "document_count": len(records),
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _read_records(path: Path) -> list[dict]:
    records: list[dict] = []
    seen: set[int] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            text = str(item.get("retrieval_card") or "").strip()
            if not text:
                continue
            vector_id = int(item.get("vector_id", len(records) + 1))
            if vector_id in seen:
                raise ValueError(f"duplicate vector_id={vector_id} at line {line_no}")
            seen.add(vector_id)
            item["vector_id"] = vector_id
            item["retrieval_card"] = text
            records.append(item)
    return records


if __name__ == "__main__":
    raise SystemExit(main())
