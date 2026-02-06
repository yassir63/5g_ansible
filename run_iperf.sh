#!/bin/bash

# Default inventory
DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"

PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
INVENTORY="${INVENTORY:-./inventory/${DEFAULT_INVENTORY}/hosts.ini}"

# Install required Ansible collections
ansible-galaxy install -r collections/requirements.yml

# Run playbook
ansible-playbook -i "$INVENTORY" "$TEST_PLAYBOOK"
