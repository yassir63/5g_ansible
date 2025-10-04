ansible-galaxy install -r collections/requirements.yml
time ansible-playbook -i inventory/hosts-test.ini playbooks/deploy_benetel-without-r2lab.yml 2>&1 | tee logs-run.txt &
time ansible-playbook -i inventory/hosts-test.ini playbooks/deploy_r2lab.yml 2>&1 | tee logs-r2lab.txt &
