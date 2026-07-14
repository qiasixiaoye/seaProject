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
