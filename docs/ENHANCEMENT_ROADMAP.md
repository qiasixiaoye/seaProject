# 海洋数字地球 — 完善路线图
> 涵盖 RAGFlow、多 Agent、MCP、A2A 及工程化基础设施

---

## 当前架构快照

```
Browser (Vue 3 + Cesium + OL)
    │
    ├── /api/*          Flask backend (port 5000)
    │     ├── NetCDF 读取 + ETOPO 陆地掩膜
    │     └── LangGraph 多 Agent 流水线
    │
    ├── /geo-api/*      GeoAgent API (port 5001)
    │     ├── SSE 流式报告
    │     └── Intent→Retrieval→Context→Screening→Reasoning→Report→Critic
    │
    ├── RAGFlow (port 9380) — 可选，离线自动降级
    └── GeoServer (port 8081) — WMS/WCS 扩展点
```

---

## Phase 1 — 陆地掩膜彻底修复（已完成）

### 方案
- **前端**：topojson-client + world-atlas 110m，画布级像素掩膜
  - `applyLandMaskToCanvas(canvas, bounds)` 异步后处理
  - 与 ETOPO 完全解耦，全球任意 bbox 有效
- **后端**：`_compute_hires_land_mask()` 120×240 numpy 向量化 ETOPO 辅助掩膜

---

## Phase 2 — NetCDF 数据层完善（1-2 周）

### 2.1 时间/深度轴选择
```python
# 新增 query_grid 参数
POST /api/ocean/query
{
  "dataset": "...",
  "variable": "sst",
  "bounds": {...},
  "time_index": 0,          # 新增：时间步索引
  "depth_index": 0,         # 新增：深度层索引
  "time_range": [0, 10]     # 新增：时间范围动画帧
}
```
- 前端：时间轴滑条 + 播放按钮（帧动画）
- 深度层下拉（适用 3D 数据集如 HYCOM）

### 2.2 真实矢量场支持
```
目前：u/v 分量分开查询，需要用户手动选
目标：POST /api/ocean/vector-field?dataset=hycom&bounds=...
      返回 {u_grid, v_grid, speed_grid, direction_grid}
      前端粒子流自动激活
```

### 2.3 数据缓存层
```python
# LRU 缓存避免重复读取大 NC 文件
from functools import lru_cache

@lru_cache(maxsize=32)
def _cached_nc_slice(path_str, var, bbox_key, step):
    ...
```

---

## Phase 3 — RAGFlow 深度集成（2-3 周）

### 3.1 知识库管理 API
```python
# 新增端点
GET  /api/rag/status          # 知识库健康 + 文档统计
POST /api/rag/upload          # 上传 PDF/MD 到 RAGFlow
POST /api/rag/sync-local      # 将 data/knowledge_docs/ 同步到 RAGFlow
GET  /api/rag/documents       # 列出已入库文档
DELETE /api/rag/document/{id} # 删除文档
```

### 3.2 检索增强
```python
# geo_agent/nodes/retrieval.py 改进
class HybridRetriever:
    """Dense (RAGFlow) + BM25 (本地) 混合检索 + 交叉编码器重排"""
    
    def retrieve(self, query, top_k=10):
        # 1. RAGFlow dense 检索
        dense_docs = self.ragflow_search(query, top_k=top_k*2)
        # 2. 本地 BM25 检索
        bm25_docs = self.bm25_search(query, top_k=top_k)
        # 3. RRF 融合 (Reciprocal Rank Fusion)
        fused = self.rrf_merge(dense_docs, bm25_docs)
        # 4. Cross-encoder 重排
        return self.rerank(fused, query, top_k=top_k)
```

### 3.3 引用链验证
```python
# CriticNode 增加引用验证
def verify_citations(report_text, evidence_docs):
    """确保报告中每个引用句都能追溯到具体片段"""
    ...
```

### 3.4 知识库内容规划
| 来源 | 数量 | 更新频率 |
|------|------|----------|
| arXiv 海洋学论文（已有自动拉取） | 200+ | 每周 |
| IPCC AR6 Chapter 5-9 | 5 份 PDF | 一次性 |
| NOAA 数据集说明文档 | 20+ | 季度 |
| 中文海洋学教材摘要 | 10 份 | 一次性 |

---

## Phase 4 — 多 Agent 架构升级（3-4 周）

### 4.1 LangGraph 持久化
```python
# 使用 SqliteSaver 实现会话断点续传
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("instance/agent_state.db")
graph = build_graph().compile(checkpointer=checkpointer)

# 支持 human-in-the-loop
result = graph.invoke(
    state,
    config={"configurable": {"thread_id": session_id}}
)
```

### 4.2 Agent 能力扩展
```
当前 7 Agent：Intent → Retrieval → Context → Screening
              → Reasoning → Report → Critic

新增：
  ├── DataAgent        专门处理 NetCDF/ERDDAP/OPeNDAP 查询
  ├── LiteratureAgent  arXiv + Semantic Scholar 文献搜索  
  ├── VisualizationAgent  生成渲染建议和图表说明
  ├── PlannerAgent     分解复杂问题，并行协调子 Agent
  └── EvaluatorAgent   自动评测报告质量（RAGAS 指标）
```

### 4.3 工具注册表
```python
# geo_agent/tool_registry.py
TOOL_REGISTRY = {
    "query_ocean": {
        "fn": ocean_tools.query_multi_variables,
        "allowed_agents": ["DataAgent", "ContextAgent"],
        "timeout": 30,
    },
    "search_arxiv": {
        "fn": arxiv_fetcher.search,
        "allowed_agents": ["LiteratureAgent", "RetrievalAgent"],
        "timeout": 20,
    },
    "ragflow_search": {
        "fn": ragflow_client.retrieve,
        "allowed_agents": ["RetrievalAgent"],
        "timeout": 15,
    },
}
```

### 4.4 评测框架完善
```bash
# 新增指标
python eval/run_eval.py \
  --metrics retrieval_hit,answer_faithfulness,citation_coverage,latency \
  --judge deepseek   # LLM-as-judge 忠实度评分
```

---

## Phase 5 — MCP 服务器（2 周）

### 5.1 MCP Server 实现
新建 `mcp_server.py`，实现 [MCP 协议](https://modelcontextprotocol.io)：

```python
# mcp_server.py — 通过 stdio 或 SSE 与 MCP 客户端通信
from mcp.server import Server
from mcp.server.stdio import stdio_server

app = Server("ocean-geo-agent")

@app.list_tools()
async def list_tools():
    return [
        Tool(name="query_ocean_data",
             description="查询指定海域的海洋要素（SST、盐度、叶绿素等）",
             inputSchema={...}),
        Tool(name="run_ocean_report",
             description="运行多 Agent 分析，生成海洋风险报告",
             inputSchema={...}),
        Tool(name="list_datasets",
             description="列出可用的 NetCDF 数据集和变量",
             inputSchema={}),
        Tool(name="search_literature",
             description="在知识库中搜索相关海洋学文献",
             inputSchema={...}),
    ]

@app.call_tool()
async def call_tool(name, arguments):
    if name == "query_ocean_data":
        return await handle_ocean_query(arguments)
    elif name == "run_ocean_report":
        return await handle_report(arguments)
    ...
```

### 5.2 Claude Desktop 集成配置
```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "ocean-geo-agent": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "DEEPSEEK_API_KEY": "...",
        "OCEAN_DATA_DIR": "/path/to/data"
      }
    }
  }
}
```

### 5.3 GeoAgent 作为 MCP 客户端
```python
# geo_agent/nodes/retrieval.py — 调用外部 MCP 工具
from mcp import ClientSession

async def mcp_search(query):
    async with ClientSession() as session:
        result = await session.call_tool(
            "brave-search",  # Brave Search MCP
            {"query": f"ocean {query} site:arxiv.org"}
        )
        return result
```

---

## Phase 6 — A2A 协议（Agent-to-Agent）（2 周）

### 6.1 完整 A2A 实现
按 [Google A2A 规范](https://google.github.io/A2A/) 实现：

```python
# geo_api.py 新增路由
routes = {
    "GET  /.well-known/agent-card.json": agent_card,  # 已有
    "POST /a2a/tasks":                  create_task,
    "GET  /a2a/tasks/{id}":             get_task,
    "DELETE /a2a/tasks/{id}":           cancel_task,
    "GET  /a2a/tasks/{id}/events":      task_events_sse,
}
```

```json
// agent-card.json (完整版)
{
  "name": "OceanGeoAgent",
  "description": "海洋空间数据分析与知识检索代理",
  "version": "2.0.0",
  "url": "http://your-host:8000",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [
    {
      "id": "marine-risk-analysis",
      "name": "海洋风险分析",
      "description": "基于 NetCDF + RAGFlow 的区域海洋风险评估",
      "inputModes": ["text", "bbox"],
      "outputModes": ["text", "json"]
    },
    {
      "id": "stargazing-assessment",
      "name": "观星适宜性评估",
      "description": "月相、光污染、大气透明度综合评估"
    }
  ]
}
```

### 6.2 多 Agent 联邦演示
```
用户问题：「评估台湾东部珊瑚礁本月的综合风险」

PlannerAgent 分析后并行调用：
  ├── OceanAgent (A2A)    → SST 异常、盐度、叶绿素数据
  ├── BiologyAgent (A2A)  → 珊瑚白化阈值评估
  └── ClimateAgent (A2A)  → 月度气候背景

结果聚合 → ReportAgent → 综合风险报告
```

---

## Phase 7 — 基础设施（贯穿全程）

### 7.1 消息队列（长任务异步化）
```python
# 使用 Redis + RQ
from rq import Queue
from redis import Redis

q = Queue(connection=Redis())

# 长时间 Agent 任务异步入队
job = q.enqueue(run_geo_agent_pipeline, question, bbox, domain)
# 前端轮询 /api/tasks/{job_id} 获取进度
```

### 7.2 可观测性
```python
# OpenTelemetry 追踪每个 Agent 节点
from opentelemetry import trace
tracer = trace.get_tracer("geo_agent")

def intent_node(state):
    with tracer.start_as_current_span("IntentNode") as span:
        span.set_attribute("domain", state["domain"])
        span.set_attribute("question_len", len(state["question"]))
        result = _run_intent(state)
        span.set_attribute("tokens_used", result.get("token_usage", {}).get("total", 0))
        return result
```

### 7.3 Docker Compose 优化
```yaml
# 新增服务
services:
  redis:
    image: redis:7-alpine
    
  rq-worker:
    build: .
    command: rq worker --with-scheduler
    depends_on: [redis, backend]
    
  mcp-server:
    build: .
    command: python mcp_server.py
    ports: ["3000:3000"]  # MCP stdio/SSE
```

---

## 优先级矩阵

| 功能 | 演示价值 | 技术难度 | 优先级 |
|------|---------|---------|--------|
| ~~topojson 陆地掩膜~~ | ✅ 已完成 | — | — |
| NetCDF 时间轴动画 | ⭐⭐⭐ | ★★ | 🔴 优先 |
| RAGFlow 知识库管理 UI | ⭐⭐⭐ | ★★ | 🔴 优先 |
| MCP Server (mcp_server.py) | ⭐⭐⭐⭐ | ★★★ | 🟠 近期 |
| 真实矢量场粒子流 (u/v合成) | ⭐⭐⭐⭐ | ★★ | 🟠 近期 |
| LangGraph 持久化 | ⭐⭐ | ★★ | 🟡 中期 |
| A2A 完整协议 | ⭐⭐⭐ | ★★★ | 🟡 中期 |
| 混合检索 + Reranker | ⭐⭐ | ★★★ | 🟡 中期 |
| 多 Agent 联邦 | ⭐⭐⭐⭐ | ★★★★ | 🟢 长期 |
| OpenTelemetry 可观测性 | ⭐⭐ | ★★★ | 🟢 长期 |

---

## 下一步具体行动（本周）

1. **下载真实 HYCOM u/v 数据** → 解锁粒子流，是最直观的演示升级
2. **实现 MCP Server 基础版** → `mcp_server.py` + Claude Desktop 配置
3. **完善 A2A agent-card.json** → 填写完整 skills 和 capabilities  
4. **RAGFlow 上传 IPCC 文档** → 提升检索质量

