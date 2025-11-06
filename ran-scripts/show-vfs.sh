#!/usr/bin/env bash

echo ""
printf "%-14s %-14s %-6s %-14s %-18s %-6s\n" \
"PF-NAME" "PF-PCI" "VF-ID" "VF-PCI" "MAC" "NUMA"
echo "---------------------------------------------------------------------------------------------"

for PF in $(ls /sys/class/net | grep -E 'ens|enp|eth'); do
  PF_PATH="/sys/class/net/$PF/device"
  PF_PCI=$(basename "$(readlink -f $PF_PATH)")

  # Skip PFs without VFs
  [ ! -d "$PF_PATH/virtfn0" ] && continue

  for VF_DIR in "$PF_PATH"/virtfn*; do
    VF_INDEX=$(basename "$VF_DIR" | sed 's/virtfn//')
    VF_PCI=$(basename "$(readlink -f "$VF_DIR")")

    # Try to find VF interface name (may not exist when passed to pod)
    VF_NET=$(basename "$(ls -d /sys/bus/pci/devices/$VF_PCI/net/* 2>/dev/null)" 2>/dev/null)

    # Resolve MAC address
    if [ -n "$VF_NET" ]; then
      MAC=$(cat /sys/bus/pci/devices/$VF_PCI/net/$VF_NET/address 2>/dev/null)
    else
      MAC=$(ip link show dev $PF 2>/dev/null | grep "vf $VF_INDEX" | awk '{print $4}')
    fi
    [ -z "$MAC" ] && MAC="-"

    NUMA=$(cat /sys/bus/pci/devices/$VF_PCI/numa_node 2>/dev/null)
    [ "$NUMA" = "-1" ] && NUMA="0"

    printf "%-14s %-14s %-6s %-14s %-18s %-6s\n" \
      "$PF" "$PF_PCI" "$VF_INDEX" "$VF_PCI" "$MAC" "$NUMA"
  done
done

echo ""
