"""Standalone smoke tests for multimodal retrieval fusion (no model download required)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_agent import multimodal_client
from geo_agent.nodes import retrieval
from ocean_agents_demo.core import Doc


def test_rrf_merges_shared_chunk() -> None:
    text = [{"id": "c1", "chunk_id": "c1", "title": "text", "abstract": "海温异常", "score": 0.7}]
    image = [{"id": "c1", "chunk_id": "c1", "title": "image", "abstract": "", "score": 0.8, "clip_score": 0.8}]
    fused = retrieval._rrf_fuse(text, image, 10)
    assert len(fused) == 1
    assert fused[0]["title"] == "text"
    assert fused[0]["clip_score"] == 0.8
    assert fused[0]["retrieval_routes"] == ["image", "text"]
    assert fused[0]["score"] == 1.0


def test_multimodal_failure_degrades_to_text() -> None:
    original_text = retrieval._deterministic_retrieve
    original_image = multimodal_client.search_by_ref
    try:
        retrieval._deterministic_retrieve = lambda *_args: ([
            Doc(id="c1", title="海温异常", kind="local", year=2024, source="test", topics=["海温"], abstract="海温异常证据", score=0.8)
        ], "local", {"queries": ["海温异常"]})

        def fail_image(*_args, **_kwargs):
            raise multimodal_client.MultimodalServiceError("offline")

        multimodal_client.search_by_ref = fail_image
        fused, used, info, image_candidates = retrieval._multimodal_fused_retrieve(
            {"retrieval_query": "海温异常", "keywords": ["海温"]}, 6, "local", "img-test"
        )
        assert len(fused) == 1
        assert used == "local"
        assert not image_candidates
        assert "offline" in str(info["multimodal_error"])
    finally:
        retrieval._deterministic_retrieve = original_text
        multimodal_client.search_by_ref = original_image


def test_image_hit_contract() -> None:
    hit = retrieval._normalize_multimodal_hit({
        "vector_id": 1,
        "doc_id": "doc-1",
        "chunk_id": "chunk-1",
        "title": "SST anomaly",
        "content": "海表温度异常分布。",
        "source": "paper.pdf",
        "page": 3,
        "clip_score": 0.72,
    }, {"model": {"model": "test-model"}, "index": {"index_version": "test-v1"}})
    assert hit["id"] == "chunk-1"
    assert hit["abstract"] == "海表温度异常分布。"
    assert hit["evidence"]["doc_id"] == "doc-1"
    assert hit["metadata"]["index_version"] == "test-v1"


if __name__ == "__main__":
    tests = [test_rrf_merges_shared_chunk, test_multimodal_failure_degrades_to_text, test_image_hit_contract]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")

