# elasticsearch

RAGFlow 文档索引服务。

- 镜像：`elasticsearch:${STACK_VERSION:-8.11.3}`
- 宿主机端口：默认 `11200`
- 数据目录：`../ragflow/data/elasticsearch`

该服务只在 `--profile ragflow` 时启动。
