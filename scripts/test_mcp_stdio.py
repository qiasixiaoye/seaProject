"""Smoke-test the custom MCP stdio server.

This test writes JSON-RPC requests using MCP Content-Length framing and verifies
the required response shapes. It avoids LLM-dependent tools so it can run in a
local/offline development environment.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"


def frame(obj: dict[str, Any]) -> bytes:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


def parse_frames(data: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pos = 0
    while pos < len(data):
        header_end = data.find(b"\r\n\r\n", pos)
        if header_end < 0:
            raise AssertionError(f"incomplete MCP header at byte {pos}")
        header = data[pos:header_end].decode("utf-8")
        length = None
        for line in header.splitlines():
            key, _, value = line.partition(":")
            if key.lower() == "content-length":
                length = int(value.strip())
                break
        if length is None:
            raise AssertionError(f"missing Content-Length in header: {header!r}")
        body_start = header_end + 4
        body_end = body_start + length
        body = data[body_start:body_end]
        if len(body) != length:
            raise AssertionError("incomplete MCP body")
        out.append(json.loads(body.decode("utf-8")))
        pos = body_end
    return out


def request_batch() -> list[dict[str, Any]]:
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_datasets",
                "arguments": {"include_variables": False},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "query_ocean_data",
                "arguments": {
                    "bounds": {"west": 110, "east": 130, "south": 10, "north": 30},
                    "max_points": 300,
                    "include_grid": False,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "query_ocean_data",
                "arguments": {
                    "bounds": {"west": 130, "east": 110, "south": 10, "north": 30},
                    "include_grid": False,
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "search_literature",
                "arguments": {
                    "query": "marine heatwave coral fishery",
                    "top_k": 3,
                    "backend": "local",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "run_ocean_report",
                "arguments": {
                    "question": "Summarize ocean acidification risks for shellfish.",
                    "backend": "local",
                    "top_k": 3,
                    "trace_summary": True,
                },
            },
        },
        {"jsonrpc": "2.0", "id": 8, "method": "resources/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "resources/read",
            "params": {"uri": "ocean://datasets"},
        },
        {"jsonrpc": "2.0", "id": 10, "method": "ping", "params": {}},
    ]


def assert_ok(resp: dict[str, Any], req_id: int) -> dict[str, Any]:
    assert resp.get("jsonrpc") == "2.0", resp
    assert resp.get("id") == req_id, resp
    assert "error" not in resp, resp
    result = resp.get("result")
    assert isinstance(result, dict), resp
    return result


def main() -> int:
    payload = b"".join(frame(item) for item in request_batch())
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(proc.returncode)

    responses = parse_frames(proc.stdout)
    assert len(responses) == 10, f"expected 10 responses, got {len(responses)}"

    init = assert_ok(responses[0], 1)
    assert init["serverInfo"]["name"] == "ocean-geo-agent"

    tools = assert_ok(responses[1], 2).get("tools", [])
    tool_names = {tool.get("name") for tool in tools}
    assert {"query_ocean_data", "run_ocean_report", "list_datasets", "search_literature"} <= tool_names

    tool_call = assert_ok(responses[2], 3)
    assert tool_call.get("isError") is False
    assert tool_call.get("content") and tool_call["content"][0]["type"] == "text"
    dataset_payload = json.loads(tool_call["content"][0]["text"])
    assert "datasets" in dataset_payload

    ocean_call = assert_ok(responses[3], 4)
    assert ocean_call.get("isError") is False
    ocean_payload = json.loads(ocean_call["content"][0]["text"])
    assert ocean_payload.get("grid_omitted") is True
    assert "stats" in ocean_payload and "values" not in ocean_payload

    invalid_call = assert_ok(responses[4], 5)
    assert invalid_call.get("isError") is True
    invalid_payload = json.loads(invalid_call["content"][0]["text"])
    assert {"code", "message", "details", "retryable"} <= set(invalid_payload), invalid_payload
    assert invalid_payload["code"] == "validation_error"
    assert invalid_payload["retryable"] is False

    search_call = assert_ok(responses[5], 6)
    assert search_call.get("isError") is False
    search_payload = json.loads(search_call["content"][0]["text"])
    assert search_payload.get("documents"), search_payload
    first_doc = search_payload["documents"][0]
    assert {"doc_id", "chunk_id", "page", "source", "score", "rerank_reason"} <= set(first_doc), first_doc

    report_call = assert_ok(responses[6], 7)
    assert report_call.get("isError") is False
    report_payload = json.loads(report_call["content"][0]["text"])
    assert report_payload.get("report"), report_payload
    assert "trace_summary" in report_payload and "node_count" in report_payload["trace_summary"]

    resources = assert_ok(responses[7], 8).get("resources", [])
    resource_uris = {item.get("uri") for item in resources}
    assert {"ocean://datasets", "ocean://knowledge"} <= resource_uris

    resource_read = assert_ok(responses[8], 9)
    contents = resource_read.get("contents", [])
    assert contents and contents[0].get("uri") == "ocean://datasets"
    assert "datasets" in json.loads(contents[0].get("text", "{}"))

    assert_ok(responses[9], 10)
    print(json.dumps({
        "status": "ok",
        "responses": len(responses),
        "tools": sorted(tool_names),
        "resources": sorted(resource_uris),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
