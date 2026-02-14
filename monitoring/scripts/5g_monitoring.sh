kubectl apply -f ../prometheus_alertmanager/prometheus-rbac.yaml
kubectl apply -f ../prometheus_alertmanager/prometheus-deployment.yaml
kubectl apply -f ../prometheus_alertmanager/alertmanager-config.yaml
kubectl apply -f ../prometheus_alertmanager/alertmanager-service.yaml
kubectl apply -f ../prometheus_alertmanager/alertmanager-deployment.yaml
