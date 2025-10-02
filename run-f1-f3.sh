ansible-galaxy install -r collections/requirements.yml
time ansible-playbook -i inventory/hosts-test.ini playbooks/deploy_benetel-test2.yml 2>&1 | tee logs-run.txt
