FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

EXPOSE 5000

CMD ["gunicorn", "-w", "2", "--timeout", "180", "--graceful-timeout", "30", "-b", "0.0.0.0:5000", "backend.app:app"]
