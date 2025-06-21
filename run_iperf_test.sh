#!/bin/bash

ansible-galaxy install -r collections/requirements.yml

PLAYBOOK="playbooks/run_iperf_test.yml"
while getopts ":p" opt; do
  case ${opt} in
    p )
      PLAYBOOK="playbooks/run_parallel_iperf_test.yml"
      ;;
    \? )
      echo "Usage: $0 [-p]"
      exit 1
      ;;
  esac
done

ansible-playbook -i inventory/hosts.ini "$PLAYBOOK"
