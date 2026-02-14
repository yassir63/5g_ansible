FROM python:3.10-slim

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y tshark && \
    pip install pyshark redis && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY amf_sniffer.py /app/amf_sniffer.py

# Run as default user (optional: use root for full interface access if needed)
CMD ["python", "/app/amf_sniffer.py"]
