#!/usr/bin/env bash
[ -z "$BASH_VERSION" ] && exec /usr/bin/env bash "$0" "$@"

NIC="ens15f0np0"

echo "================ CPU GROUPS ================="
echo "Kubernetes reserved cores: $(grep reservedSystemCPUs /var/lib/kubelet/config.yaml | awk '{print $2}')"
echo "OVS cores: $(taskset -pc $(pidof ovs-vswitchd 2>/dev/null) 2>/dev/null | awk -F': ' '{print $2}')"

GNB_PID=$(pgrep -f "oai-gnb|gnb|nr-softmodem|nsa|sa" | head -n1)
if [ -n "$GNB_PID" ]; then
  echo "gNB cores: $(taskset -pc $GNB_PID 2>/dev/null | awk -F': ' '{print $2}')"
else
  echo "gNB cores: (process not detected)"
fi
echo

echo "================ IRQ GROUPING ($NIC) ================="
unset GROUPS; declare -A GROUPS

for IRQ in $(grep -i "$NIC" /proc/interrupts | awk '{print $1}' | tr -d ':'); do
  MASK=$(cat /proc/irq/$IRQ/smp_affinity_list 2>/dev/null)
  GROUPS["$MASK"]+="$IRQ "
done

for MASK in "${!GROUPS[@]}"; do
  printf "Cores %-12s ← IRQs: %s\n" "$MASK" "${GROUPS[$MASK]}"
done
echo

echo "================ NUMA & DEVICES ================="
for IF in $(ls /sys/class/net | grep -E 'ens|enp'); do
  PCI=$(basename "$(readlink -f /sys/class/net/$IF/device)")
  NUMA=$(cat /sys/bus/pci/devices/$PCI/numa_node 2>/dev/null)
  printf "%-12s PCI %-12s NUMA %s\n" "$IF" "$PCI" "$NUMA"
done
echo

echo "================ ACTIVE CPU LOAD (>0.5%) ================="
mpstat -P ALL 1 1 | awk '
/Average:/ && $2 ~ /^[0-9]+$/ {
  cpu=$2;
  idle=$12;
  load=100-idle;
  if(load>0.5)
    printf("CPU%-3s load: %.1f%%\n", cpu, load);
}'
