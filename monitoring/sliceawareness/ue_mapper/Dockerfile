# Build from repository root:
# docker build -f monitoring/sliceawareness/ue_mapper/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

WORKDIR /app
COPY monitoring/sliceawareness/ue_mapper/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY monitoring/sliceawareness/ue_mapper/ue_mapper_api.py /app/ue_mapper_api.py
COPY probes/ue_mapper_metrics/mapper_metrics.py /app/mapper_metrics.py
CMD ["python", "/app/ue_mapper_api.py"]
