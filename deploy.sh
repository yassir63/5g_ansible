#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="$(dirname "$0")/deployments"

usage() {
  cat <<EOF
Usage: $0 <target>

Targets (explicit):

  oai_r2lab         → deploy_oai.sh
      Runs OAI core + OAI gNB (on R2lab testbed).

  open5gs_oai       → deploy.sh
      Runs Open5GS core + OAI (on R2lab testbed).

  oai_rfsim         → deploy_rfsim_oai_core.sh
      Runs OAI core + OAI gNB (RFSIM emulation).

  open5gs_rfsim     → deploy_open5gs_rfsim.sh
      Runs Open5GS core + OAI gNB (RFSIM emulation).

  open5gs_srsran    → deploy_open5gs_srsRAN.sh
      Runs Open5GS core + srsRAN gNB + UEs (ZMQ emulation).

  open5gs_ueransim  → deploy_open5gs_ueransim.sh
      Runs Open5GS core + UERANSIM gNB + emulated UEs.

Examples:
  $0 oai_r2lab
  $0 open5gs_oai
  $0 oai_rfsim
  $0 open5gs_rfsim
  $0 open5gs_srsran
  $0 open5gs_ueransim
EOF
  exit 1
}

target="${1:-}"

case "$target" in
  oai_r2lab)
    "$DEPLOY_DIR/deploy_oai.sh"
    ;;
  open5gs_oai)
    "$DEPLOY_DIR/deploy.sh"
    ;;
  oai_rfsim)
    "$DEPLOY_DIR/deploy_rfsim_oai_core.sh"
    ;;
  open5gs_rfsim)
    "$DEPLOY_DIR/deploy_open5gs_rfsim.sh"
    ;;
  open5gs_srsran)
    "$DEPLOY_DIR/deploy_open5gs_srsRAN.sh"
    ;;
  open5gs_ueransim)
    "$DEPLOY_DIR/deploy_open5gs_ueransim.sh"
    ;;
  ""|-h|--help)
    usage
    ;;
  *)
    echo "❌ Unknown target: $target"
    usage
    ;;
esac
