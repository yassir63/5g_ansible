# Build from repository root:
# docker build -f probes/smf_sniffer/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends tshark && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY probes/smf_sniffer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY probes/smf_sniffer/smf_sniffer.py /app/smf_sniffer.py
COPY probes/pfcp_procedure/metrics.py /app/pfcp_procedure_metrics.py
CMD ["python", "/app/smf_sniffer.py"]
