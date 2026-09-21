# Build from repository root:
# docker build -f probes/user_plane_path/Dockerfile -t IMAGE:TAG .
FROM python:3.10-slim

WORKDIR /app
COPY probes/user_plane_path/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY probes/user_plane_path/probe.py /app/probe.py

CMD ["python", "/app/probe.py"]
