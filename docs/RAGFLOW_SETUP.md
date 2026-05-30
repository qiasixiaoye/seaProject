# RAGFlow 配置步骤

## 当前状态

- RAGFlow Web: http://127.0.0.1:8088
- RAGFlow API: http://127.0.0.1:9380
- Demo 后端访问 RAGFlow API 使用：`http://host.docker.internal:9380`
- 本地 PDF 已归档到：
  - `data/pdf_reports/cn`
  - `data/pdf_reports/en`

## 人工操作

1. 打开 http://127.0.0.1:8088。
2. 首次进入时按页面提示创建账号或登录。
3. 在 RAGFlow 中配置模型供应商：
   - Chat 模型可配置 DeepSeek，Base URL 使用 `https://api.deepseek.com`。
   - 模型名优先用 `deepseek-chat`；如果你的账号启用了其他 DeepSeek v 系列模型，再换成对应模型名。
   - Embedding 模型需要单独配置。DeepSeek 通常不提供通用 embedding；如果 RAGFlow 没有可用默认 embedding，需要配置一个 embedding 提供商或本地 embedding 服务。
4. 创建知识库，建议先建两个：
   - `ocean_cn_reports`：上传 `data/pdf_reports/cn` 中与海洋生态、海平面、灾害、近岸海域相关的中文报告。
   - `ocean_en_products`：上传 `data/pdf_reports/en` 中 SST、wave、salinity、BGC、sea level 等英文产品文档。
5. 等待 RAGFlow 完成解析、切片、向量化。大 PDF 多时会比较慢，建议先上传 10-20 个核心文档验证。
6. 在 RAGFlow 里创建 API Key。
7. 找到知识库 Dataset ID。通常在知识库设置页、API 页或浏览器 URL 中可看到。
8. 修改项目根目录 `.env`：

```text
RAGFLOW_BASE_URL=http://host.docker.internal:9380
RAGFLOW_API_KEY=<你的 RAGFlow API Key>
RAGFLOW_DATASET_IDS=<dataset_id_1,dataset_id_2>
```

9. 重启 demo 后端：

```powershell
cd C:\Users\lmh\Desktop\海洋rag+多agent
docker compose up -d backend
```

10. 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/rag/status
```

如果 `ragflow.configured=true`，前端 AI 助手里把后端选择为 `ragflow` 或 `auto` 即可。

## 当前 demo 的降级逻辑

- `backend=ragflow`：强制使用 RAGFlow，配置不完整会报错。
- `backend=auto`：优先 RAGFlow，失败后自动回退本地 RAG。
- `backend=local`：只使用本地 RAG。

