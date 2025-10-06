ansible-galaxy install -r collections/requirements.yml
time ansible-playbook -i inventory/hosts-f1-f3.ini playbooks/deploy_benetel-without-r2lab.yml 2>&1 | tee logs-run.txt
