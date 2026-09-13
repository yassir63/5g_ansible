FROM python:3.11-slim

# Install kubectl (required by your kill logic)
RUN apt-get update && apt-get install -y curl ca-certificates && \
    curl -L -o /usr/local/bin/kubectl https://dl.k8s.io/release/v1.29.0/bin/linux/amd64/kubectl && \
    chmod +x /usr/local/bin/kubectl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir kopf kubernetes requests

COPY k8scontroller.py /app/k8scontroller.py

# Run kopf
CMD ["kopf", "run", "-A", "/app/k8scontroller.py"]