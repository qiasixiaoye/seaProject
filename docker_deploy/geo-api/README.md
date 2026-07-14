# geo-api

GeoAgent v2 HTTP/SSE API 服务，提供 `/api/geo/*`、A2A 风格接口和兼容的旧接口。

## 镜像

- 镜像名：`ocean-rag-agent/geo-api:latest`
- Dockerfile：`docker_deploy/geo-api/Dockerfile`
- 宿主机端口：默认 `5001`

## 映射

- `../../geo_api.py` -> `/app/geo_api.py`
- `../../geo_agent` -> `/app/geo_agent`
- `../../ocean_agents_demo` -> `/app/ocean_agents_demo`
- `../../data` -> `/app/data`
