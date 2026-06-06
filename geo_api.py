"""GeoAgent API Server。

新增端点（兼容旧 api_server.py 接口）：
  POST /api/geo/report          - 标准 JSON 报告（新 GeoAgent）
  GET/POST /api/geo/stream      - SSE 流式报告
  GET  /api/geo/domains         - 支持的领域列表
  POST /api/geo/fetch-literature - 触发 arXiv 文献拉取
  GET  /api/geo/token-usage     - 当前 token 消耗统计
  GET  /api/health              - 健康检查（同旧接口）

旧接口保持兼容：
  POST /api/ask                 - 旧 RAG 报告（ocean_agents_demo）
  POST /api/query-ocean         - NetCDF 区域查询

运行：
    DEEPSEEK_API_KEY=xxx python geo_api.py
    或加入 docker-compose 替换 api_server.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_agent import graph as geo_graph
from geo_agent import llm as geo_llm
from geo_agent.catalog import agent_card, agent_catalog
from geo_agent.state import merge_trace, normalize_trace
from geo_agent.tool_registry import list_tools
from geo_agent.tools import ocean as ocean_tools
from ocean_agents_demo import deepseek_client
from ocean_agents_demo.core import run_pipeline, rag_status
from ocean_agents_demo.nc_data import dataset_summary, list_variables, query_grid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("geo_api")

PORT = int(os.getenv("PORT", "5001"))


# ── CORS / SSE 头 ─────────────────────────────────────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def _send_json(handler, data: dict | list, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for k, v in CORS_HEADERS.items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _read_query(handler) -> dict:
    parsed = urlparse(handler.path)
    params = parse_qs(parsed.query)
    body: dict[str, Any] = {}
    for key, values in params.items():
        if not values:
            continue
        raw = values[-1]
        if key in {"bbox", "region", "variables"}:
            try:
                body[key] = json.loads(raw)
            except Exception:
                body[key] = raw
        else:
            body[key] = raw
    return body


def _request_payload(handler) -> dict:
    if getattr(handler, "command", "").upper() == "POST":
        body = _read_body(handler)
        if body:
            return body
    return _read_query(handler)


# ── 请求处理器 ────────────────────────────────────────────────────────────────
class GeoAPIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        routes = {
            "/api/health":         self._health,
            "/api/geo/stream":     self._geo_stream,
            "/api/geo/domains":    self._geo_domains,
            "/api/geo/agents":     self._geo_agents,
            "/api/geo/token-usage": self._token_usage,
            "/.well-known/agent-card.json": self._agent_card,
            "/api/rag-status":     self._rag_status,
            "/api/datasets":       self._datasets,
            "/api/variables":      self._variables,
        }
        handler = routes.get(path)
        if handler:
            handler()
            return
        # A2A dynamic GET routes
        if path.startswith("/a2a/tasks/") and path.endswith("/events"):
            self._a2a_task_events(path[len("/a2a/tasks/"):-len("/events")])
            return
        if path.startswith("/a2a/tasks/"):
            self._a2a_get_task(path[len("/a2a/tasks/"):])
            return
        _send_json(self, {"error": f"Not found: {path}"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path.startswith("/a2a/tasks/"):
            self._a2a_cancel_task(path[len("/a2a/tasks/"):])
        else:
            _send_json(self, {"error": f"Not found: {path}"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        routes = {
            "/api/geo/report":           self._geo_report,
            "/api/geo/stream":           self._geo_stream,
            "/api/geo/fetch-literature": self._fetch_literature,
            "/api/ask":                  self._legacy_ask,
            "/api/query-ocean":          self._query_ocean,
            "/api/ocean/vector-field":   self._vector_field,
            "/a2a/tasks":                self._a2a_create_task,
        }
        handler = routes.get(path)
        # A2A dynamic: /a2a/tasks/{id}
        if not handler and path.startswith("/a2a/tasks/"):
            task_id2 = path[len("/a2a/tasks/"):]
            self._a2a_cancel_task(task_id2)
            return
        if handler:
            handler()
        else:
            _send_json(self, {"error": f"Not found: {path}"}, 404)

    # ── 新 GeoAgent 端点 ──────────────────────────────────────────────────────

    def _geo_report(self):
        """POST /api/geo/report — 完整 GeoAgent JSON 报告。"""
        body = _read_body(self)
        question = str(body.get("question", "")).strip()
        if not question:
            return _send_json(self, {"error": "question is required"}, 400)

        bbox = body.get("bbox") or body.get("region")
        domain = body.get("domain") or None
        variables = body.get("variables") or []
        backend = body.get("backend", "auto")
        top_k = int(body.get("top_k", 6))
        threshold = float(body.get("threshold", 0.22))
        max_revisions = int(body.get("max_revisions", 1))
        trace = bool(body.get("trace", False))
        parallel = bool(body.get("parallel", True))

        try:
            result = geo_graph.run(
                question=question,
                bbox=bbox,
                domain=domain,
                variables=variables,
                backend=backend,
                top_k=top_k,
                threshold=threshold,
                max_revisions=max_revisions,
                trace_enabled=trace,
                use_parallel=parallel,
            )
            _send_json(self, result)
        except Exception as exc:
            log.error("geo_report error: %s\n%s", exc, traceback.format_exc())
            _send_json(self, {"error": str(exc)}, 500)

    def _geo_stream(self):
        """GET/POST /api/geo/stream — SSE 流式报告生成。

        客户端接收格式：
          data: {"type":"token","content":"..."}
          data: {"type":"domain","content":"stargazing"}
          data: {"type":"analysis","content":{...}}
          data: {"type":"done","elapsed_ms":1234,"token_usage":{...}}
          data: {"type":"error","content":"..."}
        """
        body = _request_payload(self)
        question = str(body.get("question", "")).strip()
        if not question:
            return _send_json(self, {"error": "question is required"}, 400)

        bbox = body.get("bbox") or body.get("region")
        domain = body.get("domain") or None
        variables = body.get("variables") or []
        backend = body.get("backend", "auto")
        top_k = int(body.get("top_k", 6))
        threshold = float(body.get("threshold", 0.22))
        max_revisions = int(body.get("max_revisions", 1))

        # 发送 SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

        def sse(data: dict) -> None:
            if "content" in data and "data" not in data:
                data["data"] = data["content"]
            if data.get("type") == "error" and "message" not in data:
                data["message"] = str(data.get("content", ""))
            if data.get("type") == "done" and "token_usage" in data and "usage" not in data:
                data["usage"] = data["token_usage"]
            msg = f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            try:
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except BrokenPipeError:
                pass

        started = time.time()
        geo_llm.reset_usage()

        try:
            # Phase 1: 运行前置节点（intent + planner + context + retrieval + data + screening + reasoning）
            from geo_agent.nodes import intent as intent_node, planner as planner_node, context as ctx_node
            from geo_agent.nodes import retrieval as ret_node, screening as scr_node
            from geo_agent.nodes import data as data_node, reasoning as reas_node, visualization as vis_node

            state = {
                "question": question,
                "bbox": bbox,
                "domain": domain or "",
                "variables": variables,
                "backend": backend,
                "top_k": top_k,
                "threshold": threshold,
                "max_revisions": max_revisions,
                "revisions": 0,
                "trace": [],
                "errors": [],
                "token_usage": {},
            }

            state.update(intent_node.run(state))
            sse({"type": "domain", "content": state.get("domain", "general")})
            sse({"type": "intent", "content": state.get("intent", {})})

            state.update(planner_node.run(state))
            sse({"type": "planner", "content": state.get("execution_plan", {})})

            # 并行 retrieval + context
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fut_ret = executor.submit(ret_node.run, state)
                fut_ctx = executor.submit(ctx_node.run, state)
                ret_result = fut_ret.result()
                ctx_result = fut_ctx.result()

            merged_trace = merge_trace(
                merge_trace(state.get("trace", []), ret_result.get("trace", [])),
                ctx_result.get("trace", []),
            )
            state.update(ret_result)
            state.update(ctx_result)
            state["trace"] = merged_trace
            state.update(data_node.run(state))
            sse({"type": "context", "content": {
                "candidates": len(state.get("candidates", [])),
                "ocean_vars": len(state.get("ocean_data", {}).get("variables", [])),
                "data_context": state.get("data_context", {}),
            }})

            state.update(scr_node.run(state))
            state.update(reas_node.run(state))
            state.update(vis_node.run(state))
            sse({"type": "analysis", "content": {
                "domain_analysis": state.get("domain_analysis", {}),
                "risk_hypotheses": state.get("risk_hypotheses", []),
                "visualization": state.get("visualization", {}),
                "kept_documents": state.get("kept_docs", []),
                "passed_documents": state.get("passed_docs", []),
                "kept_docs": len(state.get("kept_docs", [])),
            }})

            # Phase 2: 流式生成报告
            from geo_agent.nodes import report as report_node
            full_report = []
            for token in report_node.stream(state):
                full_report.append(token)
                sse({"type": "token", "content": token})
            state["report"] = "".join(full_report)
            state["trace"] = list(state.get("trace", [])) + [{
                "node": "ReportNode",
                "mode": "stream",
                "chars": len(state["report"]),
                "revisions": state.get("revisions", 0),
            }]

            # Phase 3: Critic
            from geo_agent.nodes import critic as critic_node
            state.update(critic_node.run(state))

            # Critic 反思循环（流式修订）
            while (
                not state.get("critic_result", {}).get("passed", True)
                and state.get("revisions", 0) < max_revisions
            ):
                sse({"type": "revision", "content": {
                    "revision": state["revisions"] + 1,
                    "feedback": state["critic_result"].get("feedback", ""),
                }})
                full_report = []
                for token in report_node.stream(state, feedback=state["critic_result"].get("feedback")):
                    full_report.append(token)
                    sse({"type": "token", "content": token})
                state["report"] = "".join(full_report)
                state["revisions"] = state.get("revisions", 0) + 1
                state["trace"] = list(state.get("trace", [])) + [{
                    "node": "ReportNode",
                    "mode": "stream",
                    "revised": True,
                    "chars": len(state["report"]),
                    "revisions": state.get("revisions", 0),
                }]
                state.update(critic_node.run(state))

            # 完成
            from geo_agent.nodes import evaluator as evaluator_node
            state.update(evaluator_node.run(state))
            elapsed = round((time.time() - started) * 1000, 2)
            token_usage = geo_llm.get_usage()
            evaluation = _attach_system_metrics(
                state.get("evaluation", {}),
                elapsed,
                token_usage,
            )
            state["evaluation"] = evaluation
            trace = normalize_trace(state.get("trace", []))
            sse({"type": "done", "elapsed_ms": elapsed,
                 "token_usage": token_usage,
                 "domain": state.get("domain", "general"),
                 "trace": trace,
                 "evaluation": evaluation,
                 "visualization": state.get("visualization", {}),
                 "critic": state.get("critic_result", {}),
                 "revisions": state.get("revisions", 0)})

        except Exception as exc:
            log.error("sse stream error: %s", exc)
            sse({"type": "error", "content": str(exc)})

    def _geo_domains(self):
        """GET /api/geo/domains — 返回支持的领域及其描述。"""
        _send_json(self, {
            "domains": [
                {"id": "marine", "name": "海洋要素分析",
                 "description": "SST、叶绿素、盐度、浪高等要素的区域统计与风险评估"},
                {"id": "stargazing", "name": "观星适宜性",
                 "description": "月相、光污染（Bortle量表）、大气透明度综合评估"},
                {"id": "biology", "name": "海洋生物分析",
                 "description": "珊瑚白化、有害藻华、鱼种栖息地适宜性评估"},
                {"id": "navigation", "name": "航行安全评估",
                 "description": "道格拉斯海况、蒲福风级、分船型风险矩阵"},
                {"id": "general", "name": "综合分析",
                 "description": "自动识别领域，综合分析"},
            ],
            "agents": agent_catalog(),
            "tools": _tool_catalog(),
        })

    def _geo_agents(self):
        """GET /api/geo/agents — 返回 Agent 与工具权限清单。"""
        _send_json(self, {"agents": agent_catalog(), "tools": list_tools()})

    def _agent_card(self):
        """GET /.well-known/agent-card.json — A2A-style Agent Card."""
        _send_json(self, agent_card())

    def _token_usage(self):
        """GET /api/geo/token-usage — 当前 session token 消耗。"""
        usage = geo_llm.get_usage()
        _send_json(self, {"token_usage": usage})

    def _fetch_literature(self):
        """POST /api/geo/fetch-literature — 触发 arXiv 文献拉取。

        Body: {"domain": "marine"|"stargazing"|"biology"|"navigation"|"all", "max": 5}
        """
        body = _read_body(self)
        domain = str(body.get("domain", "all")).strip()
        max_per_q = int(body.get("max", 5))
        delay_seconds = float(body.get("delay", 3.0))
        timeout = int(body.get("timeout", 20))
        queries_per_domain = int(body.get("queries_per_domain", 0)) or None

        from geo_agent.knowledge.arxiv_fetcher import fetch_all, fetch_domain, DOMAIN_QUERIES
        try:
            if domain == "all":
                results = fetch_all(
                    max_per_query=max_per_q,
                    verbose=False,
                    delay_seconds=delay_seconds,
                    timeout=timeout,
                    queries_per_domain=queries_per_domain,
                )
                total = sum(len(v) for v in results.values())
                _send_json(self, {
                    "status": "ok",
                    "total_saved": total,
                    "by_domain": {d: len(ps) for d, ps in results.items()},
                })
            elif domain in DOMAIN_QUERIES:
                paths = fetch_domain(
                    domain,
                    max_per_query=max_per_q,
                    verbose=False,
                    delay_seconds=delay_seconds,
                    timeout=timeout,
                    queries_per_domain=queries_per_domain,
                )
                _send_json(self, {"status": "ok", "domain": domain, "saved": len(paths)})
            else:
                _send_json(self, {"error": f"Unknown domain: {domain}"}, 400)
        except Exception as exc:
            log.error("fetch_literature error: %s", exc)
            _send_json(self, {"error": str(exc)}, 500)

    # ── 旧接口兼容 ────────────────────────────────────────────────────────────

    def _legacy_ask(self):
        """POST /api/ask — 旧 RAG 报告（兼容旧前端）。"""
        body = _read_body(self)
        question = str(body.get("question") or body.get("q", "")).strip()
        if not question:
            return _send_json(self, {"error": "question is required"}, 400)
        top_k = int(body.get("top_k", 6))
        threshold = float(body.get("threshold", 0.22))
        backend = str(body.get("backend", "auto"))
        trace = bool(body.get("trace", False))
        region = body.get("region")
        variables = body.get("variables")
        try:
            result = run_pipeline(
                question=question, top_k=top_k, threshold=threshold,
                backend=backend, trace=trace, region=region, variables=variables,
            )
            _send_json(self, result)
        except Exception as exc:
            log.error("ask error: %s", exc)
            _send_json(self, {"error": str(exc)}, 500)

    def _query_ocean(self):
        """POST /api/query-ocean — NetCDF 区域查询（支持 time_index/depth_index）。"""
        body = _read_body(self)
        try:
            result = query_grid(body)
            _send_json(self, result)
        except Exception as exc:
            _send_json(self, {"error": str(exc)}, 500)

    def _vector_field(self):
        """POST /api/ocean/vector-field — 联合返回 u/v/speed 矢量场。"""
        import math as _math
        body = _read_body(self)
        try:
            bounds      = body.get("bounds") or body
            west        = float(bounds.get("west", 117))
            east        = float(bounds.get("east", 127))
            south       = float(bounds.get("south", 18))
            north       = float(bounds.get("north", 26))
            max_pts     = max(100, min(50000, int(body.get("max_points", 4000))))
            time_index  = max(0, int(body.get("time_index") or 0))
            depth_index = max(0, int(body.get("depth_index") or 0))
            step        = max(0, int(body.get("step") or 0))
            dataset     = str(body.get("dataset") or "")
            u_dataset   = str(body.get("u_dataset") or dataset)
            v_dataset   = str(body.get("v_dataset") or dataset)
            u_variable  = str(body.get("u_variable") or "water_u")
            v_variable  = str(body.get("v_variable") or "water_v")

            u_result = query_grid({
                "dataset": u_dataset, "variable": u_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "max_points": max_pts, "step": step,
                "time_index": time_index, "depth_index": depth_index,
            })
            # 若数据集已包含完整矢量（u_grid 字段存在），直接透传
            if "u_grid" in u_result:
                _send_json(self, u_result)
                return

            v_result = query_grid({
                "dataset": v_dataset, "variable": v_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "max_points": max_pts, "step": step,
                "time_index": time_index, "depth_index": depth_index,
            })
            u_grid = u_result.get("values", [])
            v_grid = v_result.get("values", [])
            lats   = u_result.get("lats", [])
            lons   = u_result.get("lons", [])

            speed_grid = []
            flat_speed = []
            for i, u_row in enumerate(u_grid):
                s_row = []
                for j, u_val in enumerate(u_row):
                    v_val = v_grid[i][j] if i < len(v_grid) and j < len(v_grid[i]) else None
                    if u_val is None or v_val is None:
                        s_row.append(None)
                    else:
                        import math as _math
                        spd = round(_math.hypot(float(u_val), float(v_val)), 4)
                        s_row.append(spd)
                        flat_speed.append(spd)
                speed_grid.append(s_row)

            if not flat_speed:
                _send_json(self, {"error": "no valid vector data in selected region"}, 400)
                return

            _send_json(self, {
                "u_dataset": u_dataset, "v_dataset": v_dataset,
                "u_variable": u_variable, "v_variable": v_variable,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "lats": lats, "lons": lons,
                "u_grid": u_grid, "v_grid": v_grid, "speed_grid": speed_grid,
                "time_index": time_index, "depth_index": depth_index,
                "category": "vector",
                "render_modes": ["heatmap", "particles", "contour", "points"],
                "particle_ready": True,
                "shape": {"lat": len(lats), "lon": len(lons)},
                "stats": {
                    "min": round(min(flat_speed), 4),
                    "max": round(max(flat_speed), 4),
                    "mean": round(sum(flat_speed) / len(flat_speed), 4),
                    "count": len(flat_speed),
                },
            })
        except Exception as exc:
            log.error("vector-field error: %s", exc)
            _send_json(self, {"error": str(exc)}, 500)


def _health(self):
    llm_configured = geo_llm.configured()
    llm_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    _send_json(self, {
        "status": "ok",
        "server": "GeoAgent API",
        "version": "2.0.0",
        "llm_configured": llm_configured,
        "llm_model": llm_model,
        "llm": {"configured": llm_configured, "model": llm_model},
        "domains": ["marine", "stargazing", "biology", "navigation"],
    })


def _rag_status(self):
    try:
        _send_json(self, rag_status())
    except Exception as exc:
        _send_json(self, {"error": str(exc)}, 500)


def _datasets(self):
    try:
        _send_json(self, dataset_summary())
    except Exception as exc:
        _send_json(self, {"error": str(exc)}, 500)


def _variables(self):
    try:
        _send_json(self, list_variables())
    except Exception as exc:
        _send_json(self, {"error": str(exc)}, 500)


def _tool_catalog() -> list[dict]:
    from geo_agent.tools import tool_specs
    return [{"name": t["name"], "description": t["description"]} for t in tool_specs()]


def _attach_system_metrics(evaluation: dict, elapsed_ms: float, token_usage: dict) -> dict:
    out = dict(evaluation or {})
    if not out:
        return out
    metrics = dict(out.get("metrics", {}) or {})
    metrics["system"] = {
        "elapsed_ms": elapsed_ms,
        "token_usage": token_usage,
    }
    out["metrics"] = metrics
    return out


def run_server(port: int = PORT) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), GeoAPIHandler)
    log.info("GeoAgent API listening on http://0.0.0.0:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped.")


# ── A2A in-memory task store ──────────────────────────────────────────────────
import threading as _a2a_thread
import datetime as _dt
import uuid as _uuid_mod

_A2A_TASKS: dict = {}
_A2A_LOCK = _a2a_thread.Lock()


def _isoformat() -> str:
    return _dt.datetime.utcnow().isoformat() + "Z"


def _a2a_new_task(input_data: dict) -> dict:
    task_id = str(_uuid_mod.uuid4())
    task = {
        "id": task_id,
        "status": {"state": "submitted", "timestamp": _isoformat()},
        "input": input_data,
        "result": None,
        "events": [],
        "created_at": _isoformat(),
        "updated_at": _isoformat(),
    }
    with _A2A_LOCK:
        _A2A_TASKS[task_id] = task
    return task


def _run_a2a_task_async(task_id: str) -> None:
    def _push(kind: str, data: dict):
        ts = _isoformat()
        with _A2A_LOCK:
            t = _A2A_TASKS.get(task_id)
            if t:
                t["events"].append({"type": kind, "timestamp": ts, **data})
                t["updated_at"] = ts

    def _run():
        with _A2A_LOCK:
            t = _A2A_TASKS.get(task_id)
            if not t:
                return
            t["status"] = {"state": "working", "timestamp": _isoformat()}
            inp = t["input"]
        _push("status_update", {"state": "working"})
        try:
            result = geo_graph.run(
                question=str(inp.get("question", "")).strip(),
                bbox=inp.get("bbox"),
                domain=inp.get("domain") if inp.get("domain") not in (None, "auto") else None,
                backend=str(inp.get("backend") or "auto"),
                top_k=int(inp.get("top_k") or 6),
                max_revisions=int(inp.get("max_revisions") or 1),
                trace_enabled=False,
                use_parallel=True,
            )
            _push("artifact", {"artifact": {
                "type": "report",
                "content": result.get("report", ""),
                "domain": result.get("domain"),
                "critic_result": result.get("critic_result"),
                "token_usage": result.get("token_usage"),
                "elapsed_ms": result.get("elapsed_ms"),
            }})
            with _A2A_LOCK:
                t = _A2A_TASKS.get(task_id)
                if t:
                    t["result"] = result
                    t["status"] = {"state": "completed", "timestamp": _isoformat()}
            _push("status_update", {"state": "completed"})
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.error("A2A task %s failed: %s", task_id, msg)
            with _A2A_LOCK:
                t = _A2A_TASKS.get(task_id)
                if t:
                    t["status"] = {"state": "failed", "timestamp": _isoformat(), "error": msg}
            _push("status_update", {"state": "failed", "error": msg})

    _a2a_thread.Thread(target=_run, daemon=True).start()


# ── Patch A2A methods into GeoAPIHandler ─────────────────────────────────────

def _a2a_create_task(self):
    """POST /a2a/tasks"""
    body = _read_body(self)
    question = str(body.get("question") or "").strip()
    if not question:
        # 支持 A2A message 格式
        for part in body.get("message", {}).get("parts", []):
            if "text" in part:
                question = str(part["text"]).strip()
                break
    if not question:
        _send_json(self, {"error": "question is required"}, 400)
        return
    inp = {k: body[k] for k in ("bbox","domain","backend","top_k","max_revisions")
           if k in body}
    inp["question"] = question
    task = _a2a_new_task(inp)
    _run_a2a_task_async(task["id"])
    _send_json(self, {
        "id": task["id"],
        "status": task["status"],
        "metadata": {"agent": "OceanGeoAgent", "input": inp},
    }, 201)


def _a2a_get_task(self, task_id: str):
    """GET /a2a/tasks/{id}"""
    with _A2A_LOCK:
        task = _A2A_TASKS.get(task_id)
    if not task:
        _send_json(self, {"error": f"Task not found: {task_id}"}, 404)
        return
    resp = {
        "id": task["id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }
    if task["result"]:
        resp["output"] = {
            "report":        task["result"].get("report", ""),
            "domain":        task["result"].get("domain"),
            "critic_result": task["result"].get("critic_result"),
            "token_usage":   task["result"].get("token_usage"),
            "elapsed_ms":    task["result"].get("elapsed_ms"),
        }
    _send_json(self, resp)


def _a2a_task_events(self, task_id: str):
    """GET /a2a/tasks/{id}/events — SSE stream"""
    with _A2A_LOCK:
        task = _A2A_TASKS.get(task_id)
    if not task:
        _send_json(self, {"error": f"Task not found: {task_id}"}, 404)
        return
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("X-Accel-Buffering", "no")
    for k, v in CORS_HEADERS.items():
        self.send_header(k, v)
    self.end_headers()

    sent = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        with _A2A_LOCK:
            task = _A2A_TASKS.get(task_id, {})
            events = list(task.get("events", []))
            state = task.get("status", {}).get("state", "")
        while sent < len(events):
            data = json.dumps(events[sent], ensure_ascii=False)
            try:
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                return
            sent += 1
        if state in ("completed", "failed", "cancelled"):
            try:
                self.wfile.write(b'data: "done"\n\n')
                self.wfile.flush()
            except Exception:
                pass
            return
        time.sleep(0.4)


def _a2a_cancel_task(self, task_id: str):
    """DELETE /a2a/tasks/{id}"""
    with _A2A_LOCK:
        task = _A2A_TASKS.get(task_id)
    if not task:
        _send_json(self, {"error": f"Task not found: {task_id}"}, 404)
        return
    with _A2A_LOCK:
        if task["status"]["state"] in ("submitted", "working"):
            task["status"] = {"state": "cancelled", "timestamp": _isoformat()}
    _send_json(self, {"id": task_id, "status": task["status"]})


# 注入方法到 GeoAPIHandler
GeoAPIHandler._health = _health
GeoAPIHandler._rag_status = _rag_status
GeoAPIHandler._datasets = _datasets
GeoAPIHandler._variables = _variables
GeoAPIHandler._a2a_create_task = _a2a_create_task
GeoAPIHandler._a2a_get_task    = _a2a_get_task
GeoAPIHandler._a2a_task_events = _a2a_task_events
GeoAPIHandler._a2a_cancel_task = _a2a_cancel_task


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GeoAgent API Server")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    run_server(args.port)
