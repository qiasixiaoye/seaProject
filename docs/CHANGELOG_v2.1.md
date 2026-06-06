# 海洋数字地球系统 — 本轮更新文档

**版本**：v2.1.0  
**更新日期**：2026-06  
**对应路线图阶段**：Phase 2（NetCDF 数据层）、Phase 3（RAGFlow 知识库）、Phase 5（MCP Server）、Phase 6（A2A 协议）

---

## 一、Phase 2.1 — NetCDF 时间/深度切片 + LRU 元数据缓存

**文件**：`ocean_agents_demo/nc_data.py`

### 1.1 time_index / depth_index 支持

`query_grid()` 新增两个参数，允许前端指定读取哪一个时间步或深度层：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `time_index` | int | 0 | 时间维度索引，自动 clamp 到合法范围 |
| `depth_index` | int | 0 | 深度维度索引，自动 clamp 到合法范围 |

内部实现：`_query_grid_netcdf4()` 新增 `_dim_index_for()` 辅助函数，能识别多种维度命名规范（`time/t/ocean_time/depth/lev/zlev/level` 等），在构建 NetCDF 切片选择器时使用对应 index，而不再一律 `selector.append(0)`。

所有返回体统一带以下新字段：

```json
{
  "time_index": 0,
  "depth_index": 0,
  "selected_time": 1234567.0,
  "selected_depth": 0.0,
  "render_time_ms": 42.3
}
```

### 1.2 dataset_summary 返回时间/深度元数据

`_summary_netcdf4()` 新增 time/depth 维度检测，返回给前端用于渲染时间轴滑条和深度下拉：

```json
{
  "time_dim":    "time",
  "time_count":  12,
  "time_units":  "days since 2024-01-01",
  "time_values": [0, 31, 59, ...],
  "depth_dim":   "depth",
  "depth_count": 40,
  "depth_units": "m",
  "depth_values": [0.0, 5.0, 10.0, ...]
}
```

### 1.3 LRU 元数据缓存

新增 `_DATASET_META_CACHE`（最多 32 条），key 为 `(path, mtime_ns)`，文件修改后自动失效。`dataset_summary()` 优先命中缓存，避免大文件重复解析。测试表明缓存命中时重复调用无额外开销。

---

## 二、Phase 2.2 — 矢量场端点 `/api/ocean/vector-field`

**文件**：`backend/app.py`、`geo_api.py`

新增 `POST /api/ocean/vector-field`，联合查询 u / v 分量，返回前端粒子流所需的完整矢量数据：

```json
// 请求体
{
  "dataset": "hycom_luzon_uv_surface_20240905",
  "u_variable": "water_u",
  "v_variable": "water_v",
  "bounds": {"west": 117, "east": 127, "south": 18, "north": 26},
  "time_index": 0,
  "depth_index": 0,
  "max_points": 4000
}

// 响应体（核心字段）
{
  "u_grid": [[...]],
  "v_grid": [[...]],
  "speed_grid": [[...]],
  "category": "vector",
  "particle_ready": true,
  "render_modes": ["heatmap", "particles", "contour", "points"],
  "stats": {"min": 0.02, "max": 1.34, "mean": 0.41, "count": 1200}
}
```

支持以下场景：
- u/v 在同一数据集（只传 `dataset`）
- u/v 分属两个数据集（分别传 `u_dataset` / `v_dataset`）
- 若查询结果本身已包含 `u_grid`（netCDF4 自动合成矢量路径），直接透传

两个服务（Flask backend :5000 和 GeoAgent API :5001）同时实现该端点。

---

## 三、Phase 2.3 — 前端时间轴 + 深度层 + 播放动画

**文件**：`frontend/index.html`、`frontend/assets/app.js`

### 3.1 时间轴滑条

当所选数据集的 `time_count > 1` 时，侧边栏自动显示时间轴控制区：

- 范围滑条（`input[type=range]`），拖动后立即重新查询并渲染对应时间步
- 当前帧标签（`N / Total — 时间值`）
- ▶/■ 播放/停止按钮
- 帧间隔下拉（0.5s / 1s / 2s / 3s）

### 3.2 深度层选择

当 `depth_count > 1` 时显示深度层下拉，列出实际深度值（单位来自数据集）。

### 3.3 Vue 3 响应式新增字段

```js
// data()
timeIndex: 0, depthIndex: 0,
timeCount: 0, depthCount: 0,
timeValues: [], depthValues: [], depthUnits: 'm',
playing: false, playTimer: null, playInterval: 1000,
datasetMeta: {}   // dataset_id → {time_count, depth_count, ...}
```

### 3.4 新增方法

| 方法 | 说明 |
|---|---|
| `playTimelapse()` | 启动播放循环，每帧调用 queryOcean() 后 setTimeout |
| `stopPlayback()` | 停止播放，清除 timer |
| `onTimeSliderChange()` | 滑条拖动：停止播放 + 立即渲染 |
| `onDepthChange()` | 深度切换：立即渲染 |

`selectedVar` watcher 切换数据集时自动重置 timeIndex/depthIndex 并同步 timeCount/depthCount。

---

## 四、Phase 5 — MCP Server（Model Context Protocol）

**文件**：`mcp_server.py`（新建）、`docs/claude_desktop_config_example.json`（新建）

### 4.1 概述

纯 Python 实现，不依赖 `mcp` SDK 包，通过 stdio 传输（Content-Length 帧）与 MCP 客户端通信，可直接被 Claude Desktop 调用。

### 4.2 暴露工具（Tools）

| 工具名 | 说明 |
|---|---|
| `query_ocean_data` | 查询 NetCDF 区域格点数据（支持 time_index/depth_index） |
| `run_ocean_report` | 运行完整多 Agent 报告流水线，返回报告文本和证据链 |
| `list_datasets` | 列出所有可用数据集（含变量、时间/深度维度、推荐渲染模式） |
| `search_literature` | 检索海洋学知识库（RAGFlow 优先，本地兜底） |

### 4.3 暴露资源（Resources）

| URI | 说明 |
|---|---|
| `ocean://datasets` | 实时数据集目录 JSON |
| `ocean://knowledge` | 本地知识库状态（文档数、RAGFlow 配置） |

### 4.4 协议实现

实现以下 JSON-RPC 2.0 方法：`initialize`、`tools/list`、`tools/call`、`resources/list`、`resources/read`、`ping`。

工具调用结果用 MCP 标准 `content` 数组包装：

```json
{
  "content": [{"type": "text", "text": "<json result>"}],
  "isError": false
}
```

### 4.5 Claude Desktop 配置

```bash
python mcp_server.py --config   # 打印配置片段
```

输出示例（合并到 `%APPDATA%/Claude/claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "ocean-geo-agent": {
      "command": "python",
      "args": ["C:/path/to/mcp_server.py"],
      "env": {
        "DEEPSEEK_API_KEY": "...",
        "OCEAN_DATA_DIR": "C:/path/to/data"
      }
    }
  }
}
```

---

## 五、Phase 3 — RAGFlow 知识库管理 API + 前端 UI

**文件**：`backend/app.py`、`frontend/index.html`、`frontend/assets/app.js`

### 5.1 新增后端端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/rag/status` | GET | 返回 LLM、本地知识库、RAGFlow 配置状态（原有） |
| `/api/rag/documents` | GET | 合并返回本地文档列表 + RAGFlow 文档列表 |
| `/api/rag/upload` | POST | 上传 PDF/MD/TXT/JSON 到本地知识库目录（multipart/form-data） |
| `/api/rag/sync-local` | POST | 重新扫描本地知识库目录，清除缓存，返回最新文档数 |

上传限制：单文件最大 50 MB，支持 `.pdf`、`.md`、`.txt`、`.json`。PDF 自动存入 `data/pdf_reports/cn/`，其余存入 `data/knowledge_docs/`。文件名重复时自动添加 `_1`、`_2` 等后缀。

### 5.2 前端知识库管理面板

侧边栏底部新增可折叠「知识库管理」面板，默认折叠。展开后包含：

- **文件上传**：选择文件 + 上传按钮，上传进度和结果提示
- **操作按钮**：重新扫描本地（调 sync-local）、刷新列表（调 documents）
- **本地文档列表**：最多显示 8 条，显示 kind 标签（paper/government_overview/…）和标题
- **RAGFlow 文档列表**：RAGFlow 配置后显示，最多 6 条

折叠标题实时显示总文档数（本地 + RAGFlow）。

---

## 六、Phase 6 — A2A 完整协议端点

**文件**：`geo_api.py`

### 6.1 概述

A2A（Agent-to-Agent）是 Google 提出的多 Agent 互操作协议。本次实现基于 in-memory 任务队列 + 后台线程异步执行，支持标准任务生命周期：

```
submitted → working → completed | failed | cancelled
```

### 6.2 端点列表

| 端点 | 方法 | 说明 |
|---|---|---|
| `POST /a2a/tasks` | POST | 创建并提交任务，立即返回 task_id 和初始状态，后台异步执行 |
| `GET /a2a/tasks/{id}` | GET | 查询任务状态、创建时间、结果（completed 后才有 output） |
| `GET /a2a/tasks/{id}/events` | GET | SSE 实时推送执行进度（text/event-stream，最长 2 分钟） |
| `DELETE /a2a/tasks/{id}` | DELETE | 取消任务（submitted/working 状态有效） |
| `GET /.well-known/agent-card.json` | GET | AgentCard 发现（原有，已含 skills/capabilities） |

### 6.3 请求格式

支持两种格式（自动检测）：

```json
// 简单格式
{"question": "台湾海峡 SST 风险分析", "domain": "marine", "bbox": {...}}

// A2A message 格式
{"message": {"parts": [{"type": "text", "text": "台湾海峡 SST 风险分析"}]}, "domain": "marine"}
```

### 6.4 响应格式

```json
// POST /a2a/tasks 创建响应（201 Created）
{
  "id": "3f8a1b2c-...",
  "status": {"state": "submitted", "timestamp": "2026-06-06T10:00:00Z"},
  "metadata": {"agent": "OceanGeoAgent", "input": {...}}
}

// GET /a2a/tasks/{id} 完成后
{
  "id": "3f8a1b2c-...",
  "status": {"state": "completed", "timestamp": "..."},
  "output": {
    "report": "## 海洋风险分析报告\n...",
    "domain": "marine",
    "critic_result": {...},
    "token_usage": {...},
    "elapsed_ms": 8420
  }
}
```

### 6.5 SSE 事件流格式

```
data: {"type": "status_update", "state": "working", "timestamp": "..."}

data: {"type": "artifact", "artifact": {"type": "report", "content": "...", "domain": "marine"}}

data: {"type": "status_update", "state": "completed", "timestamp": "..."}

data: "done"
```

---

## 七、文件变更汇总

| 文件 | 类型 | 变更说明 |
|---|---|---|
| `ocean_agents_demo/nc_data.py` | 修改 | time/depth 切片、LRU缓存、dataset_summary 扩展 |
| `backend/app.py` | 修改 | 新增 vector-field + 3个 rag 知识库管理端点 |
| `geo_api.py` | 修改 | 新增 vector-field + 完整 A2A 任务协议（5个端点） |
| `frontend/index.html` | 修改 | 时间轴/深度控件、知识库管理面板、配套 CSS |
| `frontend/assets/app.js` | 修改 | Vue 3 响应式字段、时间轴/知识库方法、queryOcean 扩展 |
| `mcp_server.py` | **新建** | 独立 MCP stdio 服务，4工具 + 2资源 |
| `docs/claude_desktop_config_example.json` | **新建** | Claude Desktop 配置片段示例 |

---

## 八、API 变更速查

### 修改端点

| 端点 | 变更 |
|---|---|
| `POST /api/ocean/query` | 新增 `time_index`、`depth_index` 请求参数；响应体新增 `time_index`、`depth_index`、`selected_time`、`selected_depth`、`render_time_ms` |
| `GET /api/ocean/datasets` | 响应体每个数据集新增 `time_count`、`time_values`、`time_units`、`depth_count`、`depth_values`、`depth_units`（当数据集包含对应维度时） |

### 新增端点

| 端点 | 说明 |
|---|---|
| `POST /api/ocean/vector-field` | 矢量场联合查询 |
| `GET /api/rag/documents` | 文档列表 |
| `POST /api/rag/upload` | 文件上传 |
| `POST /api/rag/sync-local` | 重新扫描知识库 |
| `POST /a2a/tasks` | 创建 A2A 任务 |
| `GET /a2a/tasks/{id}` | 查询任务 |
| `GET /a2a/tasks/{id}/events` | SSE 进度流 |
| `DELETE /a2a/tasks/{id}` | 取消任务 |

---

## 九、已知边界与后续路线

当前实现的限制：

- **A2A 任务存储**：使用 in-memory dict，服务重启后任务丢失。生产环境建议替换为 Redis 或 SQLite。
- **MCP Server**：目前为 stdio 模式，不支持 SSE HTTP 传输（Claude Desktop 使用 stdio 足够）。
- **时间轴播放**：每帧独立调用 `/api/ocean/query`，帧间无预加载。数据量大时播放有延迟，后续可加预取队列。
- **知识库上传**：仅存入本地文件系统，不自动推送到 RAGFlow。需在 RAGFlow Web UI 手动同步或调用 RAGFlow 上传 API。

路线图剩余项（🟢长期）：

- Redis + RQ：长时 Agent 任务异步化，A2A 任务持久化
- OpenTelemetry：逐节点耗时 + token 追踪
- Prometheus + Grafana：QPS / 错误率 / latency 监控
- 混合检索 + Cross-encoder Reranker
- LangGraph SqliteSaver 持久化（断点续传）
