# nginx

静态前端和反向代理服务。

## 镜像

- 使用上游镜像：`nginx:1.27-alpine`
- 宿主机端口：默认 `8000`

## 映射

- `../../frontend` -> `/usr/share/nginx/html`
- `./default.conf` -> `/etc/nginx/conf.d/default.conf`

## 路由

- `/`：前端静态页面
- `/api/`：反代到 `backend:5000`
- `/geo-api/`：反代到 `geo-api:5001`
- `/geoserver/`：反代到 `geoserver:8080/geoserver/`
