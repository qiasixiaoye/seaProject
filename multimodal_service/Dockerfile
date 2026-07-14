FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY multimodal_service/requirements.txt /app/multimodal_service/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
RUN pip install -r /app/multimodal_service/requirements.txt

COPY multimodal_service /app/multimodal_service

EXPOSE 8001
CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "180", "-b", "0.0.0.0:8001", "multimodal_service.app.api:app"]
