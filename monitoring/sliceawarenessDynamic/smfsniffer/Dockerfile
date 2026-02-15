FROM python:3.10-slim

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y tshark && \
    pip install scapy redis && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY smf_sniffer.py /app/smf_sniffer.py
CMD ["python", "/app/smf_sniffer.py"]
