#!/bin/bash

# --- Default configuration ---
DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"

# --- Set Profile and Inventory path ---
# Use environment variables if they exist, otherwise use defaults
PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
INVENTORY="${INVENTORY:-./inventory/${DEFAULT_INVENTORY}/hosts.ini}"

# --- Validation ---
# Check if the inventory file exists before proceeding
if [[ ! -f "$INVENTORY" ]]; then
    echo "Error: Inventory file not found at $INVENTORY"
    exit 1
fi

echo "Using Inventory: $INVENTORY"
echo "Using Profile: $PROFILE_5G"

# --- Requirements ---
# Install required Ansible collections defined in requirements.yml
echo "Installing/Updating Ansible collections..."
ansible-galaxy install -r collections/requirements.yml --ignore-errors

# --- Playbook Execution ---
# "$@" is a special shell variable that captures all arguments passed to this script.
# This allows you to run: ./run.sh -e "nb_ues=5" -e "duration=20"
echo "Starting Playbook..."
ansible-playbook -i "$INVENTORY" playbooks/run_scenario_interference.yml "$@"
