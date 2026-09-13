#!/bin/bash
# this script should be copied on the node that controls the benetel RU
# at /usr/local/bin location

CONFIG_DIR="/etc/5g_ansible/ru_profiles/benetel/config"
CONFIG_FILE="benetel1-srsran-ru_config-tdd_n78_100mhz_4x2.cfg"
CONFIG="${CONFIG_DIR}"/"${CONFIG_FILE}"

RU_IP="192.168.233.101"

# if RU not alive, exit
ping -c 1 -W 2 "${RU_IP}" >/dev/null 2>&1 || { echo "RU ${RU_IP} is unreachable, did you switch it on before ?"; exit 1; }

# Copy the target benetel config file to the RU
scp -O "$CONFIG" root@"${RU_IP}":/etc/ru_config.cfg

# Reboot the RU
ssh root@"${RU_IP}" /sbin/reboot

# sleep 60s
sleep 60

timeout 60 bash -c "until ping -c 1 -W 2 ${RU_IP} >/dev/null 2>&1; do sleep 1; done" || { echo "Error: RU ${RU_IP} unreachable after reboot"; exit 1; }

echo "Successfully load $CONFIG on ${RU_IP}, now up and running"
