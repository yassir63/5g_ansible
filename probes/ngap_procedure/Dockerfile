# Build this Dockerfile from the repository root; see README.md. Do not build in-place.
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends tshark \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY probes/ngap_procedure/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY probes/ngap_procedure/probe.py /app/probe.py

CMD ["python", "/app/probe.py"]
