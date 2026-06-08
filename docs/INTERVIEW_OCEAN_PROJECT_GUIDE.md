# 海洋数据智能分析与三维可视化平台面试说明稿

本文档用于面试前快速复盘项目。目标不是背代码，而是能把每个简历点讲清楚：

- 这个点解决了什么问题。
- 底层逻辑是什么。
- 代码大致怎么实现。
- 做了哪些工程优化。
- 大厂面试官可能怎么追问。
- 哪些地方要如实说明边界。

## 1. 项目一句话定位

这个项目不是简单把大模型接到地图上，而是把多源海洋数据解析、三维地球可视化、RAG 检索、多 Agent 推理、报告生成、Critic 审查、trace 可观测和离线评测串成了一条可演示、可解释、可降级的海洋分析链路。

可以概括为：

```text
多源数据上传/解析
  -> 标准化 metadata 入库
  -> NetCDF 空间查询与三维可视化
  -> RAGFlow / 本地知识库检索
  -> 多 Agent 分析与报告生成
  -> Critic 审查
  -> trace 可观测 + eval 离线评测
```

推荐开场话术：

> 我这个项目的核心不是做一个聊天机器人，而是把海洋空间数据、科研知识库和多 Agent 推理做成一个闭环。用户可以在三维地球上框选区域，后端查询 NetCDF 海洋要素，同时检索海洋文献证据，再由多 Agent 生成带证据引用和质量审查的分析报告。

## 2. 系统架构

```text
Browser UI
  Cesium / OpenLayers
        |
        v
Flask API / GeoAgent API
        |
        +-- NetCDF Reader
        |     bbox slice / stats / land mask / vector field
        |
        +-- Data Ingestion
        |     NetCDF / JSON / MAT metadata parsing
        |
        +-- RAG Retrieval
        |     RAGFlow first / local fallback / EvidenceChunk
        |
        +-- Multi-Agent Pipeline
              Intent / Planner / Retrieval / Context / Screening
              Reasoning / Visualization / Report / Critic / Evaluator
```

代码位置：

| 模块 | 代码位置 | 作用 |
|---|---|---|
| Flask API | `backend/app.py` | 后端 REST API、上传、查询、报告入口 |
| NetCDF 查询 | `ocean_agents_demo/nc_data.py` | 数据集扫描、bbox 切片、统计、land mask |
| 数据入库 | `ocean_agents_demo/ingestion.py` | NetCDF / JSON / MAT 解析并写 metadata 表 |
| RAG 核心 | `ocean_agents_demo/core.py` | 检索、EvidenceChunk、intent、rerank、report |
| 多 Agent 图 | `geo_agent/graph.py`, `geo_agent/nodes/*` | LangGraph / fallback 多节点流程 |
| MCP 服务 | `mcp_server.py` | 标准工具服务，供外部 Agent 客户端调用 |
| 离线评测 | `eval/run_eval.py` | Recall@K、Precision@K、MRR、trace completeness |

## 3. 多源数据解析与入库

### 简历对应点

> 开发数据解析与入库接口，实现多源数据标准化处理与高效入库。  
> 支持 NetCDF / MAT / JSON 等格式文件解析，并完成结构化存储。

### 底层逻辑

海洋数据源格式复杂，不能让前端或 Agent 直接面对原始文件。需要先把不同格式抽象成统一资产 metadata。

统一字段包括：

```text
asset_key
name
ext
kind
parser
status
path
size
mtime
metadata
```

这样后续无论是 NetCDF、JSON 还是 MAT，都能通过同一个 API 查看解析状态、变量信息和文件元数据。

### 编码实现

核心文件：`ocean_agents_demo/ingestion.py`

大致逻辑：

```python
def parse_asset(path):
    ext = path.suffix.lower()

    if ext == ".nc":
        metadata = _parse_netcdf(path)
    elif ext == ".json":
        metadata = _parse_json(path)
    elif ext == ".mat":
        metadata = _parse_mat(path)
    else:
        metadata = {"error": "unsupported"}

    return {
        "name": path.name,
        "ext": ext,
        "kind": kind,
        "parser": parser,
        "status": status,
        "metadata": metadata,
    }
```

Flask API：

```text
POST /api/data/upload
POST /api/data/sync
GET  /api/data/assets
```

### 优化点

- `.mat` 文件优先用 `scipy.io.loadmat` 读取变量；没有 scipy 时降级读取 MAT header，保证 demo 环境不崩。
- `.json` 不只是保存文件，而是抽取 top-level keys 和字段类型。
- `.nc` 抽取变量、单位、long_name、shape、category。
- 当前用 SQLite 做演示级 metadata store，表结构可以迁移到 Postgres。

### 大厂可能追问

**Q1：为什么不用前端直接读取文件？**

回答要点：

- 前端直接读大文件会有性能和安全问题。
- NetCDF / MAT 这类科学数据格式需要后端解析。
- 后端可以统一做权限、解析状态、metadata 标准化和缓存。

**Q2：为什么当前用 SQLite，不是 Postgres？**

回答要点：

- 当前项目是可演示原型，SQLite 降低启动成本。
- 表结构是标准资产 metadata contract，可以迁移到 Postgres。
- 生产环境会加 migration、索引、任务状态表和异步解析队列。

**Q3：MAT 没有 scipy 怎么办？**

回答要点：

- 做了 metadata-only fallback。
- 没有完整变量解析能力时，仍能记录文件、大小、header、解析状态。
- 这样系统不会因为一个解析器缺失导致上传链路失败。

## 4. Flask API 与海洋空间数据查询

### 简历对应点

> 基于 Flask 构建 RESTful API，支撑三维地球可视化系统的数据调用与业务交互。

### 底层逻辑

前端只负责交互和渲染，不直接读 NetCDF。用户在地图上框选 bbox 后，后端根据 dataset、variable 和 bbox 做数据切片。

查询链路：

```text
前端 bbox
  -> Flask API
  -> 找到 NetCDF 文件
  -> 识别 lat/lon
  -> 按 bbox 切片
  -> 降采样
  -> land mask
  -> 计算 min/max/mean/count
  -> 返回 grid + render metadata
```

### 编码实现

核心入口：

```text
GET  /api/ocean/datasets
POST /api/ocean/query
POST /api/ocean/vector-field
```

核心函数：`ocean_agents_demo/nc_data.py`

大致逻辑：

```python
def query_grid(payload):
    path = _dataset_path(payload["dataset"])
    variable = payload["variable"]
    bounds = payload["bounds"]

    lat_idx = filter_lat(bounds)
    lon_idx = filter_lon(bounds)
    stride = compute_stride(max_points)
    grid = read_variable(variable, lat_idx, lon_idx, stride)
    grid = apply_land_mask(grid)
    stats = compute_stats(grid)

    return {
        "values": grid,
        "stats": stats,
        "render_modes": render_modes,
    }
```

### 优化点

- `max_points` 控制返回点数，避免大网格压垮接口。
- 支持 `time_index`、`depth_index` 的基础选择。
- ETOPO land mask 避免海洋变量渲染到陆地。
- 变量分类控制渲染方式，避免物理意义错误。

### 大厂可能追问

**Q1：大 NetCDF 文件怎么优化读取？**

回答要点：

- 先做 metadata cache，避免重复解析。
- 查询时按 bbox 做局部切片，不全量加载。
- 用 `max_points` 自动降采样。
- 生产环境可以加 LRU cache、对象存储、异步预处理、分块索引。

**Q2：经纬度索引怎么做？**

回答要点：

- 从 NetCDF 变量中识别 latitude/longitude 坐标。
- 根据 bbox 过滤 lat/lon index。
- 对二维网格变量按维度映射取值。

**Q3：为什么要做 land mask？**

回答要点：

- 海洋变量不应该渲染到陆地。
- 粗分辨率数据可能跨陆海边界。
- 用地形/水深数据把陆地区域置空，可以提高可视化可信度。

## 5. 三维地球可视化

### 简历对应点

> 支撑三维地球场景下栅格、流场等海洋数据的动态加载与展示。

### 底层逻辑

不同海洋变量的物理含义不同，渲染方式不能一刀切。

| 数据类型 | 示例 | 合理渲染 |
|---|---|---|
| 标量 | SST、盐度、叶绿素 | heatmap / contour / points |
| 地形 | bathymetry | heatmap / contour |
| 矢量分量 | u/v | 单独识别，不直接粒子流 |
| 真实矢量场 | u_grid + v_grid | particles / speed heatmap |

### 编码实现

后端返回渲染 metadata：

```json
{
  "category": "scalar",
  "render_modes": ["heatmap", "contour", "points"],
  "particle_ready": false
}
```

真实矢量场：

```json
{
  "category": "vector",
  "render_modes": ["heatmap", "particles", "contour", "points"],
  "particle_ready": true,
  "u_grid": [],
  "v_grid": [],
  "speed_grid": []
}
```

### 优化点

- 不为了视觉效果把所有变量都渲染成粒子流。
- 只有真实 u/v 矢量场才开放 particles。
- 前端根据后端能力声明渲染，不自己猜。

### 大厂可能追问

**Q：为什么不能用 SST 梯度做粒子流？**

回答要点：

- SST 是标量场，不表示流速方向。
- 用梯度伪造粒子流会误导用户。
- 可视化要遵守数据物理含义。

## 6. RAGFlow 与科研知识库

### 简历对应点

> 基于 Qwen72B + RAGFlow 构建科研知识库，对海洋领域文档、文献进行语义切分、向量检索与问答实现。

### 底层逻辑

RAG 的核心流程：

```text
用户问题
  -> intent 抽取
  -> query variants
  -> RAGFlow / local retrieve
  -> rerank
  -> EvidenceChunk
  -> report
```

不能直接让大模型回答，因为大模型可能幻觉；必须先取证据。

### 编码实现

RAGFlow 检索大致逻辑：

```python
payload = {
    "question": query,
    "dataset_ids": dataset_ids,
    "top_k": top_k,
    "similarity_threshold": 0.15,
    "vector_similarity_weight": 0.75,
}
```

本地 fallback：

```python
keyword_score = compute_keyword_overlap(query, doc)
embedding_score = compute_embedding_similarity(query, doc)
final_score = hybrid(keyword_score, embedding_score)
```

### 优化点

- RAGFlow 优先，本地知识库兜底。
- 本地检索支持 hash/ngram embedding，不依赖外部模型。
- 检索结果统一为 EvidenceChunk。
- 每条结果保留 `score`、`vector_similarity`、`term_similarity`、`rerank_reason`。

### 大厂可能追问

**Q1：RAGFlow 挂了怎么办？**

回答要点：

- backend=auto 时优先 RAGFlow。
- 调用失败会 fallback 到本地知识库。
- 本地仍有 keyword + embedding hybrid 检索能力。

**Q2：为什么要本地 fallback？**

回答要点：

- demo 和面试环境不一定能启动 RAGFlow。
- fallback 保证链路可演示。
- 也方便离线评测和回归测试。

**Q3：Qwen72B 在代码里在哪里？**

回答要点要诚实：

- 实习环境中可接 Qwen72B。
- 当前仓库采用 OpenAI-compatible LLM 配置，默认写的是 DeepSeek。
- 模型层通过环境变量和 client 封装可替换，不影响 RAG 和 Agent 主链路。

## 7. Intent 抽取与召回优化

### 简历对应点

> 了解向量检索、文本切分与语义召回流程。

### 底层逻辑

用户问题不能直接拿去搜。要先抽出结构化意图：

```json
{
  "intent_type": "risk_assessment",
  "entities": ["coral_reef", "fishery"],
  "hazards": ["marine_heatwave"],
  "variables": ["sst", "chlorophyll"]
}
```

然后根据实体、风险、变量生成中英文 query variants。

### 编码实现

核心函数：

```python
def condense_intent(question):
    entities = _extract_alias_matches(question, ENTITY_ALIASES)
    hazards = _extract_alias_matches(question, HAZARD_ALIASES)
    variables = _extract_alias_matches(question, VARIABLE_ALIASES)
    query_variants = _build_intent_query_variants(...)
    return intent
```

### 优化点

- 支持中文和英文 alias。
- 支持 SST、chlorophyll、SWH 等海洋变量同义词。
- query variants 会进入 retrieval trace，方便解释检索来源。

### 大厂可能追问

**Q：为什么不用 LLM 直接改写 query？**

回答要点：

- LLM query rewrite 可用，但不稳定。
- 海洋变量 alias 和领域词可以确定性维护。
- 当前是 deterministic first，LLM optional。

## 8. EvidenceChunk 证据链

### 简历对应点

> 空间数据查询、知识检索、Agent 推理、报告生成与可视化展示串联为可演示链路。

### 底层逻辑

报告中的结论要能追溯到证据。每条证据都要知道来源。

EvidenceChunk 字段：

```text
doc_id
chunk_id
page/pages
source
source_path
section
content_type
similarity
rerank_score
rerank_reason
```

### 编码实现

```python
def enrich_evidence_metadata(docs, route):
    evidence = {
        "doc_id": doc.id,
        "chunk_id": chunk_id,
        "source": doc.source,
        "page": page,
        "similarity": doc.score,
    }
```

### 优化点

- RAGFlow 和 local 输出统一结构。
- 报告显式引用 `[E1: doc_id/chunk_id]`。
- Critic 可以检查引用缺失。

### 大厂可能追问

**Q：怎么防止模型乱引用？**

回答要点：

- 报告输入只给 kept evidence。
- evidence 有稳定 id。
- Critic 检查 citation missing。
- 后续可以加 sentence-level citation verifier。

## 9. 多 Agent 工作流

### 简历对应点

> 设计多 Agent 工作流，覆盖意图识别、任务规划、检索、报告生成与 Critic 质量反馈。

### 底层逻辑

一个 Agent 很难同时完成所有事情，所以拆成多个职责明确的节点：

```text
Intent
Planner
Retrieval
Context/Data
Screening
Reasoning
Visualization
Report
Critic
Evaluator
```

### 编码实现

大致流程：

```python
state = intent.run(state)
state = planner.run(state)
state = retrieval.run(state)
state = context.run(state)
state = screening.run(state)
state = reasoning.run(state)
state = report.run(state)
state = critic.run(state)
state = evaluator.run(state)
```

### 优化点

- LLM 不可用时有 heuristic fallback。
- LangGraph 可用时走图编排，不可用时走 fallback。
- Retrieval 和 Context 可以并行。
- 每个节点输出 trace。

### 大厂可能追问

**Q1：为什么要多 Agent，不用一个 prompt？**

回答要点：

- 单 prompt 可控性差。
- 多 Agent 职责清晰，方便 trace、调试和降级。
- 检索、数据查询、报告生成、质量检查是不同任务。

**Q2：多 Agent 会不会增加延迟？**

回答要点：

- 会，所以需要 fallback 和并行。
- 检索和数据查询可并行。
- 离线评测记录 latency。
- 对简单问题可以裁剪节点。

## 10. Critic 质量反馈

### 简历对应点

> Critic 质量反馈。

### 底层逻辑

报告生成后再检查：

- 有没有证据引用。
- 有没有无证据强结论。
- 有没有说明数据局限。
- 有没有覆盖用户问题。

### 编码实现

```python
if state.kept and not has_evidence_ref:
    issues.append("citation_missing")

if not state.kept and has_strong_claim:
    issues.append("unsupported_strong_claim")

if missing_variables and not has_limitation:
    issues.append("data_limitation_missing")
```

### 优化点

- 无 LLM key 时走规则 Critic。
- 有 LLM 时走自然语言审查。
- Critic 可触发报告重写。

### 大厂可能追问

**Q：Critic 本身会不会也幻觉？**

回答要点：

- 所以保留规则 Critic。
- LLM Critic 只做增强，不作为唯一判断。
- 关键约束如 citation missing 用规则可确定检查。

## 11. Trace 可观测

### 简历对应点

> 支持 trace 结构化输出。

### 底层逻辑

多 Agent 系统如果没有 trace，就无法解释为什么结果这样生成。每个节点都要输出统一结构。

标准 trace：

```text
node
mode
status
elapsed_ms
input_summary
output_summary
error
```

### 编码实现

```python
def normalize_trace(trace):
    return {
        "node": node,
        "mode": mode,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "error": error,
    }
```

### 优化点

- LangGraph 和 fallback 路径统一 trace schema。
- Evaluator 统计 trace completeness。
- 前端可以展示节点状态、模式和摘要。

### 大厂可能追问

**Q：trace 和 log 有什么区别？**

回答要点：

- log 偏系统排障。
- trace 是业务链路可观测，记录每个 Agent 的输入输出摘要和状态。
- trace 可用于 UI 展示、评测和复盘。

## 12. MCP 工具服务

### 简历对应点

> 了解 Tool Calling、MCP、Agent Skills。

### 底层逻辑

MCP 是把系统能力暴露给外部 Agent 客户端的标准工具服务。它不是替代 Flask，而是提供一个 Agent-friendly 的工具入口。

工具包括：

```text
query_ocean_data
search_literature
run_ocean_report
list_datasets
```

### 编码实现

错误 payload 统一：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "...",
  "details": {},
  "retryable": false
}
```

### 优化点

- `query_ocean_data` 默认 compact 输出，避免大网格。
- `run_ocean_report` 可选 trace_summary。
- smoke test 覆盖工具列表、无效 bbox、文献检索和报告生成。

### 大厂可能追问

**Q：MCP 在你的项目里是不是主链路？**

回答要点要诚实：

- 当前主业务链路还是 Flask / GeoAgent API。
- MCP 是外部 Agent 客户端的工具服务暴露。
- 它证明项目能力可以被标准 Agent 客户端调用，但不是强依赖主链路。

## 13. 离线评测 Harness

### 简历对应点

> 具备从工具封装到链路评测的实践经验。

### 底层逻辑

RAG 和 Agent 不能只靠主观判断，需要固定数据集和指标。

评测指标：

```text
Recall@K
Precision@K
MRR
nDCG@K
citation coverage
trace completeness
latency
missed gold
top retrieved
```

### 编码实现

命令：

```bash
python eval/run_eval.py --backend local
```

输出：

```text
eval/report.json
eval/report.md
```

### 优化点

- 每次记录 backend、top_k、commit hash、运行时间。
- 可以比较不同检索策略的效果。
- 支持后续接入 RAGAS 或 LLM-as-judge。

### 大厂可能追问

**Q：Recall@K 和 Precision@K 分别说明什么？**

回答要点：

- Recall@K 看 gold evidence 是否被召回。
- Precision@K 看 top K 里有多少是相关结果。
- RAG 里 Recall 重要，因为漏掉关键证据会导致回答错误。

**Q：你的评测集够不够？**

回答要点要诚实：

- 当前是轻量回归评测集，不是学术 benchmark。
- 作用是保证每次改动不明显回退。
- 后续可以扩展 graded relevance、负样本和 LLM-as-judge。

## 14. Docker 工程化部署

### 简历对应点

> 基于 Docker 完成服务容器化构建与部署，提高环境一致性与交付效率。

### 底层逻辑

把系统拆成多个服务，统一用 Docker Compose 管理：

```text
backend
geo-api
nginx
geoserver
ragflow
```

### 编码实现

核心文件：

```text
docker-compose.yml
backend/Dockerfile
```

Dockerfile 大致逻辑：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt
COPY backend /app/backend
COPY ocean_agents_demo /app/ocean_agents_demo
COPY geo_agent /app/geo_agent
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "backend.app:app"]
```

### 优化点

- Flask/Gunicorn 容器化。
- Nginx 代理前端。
- GeoAgent API 单独服务。
- RAGFlow 通过环境变量接入。
- 数据目录 volume 挂载。

### 大厂可能追问

**Q：Docker 部署遇到过什么问题？**

回答要点：

- 中文路径下 Docker Desktop build session 可能有编码问题。
- 长期建议英文路径 clone。
- 生产环境要补 healthcheck、日志挂载、镜像版本锁定和 CI 构建。

## 15. 项目边界与诚实说法

面试时不要把 demo 讲成生产系统。可以这样说明边界：

| 点 | 当前状态 | 面试说法 |
|---|---|---|
| Postgres | 当前默认 SQLite | 表结构已标准化，生产可迁 Postgres |
| Qwen72B | 仓库默认 DeepSeek/OpenAI-compatible | 实习环境可接 Qwen72B，项目模型层可替换 |
| RAGFlow | 支持 API 接入，未强依赖 | RAGFlow 优先，本地 fallback 保证可演示 |
| MAT 解析 | 有 scipy 时完整解析，否则 metadata-only | 做了降级，保证链路稳定 |
| PDF 深度解析 | 默认 metadata / text chunk，MinerU 规划中 | 后续可接 MinerU 做图表和版面解析 |
| MCP | 工具服务暴露，不是主业务链路 | 用于外部 Agent 客户端调用 |

## 16. 2 分钟面试介绍

> 我做的是一个海洋数据智能分析与三维可视化平台，核心是把多源科学数据、三维地球、RAG 检索和多 Agent 推理串成一条可演示链路。数据层面，我做了 NetCDF、JSON、MAT 的解析和标准化 metadata 入库，上传后能记录变量、shape、解析器、状态和文件信息。空间查询层面，前端在三维地球上框选区域，后端根据 bbox 对 NetCDF 做切片、降采样、统计和 land mask，然后返回 grid 和渲染 metadata。知识层面，我接入 RAGFlow，同时保留本地 fallback，用户问题会先做 intent 抽取，生成中英文 query variants，再做多路召回和 rerank，证据统一成 EvidenceChunk。Agent 层面，我把流程拆成 Intent、Planner、Retrieval、Context、Screening、Reasoning、Report、Critic 和 Evaluator，每个节点都有 trace，Critic 会检查引用缺失、无证据强结论和数据局限。工程化上，我还做了 MCP 工具服务、Docker Compose 部署和离线 eval harness，用 Recall@K、Precision@K、MRR、citation coverage 和 trace completeness 评估链路效果。

## 17. 5 分钟深度介绍

> 这个项目最开始是一个海洋数据可视化平台，后来我把它扩展成了空间数据 + 知识检索 + 多 Agent 分析的闭环。第一层是数据解析和管理，我新增了数据资产入库流程，支持 NetCDF、JSON、MAT 等格式。NetCDF 会抽取变量、单位、shape 和 long_name，JSON 会抽取字段结构，MAT 在 scipy 可用时解析变量，不可用时做 header-level metadata fallback。所有结果写入 dataset_assets 元数据表，并通过 `/api/data/upload`、`/api/data/sync`、`/api/data/assets` 对外提供。
>
> 第二层是空间数据查询。用户在 Cesium 或 OpenLayers 上框选 bbox，后端根据 dataset 和 variable 找到 NetCDF 文件，识别经纬度坐标后做局部切片，并根据 max_points 自动降采样，最后返回 grid、统计值和渲染建议。这里我做了一个比较重要的约束：不同变量按物理意义限制渲染方式，比如 SST、盐度、叶绿素是标量，只开放 heatmap、contour、points；只有真实 u/v 矢量场才开放粒子流。这样避免了看起来很炫但物理上错误的可视化。
>
> 第三层是 RAG。用户问题不会直接交给大模型，而是先经过结构化 intent 抽取，识别风险因子、实体和海洋变量，比如 marine_heatwave、coral_reef、sst、chlorophyll，然后生成中英文 query variants。检索时优先调用 RAGFlow，失败时降级到本地知识库。本地检索不是纯关键词，而是 keyword score 和 hash/ngram embedding score 做 hybrid fusion。所有检索结果都会统一成 EvidenceChunk，保留 doc_id、chunk_id、page、source、similarity 和 rerank_reason。
>
> 第四层是多 Agent。我把流程拆成 Intent、Planner、Retrieval、Context/Data、Screening、Reasoning、Visualization、Report、Critic、Evaluator。这样每个 Agent 的职责清晰，方便调试和降级。比如 Retrieval 只负责证据召回，Context 负责空间数据查询，Screening 负责证据筛选，Report 生成报告，Critic 检查引用缺失、无证据强结论和数据局限。所有节点都会输出统一 trace，包括 node、mode、status、elapsed_ms、input_summary、output_summary 和 error。
>
> 最后一层是工程闭环。我做了 MCP stdio 服务，把查询海洋数据、检索文献、生成报告等能力暴露成标准工具；也做了离线 eval harness，用固定问题集评估 Recall@K、Precision@K、MRR、citation coverage、trace completeness 和 latency。这样每次优化检索或 Agent 流程，不是靠感觉判断，而是能看到指标有没有回退。

## 18. 高频追问速答

### Q1：这个项目最难的点是什么？

回答：

> 最难的是把空间数据、文献证据和 Agent 推理统一起来。空间数据强调 bbox、变量、时间/深度层，RAG 强调 chunk、source、score，多 Agent 强调状态流和 trace。我的做法是把数据资产、证据和 trace 都标准化，分别形成 dataset_assets、EvidenceChunk 和 normalized trace 三个 contract。

### Q2：如果 RAG 检索结果不准怎么办？

回答：

> 我做了几层优化：第一是 intent 抽取，把问题转成实体、风险、变量；第二是中英文 query variants；第三是 RAGFlow 和本地 fallback 多路召回；第四是 keyword + embedding hybrid；第五是 rerank 和 EvidenceChunk；最后用 eval harness 看 Recall@K 和 Precision@K。

### Q3：为什么不用一个 Agent 全部完成？

回答：

> 一个 Agent 全包虽然简单，但可控性差，也不好排查错误。多 Agent 拆分后，每个节点职责明确，而且能输出 trace。比如检索错了能看 RetrievalNode，数据缺失能看 ContextNode，报告引用问题能看 CriticNode。

### Q4：如何避免大模型幻觉？

回答：

> 首先报告生成前必须有 evidence，证据统一成 EvidenceChunk；其次报告里显式引用 `[E#]`；再由 Critic 检查引用缺失和无证据强结论；最后用 citation coverage 做离线评测。

### Q5：这个项目如何评测？

回答：

> 我维护了 `eval/dataset.jsonl`，每条问题有 relevant_documents 和 relevant_chunks。评测脚本会跑完整 pipeline，计算 Recall@K、Precision@K、MRR、citation coverage、trace completeness 和 latency，并输出 report.json/report.md。

### Q6：MCP 在项目里到底有什么用？

回答：

> MCP 不是主业务链路，而是把系统能力暴露给外部 Agent 客户端，比如 Claude Desktop。当前暴露了 query_ocean_data、search_literature、run_ocean_report、list_datasets。它证明这个项目的能力可以作为标准工具服务被外部 Agent 调用。

### Q7：当前项目和生产系统差距在哪里？

回答：

> 主要差在生产级数据治理和部署。当前 metadata store 是 SQLite，生产会迁到 Postgres；RAGFlow 同步还可以继续完善；PDF 图表解析可以接 MinerU；Docker 需要补 CI、healthcheck 和日志体系。但面试 demo 的主链路已经完整：数据解析、空间查询、RAG、多 Agent、trace、Critic、eval 都能跑。

## 19. 面试前建议演示顺序

推荐演示顺序：

```text
1. 打开前端三维地球，框选区域。
2. 调用 /api/ocean/query 展示 NetCDF 查询结果。
3. 调用 /api/data/assets 展示数据资产入库结果。
4. 提一个海洋热浪 / 酸化 / SST 相关问题。
5. 展示 Agent trace。
6. 展示 kept evidence 和 report。
7. 展示 eval/report.md。
8. 如果被问外部工具，展示 MCP smoke test。
```

推荐测试命令：

```bash
python scripts/test_data_ingestion.py
python eval/run_eval.py --backend local
python scripts/test_mcp_stdio.py
python -m py_compile ocean_agents_demo/core.py geo_agent/graph.py geo_agent/state.py mcp_server.py eval/run_eval.py
```

## 20. 最终总结

这个项目最适合强调三点：

1. **不是单点功能，而是闭环系统**  
   数据解析、空间查询、知识检索、多 Agent、报告、Critic、eval 都串起来了。

2. **不是黑盒生成，而是可解释链路**  
   EvidenceChunk、trace、rerank_reason、citation coverage 都能解释结果来源。

3. **不是只依赖外部服务，而是可降级可演示**  
   RAGFlow 不可用时本地 fallback，LLM 不可用时 heuristic fallback，MAT parser 不可用时 metadata fallback。

