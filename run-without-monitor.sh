ansible-galaxy install -r collections/requirements.yml
time ansible-playbook --force -i inventory/hosts-test.ini playbooks/deploy_benetel-without_monitor.yml 2>&1 | tee logs-run.txt
