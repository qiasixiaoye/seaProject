# 海洋 RAGFlow 多 Agent 报告生成框架设计

## 目标

在现有系统基础上，把“海洋知识问答”升级为“可解释的海洋专题报告生成流水线”：

- 用户输入自然语言问题，可附带当前地球框选区域、海洋要素统计结果。
- 多 Agent 先凝练意图，再调用 RAGFlow 检索海洋文档。
- 对检索结果做摘要阅读、相关性筛选、证据分组和冲突检查。
- 最终用 DeepSeek 生成带证据来源、风险判断和规划建议的海洋报告。
- 短期使用 Flask 内部编排；中长期暴露 A2A 风格接口，便于每个 Agent 独立服务化。

## 外部依据

- RAGFlow HTTP API 支持 `POST /api/v1/retrieval`，可指定 `dataset_ids`、`document_ids`、`top_k`、`similarity_threshold`、`vector_similarity_weight`、`rerank_id`、`keyword`、`highlight`、`cross_languages`、`use_kg`、`toc_enhance` 等检索参数。
- A2A 的核心抽象适合长期演进：`AgentCard` 描述能力，`Task` 表示有状态协作过程，`Message` 承载交互内容，`Artifact` 承载最终产物。

## 总体架构

```text
Browser / Cesium UI
  -> Flask API
      -> OrchestratorAgent
          -> IntentAgent
          -> ContextAgent
          -> RetrievalAgent
                -> RAGFlow /api/v1/retrieval
                -> local fallback retriever
          -> EvidenceScreeningAgent
          -> EvidenceSynthesisAgent
          -> DomainReasoningAgent
          -> ReportAgent
          -> CriticAgent
      -> DeepSeek Chat Model
      -> SQLite trace store
```

## Agent 职责

### 1. OrchestratorAgent

入口编排器，负责：

- 创建任务 ID。
- 汇总用户问题、前端框选区域、海洋要素统计值。
- 顺序或并行调用下游 Agent。
- 保存 trace，前端可展示“检索了哪些文档、保留了哪些证据、过滤了哪些证据”。

输入：

```json
{
  "question": "目标海域海温升高对渔业有什么影响？",
  "region": {"west": 118, "east": 123, "south": 21, "north": 25},
  "ocean_grid": {"variable": "sst", "stats": {"mean": 28.4}},
  "backend": "auto",
  "top_k": 8
}
```

输出：

```json
{
  "task_id": "report_20260521_001",
  "status": "completed",
  "report": "...",
  "evidence": [...],
  "trace": [...]
}
```

### 2. IntentAgent

负责问题凝练和检索计划：

- 识别主题：海温、盐度、叶绿素、海浪、海洋热浪、酸化、海平面、近岸污染、生态保护、灾害。
- 把中文问题转换为中英双语检索 query。
- 判断是否需要区域上下文、海洋要素统计、年份过滤、报告类型过滤。

输出示例：

```json
{
  "intent": "risk_assessment",
  "topics": ["海洋热浪", "珊瑚", "渔业"],
  "queries": [
    "海洋热浪 珊瑚 渔业 风险 监测 规划",
    "marine heatwave coral reef fishery risk monitoring planning"
  ],
  "filters": {
    "language": ["zh", "en"],
    "document_types": ["report", "paper", "product_manual"]
  }
}
```

### 3. ContextAgent

负责把地球框选和 NC 查询结果转成报告上下文：

- 区域边界。
- 数据集名和变量名。
- 统计量：min/max/mean/count/step。
- 数据是否为空、是否只覆盖部分区域。

输出示例：

```json
{
  "region_summary": "框选区域约为台湾周边 119E-123E, 21N-25N。",
  "ocean_variables": [
    {"variable": "sst", "mean": 28.4, "units": "Celsius"},
    {"variable": "chlor_a", "mean": 0.38, "units": "mg m^-3"}
  ],
  "limitations": ["当前仅为局部格点切片，不代表长期趋势。"]
}
```

### 4. RetrievalAgent

负责调用 RAGFlow 或本地 fallback：

- `backend=ragflow`：只调 RAGFlow，失败即报错。
- `backend=auto`：优先 RAGFlow，失败回退本地 RAG。
- `backend=local`：只用本地 metadata/seed 文档。

RAGFlow 请求建议：

```json
{
  "question": "海洋热浪 珊瑚 渔业 风险 监测 规划 marine heatwave coral fishery risk",
  "dataset_ids": ["ocean_cn_reports", "ocean_en_products"],
  "page": 1,
  "page_size": 12,
  "top_k": 24,
  "similarity_threshold": 0.15,
  "vector_similarity_weight": 0.75,
  "keyword": true,
  "highlight": false,
  "cross_languages": ["Chinese", "English"],
  "toc_enhance": true
}
```

输出统一成：

```json
{
  "chunk_id": "...",
  "document_id": "...",
  "document_name": "2023年中国海洋生态环境状况公报.pdf",
  "content": "...",
  "score": 0.71,
  "source": "ragflow",
  "metadata": {"language": "zh", "year": 2023}
}
```

### 5. EvidenceScreeningAgent

负责“读摘要/片段，判断相关性高不高”：

- 对每个 chunk 输出 keep/pass。
- 给出相关性分数和理由。
- 过滤标题相似但内容不足的文档。
- 区分直接证据、背景证据、无关证据。

决策结构：

```json
{
  "document_name": "2023年中国海洋生态环境状况公报.pdf",
  "decision": "keep",
  "score": 0.82,
  "evidence_type": "direct",
  "reason": "包含近岸海域生态环境、赤潮、海洋垃圾或生态监测相关信息。"
}
```

### 6. EvidenceSynthesisAgent

负责把保留证据分组：

- 气候与海温异常。
- 生态风险。
- 渔业与养殖。
- 近岸污染与水质。
- 灾害风险。
- 监测指标。
- 管理建议。

输出结构：

```json
{
  "evidence_groups": [
    {
      "name": "生态风险",
      "claims": [
        {
          "claim": "海洋热浪会增加珊瑚白化和生态系统服务损失风险。",
          "sources": ["Marine heatwaves under global warming"]
        }
      ]
    }
  ]
}
```

### 7. DomainReasoningAgent

负责结合海洋要素数值与知识证据：

- 如果 SST 偏高，触发热浪/珊瑚/渔业风险推理。
- 如果 chlor_a 偏高，提示富营养化/藻华监测。
- 如果 wave height 高，提示航运/近岸灾害风险。
- 如果 salinity 异常，提示淡水输入、河口、海气过程不确定性。

它不直接编造结论，只输出“可由证据支持的风险假设”和“不确定性”。

### 8. ReportAgent

负责最终报告：

报告结构：

1. 问题凝练
2. 区域与数据背景
3. 检索与证据筛选说明
4. 核心发现
5. 风险评估
6. 监测指标建议
7. 空间规划/治理建议
8. 不确定性与下一步数据需求
9. 参考来源

### 9. CriticAgent

负责报告质量检查：

- 是否引用了证据。
- 是否把 metadata 当作正文证据。
- 是否存在“证据不足却强结论”的问题。
- 是否遗漏用户问题中的核心要素。
- 是否输出可执行规划建议。

若检查失败，返回修改意见给 ReportAgent 重写。

## Trace 设计

每次报告生成保存一份 trace：

```json
{
  "task_id": "...",
  "intent": {...},
  "retrieval": {
    "backend": "ragflow",
    "queries": [...],
    "candidate_count": 24
  },
  "screening": [
    {"document": "...", "decision": "keep", "score": 0.82},
    {"document": "...", "decision": "pass", "score": 0.18}
  ],
  "report": {
    "model": "deepseek-chat",
    "elapsed_ms": 18320
  }
}
```

前端可以把 trace 做成“证据链抽屉”，用户点击报告中的来源即可查看对应文档片段。

## API 设计

### 生成报告

`POST /api/agents/report`

```json
{
  "question": "...",
  "backend": "auto",
  "top_k": 8,
  "region": {"west": 118, "east": 123, "south": 21, "north": 25},
  "ocean_grid": {...},
  "trace": true
}
```

### 查询任务

`GET /api/agents/tasks/{task_id}`

### Agent 能力清单

`GET /api/agents`

### A2A 兼容 Agent Card

`GET /.well-known/agent-card.json`

```json
{
  "name": "OceanReportAgent",
  "description": "Generate evidence-grounded ocean risk and planning reports using RAGFlow and ocean gridded data.",
  "version": "0.1.0",
  "url": "http://127.0.0.1:8000/api/a2a",
  "skills": [
    {"name": "ocean_report", "description": "Generate ocean knowledge reports with citations."},
    {"name": "ocean_risk_assessment", "description": "Assess regional marine risks from RAG and NetCDF data."}
  ],
  "defaultInputModes": ["text", "application/json"],
  "defaultOutputModes": ["text/markdown", "application/json"]
}
```

## 实施路线

### 第 1 阶段：内部多 Agent 编排

- 新增 `ocean_agents_demo/agents/`。
- 把当前 `run_pipeline()` 拆成多个 Agent 类。
- 新增 `/api/agents/report`。
- 前端 AI 抽屉展示 trace。

### 第 2 阶段：RAGFlow 深度接入

- `.env` 配置 `RAGFLOW_API_KEY` 和 `RAGFLOW_DATASET_IDS`。
- RetrievalAgent 支持多 query 融合：
  - 中文 query
  - 英文 query
  - HyDE query
- 增加 metadata 过滤：年份、语言、报告类型。
- 支持 RAGFlow 返回 chunk 高亮和文档来源。

### 第 3 阶段：报告质量控制

- 加入 CriticAgent。
- 对低证据报告强制标注“证据不足”。
- 增加来源覆盖率指标：
  - 至少 3 个保留证据。
  - 至少 1 个区域/数据上下文。
  - 每个核心结论至少绑定 1 个来源。

### 第 4 阶段：A2A 兼容

- 暴露 Agent Card。
- 增加 task/message/artifact 数据结构。
- 支持异步任务与轮询。
- 后续可把 RetrievalAgent、ReportAgent 拆成独立容器。

## 当前推荐实现优先级

1. 先实现内部多 Agent，不急着拆微服务。
2. RAGFlow 解析完成后，优先调通 `RetrievalAgent -> RAGFlow /api/v1/retrieval`。
3. 在报告里展示“保留证据/过滤证据/来源片段”。
4. 再加 CriticAgent，防止模型在证据不足时强行下结论。
5. 最后做 A2A 兼容接口。

