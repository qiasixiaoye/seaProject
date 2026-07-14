# redis

RAGFlow 缓存服务，使用 Valkey 镜像。

- 镜像：`valkey/valkey:8`
- 宿主机端口：默认 `16379`
- 数据目录：`../ragflow/data/redis`

该服务只在 `--profile ragflow` 时启动。
