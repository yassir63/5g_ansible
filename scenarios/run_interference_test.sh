#!/bin/bash

ansible-galaxy install -r collections/requirements.yml

# By default, run setup
RUN_SETUP=true

# First handle long options
new_args=()
for arg in "$@"; do
  case "$arg" in
    --no-setup)
      RUN_SETUP=false
      ;;
    --*)
      echo "Unknown option: $arg"
      echo "Usage: $0 [--no-setup]"
      exit 1
      ;;
    *)
      new_args+=("$arg")
      ;;
  esac
done
set -- "${new_args[@]}"

if $RUN_SETUP; then
    ansible-playbook -i inventory/hosts.ini playbooks/interference_test_setup.yml
fi

if [[ "$MODE" == "TDD" ]]; then
    ansible-playbook -i inventory/hosts.ini playbooks/run_interference_test_tdd.yml
elif [[ "$MODE" == "FDD" ]]; then
    ansible-playbook -i inventory/hosts.ini playbooks/run_interference_test_fdd.yml
else
    echo "Error: MODE must be set to either 'TDD' or 'FDD'"
    exit 1
fi
