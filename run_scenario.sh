#!/bin/bash
set -e

DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"

PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
INVENTORY="${INVENTORY:-./inventory/${DEFAULT_INVENTORY}/hosts.ini}"

IPERF_PLAYBOOK="playbooks/run_scenario_iperf.yml"
SETUP_IPERF_PLAYBOOK="playbooks/setup_iperf.yml"
INTERFERENCE_PLAYBOOK="playbooks/run_scenario_interference.yml"
SETUP_INTERFERENCE_PLAYBOOK="playbooks/setup_interference.yml"

RUN_SETUP=true
SETUP_PLAYBOOK="${SETUP_IPERF_PLAYBOOK}"
TARGET_PLAYBOOK="${IPERF_PLAYBOOK}"
DRY_RUN=false

EXTRA_VARS_ARRAY=()

run_cmd() {
  if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY-RUN] $*"
  else
    echo "🔹 Running: $*"
    "$@"
  fi
}

usage() {
    echo "Usage: $0 [-d|-i] [--no-setup] [--inventory=name] [-e vars] [--dry_run]"
}

# Proper argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-setup)
            RUN_SETUP=false
            shift
            ;;
        --inventory=*)
            INVENTORY="./inventory/${1#*=}/hosts.ini"
            shift
            ;;
        --dry_run)
            DRY_RUN=true
            shift
            ;;
        -e|--extra-vars)
            EXTRA_VARS_ARRAY+=("$2")
            shift 2
            ;;
        -d)
            SETUP_PLAYBOOK="${SETUP_IPERF_PLAYBOOK}"
            TARGET_PLAYBOOK="${IPERF_PLAYBOOK}"
            shift
            ;;
        -i)
            SETUP_PLAYBOOK="${SETUP_INTERFERENCE_PLAYBOOK}"
            TARGET_PLAYBOOK="${INTERFERENCE_PLAYBOOK}"
            shift
            ;;
        -h|--help)
            usage; exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate inventory AFTER parsing
if [[ ! -f "$INVENTORY" ]]; then
    echo "Error: Inventory file not found at $INVENTORY"
    exit 1
fi

echo "Using Inventory: $INVENTORY"
echo "Using Profile: $PROFILE_5G"

echo "Installing/Updating Ansible collections..."
dry_run ansible-galaxy install -r collections/requirements.yml --ignore-errors

vars="fiveg_profile=${PROFILE_5G}"

for ev in "${EXTRA_VARS_ARRAY[@]}"; do
    vars="$vars ${ev#--}"
done

ANSIBLE_EXTRA_ARGS=(-e "$vars")

if [[ "$RUN_SETUP" == true ]]; then
    run_cmd ansible-playbook -i "$INVENTORY" \
        "${ANSIBLE_EXTRA_ARGS[@]}" \
        "$SETUP_PLAYBOOK"
fi

run_cmd ansible-playbook -i "$INVENTORY" \
    "${ANSIBLE_EXTRA_ARGS[@]}" \
    "$TARGET_PLAYBOOK"
