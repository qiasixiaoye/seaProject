# RAGFlow 与 Docker 启动配置

本文档记录当前项目接入 RAGFlow、Docker Compose 启动、健康检查和常见故障处理方式。它面向本地演示环境，默认工作目录为：

```powershell
C:\Users\lmh\Desktop\海洋rag+多agent
```

## 当前服务关系

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端 | `http://127.0.0.1:5173` | Vite 开发服务 |
| GeoAgent Demo API | `http://127.0.0.1:8000` | FastAPI 后端 |
| RAGFlow Web | `http://127.0.0.1:8088` | RAGFlow 控制台 |
| RAGFlow API | `http://127.0.0.1:9380` | 后端调用的 RAGFlow API |
| Demo 容器访问 RAGFlow | `http://host.docker.internal:9380` | 容器内访问宿主机端口 |

后端支持三种检索模式：

| 参数 | 行为 |
| --- | --- |
| `backend=ragflow` | 强制调用 RAGFlow。未配置或不可达时直接返回错误。 |
| `backend=auto` | 优先调用 RAGFlow，失败后回退本地 RAG。推荐演示使用。 |
| `backend=local` | 只使用本地 PDF/文本检索，不依赖 RAGFlow。 |

## 启动顺序

1. 启动 Docker Desktop，确认 Docker Engine 正常。
2. 启动项目服务：

```powershell
docker compose up -d
```

3. 查看容器状态：

```powershell
docker compose ps
```

4. 检查后端健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/rag/status
```

5. 打开前端：

```text
http://127.0.0.1:5173
```

## RAGFlow 人工配置

1. 打开 `http://127.0.0.1:8088`。
2. 首次进入时创建账号或登录。
3. 在 RAGFlow 中配置模型供应商：
   - Chat 模型可配置 DeepSeek，Base URL 使用 `https://api.deepseek.com`。
   - 模型名优先使用 `deepseek-chat`。
   - Embedding 模型需要单独配置。DeepSeek 通常不提供通用 embedding，如果 RAGFlow 没有可用默认 embedding，需要配置其他 embedding 服务或本地 embedding 模型。
4. 创建知识库，建议先建立两个：
   - `ocean_cn_reports`：上传 `data/pdf_reports/cn` 下的中文海洋报告。
   - `ocean_en_products`：上传 `data/pdf_reports/en` 下的英文产品文档。
5. 等待解析、切片、向量化完成。大 PDF 处理较慢，建议先上传 10 到 20 个核心文档验证链路。
6. 在 RAGFlow 创建 API Key。
7. 找到知识库 Dataset ID。
8. 修改项目根目录 `.env`：

```text
RAGFLOW_BASE_URL=http://host.docker.internal:9380
RAGFLOW_API_KEY=<your_ragflow_api_key>
RAGFLOW_DATASET_IDS=<dataset_id_1,dataset_id_2>
```

9. 重启后端：

```powershell
docker compose up -d backend
```

10. 验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/rag/status
```

如果 `ragflow.configured=true`，前端 AI 助手选择 `ragflow` 或 `auto` 即可进入 RAGFlow 检索链路。

## 后端构建注意事项

当前项目路径包含中文字符。Docker Desktop 在某些 Windows 环境下执行 BuildKit session 时，可能出现类似错误：

```text
header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters
```

这不是 Python 代码问题，而是 Docker 构建上下文路径和 gRPC header 的兼容性问题。处理方式按优先级如下：

1. 推荐：把项目复制或克隆到纯英文路径，例如 `C:\work\seaProject`，再执行 `docker compose build backend`。
2. 临时调试：如果只是改了 Python 文件，可以把文件复制进运行中容器后重启：

```powershell
docker cp ocean_agents_demo\nc_data.py ocean-demo-backend:/app/ocean_agents_demo/nc_data.py
docker compose restart backend
```

3. 如果需要完整镜像验证，仍应回到纯英文路径重建，避免临时容器补丁和镜像内容不一致。

## 快速自检命令

```powershell
docker compose ps
docker compose logs --tail=80 backend
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/rag/status
```

海洋数据查询自检：

```powershell
$body = @{
  dataset = "sst_anomaly_oisst_taiwan_small"
  variable = "anom"
  bbox = @{ west = 117; east = 127; south = 20; north = 26 }
  step = 5
  max_points = 9000
} | ConvertTo-Json -Depth 8

Invoke-RestMethod http://127.0.0.1:8000/api/ocean/query `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

返回结果中需要重点检查：

| 字段 | 期望 |
| --- | --- |
| `count` | 大于 0 |
| `shape` | 与采样网格一致 |
| `land_mask_applied` | 框选包含陆地时应大于 0 |
| `min/max/mean` | 与图例范围一致 |
| `units` | 前端图例能正常展示 |

## 常见问题

### RAGFlow 没启动

现象：

- `/api/rag/status` 中 `ragflow.available=false`
- 前端 AI 助手切到 `ragflow` 后报错
- `backend=auto` 可以回答，但引用来自本地 RAG

处理：

```powershell
docker compose ps
docker compose logs --tail=100 ragflow
```

如果项目的 compose 文件没有托管 RAGFlow，需要先按 RAGFlow 官方部署方式启动，再确认 `9380` 端口可被访问。

### RAGFlow 已启动但后端仍不可用

检查 `.env`：

```text
RAGFLOW_BASE_URL=http://host.docker.internal:9380
RAGFLOW_API_KEY=<非空>
RAGFLOW_DATASET_IDS=<至少一个 Dataset ID>
```

然后重启后端：

```powershell
docker compose up -d backend
```

### 前端能打开但地图没有数据

优先检查：

1. 后端 `/api/health` 是否正常。
2. `data/nc_uploads` 是否存在对应 `.nc` 文件。
3. `/api/ocean/datasets` 是否列出当前数据集。
4. `/api/ocean/query` 是否返回 `count > 0`。

### 陆地仍然被渲染

当前后端已经使用 ETOPO 派生海陆掩膜过滤网格点。若仍看到陆地上色，通常是以下原因：

1. 当前 `.nc` 网格过粗，粗格点中心落在海上但像素覆盖到陆地。
2. 前端使用 Canvas 插值后把海上格点颜色扩展到了陆地区域。
3. 容器内后端代码没有同步最新版本。

验证方式：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/ocean/query -Method POST -ContentType "application/json" -Body $body
```

重点看 `land_mask_applied`。如果该值为 0，但框选明显覆盖陆地，需要检查 ETOPO 文件和容器代码版本。

## 后续建议

短期保持 `backend=auto`，这样 RAGFlow 未启动时演示不会中断。等知识库、embedding 和 API Key 稳定后，再在正式演示中切到 `backend=ragflow`，用错误显式暴露配置问题。
