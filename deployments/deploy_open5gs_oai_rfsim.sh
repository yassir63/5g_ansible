#!/bin/bash

ansible-galaxy install -r collections/requirements.yml
ansible-playbook -i inventory/hosts.ini playbooks/deploy_open5gs_oai_rfsim.yml  -e "rru=$RRU monitoring_enabled=$monitoring_enabled"