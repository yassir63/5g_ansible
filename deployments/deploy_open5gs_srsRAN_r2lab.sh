#!/bin/bash

ansible-galaxy install -r collections/requirements.yml
ansible-playbook -i inventory/hosts.ini playbooks/deploy_open5gs_srsRAN_r2lab.yml  -e "rru=$RRU monitoring_enabled=$monitoring_enabled"
