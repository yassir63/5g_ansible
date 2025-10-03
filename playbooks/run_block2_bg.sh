#!/bin/bash

ANSIBLE_PLAYBOOK="/usr/bin/ansible-playbook"
INVENTORY="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/inventory/hosts-test.ini"
BLOCK2_PLAYBOOK="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/playbooks/block2_r2lab.yml"
LOG_FILE="/home/turletti/xp/post5g-beta/ptp_test/5g_ansible/playbooks/block2.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Run Block2 fully detached
setsid $ANSIBLE_PLAYBOOK -i $INVENTORY $BLOCK2_PLAYBOOK -c local > $LOG_FILE 2>&1 < /dev/null &
echo "Block2 launched, logs at $LOG_FILE"
