# Ocean RAG Multi-Agent Docker Deploy

这个目录是一套独立 Docker 交付包，保留项目源码在宿主机，容器只安装运行依赖并通过 volume 映射代码和数据。

> **⚠️ 迁移到另一台电脑前必读**：本 compose 通过 `../backend`、`../data`、`../frontend` 等相对路径挂载父目录的源码和数据，所以**必须连同整个项目根目录 `海洋rag+多agent/` 一起拷贝**，不能只拷 `docker_deploy/`。完整迁移步骤见文末「跨机器迁移」一节。

## 目录说明

```text
docker_deploy/
  docker-compose.yml      # 统一启动入口
  .env.example            # 环境变量模板
  README.md               # 使用手册
  backend/                # Flask/Gunicorn 后端镜像
  geo-api/                # GeoAgent HTTP/SSE API 镜像
  nginx/                  # 前端静态站点和反向代理配置
  geoserver/              # GeoServer 服务说明
  mcp-server/             # stdio MCP 服务镜像
  ragflow/                # RAGFlow 数据、日志和说明
  elasticsearch/          # RAGFlow ES 服务说明
  mysql/                  # RAGFlow MySQL 服务说明
  minio/                  # RAGFlow MinIO 服务说明
  redis/                  # RAGFlow Redis 服务说明
  scripts/                # 构建、导出、导入镜像脚本
  image-tars/             # docker save 导出的镜像包输出目录
```

## 首次配置

```powershell
cd C:\Users\lmh\Desktop\海洋rag+多agent\docker_deploy
Copy-Item .env.example .env
```

按需编辑 `.env`：

- `DEEPSEEK_API_KEY`：需要真实 LLM 报告时填写。
- `RAGFLOW_API_KEY`、`RAGFLOW_DATASET_IDS`：需要连接 RAGFlow 知识库时填写。
- `APP_PORT`：默认 `8000`，浏览器访问入口。

## 启动主系统

主系统包含 `backend`、`geo-api`、`nginx`、`geoserver`：

```powershell
cd C:\Users\lmh\Desktop\海洋rag+多agent\docker_deploy
$env:DOCKER_BUILDKIT='0'
$env:COMPOSE_DOCKER_CLI_BUILD='1'
docker compose up -d --build
```

访问：

- Web：<http://127.0.0.1:8000>
- 后端健康检查：<http://127.0.0.1:8000/api/health>
- GeoAgent API：<http://127.0.0.1:5001/api/health>
- GeoServer：<http://127.0.0.1:8000/geoserver/web/>

## 启动完整 RAGFlow 栈

RAGFlow 依赖 Elasticsearch、MySQL、MinIO、Redis，资源占用较高，所以放在 `ragflow` profile：

```powershell
cd C:\Users\lmh\Desktop\海洋rag+多agent\docker_deploy
$env:DOCKER_BUILDKIT='0'
$env:COMPOSE_DOCKER_CLI_BUILD='1'
docker compose --profile ragflow up -d --build
```

当前项目路径包含中文，Docker Desktop 的 BuildKit build session 可能报 `x-docker-expose-session-sharedkey` 非 ASCII 错误。上面两行环境变量用于关闭 BuildKit 兼容构建；如果项目以后移动到纯英文路径，可以不设置。

访问：

- RAGFlow Web：<http://127.0.0.1:8088>
- RAGFlow API：<http://127.0.0.1:9380>
- MinIO Console：<http://127.0.0.1:19001>

如果 RAGFlow 不在这个 compose 里启动，而是在宿主机或别的 compose 里启动，把 `.env` 里的：

```text
RAGFLOW_BASE_URL=http://host.docker.internal:9380
```

## MCP 服务

`mcp-server` 是 stdio 服务，不适合长期 `up -d`。可以生成客户端配置片段：

```powershell
docker compose --profile mcp run --rm mcp-server python /app/mcp_server.py --config
```

真正接入 MCP 客户端时，建议客户端用 `docker run -i` 或本地 Python 进程拉起它。

## 代码和数据映射

compose 已映射：

- `../backend` -> `/app/backend`
- `../geo_agent` -> `/app/geo_agent`
- `../ocean_agents_demo` -> `/app/ocean_agents_demo`
- `../frontend` -> `/usr/share/nginx/html`
- `../data` -> `/app/data`
- `../instance` -> `/app/instance`
- `../geoserver_data` -> `/opt/geoserver_data`
- `./ragflow/data/*` -> RAGFlow 依赖服务数据目录
- `./ragflow/logs` -> `/ragflow/logs`

修改宿主机代码后，Python 服务通常需要重启容器：

```powershell
docker compose restart backend geo-api
```

## 构建和导出镜像包

只构建本项目自建镜像（`backend`、`geo-api`、`mcp-server`）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-images.ps1
```

构建并导出主系统镜像到 `image-tars`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\export-images.ps1
```

连同 RAGFlow 依赖镜像一起导出：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\export-images.ps1 -IncludeRagflow
```

在另一台机器导入：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\load-images.ps1
```

## 常用运维命令

```powershell
docker compose ps
docker compose logs -f backend
docker compose logs -f geo-api
docker compose restart backend geo-api nginx
docker compose down
docker compose --profile ragflow down
```

清理 RAGFlow 数据会删除知识库、索引、对象存储和数据库内容，只有确认不要数据时再手动删除：

```powershell
Remove-Item -Recurse -Force .\ragflow\data
```

## 跨机器迁移（完整离线包）

本目录已经预先打包好「核心应用 + 完整 RAGFlow 栈」的镜像和 RAGFlow 知识库数据，目标机器即使不联网也能跑起来。

### 迁移包内容

```text
image-tars/ocean-rag-agent-images-full-*.tar   # 全部镜像（约 12GB，含 ragflow:v0.25.3 8.4GB）
ragflow/volumes/*.tar.gz                        # RAGFlow 知识库快照（ES 索引 / MySQL 元数据 / MinIO 文档 / Redis）
scripts/load-images.ps1                         # 目标机导入镜像
scripts/restore-ragflow-data.ps1               # 目标机恢复 RAGFlow 数据到绑定目录
scripts/export-ragflow-data.ps1                # 源机重新导出 RAGFlow 数据（可选）
```

### 第 1 步：拷贝整个项目根目录

把 `海洋rag+多agent/` 整个文件夹拷到目标机器（U 盘或网络）。**因为 compose 挂载 `../` 父目录，单拷 `docker_deploy/` 跑不起来。**

> 体积优化：项目根目录里的旧文件 `docker_deploy.zip`（582MB，旧的、仅核心应用的打包）是历史产物，迁移时可以删掉不带。`image-tars/` 现在只保留最新的 `*-full-*.tar`。

### 第 2 步：目标机安装并启动 Docker Desktop

确认 `docker version` 能正常返回。

### 第 3 步：导入镜像

```powershell
cd <目标路径>\海洋rag+多agent\docker_deploy
powershell -ExecutionPolicy Bypass -File scripts\load-images.ps1
```

脚本会自动选 `image-tars/` 里最新的 `*.tar` 导入（即 `*-full-*.tar`）。导入后 `docker images` 应能看到 backend / geo-api / nginx / geoserver / elasticsearch / mysql / minio / valkey / ragflow 等。

### 第 4 步：配置 .env

```powershell
Copy-Item .env.example .env
```

按需填 `DEEPSEEK_API_KEY`。`.env.example` 里的 RAGFlow 密码、库名、端口与导出数据完全一致，**不要改动**这些（改了恢复的数据会对不上）。

### 第 5 步：恢复 RAGFlow 知识库数据

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore-ragflow-data.ps1
```

会把 `ragflow/volumes/*.tar.gz` 解压进 `ragflow/data/{elasticsearch,mysql,minio,redis}` 与 `ragflow/logs`（用临时容器解压以保留权限）。

### 第 6 步：启动

```powershell
# 主系统（镜像已导入，不需要 --build）
docker compose up -d

# 完整 RAGFlow 栈
docker compose --profile ragflow up -d
```

访问 <http://127.0.0.1:8000>、RAGFlow <http://127.0.0.1:8088>。

### 目标机能联网时的省体积替代方案

如果目标机能联网，可以**不带 12GB 镜像 tar**：删掉 `image-tars/*.tar`，第 3 步换成 `docker compose --profile ragflow build`（构建 backend/geo-api）+ `docker compose --profile ragflow pull`（拉取第三方镜像）。RAGFlow 数据仍按第 5 步恢复。中文路径下构建若报 BuildKit gRPC header 错误，先设 `$env:DOCKER_BUILDKIT='0'`。
