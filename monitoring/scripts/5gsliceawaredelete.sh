MONITORING_NS="${MONITORING_NS:-monitoring}"


kubectl -n "$MONITORING_NS" delete -f ../sliceawareness/redis/redis_deployment.yaml
kubectl -n "$MONITORING_NS" delete -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml


# kubectl -n monitoring delete -f ../sliceawareness/redis/redis_deployment.yaml
# kubectl -n monitoring delete -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml
