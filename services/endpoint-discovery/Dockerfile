FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends snmp \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 discovery && useradd --uid 10001 --gid discovery --no-create-home discovery
WORKDIR /app
COPY services/endpoint-discovery/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/endpoint-discovery/discovery ./discovery
COPY scripts/snmp-inventory/snmpinv/__init__.py scripts/snmp-inventory/snmpinv/snmp.py ./snmpinv/
USER 10001:10001
EXPOSE 8092
CMD ["uvicorn", "discovery.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8092", "--no-access-log"]
