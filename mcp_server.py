#!/usr/bin/env python3
"""Ocean GeoAgent MCP Server (stdio transport).

遵循 Model Context Protocol 规范（https://spec.modelcontextprotocol.io/）。
使用纯 Python 实现，不依赖 mcp SDK 包，可直接被 Claude Desktop 等 MCP 客户端调用。

暴露工具:
  query_ocean_data   — 查询 NetCDF 区域数据（支持 time_index / depth_index）
  run_ocean_report   — 运行多 Agent 报告流水线
  list_datasets      — 列出可用 NetCDF 数据集
  search_literature  — 检索本地/RAGFlow 知识库

暴露资源:
  ocean://datasets         — 实时数据集列表 JSON
  ocean://knowledge        — 本地知识库摘要

用法:
  python mcp_server.py           # stdio 模式（Claude Desktop）
  python mcp_server.py --config  # 仅打印 claude_desktop_config.json 片段

Claude Desktop 配置示例（~/.config/claude/claude_desktop_config.json）:
  {
    "mcpServers": {
      "ocean-geo-agent": {
        "command": "python",
        "args": ["C:/path/to/mcp_server.py"],
        "env": { "DEEPSEEK_API_KEY": "..." }
      }
    }
  }
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp_server")

SERVER_NAME = "ocean-geo-agent"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


# ── JSON-RPC stdio transport ──────────────────────────────────────────────────

def _read_message() -> dict | None:
    """从 stdin 读取一条 Content-Length 帧消息。"""
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode("utf-8").strip()
        if not line:
            break
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length", 0))
    if length == 0:
        return None
    raw = sys.stdin.buffer.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        log.warning("Failed to parse message: %s", exc)
        return None


def _write_message(obj: dict) -> None:
    """向 stdout 写出一条 Content-Length 帧消息。"""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "query_ocean_data",
        "description": (
            "查询指定海域的 NetCDF 海洋要素格点数据（SST、盐度、叶绿素、浪高、海流等）。"
            "支持多时间步和多深度层选择，返回格点值、统计摘要和渲染元数据。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset": {
                    "type": "string",
                    "description": "数据集 ID（如 sst_oisst_taiwan_small）。留空则自动选择。"
                },
                "variable": {
                    "type": "string",
                    "description": "变量名（如 sst、salinity、chlor_a）。"
                },
                "bounds": {
                    "type": "object",
                    "description": "地理范围 {west, east, south, north}（十进制度）。",
                    "properties": {
                        "west":  {"type": "number"},
                        "east":  {"type": "number"},
                        "south": {"type": "number"},
                        "north": {"type": "number"},
                    },
                    "required": ["west", "east", "south", "north"],
                },
                "time_index":  {"type": "integer", "description": "时间步索引（从 0 开始）。", "default": 0},
                "depth_index": {"type": "integer", "description": "深度层索引（从 0 开始）。", "default": 0},
                "max_points":  {"type": "integer", "description": "最大格点数（默认 4000）。", "default": 4000},
                "include_grid": {
                    "type": "boolean",
                    "description": "是否返回完整网格数组；默认 false，避免 MCP 客户端收到过大响应。",
                    "default": False,
                },
            },
            "required": ["bounds"],
        },
    },
    {
        "name": "run_ocean_report",
        "description": (
            "运行多 Agent 海洋风险分析报告流水线（Intent → Retrieval → Context → "
            "Screening → Reasoning → Report → Critic）。"
            "返回完整报告文本、证据链、领域分析和 Critic 结果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "分析问题（中文或英文）。",
                },
                "bbox": {
                    "type": "object",
                    "description": "可选：地理区域 {west, east, south, north}。",
                    "properties": {
                        "west":  {"type": "number"},
                        "east":  {"type": "number"},
                        "south": {"type": "number"},
                        "north": {"type": "number"},
                    },
                },
                "domain": {
                    "type": "string",
                    "description": "领域（marine/biology/navigation/stargazing/general）。",
                    "enum": ["marine", "biology", "navigation", "stargazing", "general", "auto"],
                    "default": "auto",
                },
                "backend": {
                    "type": "string",
                    "description": "检索后端（auto/local/ragflow）。",
                    "enum": ["auto", "local", "ragflow"],
                    "default": "auto",
                },
                "top_k": {"type": "integer", "description": "检索文档数。", "default": 6},
            },
            "required": ["question"],
        },
    },
    {
        "name": "list_datasets",
        "description": (
            "列出所有可用的 NetCDF 海洋数据集，包括变量、分辨率、时间/深度维度和推荐渲染模式。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_variables": {
                    "type": "boolean",
                    "description": "是否包含变量详情（默认 true）。",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "search_literature",
        "description": (
            "检索海洋学知识库（RAGFlow 优先，本地 JSON/Markdown 兜底）。"
            "返回相关文档片段、来源和置信分。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询（支持中英文）。",
                },
                "top_k": {"type": "integer", "description": "返回文档数（默认 5）。", "default": 5},
                "backend": {
                    "type": "string",
                    "description": "检索后端（auto/local/ragflow）。",
                    "enum": ["auto", "local", "ragflow"],
                    "default": "auto",
                },
            },
            "required": ["query"],
        },
    },
]


# ── Resource definitions ──────────────────────────────────────────────────────

RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "ocean://datasets",
        "name": "海洋数据集目录",
        "description": "实时列出所有可用 NetCDF 数据集（含变量、时间/深度维度）。",
        "mimeType": "application/json",
    },
    {
        "uri": "ocean://knowledge",
        "name": "海洋知识库摘要",
        "description": "本地海洋学知识库文档列表（PDF 报告、Markdown 摘要）。",
        "mimeType": "application/json",
    },
]


# ── Tool dispatch ─────────────────────────────────────────────────────────────

def _tool_query_ocean_data(args: dict) -> dict:
    from ocean_agents_demo.nc_data import query_grid
    payload = dict(args or {})
    payload["bounds"] = _validate_bbox(payload.get("bounds") or payload)
    payload["max_points"] = _bounded_int(payload.get("max_points"), 100, 5000, 1000)
    payload["time_index"] = _bounded_int(payload.get("time_index"), 0, 100000, 0)
    payload["depth_index"] = _bounded_int(payload.get("depth_index"), 0, 100000, 0)
    include_grid = bool(payload.pop("include_grid", False))
    result = query_grid(payload)
    if include_grid:
        return result
    compact_keys = {
        "dataset", "variable", "long_name", "units", "bounds", "shape", "stats",
        "time_index", "depth_index", "selected_time", "selected_depth",
        "render_time_ms", "category", "render_modes", "particle_ready",
        "requested_step", "auto_step",
    }
    compact = {key: result.get(key) for key in compact_keys if key in result}
    compact["grid_omitted"] = True
    compact["grid_fields"] = [
        key for key in ("values", "u_grid", "v_grid", "speed_grid", "lats", "lons")
        if key in result
    ]
    return compact


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _validate_bbox(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError("bounds must be an object with west/east/south/north")
    out: dict[str, float] = {}
    for key in ("west", "east", "south", "north"):
        if key not in value:
            raise ValueError(f"bounds.{key} is required")
        try:
            out[key] = float(value[key])
        except (TypeError, ValueError):
            raise ValueError(f"bounds.{key} must be a number") from None
    if not (-180 <= out["west"] <= 180 and -180 <= out["east"] <= 180):
        raise ValueError("bounds longitude must be within [-180, 180]")
    if not (-90 <= out["south"] <= 90 and -90 <= out["north"] <= 90):
        raise ValueError("bounds latitude must be within [-90, 90]")
    if out["west"] >= out["east"]:
        raise ValueError("bounds.west must be less than bounds.east")
    if out["south"] >= out["north"]:
        raise ValueError("bounds.south must be less than bounds.north")
    return out


def _tool_run_ocean_report(args: dict) -> dict:
    from geo_agent import graph as geo_graph
    question = str(args.get("question", "")).strip()
    if not question:
        raise ValueError("question is required")
    result = geo_graph.run(
        question=question,
        bbox=args.get("bbox"),
        domain=args.get("domain") if args.get("domain") not in (None, "auto") else None,
        backend=str(args.get("backend") or "auto"),
        top_k=int(args.get("top_k") or 6),
        max_revisions=1,
        trace_enabled=False,
        use_parallel=True,
    )
    # 精简返回体（不传完整 trace，减少 token）
    return {
        "task_id":       result.get("task_id"),
        "domain":        result.get("domain"),
        "report":        result.get("report"),
        "risk_hypotheses": result.get("risk_hypotheses"),
        "critic_result": result.get("critic_result"),
        "backend_used":  result.get("backend_used"),
        "elapsed_ms":    result.get("elapsed_ms"),
        "token_usage":   result.get("token_usage"),
    }


def _tool_list_datasets(args: dict) -> dict:
    from ocean_agents_demo.nc_data import dataset_summary
    result = dataset_summary()
    if not args.get("include_variables", True):
        for ds in result.get("datasets", []):
            ds.pop("variables", None)
    return result


def _tool_search_literature(args: dict) -> dict:
    from ocean_agents_demo import core
    query = str(args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 5)
    backend = str(args.get("backend") or "auto")
    docs, backend_used = core.retrieve(
        {"retrieval_query": query, "keywords": [], "original_question": query},
        top_k,
        backend,
    )
    return {
        "backend_used": backend_used,
        "count": len(docs),
        "documents": [
            {
                "title":   getattr(d, "title",   str(d)[:60]),
                "snippet": getattr(d, "snippet", ""),
                "score":   getattr(d, "score",   None),
                "source":  getattr(d, "source",  ""),
            }
            for d in docs
        ],
    }


TOOL_DISPATCH: dict[str, Any] = {
    "query_ocean_data":  _tool_query_ocean_data,
    "run_ocean_report":  _tool_run_ocean_report,
    "list_datasets":     _tool_list_datasets,
    "search_literature": _tool_search_literature,
}


# ── Resource dispatch ─────────────────────────────────────────────────────────

def _read_resource(uri: str) -> tuple[str, str]:
    """返回 (content_text, mime_type)。"""
    if uri == "ocean://datasets":
        from ocean_agents_demo.nc_data import dataset_summary
        data = dataset_summary()
        return json.dumps(data, ensure_ascii=False, indent=2), "application/json"

    if uri == "ocean://knowledge":
        from ocean_agents_demo import core
        rag = core.rag_status()
        result = {
            "llm":    rag.get("llm"),
            "ragflow": rag.get("ragflow"),
            "local":   rag.get("local"),
        }
        return json.dumps(result, ensure_ascii=False, indent=2), "application/json"

    raise ValueError(f"Unknown resource URI: {uri}")


# ── Request handlers ──────────────────────────────────────────────────────────

def handle_initialize(req_id: Any, params: dict) -> dict:
    return _ok(req_id, {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {
            "tools":     {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
        },
    })


def handle_tools_list(req_id: Any, _params: dict) -> dict:
    return _ok(req_id, {"tools": TOOLS})


def handle_tools_call(req_id: Any, params: dict) -> dict:
    name = params.get("name") or ""
    args = params.get("arguments") or {}
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return _err(req_id, -32601, f"Unknown tool: {name}")
    try:
        result = fn(args)
        # MCP 规范：tool 结果用 content 数组包装
        content_text = json.dumps(result, ensure_ascii=False, default=str)
        return _ok(req_id, {
            "content": [{"type": "text", "text": content_text}],
            "isError": False,
        })
    except Exception as exc:
        log.error("Tool %s failed: %s\n%s", name, exc, traceback.format_exc())
        return _ok(req_id, {
            "content": [{"type": "text", "text": f"Error: {exc}"}],
            "isError": True,
        })


def handle_resources_list(req_id: Any, _params: dict) -> dict:
    return _ok(req_id, {"resources": RESOURCES})


def handle_resources_read(req_id: Any, params: dict) -> dict:
    uri = params.get("uri") or ""
    try:
        text, mime = _read_resource(uri)
        return _ok(req_id, {
            "contents": [{"uri": uri, "mimeType": mime, "text": text}]
        })
    except Exception as exc:
        return _err(req_id, -32602, f"Resource read failed: {exc}")


def handle_ping(req_id: Any, _params: dict) -> dict:
    return _ok(req_id, {})


HANDLERS: dict[str, Any] = {
    "initialize":       handle_initialize,
    "tools/list":       handle_tools_list,
    "tools/call":       handle_tools_call,
    "resources/list":   handle_resources_list,
    "resources/read":   handle_resources_read,
    "ping":             handle_ping,
}

NOTIFICATION_METHODS = {"initialized", "notifications/cancelled"}


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_stdio() -> None:
    """主循环：从 stdin 读消息，向 stdout 写响应。"""
    log.info("MCP server started (stdio), server=%s v%s", SERVER_NAME, SERVER_VERSION)
    while True:
        try:
            msg = _read_message()
            if msg is None:
                break

            method  = msg.get("method") or ""
            req_id  = msg.get("id")
            params  = msg.get("params") or {}

            # 通知类消息无需响应
            if method in NOTIFICATION_METHODS or req_id is None:
                continue

            handler = HANDLERS.get(method)
            if handler is None:
                _write_message(_err(req_id, -32601, f"Method not found: {method}"))
                continue

            response = handler(req_id, params)
            _write_message(response)

        except EOFError:
            break
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("Unhandled error: %s\n%s", exc, traceback.format_exc())


def print_config() -> None:
    """打印 Claude Desktop 配置片段。"""
    script_path = str(Path(__file__).resolve()).replace("\\", "/")
    config = {
        "mcpServers": {
            "ocean-geo-agent": {
                "command": "python",
                "args": [script_path],
                "env": {
                    "DEEPSEEK_API_KEY": "<your-deepseek-api-key>",
                    "DEEPSEEK_MODEL": "deepseek-chat",
                    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                    "RAGFLOW_BASE_URL": "http://127.0.0.1:9380",
                    "RAGFLOW_API_KEY": "",
                    "RAGFLOW_DATASET_IDS": "",
                    "OCEAN_DATA_DIR": str(ROOT / "data"),
                },
            }
        }
    }
    print(json.dumps(config, indent=2, ensure_ascii=False))
    print()
    print("# 将以上片段合并到 ~/.config/claude/claude_desktop_config.json")
    print("# Windows: %APPDATA%/Claude/claude_desktop_config.json")


if __name__ == "__main__":
    if "--config" in sys.argv or "-c" in sys.argv:
        print_config()
    else:
        run_stdio()
