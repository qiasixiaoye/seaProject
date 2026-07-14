# backend

Flask + Gunicorn 主后端，提供 `/api/*`、数据上传、NetCDF 查询、RAG/Agent 报告等接口。

## 镜像

- 镜像名：`ocean-rag-agent/backend:latest`
- Dockerfile：`docker_deploy/backend/Dockerfile`
- 端口：容器内 `5000`，由 `nginx` 反代，不直接暴露到宿主机。

## 映射

- `../../backend` -> `/app/backend`
- `../../geo_agent` -> `/app/geo_agent`
- `../../ocean_agents_demo` -> `/app/ocean_agents_demo`
- `../../data` -> `/app/data`
- `../../instance` -> `/app/instance`
