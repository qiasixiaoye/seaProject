# RAG Chunking Strategy

This document defines the current local chunking policy for papers, reports,
and Markdown knowledge files. It is the baseline before adding MinerU or another
layout-aware parser.

## Goals

The chunker should preserve evidence boundaries that matter for scientific RAG:

| Requirement | Current method |
|---|---|
| Keep page and source auditability | Every PDF-derived chunk stores `doc_id`, `chunk_id`, `page`, `pages`, `source_path`, and `document_name`. |
| Avoid crossing unrelated sections | Markdown is split by heading hierarchy; PDF text uses common scientific headings as boundaries. |
| Keep chunks semantically complete | Paragraph groups are kept together until the profile limit is reached. |
| Avoid losing long sections | Very long paragraphs are split with overlap inside the same section only. |
| Mark non-paragraph evidence | Table-like blocks and figure/table captions are marked through `content_type`. |

## Default Profiles

| Profile | Applies to | Unit | Max size | Overlap | Rationale |
|---|---|---:|---:|---:|---|
| `chinese_report` | Chinese-heavy reports and notes | chars | 800 | 120 | Chinese tokenization is less stable locally, so character windows are predictable. 600-900 chars keeps enough local context without overloading retrieval. |
| `english_paper` | English PDF papers | tokens | 520 | 100 | 350-600 tokens usually keeps one methods/results paragraph group plus nearby context. |
| `markdown_note` | Local Markdown knowledge notes | chars | 900 | 120 | Markdown files are often short summaries; slightly larger chunks reduce fragmentation. |
| `metadata_only` | PDFs when full parsing is disabled | chars | 900 | 0 | Keeps filename/title metadata searchable without pretending page-level evidence exists. |

The profile can be forced with `OCEAN_CHUNK_PROFILE=chinese_report|english_paper|markdown_note|metadata_only`.

## PDF Parsing Modes

| Mode | Trigger | Behavior |
|---|---|---|
| Metadata-only | default | PDF files under `data/pdf_reports/` become one `local_pdf_metadata` document. This is safe for demos but weak for citation accuracy. |
| Text chunks | `OCEAN_PARSE_PDF_ON_LOAD=true` | The loader extracts up to `OCEAN_PDF_PAGE_LIMIT` pages, defaults to 24, and emits `local_pdf_chunk` chunks with page metadata. |
| Layout-aware future mode | MinerU or equivalent parser | Should add bounding boxes, figure/table images, OCR text, captions, formulas, and section hierarchy from layout. |

## MinerU Integration Point

MinerU should be added as a parser stage before chunking:

1. Parse PDF into structured blocks: section heading, paragraph, table, figure,
   caption, formula, OCR text, page, and bounding box.
2. Normalize each block into the existing chunk metadata contract.
3. Run the same profile-based grouping only inside compatible block groups.
4. Preserve image paths for figure/table evidence in `metadata.figure_path` or
   `metadata.table_image_path`.

The current functions to replace or extend are:

| Function | Role |
|---|---|
| `load_structured_pdf_docs()` | Selects metadata-only or parsed PDF mode. |
| `structure_pdf_chunks()` | Groups page text into evidence chunks. |
| `_chunk_metadata()` | Normalizes chunk metadata into the EvidenceChunk contract. |

## Evaluation Hooks

Chunking quality should be measured with the retrieval metrics already defined
in `docs/EVALUATION_METRICS.md`:

| Metric | Why it matters |
|---|---|
| `Recall@K` | Whether the correct paper/page/chunk is retrievable. |
| `Precision@K` | Whether top chunks are not diluted by broad or mixed sections. |
| `MRR` | Whether the first relevant chunk appears early enough for top-k evidence. |
| `nDCG@K` | Whether higher-quality evidence is ranked above weak matches. |
| Citation accuracy | Whether page/section chunks actually support answer claims. |

For ablation, compare at least:

| Variant | Expected question |
|---|---|
| Metadata-only PDF | How much quality is lost without full text? |
| Current text chunker | Is page-level text enough for common paper questions? |
| MinerU layout chunks | Do figures/tables/captions improve recall and citation accuracy? |
| Different chunk sizes | Which profile maximizes Recall@K without lowering Precision@K too much? |
