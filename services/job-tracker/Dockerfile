FROM python:3.12-slim
WORKDIR /app
COPY server.py demo.py ./
COPY static ./static
USER 10001:10001
EXPOSE 8090
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TRACKER_HOST=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4)"
CMD ["python", "server.py"]
