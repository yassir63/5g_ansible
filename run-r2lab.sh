ansible-galaxy install -r collections/requirements.yml
( ansible-playbook -i inventory/hosts-f1-f3.ini playbooks/deploy_r2lab.yml 2>&1 | tee logs-r2lab.txt ) &
pid=$!; echo "**** PID of R2lab setup script: $pid"
