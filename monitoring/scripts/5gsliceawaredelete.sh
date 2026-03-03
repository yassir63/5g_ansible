CORE_NS="${CORE_NS:-open5gs}"   # default if not provided


kubectl -n "$CORE_NS" delete -f ../sliceawareness/redis/redis_deployment.yaml
kubectl -n "$CORE_NS" delete -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml


# kubectl -n oail1 delete -f ../sliceawareness/redis/redis_deployment.yaml
# kubectl -n oail1 delete -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml
