FROM python:3.10-slim

RUN pip install flask redis

WORKDIR /app
COPY ue_mapper_api.py /app/ue_mapper_api.py
CMD ["python", "/app/ue_mapper_api.py"]
