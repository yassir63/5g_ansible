#!/bin/bash

CONFIG_DIR="/etc/5g_ansible/ru_profiles/benetel/config"
mkdir -p "${CONFIG_DIR}"

echo "Copy all RUs config files to ${CONFIG_DIR}"
cp ../config/*.cfg "${CONFIG_DIR}"

echo "Copy all RUs config scripts to /usr/local/bin"
cp ../bin/*.sh /usr/local/bin

