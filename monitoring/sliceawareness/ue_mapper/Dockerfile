FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY ue_mapper_api.py /app/ue_mapper_api.py
COPY mapper_metrics.py /app/mapper_metrics.py
CMD ["python", "/app/ue_mapper_api.py"]
