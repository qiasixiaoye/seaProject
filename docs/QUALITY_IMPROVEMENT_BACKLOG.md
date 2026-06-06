# Quality Improvement Backlog

This backlog lists the main quality gaps that should be reviewed before the
system is presented as a reliable research-grade RAG and agent platform. It is
not an experiment plan yet; it is a concrete improvement checklist with the
expected methods for each issue.

## Priority Legend

| Priority | Meaning |
|---|---|
| P0 | Blocks trustworthy use or makes evaluation misleading. |
| P1 | Important for quality and should be implemented before broad demos. |
| P2 | Useful hardening or optimization after the core quality path is stable. |

## RAG Ingestion, Chunking, Retrieval, and Reranking

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| RAG-01 | Paper/PDF parsing | Current ingestion treats documents mostly as text. Ocean papers often contain figures, tables, captions, formulas, and page-local evidence that can be lost. | Add a document parsing stage using a layout-aware parser such as MinerU, plus fallback parsers for text-only PDFs. Extract title hierarchy, paragraphs, tables, figure captions, page numbers, and bounding boxes when available. | Each chunk keeps `doc_id`, `page`, `section`, `bbox`, `content_type`, and `source_path`; figures/tables have captions and page references. | P0 |
| RAG-02 | Multimodal evidence | Figures and charts are not first-class evidence, so answers may miss conclusions only shown in plots or diagrams. | Treat figure/table captions and OCR text as retrievable text; optionally store figure image paths as metadata for later visual verification. For chart-heavy papers, add a "figure evidence" content type. | Retrieval result can identify whether evidence came from paragraph/table/figure/caption and expose it in `kept_documents`. | P1 |
| RAG-03 | Chunk boundaries | Fixed-size chunks can split method/result/caption context or mix unrelated sections. | Use structure-first chunking: section heading -> paragraph groups -> fallback token window. Keep captions attached to nearby figure/table references. Use overlap only inside long same-section text. | Chunk text is semantically complete and has `section_path`; no chunk crosses unrelated top-level sections unless explicitly marked as fallback. | P0 |
| RAG-04 | Chunk size | There is no justified default chunk size for Chinese reports, English papers, tables, or data manuals. | Define default profiles: Chinese reports 600-900 chars with 100-150 char overlap; English papers 350-600 tokens with 80-120 token overlap; tables split by logical row groups. | `docs/EVALUATION_METRICS.md` or RAG setup docs records chunk profiles and rationale. | P1 |
| RAG-05 | Metadata schema | RAGFlow chunks are converted to docs, but metadata is not yet rich enough for citation accuracy and debugging. | Normalize metadata fields across local/RAGFlow: `doc_id`, `chunk_id`, `dataset_id`, `document_name`, `page`, `section`, `year`, `content_type`, `similarity`, `vector_similarity`, `term_similarity`. | Frontend and API can display chunk source, page, section, and score for every evidence item. | P0 |
| RAG-06 | Hybrid retrieval | Current retrieval can use RAGFlow/local fallback, but quality may suffer for exact terms, acronyms, variables, and bilingual questions. | Add multi-route retrieval: dense vector + BM25/term + metadata filters + bilingual query expansion. Merge with Reciprocal Rank Fusion (RRF). | Trace shows each route, result count, and fused rank; `backend_used` distinguishes `ragflow_vector`, `ragflow_keyword`, `local_keyword`, etc. | P1 |
| RAG-07 | Query expansion | Agent-generated queries may miss scientific synonyms, variable aliases, or Chinese/English equivalents. | Add deterministic domain query expansion for terms like SST/sea surface temperature, chlorophyll-a/Chl-a, SWH/significant wave height. Keep LLM expansion optional. | Retrieval trace includes original query and expanded queries; expansion can be disabled for debugging. | P1 |
| RAG-08 | TopK only, no rerank | Returning top K directly from vector similarity is not enough; near-duplicate or weakly relevant chunks can crowd out better evidence. | Add reranking after recall: cross-encoder reranker or BGE-style reranker when available; fallback to heuristic reranker using title/topic overlap, page type, and content type. | `kept_documents` include `rank_before`, `rank_after`, `rerank_score`, and `rerank_reason`. | P0 |
| RAG-09 | Diversity control | Top K may contain several chunks from the same document/section. | Add MMR or per-document caps before final evidence selection. Keep at least one high-score chunk per distinct source when scores are close. | Evidence list contains diverse sources unless one source is clearly dominant; trace shows diversity filtering. | P1 |
| RAG-10 | Evidence granularity | Report citations currently cite documents/chunks but not always exact claim-supporting spans. | Store short evidence spans with page and section. During report generation, require claims to reference specific spans. | Report can show claim -> evidence span -> page/section mapping. | P1 |
| RAG-11 | RAGFlow settings visibility | Chunking, parser, similarity threshold, and rerank settings inside RAGFlow are not surfaced to the system. | Add `/api/rag/status` fields for RAGFlow dataset parser config when API supports it; otherwise maintain local config notes per dataset. | RAG status page shows dataset IDs, document count, chunk count, parser/chunk settings, and retrieval thresholds. | P1 |
| RAG-12 | Local/RAGFlow parity | Local fallback and RAGFlow return different score semantics and metadata shapes. | Normalize all retrieval outputs into one `EvidenceChunk` contract before screening. | Screening and Evaluator do not need backend-specific branches. | P0 |

## RAG Evaluation and Observability

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| EVAL-01 | Retrieval metrics | Existing metrics are coarse keyword/topic checks, not true gold relevance metrics. | Add labeled `relevant_documents` and `relevant_chunks` in eval rows. Compute `Recall@K`, `Precision@K`, `MRR`, and `nDCG@K`. | `eval/report.json` contains retrieval metrics for local, RAGFlow, and auto backends. | P0 |
| EVAL-02 | Citation accuracy | Citation coverage does not prove that the citation supports the claim. | Add claim extraction and citation-support judge. Start with LLM-as-judge, later add manual spot checks. | `citation_accuracy` and unsupported claim list are available in evaluation output. | P1 |
| EVAL-03 | Faithfulness | Reports may include plausible but unsupported statements. | Add evidence-groundedness judge that compares each key claim against kept evidence and NetCDF data context. | `faithfulness` and `hallucination_rate` are populated when judge mode is enabled. | P1 |
| EVAL-04 | Chunking ablation | No quantitative basis for chunk size/overlap/parser decisions. | Once metrics exist, run parser/chunk profiles against the same labeled set. Compare retrieval and answer metrics. | A table identifies the default chunk profile and why it was selected. | P2 |
| EVAL-05 | Trace completeness | Trace now has a normalized schema, but many nodes still have `elapsed_ms=null`. | Add per-node timing wrapper in graph execution or node helper. | Every node trace has non-null `elapsed_ms`; frontend displays node timing. | P1 |
| EVAL-06 | RAGFlow diagnostics | It is hard to explain why a query failed or why a poor chunk was selected. | Record per-route query, raw score, rerank score, filters, dropped duplicates, and final keep/pass decision. | Debug trace can reconstruct retrieval -> rerank -> screen decisions. | P0 |
| EVAL-07 | Regression tracking | Quality can regress silently after prompt/tool changes. | Store evaluation snapshots by commit: retrieval metrics, answer metrics, token/cost/latency. | A report can compare current commit against previous baseline. | P2 |

## Agent and Tool-Calling Quality

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| AGENT-01 | Tool call trace | Tool calls are not consistently represented in trace. | Define a `tool_calls[]` schema with name, args summary, success, latency, result size, and error. | Evaluator can compute `tool_call_count` and `tool_success_rate` reliably. | P0 |
| AGENT-02 | Tool usefulness | A successful tool call may still be irrelevant to the report. | Add `used_in_report` detection by matching tool outputs/data variable names/evidence IDs in final report. | Tool metrics include `used_in_report_rate`. | P1 |
| AGENT-03 | Tool argument validation | Tool calls can request unavailable variables, invalid bbox, excessive max points, or wrong domain. | Add schema validation and clamping before dispatch; return structured tool errors. | Invalid tool calls fail safely and appear in trace with clear reasons. | P0 |
| AGENT-04 | Planner quality | Planner may select too many variables or irrelevant tools. | Add deterministic domain profiles and compare planned variables against available datasets before ContextNode. | Planner trace shows selected variables, unavailable variables, and fallback choices. | P1 |
| AGENT-05 | Critic strictness | Critic feedback can be useful but not always reflected in final evaluation. | Convert Critic issues into structured categories: unsupported claim, missing data caveat, weak citation, vague recommendation. | Evaluation includes issue counts by category. | P1 |
| AGENT-06 | Report claim discipline | Reports can use thresholds without source/region applicability. | Add report-generation rules: every threshold must carry source or applicability caveat. | Critic flags threshold claims without source. | P1 |

## MCP Server Hardening

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| MCP-01 | Protocol compliance | MCP server is custom stdio JSON-RPC, so subtle protocol deviations are possible. | Add golden tests for `initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, and `ping` using Content-Length frames. | A local test script verifies request/response framing and required fields. | P0 |
| MCP-02 | Tool schemas | Tool input schemas may be too permissive or inconsistent with backend APIs. | Align MCP schemas with backend request contracts; define required fields and bounds for bbox, max_points, time_index, and depth_index. | Invalid MCP tool calls return structured errors instead of stack traces. | P0 |
| MCP-03 | Result size | `query_ocean_data` can return large grids, which may be unsuitable for MCP clients. | Add compact mode by default: stats, shape, sample grid, and source metadata. Allow full grid only with explicit flag and max size. | MCP results are bounded and client-safe. | P1 |
| MCP-04 | Security and paths | MCP resources expose local paths and data metadata. | Redact absolute paths by default; expose source IDs and relative paths only unless debug mode is enabled. | MCP output does not leak unnecessary local filesystem details. | P1 |
| MCP-05 | Error semantics | Tool errors need stable `isError` and machine-readable error codes. | Standardize error payloads: `code`, `message`, `details`, `retryable`. | MCP client can distinguish validation, data-missing, LLM, and internal errors. | P1 |
| MCP-06 | Regression tests | MCP is not covered by automated tests. | Add `scripts/test_mcp_stdio.py` or equivalent. | CI/local smoke test covers all MCP methods without external LLM calls. | P1 |

## A2A Protocol and External Agent Interop

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| A2A-01 | In-memory tasks | A2A tasks disappear on service restart. | Persist tasks to SQLite or Redis; keep in-memory mode for demo only. | Task state survives process restart in persistent mode. | P2 |
| A2A-02 | Cancellation | Current cancellation marks state, but long-running execution may continue. | Add cooperative cancellation checks between graph nodes and before LLM calls. | Cancelled tasks stop promptly and emit final cancellation event. | P1 |
| A2A-03 | SSE event schema | Events work but need a stricter schema for external clients. | Define event types: `status_update`, `artifact`, `trace`, `metric`, `error`, `done`. | A2A event stream is documented and stable. | P1 |
| A2A-04 | Task result contract | A2A output should match report API output. | Return report, trace, evaluation, evidence, token usage, elapsed time, and artifacts consistently. | A2A `GET task` output mirrors `/api/geo/report` essentials. | P1 |
| A2A-05 | Load and timeout | Long LLM tasks can exceed client patience or pile up. | Add task timeout, max concurrency, queue length, and backpressure response. | Server rejects or queues excessive tasks predictably. | P2 |

## NetCDF and Visualization Data Quality

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| DATA-01 | Variable aliasing | Queries may ask for `sst`, `sea_surface_temperature`, or Chinese names inconsistently. | Maintain variable alias registry and expose it through dataset metadata. | ContextNode resolves aliases and records selected dataset/variable. | P1 |
| DATA-02 | Time/depth semantics | `time_index` and `depth_index` work, but selected values may be raw numeric units. | Decode time units when possible and show ISO-like labels; preserve raw values. | API returns `selected_time_label` and `selected_depth_label`. | P2 |
| DATA-03 | Vector fields | Vector support assumes u/v naming and alignment; mismatches may silently fail. | Validate dimensions, units, shape, and coordinate alignment for u/v pairs. | Vector endpoint returns structured validation errors and pair metadata. | P1 |
| DATA-04 | Visualization guidance | VisualizationAgent returns recommendations but no quality check that the frontend rendered them. | Add frontend render-mode trace and server-side capability flags. | Report can say which render modes are supported and which were actually used. | P2 |

## Frontend Quality and Explainability

| ID | Area | Current issue / risk | Expected method | Output / acceptance criterion | Priority |
|---|---|---|---|---|---|
| UI-01 | Evidence visibility | Users need to inspect top chunks, scores, pages, and rerank reasons. | Add an evidence drawer showing kept/pass chunks, source, page, score, rerank score, and content type. | User can audit why the answer used each evidence item. | P1 |
| UI-02 | Quality panel depth | Current quality panel shows summary cards only. | Add expandable details for retrieval, evidence, answer, tools, and system metrics. | Users can inspect all `evaluation.metrics` fields without opening logs. | P2 |
| UI-03 | Trace readability | Trace list shows node/mode/status but not output summaries. | Show compact `output_summary` per trace node and errors when present. | Pipeline debugging is possible from the UI. | P2 |
| UI-04 | RAGFlow settings display | The UI does not show RAGFlow dataset/chunk configuration. | Add knowledge panel fields for dataset, document count, chunk count, parser/chunk settings if available. | RAGFlow quality assumptions are visible in the UI. | P2 |

## Recommended Implementation Order

| Step | Scope | Why first |
|---|---|---|
| 1 | RAG-05, RAG-12, AGENT-01 | Normalize evidence and tool contracts before adding more metrics. |
| 2 | RAG-03, RAG-08, RAG-06 | Improve the actual recall path: structure-aware chunking, hybrid retrieval, reranking. |
| 3 | EVAL-01, EVAL-06 | Make retrieval improvements measurable and debuggable. |
| 4 | MCP-01, MCP-02, MCP-03 | Harden external tool access before broader integration. |
| 5 | A2A-03, A2A-04 | Stabilize external agent interoperability. |
| 6 | UI-01, UI-02, UI-03 | Make the quality improvements visible to users. |

## Immediate Next Tasks

1. Define `EvidenceChunk` and `ToolCallTrace` schemas in code and docs.
2. Extend RAGFlow/local retrieval conversion to populate the normalized evidence metadata.
3. Add rerank fields to `kept_documents`: `rank_before`, `rank_after`, `rerank_score`, `rerank_reason`.
4. Add MCP stdio smoke tests for all supported methods.
5. Add frontend evidence drawer after evidence metadata is normalized.

## Progress Log

| Date | Items | Status |
|---|---|---|
| 2026-06-07 | `EvidenceChunk` contract added to retrieval outputs for local and RAGFlow documents; `ToolCallTrace` contract added to RetrievalNode tool-loop calls. | In progress |
| 2026-06-07 | Deterministic rerank fields added: `rank_before`, `rank_after`, `rerank_score`, and `rerank_reason`. External cross-encoder/BGE reranker remains future work. | In progress |
| 2026-06-07 | MCP stdio smoke test added for `initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, and `ping`. | In progress |
| 2026-06-07 | MCP `query_ocean_data` now validates bbox/index/max_points and returns compact stats/metadata by default; full grids require `include_grid=true`. | In progress |
