#!/usr/bin/env bash
set -euo pipefail

# Usage:
#  ./clean_vfs.sh        -> dry-run, montre ce qui serait fait
#  APPLY=1 ./clean_vfs.sh -> applique les changements (supprime VFs sur PFs ciblés)
#
# IMPORTANT: ce script DETECTE automatiquement la NIC d'administration (interface
# avec route par défaut / ou l'interface listée comme "ssh" si fournie).
# Il ne touchera pas cette interface.

APPLY=${APPLY:-0}
SKIP_IFACE=${SKIP_IFACE:-}   # override if you want to protect another iface

echo "🔍 Nettoyage VFs (dry-run unless APPLY=1)"
echo "APPLY=$APPLY"

# 1) detect default route interface (management)
if [ -n "${SKIP_IFACE}" ]; then
  MGMT_IFACE="$SKIP_IFACE"
else
  MGMT_IFACE=$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')
  # fallback: first non-loopback UP interface if route not found
  if [ -z "$MGMT_IFACE" ]; then
    MGMT_IFACE=$(ip -o link show up | awk -F': ' '{print $2}' | grep -v lo | head -n1)
  fi
fi

echo "🔸 Interface d'administration détectée (protégée): $MGMT_IFACE"

# helper: pf -> pci device id
pf_to_pci() {
  local ifname=$1
  readlink -f /sys/class/net/"$ifname"/device 2>/dev/null | awk -F/ '{print $NF}' || true
}

# list PFs that support SR-IOV (only show those with sriov_totalvfs file)
echo
echo "🔸 PF SR-IOV compatibles:"
for dev in /sys/class/net/*; do
  ifname=$(basename "$dev")
  pci=$(pf_to_pci "$ifname")
  [ -z "$pci" ] && continue
  if [ -f "/sys/bus/pci/devices/$pci/sriov_totalvfs" ]; then
    tot=$(cat /sys/bus/pci/devices/$pci/sriov_totalvfs)
    num=$(cat /sys/bus/pci/devices/$pci/sriov_numvfs)
    echo " - $ifname -> PCI $pci (totalvfs=$tot, numvfs=$num)"
  fi
done

# Build list of PFs to clean: PFs with active VFs but NOT the management iface
declare -a TARGET_PFS=()
for pf in /sys/class/net/*; do
  ifname=$(basename "$pf")
  pci=$(pf_to_pci "$ifname")
  [ -z "$pci" ] && continue
  if [ -f "/sys/bus/pci/devices/$pci/sriov_numvfs" ]; then
    numvfs=$(cat /sys/bus/pci/devices/$pci/sriov_numvfs)
    if [ "$numvfs" -gt 0 ] && [ "$ifname" != "$MGMT_IFACE" ]; then
      TARGET_PFS+=("$ifname")
    fi
  fi
done

if [ ${#TARGET_PFS[@]} -eq 0 ]; then
  echo "✅ Aucun PF avec VFs actifs (sauf éventuellement la mgmt iface). Rien à faire."
  exit 0
fi

echo
echo "🔸 PFs ciblés pour nettoyage (ne touche PAS $MGMT_IFACE):"
for p in "${TARGET_PFS[@]}"; do
  pci=$(pf_to_pci "$p")
  echo "   * $p -> PCI $pci (numvfs=$(cat /sys/bus/pci/devices/$pci/sriov_numvfs))"
done

echo
# Preview VFs and current driver binding
for p in "${TARGET_PFS[@]}"; do
  pci=$(pf_to_pci "$p")
  echo
  echo "---- PF $p ($pci) ----"
  for vf in /sys/bus/pci/devices/"$pci"/virtfn*; do
    [ -e "$vf" ] || continue
    vfdev=$(readlink -f "$vf" | awk -F/ '{print $NF}')
    drv=$(basename "$(readlink -f /sys/bus/pci/devices/$vfdev/driver 2>/dev/null)" || echo none)
    echo " VF $vfdev  driver=$drv"
  done
done

echo
if [ "$APPLY" != "1" ]; then
  echo "DRY-RUN: nothing will be changed. Rerun with APPLY=1 to apply cleanup."
  exit 0
fi

echo
echo "🔧 APPLY=1 : on nettoie les VFs des PFs ciblés (écrit 0 dans sriov_numvfs)"
for p in "${TARGET_PFS[@]}"; do
  pci=$(pf_to_pci "$p")
  echo
  echo "-> Cleaning PF $p (PCI $pci)"
  # try to unbind VFs from vfio-pci (if any)
  for vf in /sys/bus/pci/devices/"$pci"/virtfn*; do
    [ -e "$vf" ] || continue
    vfdev=$(readlink -f "$vf" | awk -F/ '{print $NF}')
    curdrv=$(basename "$(readlink -f /sys/bus/pci/devices/$vfdev/driver 2>/dev/null)" || echo none)
    if [ "$curdrv" = "vfio-pci" ]; then
      echo " Unbinding $vfdev from vfio-pci"
      echo -n "$vfdev" > /sys/bus/pci/drivers/vfio-pci/unbind || true
    elif [ "$curdrv" != "none" ]; then
      echo " VF $vfdev currently bound to $curdrv; leaving it (no forced rebind)."
    fi
  done

  # Now set numvfs=0 -> remove VFs
  echo " Setting sriov_numvfs -> 0 for $pci"
  echo 0 > /sys/bus/pci/devices/"$pci"/sriov_numvfs || {
    echo " ! failed to write sriov_numvfs=0 (permission/driver?)"
  }
done

echo
echo "✅ Cleanup done. Vérifie le statut:"
echo " - ip link"
echo " - lspci -nn | grep -i ether"
echo " - for p in ${TARGET_PFS[*]}; do readlink -f /sys/class/net/$p/device; cat /sys/bus/pci/devices/$(readlink -f /sys/class/net/$p/device | awk -F/ '{print $NF}')/sriov_numvfs; done"
