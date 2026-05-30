FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY ocean_agents_demo /app/ocean_agents_demo
COPY geo_agent /app/geo_agent
COPY geo_api.py /app/geo_api.py
COPY data /app/data

EXPOSE 5000
EXPOSE 5001

# 默认启动旧 Flask 后端（兼容旧前端）
# 若要启动新 GeoAgent API，改为: CMD ["python", "geo_api.py"]
CMD ["gunicorn", "-w", "2", "--timeout", "180", "--graceful-timeout", "30", "-b", "0.0.0.0:5000", "backend.app:app"]
