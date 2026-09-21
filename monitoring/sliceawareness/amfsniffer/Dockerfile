# Build from repository root:
# docker build -f monitoring/sliceawareness/amfsniffer/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends tshark \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY monitoring/sliceawareness/amfsniffer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY monitoring/sliceawareness/amfsniffer/amf_sniffer.py /app/amf_sniffer.py
COPY probes/ngap_procedure/metrics.py /app/ngap_procedure_metrics.py

CMD ["python", "/app/amf_sniffer.py"]
