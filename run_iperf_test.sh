#!/bin/bash

# Default inventory
DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"
PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
INVENTORY="${INVENTORY:-./inventory/${DEFAULT_INVENTORY}/hosts.ini}"

# Install required Ansible collections
ansible-galaxy install -r collections/requirements.yml

# Default playbooks
SETUP_PLAYBOOK="playbooks/default_iperf_test_setup.yml"
TEST_PLAYBOOK="playbooks/run_default_iperf_test.yml"

RUN_SETUP=true

# Parse arguments
for arg in "$@"; do
  case "$arg" in
    --no-setup)
      RUN_SETUP=false
      ;;
    --inventory=*)
      INVENTORY="./inventory/${arg#*=}/hosts.ini"
      ;;
    -s)
      RUN_SETUP=false
      TEST_PLAYBOOK="playbooks/run_rfsim_iperf_test.yml"
      ;;
    -d)
      SETUP_PLAYBOOK="playbooks/default_iperf_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_default_iperf_test.yml"
      ;;
    -p)
      SETUP_PLAYBOOK="playbooks/parallel_iperf_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_parallel_iperf_test.yml"
      ;;
    -i)
      SETUP_PLAYBOOK="playbooks/interference_test_setup.yml"
      TEST_PLAYBOOK="playbooks/run_interference_test.yml"
      ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: $0 [-d|-p|-i] [--no-setup] [--inventory=name]"
      exit 1
      ;;
  esac
done

# Run playbooks
[ "$RUN_SETUP" = true ] && ansible-playbook -i "$INVENTORY" "$SETUP_PLAYBOOK"
ansible-playbook -i "$INVENTORY" "$TEST_PLAYBOOK"
