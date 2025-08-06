#!/bin/bash

set -e

UE_COUNT=$1
CSV_PATH="/tmp/monitoring_overhead_ue${UE_COUNT}.csv"
DURATIONS=(30 60 120)
CONTAINERS=("oai-gnb" "metrics-parser" "oai-flexric")

echo "UE_Count,Duration,Container,Avg_CPU(millicores),Avg_MEM(Mi),Disk_Usage(Mi)" > "$CSV_PATH"

for duration in "${DURATIONS[@]}"; do
  declare -A cpu_total mem_total samples

  for c in "${CONTAINERS[@]}"; do
    cpu_total[$c]=0
    mem_total[$c]=0
    samples[$c]=0
  done

  for ((i=0; i<$duration; i++)); do
    for container in "${CONTAINERS[@]}"; do
      POD=$(kubectl -n open5gs get pod -l app=oai-gnb -o jsonpath="{.items[0].metadata.name}")
      CONTAINER_NAME="$container"
      echo $CONTAINER_NAME
      if [[ "$container" == "oai-flexric" ]]; then
        POD=$(kubectl -n open5gs get pod -l app=oai-flexric -o jsonpath="{.items[0].metadata.name}")
        CONTAINER_NAME=$(kubectl -n open5gs get pod "$POD" -o jsonpath="{.spec.containers[0].name}")
      fi

      STATS=$(kubectl -n open5gs top pod "$POD" --containers | grep "$CONTAINER_NAME" | head -n1 | awk '{print $3","$4}')
      CPU=$(echo "$STATS" | cut -d',' -f1 | tr -d 'm' | tr -d '[:space:]')
      MEM=$(echo "$STATS" | cut -d',' -f2 | tr -d 'Mi' | tr -d '[:space:]')

      echo "DEBUG: $container => CPU=$CPU MEM=$MEM"

      cpu_total[$container]=$(( ${cpu_total[$container]} + ${CPU%%.*} ))
      mem_total[$container]=$(( ${mem_total[$container]} + ${MEM%%.*} ))
      samples[$container]=$(( ${samples[$container]} + 1 ))
      echo "DEBUG: $container => CPU=$CPU MEM=$MEM"

      cpu_total[$container]=$(( ${cpu_total[$container]} + ${CPU%%.*} ))
      mem_total[$container]=$(( ${mem_total[$container]} + ${MEM%%.*} ))
      samples[$container]=$(( ${samples[$container]} + 1 ))
    done
    sleep 1
  done

  # Get Prometheus disk usage (in MiB)
  PROM_POD=$(kubectl -n monarch get pod -l app.kubernetes.io/name=prometheus -o jsonpath="{.items[0].metadata.name}")
  DISK=$(kubectl -n monarch exec "$PROM_POD" -c prometheus -- du -s /prometheus | awk '{print int($1 / 1024)}')

  for c in "${CONTAINERS[@]}"; do
    AVG_CPU=$(( ${cpu_total[$c]} / ${samples[$c]} ))
    AVG_MEM=$(( ${mem_total[$c]} / ${samples[$c]} ))
    echo "$UE_COUNT,$duration,$c,$AVG_CPU,$AVG_MEM,$DISK" >> "$CSV_PATH"
  done
done

# Upload
curl --upload-file "$CSV_PATH" https://bashupload.com/$(basename "$CSV_PATH")
