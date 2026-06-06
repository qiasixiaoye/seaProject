# Evaluation Metrics Plan

This document defines the quality signals used to explain and evaluate the
Ocean GeoAgent system. The goal is to make every report auditable before running
large experiments.

## Metric Groups

### Retrieval Quality

These metrics describe whether the RAG layer found the right evidence.

| Metric | Meaning | Current status |
|---|---|---|
| `candidate_count` | Number of retrieved chunks or documents before screening. | Available |
| `kept_count` | Number of evidence items kept after screening. | Available |
| `passed_count` | Number of candidates rejected or passed over. | Available |
| `avg_candidate_score` | Mean raw retrieval score for all candidates. | Available |
| `avg_kept_score` | Mean screening score for kept evidence. | Available |
| `top_score` | Highest raw retrieval score. | Available |
| `Recall@K` | Share of gold relevant chunks present in the top K. | Requires labeled data |
| `Precision@K` | Share of top K chunks that are gold relevant. | Requires labeled data |
| `MRR` | Reciprocal rank of the first relevant chunk. | Requires labeled data |
| `nDCG@K` | Ranking quality with graded relevance. | Requires labeled data |

Recommended initial thresholds:

| Metric | Target |
|---|---|
| `Recall@5` | >= 0.75 |
| `Precision@5` | >= 0.55 |
| `MRR` | >= 0.65 |
| `avg_candidate_score` | Track only, no universal threshold |

### Evidence Quality

These metrics describe whether the answer is visibly grounded in evidence.

| Metric | Meaning | Current status |
|---|---|---|
| `citation_coverage` | Whether kept evidence is cited or referenced in the report. | Available |
| `citation_accuracy` | Whether each citation supports the nearby claim. | Requires labeled or judge pass |
| `evidence_count` | Number of kept evidence items. | Available |
| `backend` | Retrieval source: `local`, `ragflow`, or `auto` fallback result. | Available |

Recommended target:

| Metric | Target |
|---|---|
| `citation_coverage` | >= 0.8 for evidence-heavy reports |
| `citation_accuracy` | >= 0.85 after judge support is added |

### Answer Quality

These metrics describe the final report quality.

| Metric | Meaning | Current status |
|---|---|---|
| `faithfulness` | Whether claims are supported by retrieved evidence and data. | Requires judge pass |
| `completeness` | Whether the answer covers expected question points. | Requires labeled data |
| `hallucination_rate` | Share of unsupported claims. | Requires judge pass |
| `data_grounding` | Whether NetCDF context is available and reflected in the report. | Available |
| `critic_passed` | Whether the CriticAgent accepted the report. | Available |
| `critic_issue_count` | Number of issues raised by CriticAgent. | Available |

Recommended target:

| Metric | Target |
|---|---|
| `faithfulness` | >= 0.85 |
| `completeness` | >= 0.75 |
| `hallucination_rate` | <= 0.10 |
| `critic_passed` | 1.0 for final deliverables |

### Tool Quality

These metrics describe whether internal tools are reliable and useful.

| Metric | Meaning | Current status |
|---|---|---|
| `tool_call_count` | Number of tool calls recorded in trace. | Available when trace records calls |
| `tool_success_rate` | Successful tool calls divided by all tool calls. | Available when trace records calls |
| `used_in_report` | Whether tool output appears in final report. | Future work |
| `result_size` | Size of tool output, for diagnosing empty or excessive responses. | Future work |

Recommended target:

| Metric | Target |
|---|---|
| `tool_success_rate` | >= 0.95 |
| `tool_call_count` | Track by domain and task type |

### System Quality

These metrics describe runtime behavior and cost.

| Metric | Meaning | Current status |
|---|---|---|
| `elapsed_ms` | End-to-end runtime. | Available |
| `prompt_tokens` | Prompt token usage. | Available when LLM reports usage |
| `completion_tokens` | Completion token usage. | Available when LLM reports usage |
| `total_tokens` | Total token usage. | Available when LLM reports usage |
| `estimated_cost_usd` | Estimated request cost. | Available when configured |

Recommended target:

| Metric | Target |
|---|---|
| `elapsed_ms` | Track by mode; no single threshold |
| `total_tokens` | Track regression across releases |
| `estimated_cost_usd` | Track regression across releases |

## Evaluation Output Contract

Every report-producing API should return:

```json
{
  "report": "...",
  "trace": [],
  "evaluation": {
    "score": 0.82,
    "grade": "good",
    "metrics": {
      "retrieval": {},
      "evidence": {},
      "answer": {},
      "tools": {},
      "trace": {},
      "system": {}
    }
  },
  "kept_documents": [],
  "passed_documents": [],
  "token_usage": {},
  "elapsed_ms": 12345
}
```

Metrics that require labeled data or LLM judging should be returned as `null`
until that evaluator is implemented. This keeps the API stable while making
missing evaluation capacity explicit.

## Dataset Annotation Schema

Future labeled rows should use this structure:

```json
{
  "id": "marine_heatwave_001",
  "question": "How do marine heatwaves affect coral reefs and fisheries?",
  "domain": "marine",
  "expected_topics": ["marine heatwave", "coral bleaching", "fisheries"],
  "relevant_documents": ["doc_2023_ocean_report"],
  "relevant_chunks": ["chunk_abc", "chunk_def"],
  "must_cite": ["coral bleaching", "fishery fluctuation"],
  "reference_answer_points": [
    "Marine heatwaves increase coral bleaching risk.",
    "Fish distribution and catch stability can change.",
    "SST monitoring and ecological restoration are recommended."
  ]
}
```

## Implementation Priority

1. Keep deterministic metrics available in every API response.
2. Add trace fields for retrieval, tools, data context, and critic decisions.
3. Add labeled evaluation data for `Recall@K`, `Precision@K`, `MRR`, and `nDCG`.
4. Add optional LLM-as-judge metrics for faithfulness, citation accuracy, and hallucination rate.
