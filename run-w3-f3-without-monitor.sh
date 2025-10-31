ansible-galaxy install -r collections/requirements.yml
( ansible-playbook -i inventory/hosts-w3-f3.ini playbooks/deploy_benetel-without-r2lab.yml 2>&1 | tee logs-run.txt ) &
pid=$!; echo "**** PID of main script: $pid"
( ansible-playbook -i inventory/hosts-w3-f3.ini playbooks/deploy_r2lab.yml 2>&1 | tee logs-r2lab.txt ) &
pid=$!; echo "**** PID of R2lab setup script: $pid"
