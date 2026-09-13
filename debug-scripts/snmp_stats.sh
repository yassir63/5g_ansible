#!/bin/bash

iters=$1
logfile=$2
interval="${3:-1}"

for ((i=0; i<$1; i++)); do
    echo "###SNMP### $i $(date +%s%N)" >> $logfile
    cat /proc/net/snmp >> $logfile
    sleep $interval
done
