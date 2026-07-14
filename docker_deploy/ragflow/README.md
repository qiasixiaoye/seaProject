# ragflow

RAGFlow 主服务和持久化数据目录。

## 启动

```powershell
cd docker_deploy
docker compose --profile ragflow up -d
```

## 访问

- Web：`http://127.0.0.1:8088`
- API：`http://127.0.0.1:9380`

## 数据映射

- `./data/elasticsearch`
- `./data/mysql`
- `./data/minio`
- `./data/redis`
- `./logs`

这些目录由 compose 自动创建。
