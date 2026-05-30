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
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
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
            "/api/geo/token-usage": self._token_usage,
            "/api/rag-status":     self._rag_status,
            "/api/datasets":       self._datasets,
            "/api/variables":      self._variables,
        }
        handler = routes.get(path)
        if handler:
            handler()
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
        }
        handler = routes.get(path)
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
            # Phase 1: 运行前置节点（intent + context + retrieval + screening + reasoning）
            from geo_agent.nodes import intent as intent_node, context as ctx_node
            from geo_agent.nodes import retrieval as ret_node, screening as scr_node
            from geo_agent.nodes import reasoning as reas_node

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

            # 并行 retrieval + context
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fut_ret = executor.submit(ret_node.run, state)
                fut_ctx = executor.submit(ctx_node.run, state)
                ret_result = fut_ret.result()
                ctx_result = fut_ctx.result()

            state.update(ret_result)
            state.update(ctx_result)
            sse({"type": "context", "content": {
                "candidates": len(state.get("candidates", [])),
                "ocean_vars": len(state.get("ocean_data", {}).get("variables", [])),
            }})

            state.update(scr_node.run(state))
            state.update(reas_node.run(state))
            sse({"type": "analysis", "content": {
                "domain_analysis": state.get("domain_analysis", {}),
                "risk_hypotheses": state.get("risk_hypotheses", []),
                "kept_docs": len(state.get("kept_docs", [])),
            }})

            # Phase 2: 流式生成报告
            from geo_agent.nodes import report as report_node
            full_report = []
            for token in report_node.stream(state):
                full_report.append(token)
                sse({"type": "token", "content": token})
            state["report"] = "".join(full_report)

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
                state.update(critic_node.run(state))

            # 完成
            elapsed = round((time.time() - started) * 1000, 2)
            sse({"type": "done", "elapsed_ms": elapsed,
                 "token_usage": geo_llm.get_usage(),
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
            "tools": _tool_catalog(),
        })

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
        """POST /api/query-ocean — NetCDF 区域查询。"""
        body = _read_body(self)
        try:
            result = query_grid(body)
            _send_json(self, result)
        except Exception as exc:
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


# ── 辅助 ──────────────────────────────────────────────────────────────────────
def _tool_catalog() -> list[dict]:
    from geo_agent.tools import tool_specs
    return [{"name": t["name"], "description": t["description"]} for t in tool_specs()]


# ── 启动 ──────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("GeoAgent API server starting on port %d", PORT)
    log.info("LLM configured: %s (model: %s)", geo_llm.configured(),
             os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

    server = ThreadingHTTPServer(("0.0.0.0", PORT), GeoAPIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    main()
