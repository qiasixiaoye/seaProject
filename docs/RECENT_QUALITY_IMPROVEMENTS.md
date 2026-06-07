# Recent Quality Improvements

本文档汇总 2026-06-07 以来围绕 RAG、Agent trace、MCP 与评测体系完成的质量改动，作为当前系统能力说明和后续完善入口。

## 1. 改动总览

| 方向 | 已落地能力 | 主要文件 | 当前状态 |
|---|---|---|---|
| 评测指标 | 定义 Retrieval、Evidence、Answer、Tool、System 五类指标；明确哪些已可计算，哪些需要标注或 judge。 | `docs/EVALUATION_METRICS.md` | 基础指标框架已完成 |
| 证据契约 | 统一本地/RAGFlow 检索结果为 `EvidenceChunk`，保留 chunk、页码、section、content_type、score、rerank、route 等字段。 | `ocean_agents_demo/core.py` | 已接入输出 |
| 工具调用契约 | RetrievalNode tool-loop 记录 `ToolCallTrace`，包含工具名、参数摘要、成功状态、耗时、结果大小和错误。 | `geo_agent/nodes/retrieval.py` | 已接入 trace |
| 前端质量可见性 | 质量面板可展示结构化 evaluation metrics 与 trace summary。 | `backend/app.py` 等 | 基础可视化已完成 |
| RAG rerank | 增加确定性 rerank，输出 `rank_before`、`rank_after`、`rerank_score`、`rerank_reason`。 | `ocean_agents_demo/core.py` | 启发式版本已完成 |
| Query expansion | 增加海洋领域中英别名扩展，例如 SST、chlorophyll-a、SWH、marine heatwave、coral bleaching。 | `ocean_agents_demo/core.py` | 确定性基础版已完成 |
| 多路召回 trace | 记录每个 query variant、route、backend、count、fusion summary；证据保留命中的 routes。 | `ocean_agents_demo/core.py`, `geo_agent/nodes/retrieval.py` | 基础版已完成 |
| 结构化 chunking | Markdown 按标题层级切分；PDF 可选按页/标题启发式切分；输出 chunk profile 和 parser。 | `ocean_agents_demo/core.py`, `docs/RAG_CHUNKING_STRATEGY.md` | 本地基础版已完成 |
| MCP 协议测试 | 增加 stdio Content-Length 帧 smoke test，覆盖 initialize、tools/list、tools/call、resources/list、resources/read、ping。 | `scripts/test_mcp_stdio.py` | 已完成 |
| MCP 输出约束 | `query_ocean_data` 增加 bbox/index/max_points 校验，默认返回 compact stats/metadata，full grid 需显式开启。 | `mcp_server.py` | 已完成 |
| 完善待办表 | 建立 RAG、Evaluation、Agent、MCP、A2A、Data、UI 的问题-方法-验收标准表。 | `docs/QUALITY_IMPROVEMENT_BACKLOG.md` | 持续维护 |

## 2. RAG 召回链路现在如何工作

当前 RAG 链路的主入口在 `ocean_agents_demo/core.py`：

1. `expand_retrieval_queries()` 根据原始问题、keywords、topics 生成 query variants。
2. `retrieve_with_info()` 对 query variants 执行 RAGFlow 或 local route 检索。
3. 检索结果按 `doc.id/title` 去重，保留所有命中路线到 `metadata.routes`。
4. `rerank_evidence_docs()` 对融合后的候选做启发式 rerank。
5. `doc_to_evidence_dict()` 输出统一 `EvidenceChunk`。

可在 trace 中看到：

```json
{
  "node": "RetrievalNode",
  "mode": "multi-query",
  "queries": ["原始 query", "领域别名 query"],
  "routes": [
    {"route": "ragflow_vector", "backend": "ragflow", "count": 0},
    {"route": "local_keyword", "backend": "local", "count": 6}
  ],
  "fusion": {
    "method": "best_score_then_heuristic_rerank",
    "input_count": 14,
    "output_count": 2
  }
}
```

## 3. EvidenceChunk 字段说明

每条最终证据都应包含 `evidence` 块。关键字段如下：

| 字段 | 用途 |
|---|---|
| `doc_id` | 文档级来源，用于文档召回评测和引用归因。 |
| `chunk_id` | chunk 级来源，用于 Recall@K、Precision@K、MRR、nDCG@K。 |
| `document_name` | 原始文件名或 RAGFlow 文档名。 |
| `page` / `pages` | PDF 页码审计。 |
| `section` / `section_path` | 结构化切分边界与引用上下文。 |
| `content_type` | 区分 `paragraph`、`markdown_section`、`pdf_text`、`table`、`figure_caption`、`document_metadata`。 |
| `chunk_profile` | 当前 chunk size/overlap 策略，例如 `chinese_report`、`english_paper`。 |
| `parser` | chunk 来源解析器，例如 `markdown_structure`、`pypdf_structure`、`metadata_only`、`ragflow`。 |
| `route` | 最终选择的召回路线，例如 `local_keyword`、`ragflow_vector`。 |
| `retrieval_query` / `query_variant` | 命中该 chunk 的 query 与 query 类型。 |
| `routes` | 去重前该 chunk 命中过的所有路线。 |
| `rank_before` / `rank_after` | rerank 前后排名。 |
| `rerank_score` / `rerank_reason` | rerank 分数和可解释原因。 |

## 4. Chunking 策略

当前本地 chunking 是结构优先的基础版：

| 类型 | 默认策略 | 输出 |
|---|---|---|
| Markdown | 按 `#` 标题层级切 section，再按段落组 chunk。 | `local_markdown_chunk` |
| PDF metadata-only | 默认不解析正文，仅使用文件名/元数据。 | `local_pdf_metadata` |
| PDF text mode | 设置 `OCEAN_PARSE_PDF_ON_LOAD=true` 后提取前 `OCEAN_PDF_PAGE_LIMIT` 页，按页和常见论文标题启发式切分。 | `local_pdf_chunk` |

默认 profiles：

| Profile | Unit | Max | Overlap | 适用场景 |
|---|---:|---:|---:|---|
| `chinese_report` | chars | 800 | 120 | 中文报告、中文 PDF、中文知识笔记 |
| `english_paper` | tokens | 520 | 100 | 英文论文 |
| `markdown_note` | chars | 900 | 120 | 本地 Markdown 摘要 |
| `metadata_only` | chars | 900 | 0 | 未解析 PDF 的文件级元数据 |

详细说明见 `docs/RAG_CHUNKING_STRATEGY.md`。

## 5. RAGFlow 当前体现方式

RAGFlow 仍作为独立检索后端：

| 能力 | 当前行为 |
|---|---|
| 强制使用 | `backend=ragflow`，未配置或失败时直接报错。 |
| 自动回退 | `backend=auto` 时优先 RAGFlow，失败后走 local。 |
| 元数据归一 | RAGFlow chunk 会转换为 `Doc`，并输出 `EvidenceChunk`。 |
| route 标记 | RAGFlow 召回标记为 `ragflow_vector`。 |
| 诊断 | trace 中会记录 RAGFlow 未配置或调用失败的错误。 |

尚未完成的是读取 RAGFlow 内部 parser/chunk/rerank 配置；目前只能展示 dataset、document_count、chunk_count、embedding_model 等 API 可见字段。

## 6. MCP 当前体现方式

MCP server 已完成基础硬化：

| 项目 | 当前行为 |
|---|---|
| 协议 | 自定义 stdio JSON-RPC + Content-Length 帧。 |
| 工具 | `list_datasets`、`query_ocean_data`、`run_ocean_report`、`search_literature`。 |
| 资源 | `ocean://datasets`、`ocean://knowledge`。 |
| 输入约束 | bbox、time_index、depth_index、max_points 有校验和边界。 |
| 输出约束 | `query_ocean_data` 默认 compact，返回 stats、shape、sample、metadata；full grid 需 `include_grid=true`。 |
| 回归测试 | `scripts/test_mcp_stdio.py` 覆盖主要 MCP 方法。 |

## 7. 评测指标与验收标准

当前先定义指标，不急于跑大规模实验。建议后续评测按三层推进：

| 层级 | 指标 | 依赖 |
|---|---|---|
| 检索层 | `Recall@K`、`Precision@K`、`MRR`、`nDCG@K` | 标注 `relevant_documents` / `relevant_chunks` |
| 证据层 | `citation_coverage`、`citation_accuracy`、证据 span 命中率 | claim extraction 或人工/LLM judge |
| Agent/工具层 | `tool_success_rate`、`tool_call_count`、`used_in_report_rate`、trace completeness | 结构化 trace 和工具输出关联 |

当前已可直接观察的字段包括：

| 类别 | 已有字段 |
|---|---|
| Retrieval | `candidate_count`、`kept_count`、`passed_count`、`avg_candidate_score`、`top_score` |
| Evidence | `citation_coverage`、`evidence_count`、`backend`、`route` |
| Rerank | `rank_before`、`rank_after`、`rerank_score`、`rerank_reason` |
| Tool | `tool_call_count`、`tool_success_rate`，在 trace 有 tool_calls 时可计算 |
| System | `elapsed_ms`、token usage、estimated cost |

## 8. 本地验证命令

最近几轮改动使用过的核心 smoke test：

```powershell
python -m py_compile ocean_agents_demo\core.py geo_agent\nodes\retrieval.py
python scripts\test_mcp_stdio.py
```

检查 query expansion、routes、fusion：

```powershell
python -c "from ocean_agents_demo import core; intent={'retrieval_query':'SST coral bleaching fisheries','keywords':['sst','coral','fisheries'],'original_question':'SST coral bleaching fisheries'}; docs, used, info=core.retrieve_with_info(intent, 3, 'local'); print(used, len(docs), info)"
```

检查结构化 chunk metadata：

```powershell
python -c "from ocean_agents_demo import core; core.clear_doc_cache(); docs=core.load_docs(); d=next(x for x in docs if x.kind=='local_markdown_chunk'); print(core.doc_to_evidence_dict(d)['evidence'])"
```

检查 PDF text chunk 模式：

```powershell
$env:OCEAN_PARSE_PDF_ON_LOAD='true'
$env:OCEAN_PDF_PAGE_LIMIT='1'
python -c "from ocean_agents_demo import core; core.clear_doc_cache(); docs=core.load_docs(); print(any(d.kind=='local_pdf_chunk' for d in docs))"
```

## 9. 最近提交

| Commit | 内容 |
|---|---|
| `3c3469d` | Add structured evaluation metrics |
| `de4c88d` | Expose trace schema and quality panel |
| `5ee0b9e` | Normalize evidence and tool trace contracts |
| `0bd8c43` | Add deterministic evidence reranking |
| `18a302b` | Add MCP stdio smoke coverage |
| `9cd5ad1` | Bound MCP ocean data responses |
| `c78cac4` | Add deterministic query expansion retrieval |
| `c081a38` | Add structure-aware local chunking |

## 10. 未完成但应继续完善的点

| 优先级 | 问题 | 建议方法 |
|---|---|---|
| P0 | PDF 图表、公式、OCR、bbox 还不是一等证据。 | 接入 MinerU 或同类 layout parser，输出 paragraph/table/figure/caption/formula blocks。 |
| P0 | 还没有真实 gold relevance 标注集。 | 为典型海洋问题标注 relevant documents/chunks，跑 Recall@K、Precision@K、MRR、nDCG@K。 |
| P1 | 多路召回目前是 RAGFlow vector + local keyword 基础融合，不是真 BM25/RRF。 | 加 BM25 route 和 Reciprocal Rank Fusion。 |
| P1 | rerank 是启发式，不是 cross-encoder/BGE reranker。 | 配置外部 rerank model，并保留启发式 fallback。 |
| P1 | TopK 可能来自同一文档或相邻 chunk。 | 加 MMR 或 per-document cap，trace 记录 diversity filtering。 |
| P1 | 引用还没有 claim-level evidence span 对齐。 | 抽取报告 claims，要求每个关键 claim 绑定具体 evidence span。 |
| P1 | MCP 错误语义还可更稳定。 | 标准化 `code/message/details/retryable` 错误 payload。 |
| P2 | RAGFlow 内部 parser/chunk 设置未完全暴露。 | 能从 API 取则取；不能取则维护 dataset config notes。 |

## 11. 结论

当前系统已经从“能跑通 RAG/Agent/MCP”推进到“可审计、可解释、可评测”的基础阶段。新能力主要体现在：

1. 每条证据能解释来源、切分、召回路线、query variant 和 rerank 原因。
2. RAG 召回不再只是 top-k 直出，而是 query expansion、多路召回、去重融合、rerank 后输出。
3. PDF/Markdown 本地知识开始具备结构化 chunk 元数据，为 MinerU 和图表证据接入预留接口。
4. MCP 输出更可控，并有本地协议 smoke test。
5. 后续质量提升可以围绕同一套 metrics 和 backlog 逐项验证。
