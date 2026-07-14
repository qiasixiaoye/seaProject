"""Standalone smoke tests for multimodal retrieval fusion (no model download required)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_agent import multimodal_client
from geo_agent.nodes import retrieval
from multimodal_service.app.diagnostics import score_diagnostics
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


def test_document_cap_preserves_diversity() -> None:
    candidates = [
        {"id": f"a-{i}", "doc_id": "doc-a", "score": 1 - i / 10}
        for i in range(5)
    ] + [{"id": "b-1", "doc_id": "doc-b", "score": 0.2}]
    limited = retrieval._limit_per_document(candidates, max_per_doc=2)
    assert [item["id"] for item in limited] == ["a-0", "a-1", "b-1"]


def test_relative_image_weight_is_bounded() -> None:
    ambiguous = retrieval._relative_image_weight({"top_score": 0.4, "median_score": 0.4, "std_score": 0.0})
    separated = retrieval._relative_image_weight({"top_score": 0.6, "median_score": 0.4, "std_score": 0.02})
    assert ambiguous == 0.9
    assert 0.9 < separated <= 1.2


def test_score_diagnostics_are_explicitly_uncalibrated() -> None:
    diagnostics = score_diagnostics([0.6, 0.5, 0.4])
    assert diagnostics["calibration"] == "unvalidated_relative_only"
    assert diagnostics["top_score"] == 0.6
    assert diagnostics["median_score"] == 0.5
    assert diagnostics["top_margin"] == 0.1


def test_successful_multimodal_trace_is_explainable() -> None:
    original_text = retrieval._deterministic_retrieve
    original_image = multimodal_client.search_by_ref
    try:
        retrieval._deterministic_retrieve = lambda *_args: ([
            Doc(
                id=f"text-{i}", title=f"文本文档 · p.{i + 1}", kind="local", year=2024,
                source="text.pdf", topics=["海温"], abstract="海温异常证据", score=0.8 - i / 100,
                metadata={"doc_id": "text-doc", "chunk_id": f"text-{i}", "page": i + 1},
            )
            for i in range(5)
        ], "local", {"queries": ["海温异常"]})
        multimodal_client.search_by_ref = lambda *_args: {
            "results": [
                {
                    "vector_id": i + 1, "doc_id": "image-doc", "chunk_id": f"image-{i}",
                    "title": f"图片文档 / page {i + 1}", "page": i + 1,
                    "content": "海温分布图证据", "clip_score": 0.6 - i / 100,
                }
                for i in range(5)
            ],
            "diagnostics": {
                "top_score": 0.6, "median_score": 0.58, "std_score": 0.014, "top_margin": 0.01,
            },
            "search_ms": 2.5,
            "model": {"model": "test-model"},
            "index": {"index_version": "test-v2"},
        }
        fused, used, info, image_candidates = retrieval._multimodal_fused_retrieve(
            {"retrieval_query": "海温异常", "keywords": ["海温"]}, 6, "local", "img-test"
        )
        assert used == "local+multimodal"
        assert len(image_candidates) == 3
        assert info["text_count"] == 3
        assert info["fusion"]["per_document_cap"] == 3
        assert info["fusion"]["absolute_threshold_enabled"] is False
        assert info["diagnostics"]["image_search_ms"] == 2.5
        assert info["candidate_preview"]["image"]
        assert len([item for item in fused if item.get("doc_id") == "image-doc"]) <= 3
    finally:
        retrieval._deterministic_retrieve = original_text
        multimodal_client.search_by_ref = original_image


def test_cross_backend_page_identity_merges() -> None:
    text = [{
        "id": "ragflow-1", "chunk_id": "ragflow-1", "doc_id": "remote-doc",
        "title": "造福人类、自然和经济的海洋解决方案 · p.18,19,20...", "page": 18,
        "abstract": "文本证据", "score": 0.8,
    }]
    image = [{
        "id": "local-1", "chunk_id": "local-1", "doc_id": "pdf-local",
        "title": "造福人类、自然和经济的海洋解决方案 / page 18", "page": 18,
        "abstract": "图片召回证据", "score": 0.5, "clip_score": 0.5,
    }]
    fused = retrieval._rrf_fuse(text, image, 10)
    assert len(fused) == 1
    assert fused[0]["retrieval_routes"] == ["image", "text"]
    assert fused[0]["metadata"]["fusion"]["route_ranks"] == {"text": 1, "image": 1}


if __name__ == "__main__":
    tests = [
        test_rrf_merges_shared_chunk,
        test_multimodal_failure_degrades_to_text,
        test_image_hit_contract,
        test_document_cap_preserves_diversity,
        test_relative_image_weight_is_bounded,
        test_score_diagnostics_are_explicitly_uncalibrated,
        test_successful_multimodal_trace_is_explainable,
        test_cross_backend_page_identity_merges,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
