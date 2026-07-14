# mcp-server

Ocean GeoAgent MCP stdio 服务。它不是 HTTP 服务，不默认随 `docker compose up -d` 启动。

## 镜像

- 镜像名：`ocean-rag-agent/mcp-server:latest`
- Dockerfile：`docker_deploy/mcp-server/Dockerfile`

## 生成配置片段

```powershell
cd docker_deploy
docker compose --profile mcp run --rm mcp-server python /app/mcp_server.py --config
```

MCP 客户端正式使用时，需要保持 stdin/stdout 连接，通常用 `docker run -i` 或本地 Python 进程启动。
