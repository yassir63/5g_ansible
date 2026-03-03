CORE_NS="${CORE_NS:-open5gs}"   # default if not provided

kubectl -n "$CORE_NS" apply -f ../sliceawareness/redis/redis_deployment.yaml
kubectl -n "$CORE_NS" apply -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml

# kubectl -n open5gs apply -f ../sliceawareness/redis/redis_deployment.yaml
# kubectl -n open5gs apply -f ../sliceawareness/ue_mapper/ue_mapper_deployment.yaml
