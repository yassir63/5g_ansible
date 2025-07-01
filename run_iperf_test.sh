#!/bin/bash

# Install required Ansible collections
ansible-galaxy install -r collections/requirements.yml

# Default playbook
PLAYBOOK="playbooks/run_iperf_test.yml"

# Parse options
while getopts ":pd" opt; do
  case ${opt} in
    p )
      PLAYBOOK="playbooks/run_parallel_iperf_test.yml"
      ;;
    d )
      PLAYBOOK="playbooks/run_default_test.yml"
      ;;
    \? )
      echo "Usage: $0 [-p] [-d]"
      echo "  -p  Run parallel iperf test playbook"
      echo "  -d  Run default test playbook"
      exit 1
      ;;
  esac
done

# Run the selected playbook
ansible-playbook -i inventory/hosts.ini "$PLAYBOOK"
