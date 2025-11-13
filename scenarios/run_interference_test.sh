#!/bin/bash

ansible-galaxy install -r collections/requirements.yml
ansible-playbook -i inventory/hosts.ini playbooks/interference_test_setup.yml

if [[ "$MODE" == "TDD" ]]; then
    ansible-playbook -i inventory/hosts.ini playbooks/run_interference_test_tdd.yml
elif [[ "$MODE" == "FDD" ]]; then
    ansible-playbook -i inventory/hosts.ini playbooks/run_interference_test_fdd.yml
else
    echo "Error: MODE must be set to either 'TDD' or 'FDD'"
    exit 1
fi
