#!/bin/bash

# Install required Ansible collections
ansible-galaxy install -r ../collections/requirements.yml

# Default playbooks
SETUP_PLAYBOOK="../playbooks/default_iperf_test_setup.yml"
TEST_PLAYBOOK="../playbooks/run_default_iperf_test.yml"

# By default, run setup
RUN_SETUP=true

# First handle long options
for arg in "$@"; do
  case "$arg" in
    --no-setup)
      RUN_SETUP=false
      # Remove this arg from $@ so getopts doesn't see it
      set -- "${@/--no-setup/}"
      ;;
    --*)
      echo "Unknown option: $arg"
      echo "Usage: $0 [-s] [-d] [-p] [-i] [--no-setup]"
      exit 1
      ;;
  esac
done

# Now parse short options
# For iperf tests without interference, we will not use a setup playbook that connects UEs to the network, 
# since they should already be connected by the deploy playbook.
while getopts ":dpi" opt; do
  case ${opt} in
    s )
      RUN_SETUP=false
      TEST_PLAYBOOK="../playbooks/run_rfsim_iperf_test.yml"
      ;;
    d )
      RUN_SETUP=false
      TEST_PLAYBOOK="../playbooks/run_default_iperf_test.yml"
      ;;
    p )
      RUN_SETUP=false
      TEST_PLAYBOOK="../playbooks/run_parallel_iperf_test.yml"
      ;;
    i )
      SETUP_PLAYBOOK="../playbooks/interference_test_setup.yml"
      if [[ "$MODE" == "TDD" ]]; then
          TEST_PLAYBOOK="../playbooks/run_interference_test_tdd.yml"
      elif [[ "$MODE" == "FDD" ]]; then
          TEST_PLAYBOOK="../playbooks/run_interference_test_fdd.yml"
      else
          echo "Error: MODE must be set to either 'TDD' or 'FDD' as environment variable for interference test."
          exit 1
      fi
      ;;
    \? )
      echo "Usage: $0 [-d] [-p] [-i] [--no-setup]"
      echo "  -s           Use OAI rfsim iperf test playbook"
      echo "  -d           Use default iperf test playbook"
      echo "  -p           Use parallel iperf test playbook"
      echo "  -i           Use interference iperf test playbook"
      echo "  --no-setup   Skip the setup playbook (useful if setup was already run)"
      exit 1
      ;;
  esac
done

# Shift past processed short options
shift $((OPTIND - 1))

# Run selected playbooks
if $RUN_SETUP; then
  ansible-playbook -i inventory/hosts.ini "$SETUP_PLAYBOOK"
fi

ansible-playbook -i inventory/hosts.ini "$TEST_PLAYBOOK"
