#!/bin/bash
set -e

DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"

IPERF_PLAYBOOK="playbooks/run_scenario_iperf.yml"
SETUP_IPERF_PLAYBOOK="playbooks/setup_iperf.yml"
INTERFERENCE_PLAYBOOK="playbooks/run_scenario_interference.yml"
SETUP_INTERFERENCE_PLAYBOOK="playbooks/setup_interference.yml"
MULTI_UE_PLAYBOOK="playbooks/run_scenario_iperf_multi.yml"
SETUP_MULTI_UE_PLAYBOOK="playbooks/setup_iperf.yml" # setup is the same as normal
PING_PLAYBOOK="playbooks/run_scenario_ping.yml"
SETUP_PING_PLAYBOOK="playbooks/setup_iperf.yml" # setup is the same as normal
NUTTCP_PLAYBOOK="playbooks/run_scenario_nuttcp.yml"
SETUP_NUTTCP_PLAYBOOK="playbooks/setup_iperf.yml" # setup is the same as normal

RUN_SETUP=true
RUN_SCENARIO=true
SETUP_PLAYBOOK="${SETUP_IPERF_PLAYBOOK}"
TARGET_PLAYBOOK="${IPERF_PLAYBOOK}"
DRY_RUN=false

DIR_LOGS="LOGS"
mkdir -p ${DIR_LOGS}

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
    echo "Usage: $0 [-d|-i|-m|--ping|--nuttcp] [--no-setup] [--inventory=name] [-e vars] [--dry-run]"
    echo ""
    echo "-d                       Deploy the default iperf scenario"
    echo "-i                       Deploy the interference scenario"
    echo "-m                       Deploy the multi-UE iperf scenario"
    echo "--ping                   Deploy the multi-UE ping scenario"
    echo "--nuttcp                 Deploy the multi-UE nuttcp scenario"
    echo "-n, --no-setup           Do not run the setup, use this option if R2lab devices already up and running"
    echo "-s, --only-setup         Only run the setup"
    echo "-e <vars>                Extra ansible vars, e.g., -e \"nb_ues=5\" -e \"duration=20\""
    echo "--inventory <name>       Use ./inventory/<name>/hosts.ini inventory instead of the default one"
    echo "--dry-run                Only print ansible commands"
    echo "-h, --help               Show help"
}

# Proper argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--no-setup)
            RUN_SETUP=false
            shift
            ;;
        -s|--only-setup)
            RUN_SCENARIO=false
            shift
            ;;
        --inventory=*)
            INVENTORY="./inventory/${1#*=}/hosts.ini"
            shift
            ;;
        -p|--profile5g)
            PROFILE_5G="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -e|--extra-vars)
            EXTRA_VARS_ARRAY+=("-e $2")
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
        -m)
            SETUP_PLAYBOOK="${SETUP_MULTI_UE_PLAYBOOK}"
            TARGET_PLAYBOOK="${MULTI_UE_PLAYBOOK}"
            shift
            ;;
        --ping)
            SETUP_PLAYBOOK="${SETUP_PING_PLAYBOOK}"
            TARGET_PLAYBOOK="${PING_PLAYBOOK}"
            shift
            ;;
        --nuttcp)
            SETUP_PLAYBOOK="${SETUP_NUTTCP_PLAYBOOK}"
            TARGET_PLAYBOOK="${NUTTCP_PLAYBOOK}"
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

PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
INVENTORY="${INVENTORY:-./inventory/${DEFAULT_INVENTORY}/hosts.ini}"

# Validate inventory AFTER parsing
if [[ ! -f "$INVENTORY" ]]; then
    echo "Error: Inventory file not found at $INVENTORY"
    exit 1
fi

echo "Using Inventory: $INVENTORY"
echo "Using Profile: $PROFILE_5G"

echo "Installing/Updating Ansible collections..."
run_cmd ansible-galaxy install -r collections/requirements.yml --ignore-errors

ANSIBLE_EXTRA_ARGS=("-e" "fiveg_profile=${PROFILE_5G}")

for ev in "${EXTRA_VARS_ARRAY[@]}"; do
    ANSIBLE_EXTRA_ARGS+=($ev)
done

if [[ "$RUN_SETUP" == true ]]; then
    run_cmd ansible-playbook -i "$INVENTORY" \
        "${ANSIBLE_EXTRA_ARGS[@]}" \
        "$SETUP_PLAYBOOK"
fi

if [[ "$RUN_SCENARIO" == true ]]; then
    run_cmd ansible-playbook -i "$INVENTORY" \
        "${ANSIBLE_EXTRA_ARGS[@]}" \
        "$TARGET_PLAYBOOK" 2>&1 | tee ${DIR_LOGS}/logs-scenario.txt 
fi
