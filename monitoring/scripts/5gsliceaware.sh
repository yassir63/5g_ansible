MONITORING_NS="${MONITORING_NS:-monitoring}"

kubectl create namespace "$MONITORING_NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$MONITORING_NS" apply -f ../sliceawareness/redis/redis_deployment.yaml
kubectl -n "$MONITORING_NS" apply -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml

# kubectl -n monitoring apply -f ../sliceawareness/redis/redis_deployment.yaml
# kubectl -n monitoring apply -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml
