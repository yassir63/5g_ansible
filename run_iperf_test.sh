#!/bin/bash

# Install required Ansible collections
ansible-galaxy install -r collections/requirements.yml

# Default playbooks
SETUP_PLAYBOOK="playbooks/default_iperf_test_setup.yml"
TEST_PLAYBOOK="playbooks/run_default_iperf_test.yml"

# By default, run setup
RUN_SETUP=true

# Parse short options
while getopts ":dpi" opt; do
  case ${opt} in
    d )
      SETUP_PLAYBOOK="playbooks/default_iperf_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_default_iperf_test.yml"
      ;;
    p )
      SETUP_PLAYBOOK="playbooks/parallel_iperf_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_parallel_iperf_test.yml"
      ;;
    i )
      SETUP_PLAYBOOK="playbooks/interference_iperf_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_interference_test.yml"
      ;;
    \? )
      echo "Usage: $0 [-d] [-p] [-i] [--no-setup]"
      echo "  -d           Use default iperf test playbook"
      echo "  -p           Use parallel iperf test playbook"
      echo "  -i           Use interference iperf test playbook"
      echo "  --no-setup   Skip the setup playbook (useful if setup was already run)"
      exit 1
      ;;
  esac
done

# Shift positional parameters past getopts-parsed options
shift $((OPTIND -1))

# Handle long options (e.g. --no-setup)
for arg in "$@"; do
  case $arg in
    --no-setup)
      RUN_SETUP=false
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [-d] [-p] [-i] [--no-setup]"
      exit 1
      ;;
  esac
done

# Run selected playbooks
if $RUN_SETUP; then
  ansible-playbook -i inventory/hosts.ini "$SETUP_PLAYBOOK"
fi

ansible-playbook -i inventory/hosts.ini "$TEST_PLAYBOOK"
