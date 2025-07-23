#!/bin/bash

# Install required Ansible collections
ansible-galaxy install -r collections/requirements.yml

# Default playbook
PLAYBOOK="playbooks/run_default_iperf_test.yml"

# Parse options
while getopts ":pd" opt; do
  case ${opt} in
    d )
      PLAYBOOK="playbooks/run_default_iperf_test.yml"
      ;;
    p )
      PLAYBOOK="playbooks/run_parallel_iperf_test.yml"
      ;;
    i )
      PLAYBOOK="playbooks/run_interference_test.yml"
      ;;
    \? )
      echo "Usage: $0 [-d] [-p] [-i]"
      echo "  -d  Run default iperf test playbook"
      echo "  -p  Run parallel iperf test playbook"
      echo "  -i  Run iperf test with interference playbook"
      exit 1
      ;;
  esac
done

# Run the selected playbook
ansible-playbook -i inventory/hosts.ini "$PLAYBOOK"
