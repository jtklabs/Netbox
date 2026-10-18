FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends traceroute \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY monitor.py server.py ./
COPY static ./static
RUN mkdir /data
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH_MONITOR_HOST=0.0.0.0 PATH_MONITOR_PORT=8091 \
    PATH_MONITOR_CONFIG=/config/targets.json PATH_MONITOR_DATABASE=/data/paths.sqlite3
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=4)"
CMD ["python", "server.py"]
