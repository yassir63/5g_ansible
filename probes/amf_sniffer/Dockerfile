# Build from repository root:
# docker build -f probes/amf_sniffer/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends tshark \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY probes/amf_sniffer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY probes/amf_sniffer/amf_sniffer.py /app/amf_sniffer.py
COPY probes/ngap_procedure/metrics.py /app/ngap_procedure_metrics.py

CMD ["python", "/app/amf_sniffer.py"]
