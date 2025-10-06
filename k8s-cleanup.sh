#!/bin/bash
# k8s-cleanup.sh
# Purpose: Fully clean up Kubernetes on a node, install required tools, and prepare for kubeadm init

set -euo pipefail

echo "Installing required tools..."
sudo apt update -y
sudo apt install -y lsof procps

echo "Stopping kubelet and containerd..."
sudo systemctl stop kubelet || true
sudo systemctl stop containerd || true
sudo systemctl disable kubelet || true

echo "Resetting kubeadm state..."
sudo kubeadm reset -f || true

echo "Killing leftover Kubernetes processes..."
sudo pkill -f kube || true
sudo pkill -f etcd || true

echo "Freeing used ports..."
for port in 6443 2379 2380 10257 10259; do
    pid=$(sudo lsof -t -i :$port || true)
    if [ -n "$pid" ]; then
        echo "Killing PID $pid on port $port"
        sudo kill -9 $pid
    fi
done

echo "Removing old Kubernetes data directories..."
sudo rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet /root/.kube
sudo rm -rf /var/lib/containerd/io.containerd.runtime.v2.task/k8s.io || true

echo "Restarting containerd..."
sudo systemctl restart containerd
sudo systemctl enable kubelet
sudo systemctl start kubelet

echo "Kubernetes cleanup done. Node is ready for kubeadm init."
