# geoserver

GeoServer 服务，用作 WMS/WCS 和 NetCDF 扩展入口。

## 镜像

- 使用上游镜像：`docker.osgeo.org/geoserver:3.0.x`
- 宿主机端口：默认 `8081`
- Nginx 代理入口：`http://127.0.0.1:8000/geoserver/web/`

## 映射

- `../../geoserver_data` -> `/opt/geoserver_data`
- `../../data/geoserver_upload` -> `/opt/geoserver_data/import`
