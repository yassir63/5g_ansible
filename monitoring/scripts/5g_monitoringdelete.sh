kubectl delete -f ../prometheus_alertmanager/prometheus-rbac.yaml
kubectl delete -f ../prometheus_alertmanager/prometheus-deployment.yaml
kubectl delete -f ../prometheus_alertmanager/alertmanager-config.yaml
kubectl delete -f ../prometheus_alertmanager/alertmanager-service.yaml
kubectl delete -f ../prometheus_alertmanager/alertmanager-deployment.yaml
