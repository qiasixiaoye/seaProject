from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocean_agents_demo.core import load_docs


def main() -> int:
    parser = argparse.ArgumentParser(description="Export normalized text evidence for Chinese-CLIP indexing")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--parse-pdf", action="store_true", help="extract and chunk local PDF text before export")
    args = parser.parse_args()

    if args.parse_pdf:
        os.environ["OCEAN_PARSE_PDF_ON_LOAD"] = "true"
    docs = load_docs()
    if args.limit > 0:
        docs = docs[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for vector_id, doc in enumerate(docs, 1):
            meta = dict(doc.metadata or {})
            content = str(doc.abstract or "").strip()
            if not content:
                continue
            topics = [str(item) for item in (doc.topics or []) if str(item).strip()]
            retrieval_card = _retrieval_card(doc.title, topics, content)
            record = {
                "vector_id": vector_id,
                "evidence_id": str(meta.get("chunk_id") or doc.id),
                "doc_id": str(meta.get("doc_id") or doc.id),
                "chunk_id": str(meta.get("chunk_id") or doc.id),
                "title": doc.title,
                "content": content,
                "retrieval_card": retrieval_card,
                "source": doc.source,
                "page": meta.get("page"),
                "pages": meta.get("pages") or [],
                "topics": topics,
                "content_type": meta.get("content_type") or doc.kind,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    print(json.dumps({"output": str(args.output), "count": written}, ensure_ascii=False))
    return 0


def _retrieval_card(title: str, topics: list[str], content: str) -> str:
    """Build a rich card; the model tokenizer applies its own 512-token limit."""
    normalized = " ".join(content.replace("\n", " ").split())
    parts = [str(title or "").strip(), "、".join(topics[:8]), normalized]
    return "；".join(part for part in parts if part)


if __name__ == "__main__":
    raise SystemExit(main())
