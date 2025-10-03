#!/bin/bash
# Full absolute paths
ANSIBLE_PLAYBOOK="/usr/bin/ansible-playbook"
INVENTORY="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/playbooks/inventory/hosts-test.ini"
BLOCK2_PLAYBOOK="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/playbooks/block2_r2lab.yml"
LOG_FILE="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/playbooks/block2.log"

# Launch Block2 in fully detached background
nohup $ANSIBLE_PLAYBOOK -i $INVENTORY $BLOCK2_PLAYBOOK -c local > $LOG_FILE 2>&1 &
