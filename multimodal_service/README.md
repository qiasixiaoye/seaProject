# Multimodal Retrieval Service

Independent Chinese-CLIP + Faiss service used by the Ocean GeoAgent.

## Data contract

`evidence.jsonl` contains one record per vector. Required fields are
`vector_id`, `doc_id`, `chunk_id`, `title`, `content`, and `retrieval_card`.
The Faiss index and metadata file must be built from the same model/version.

## Build an index

```powershell
python scripts/export_multimodal_evidence.py --parse-pdf --output data/multimodal_index/evidence-source.jsonl
python -m multimodal_service.scripts.build_index `
  --input data/multimodal_index/evidence-source.jsonl `
  --output-dir data/multimodal_index
```

Model weights are downloaded on first use unless `MULTIMODAL_LOCAL_FILES_ONLY=true`.
The default image installs the CPU-only PyTorch wheel. A CUDA image can be added after the
minimal retrieval flow is validated on the target hardware.

## Retrieval behavior

- The index stores normalized 512-dimensional Chinese-CLIP text vectors and uses exact
  inner-product search (cosine similarity after normalization).
- A retrieval card includes the document title, up to eight topics, and the cleaned
  evidence chunk. The tokenizer enforces the model's 512-token limit.
- Image Top-K diagnostics are relative and explicitly uncalibrated. No absolute
  relevance threshold is enabled until a labeled ocean-image validation set exists.
- GeoAgent caps each route at three chunks per source document, then applies
  confidence-aware weighted RRF. Image weight is bounded to 0.9-1.2 from relative
  score separation; text weight is 1.0.
- The current service performs image embedding only. OCR, chart parsing, and vision
  captioning are not enabled and the uploaded image is not persisted.
