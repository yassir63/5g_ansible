#!/usr/bin/env bash
set -euo pipefail

# ============================
#   DIANA 5G Deploy Tool
# ============================


echo -e "\033[1;36m
    ____  ____ __    _   __ __       ____________   ____             __               ______            __
   / __ \/  _/   |  / | / /   |     / ____/ ____/  / __ \___  ____  / /___  __  __   /_  __/___  ____  / /   
  / / / // // /| | /  |/ / /| |    /___ \/ / __   / / / / _ \/ __ \/ / __ \/ / / /    / / / __ \/ __ \/ /    
 / /_/ // // ___ |/ /|  / ___ |   ____/ / /_/ /  / /_/ /  __/ /_/ / / /_/ / /_/ /    / / / /_/ / /_/ / /     
/_____/___/_/ |_/_/ |_/_/  |_|  /_____/\____/  /_____/\___/ .___/_/\____/\__ , /    /_/  \____/\____/_/      
                                                          /_/            /____/                              
\033[0m"


# Define available nodes
nodes=("sopnode-f1" "sopnode-f2" "sopnode-f3")

# Ask for CORE
echo "Which Core do you want to deploy?"
echo "1) OAI"
echo "2) Open5GS"
read -rp "Enter choice [1-2]: " core_choice
case "$core_choice" in
  1) core="oai" ;;
  2) core="open5gs" ;;
  *) echo "Invalid choice"; exit 1 ;;
esac

# Ask for Core Node
echo "Select the node for Core deployment:"
for i in "${!nodes[@]}"; do
  echo "$((i+1))) ${nodes[i]}"
done
read -rp "Enter choice [1-${#nodes[@]}]: " core_node_choice
core_node="${nodes[core_node_choice-1]}"

# Ask for RAN
echo "Which RAN do you want to deploy?"
echo "1) OAI"
echo "2) srsRAN"
echo "3) UERANSIM"
read -rp "Enter choice [1-3]: " ran_choice
case "$ran_choice" in
  1) ran="oai" ;;
  2) ran="srsran" ;;
  3) ran="ueransim" ;;
  *) echo "Invalid choice"; exit 1 ;;
esac

# Ask for RAN Node
echo "Select the node for RAN deployment:"
for i in "${!nodes[@]}"; do
  echo "$((i+1))) ${nodes[i]}"
done
read -rp "Enter choice [1-${#nodes[@]}]: " ran_node_choice
ran_node="${nodes[ran_node_choice-1]}"

# Ask for Monitoring
read -rp "Do you want to deploy a monitoring node? [y/N]: " monitor_enabled
monitor_node=""
if [[ "$monitor_enabled" =~ ^[Yy]$ ]]; then
  echo "Select the node for Monitoring deployment:"
  for i in "${!nodes[@]}"; do
    echo "$((i+1))) ${nodes[i]}"
  done
  read -rp "Enter choice [1-${#nodes[@]}]: " monitor_node_choice
  monitor_node="${nodes[monitor_node_choice-1]}"
fi

# Ask for platform (R2Lab or RFSIM)
echo "Do you want to deploy using R2Lab or RFSIM?"
echo "1) R2Lab"
echo "2) RFSIM"
read -rp "Enter choice [1-2]: " platform_choice
case "$platform_choice" in
  1) platform="r2lab" ;;
  2) platform="rfsim" ;;
  *) echo "Invalid choice"; exit 1 ;;
esac

# If R2Lab, ask for RU and UEs
if [[ "$platform" == "r2lab" ]]; then
  echo "Select RU for R2Lab:"
  echo "1) jaguar"
  echo "2) panther"
  echo "3) n300"
  echo "4) n320"
  read -rp "Enter choice [1-4]: " ru_choice
  case "$ru_choice" in
    1) rru="jaguar" ;;
    2) rru="panther" ;;
    3) rru="n300" ;;
    4) rru="n320" ;;
    *) echo "Invalid choice"; exit 1 ;;
  esac

  echo "Select UE(s) to use (choose one or more separated by space):"
  ues=("qhat01" "qhat02" "qhat03" "qhat11")
  for i in "${!ues[@]}"; do
    echo "$((i+1))) ${ues[i]}"
  done
  read -rp "Enter choice(s): " -a ue_choices
  selected_ues=()
  for i in "${ue_choices[@]}"; do
    selected_ues+=("${ues[i-1]}")
  done
fi

# Get IP suffix from node name (f1  ^f^r 76, f2  ^f^r 77, f3  ^f^r 95)
get_ip_suffix() {
  case "$1" in
    sopnode-f1) echo "76" ;;
    sopnode-f2) echo "77" ;;
    sopnode-f3) echo "95" ;;
    *) echo "XX" ;;
  esac
}

core_ip="172.28.2.$(get_ip_suffix "$core_node")"
ran_ip="172.28.2.$(get_ip_suffix "$ran_node")"
monitor_ip=""
if [[ -n "$monitor_node" ]]; then
  monitor_ip="172.28.2.$(get_ip_suffix "$monitor_node")"
fi

# Generate hosts.ini
cat > hosts.ini <<EOF
[webshell]
localhost ansible_connection=local

[core_node]
$core_node ansible_user=root nic_interface=ens2f1 ip=$core_ip storage=sda1

[ran_node]
$ran_node ansible_user=root nic_interface=ens2f1 ip=$ran_ip storage=sda1
EOF

if [[ -n "$monitor_node" ]]; then
cat >> hosts.ini <<EOF

[monitor_node]
$monitor_node ansible_user=root nic_interface=ens2f1 ip=$monitor_ip storage=sda1
EOF
fi

cat >> hosts.ini <<EOF

[sopnodes:children]
core_node
ran_node
monitor_node

[k8s_workers:children]
ran_node
monitor_node
EOF

# Show summary
echo ""
echo "===== Deployment Summary ====="
echo "Core:       ${core^^} on $core_node ($core_ip)"
echo "RAN:        ${ran^^} on $ran_node ($ran_ip)"
if [[ -n "$monitor_node" ]]; then
  echo "Monitoring: enabled on $monitor_node ($monitor_ip)"
else
  echo "Monitoring: not enabled"
fi
echo "Platform:   ${platform^^}"
if [[ "$platform" == "r2lab" ]]; then
  echo "RRU:        $rru"
  echo "UEs:        ${selected_ues[*]}"
fi
echo "=============================="
# Determine deployment script
key="${core}-${ran}-${platform}"
case "$key" in
  oai-oai-rfsim)
    script="deploy_oai_rfsim.sh"
    ;;
  oai-oai-r2lab)
    script="deploy_oai_r2lab.sh"
    ;;
  open5gs-srsran-rfsim)
    script="deploy_open5gs_srsRAN_zmq.sh"
    ;;
  open5gs-srsran-r2lab)
    script="deploy_open5gs_srsRAN_r2lab.sh"
    ;;
  open5gs-oai-rfsim)
    script="deploy_open5gs_oai_rfsim.sh"
    ;;
  open5gs-oai-r2lab)
    script="deploy_open5gs_oai_r2lab.sh"
    ;;
  open5gs-ueransim-rfsim)
    script="deploy_open5gs_ueransim.sh"
    ;;
  *)
    echo " ^}^l No matching deployment script for $key"
    exit 1
    ;;
esac

key="$(echo "${core}-${ran}-${platform}" | tr '[:upper:]' '[:lower:]' | sed 's/_/-/g')"

# Set RRU correctly based on platform
if [[ "$platform" == "rfsim" ]]; then
  RRU="rfsim"
else
  RRU="$ru"
fi
export RRU

# Run the correct deploy script
"$DEPLOY_DIR/deploy_${key}.sh"