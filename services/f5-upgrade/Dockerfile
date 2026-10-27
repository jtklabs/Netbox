FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY f5_upgrade ./f5_upgrade
RUN pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home f5upgrade \
    && mkdir /data /backups && chown 10001:10001 /data /backups
USER 10001:10001
EXPOSE 8080
CMD ["f5-upgrade", "serve", "--host", "0.0.0.0"]
