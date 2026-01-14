#!/bin/bash

ansible-galaxy install -r collections/requirements.yml

( ansible-playbook -i inventory/hosts.ini playbooks/deploy_oai_oai_fhi72.yml -e "rru=$RRU" 2>&1 | tee logs-run.txt ) &
pid=$!; echo "**** PID of main script: $pid"

( ansible-playbook -i inventory/hosts.ini playbooks/deploy_r2lab.yml -e "rru=$RRU" 2>&1 | tee logs-r2lab.txt ) &
pid=$!; echo "**** PID of R2lab script: $pid"
