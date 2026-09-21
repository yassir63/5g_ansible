# Build from repository root:
# docker build -f probes/upf_data_plane/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

WORKDIR /app
COPY probes/upf_data_plane/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY probes/upf_data_plane/probe.py /app/probe.py

CMD ["python", "/app/probe.py"]
