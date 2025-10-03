ansible-galaxy install -r collections/requirements.yml
time ansible-playbook -i inventory/hosts-f3.ini playbooks/deploy_benetel-f3.yml 2>&1 | tee logs-run.txt
