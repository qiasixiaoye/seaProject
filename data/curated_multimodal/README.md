# 精选多模态验证集

这个目录只维护少量、来源可追溯的“官方图像—科学证据”样本，用于验证 Chinese-CLIP 图片到文字召回。

- `evidence.json`：7 条会被 `ocean_agents_demo.core.load_docs()` 自动加载的证据。
- `images/manifest.json`：3 张官方图像的来源页、资产地址、署名和期望召回证据。
- `images/`：NASA、NOAA、IPCC 官方图像副本，仅用于本项目检索验证；对外使用时保留来源与署名。
- `validation_results.json`：当前索引上的实际 Top-K 验收结果。

验收原则不是要求配对条目始终 Top-1，而是确保对应主题证据进入 Top-K，并在报告中保留能力边界。Chinese-CLIP 分数目前只有相对排序意义，尚未用人工标注集校准绝对相关阈值。

每次修改 `evidence.json` 后重新导出和建库：

```powershell
python scripts\export_multimodal_evidence.py --output data\multimodal_index\evidence-source.jsonl
docker compose --profile indexing run --rm multimodal-index-builder
docker compose restart multimodal-retrieval
```
