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
/_____/___/_/  |_/_/ |_/_/  |_|  /_____/\____/  /_____/\___/ .___/_/\____/\__, /    /_/  \____/\____/_/      
                                                          /_/            /____/                              
\033[0m"


get_ip_suffix() {
  case "$1" in
    sopnode-f1) echo "76" ;;
    sopnode-f2) echo "77" ;;
    sopnode-f3) echo "95" ;;
    *) echo "XX" ;;
  esac
}

echo "Which core do you want to deploy?"
echo "1) OAI"
echo "2) Open5GS"
read -rp "Enter choice [1-2]: " core_choice
case "$core_choice" in
  1) core="oai" ;;
  2) core="open5gs" ;;
  *) echo "Invalid core choice"; exit 1 ;;
esac

echo "Select the node to deploy CORE ($core) on:"
echo "1) sopnode-f1"
echo "2) sopnode-f2"
echo "3) sopnode-f3"
read -rp "Enter choice [1-3]: " core_node_choice
case "$core_node_choice" in
  1) core_node="sopnode-f1" ;;
  2) core_node="sopnode-f2" ;;
  3) core_node="sopnode-f3" ;;
  *) echo "Invalid core node"; exit 1 ;;
esac

if [[ "$core" == "oai" ]]; then
  echo ""
  echo "Only OAI RAN is supported with OAI Core"
  ran="oai"
else
  echo ""
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
fi

echo "Select the node to deploy RAN ($ran) on:"
echo "1) sopnode-f1"
echo "2) sopnode-f2"
echo "3) sopnode-f3"
read -rp "Enter choice [1-3]: " ran_node_choice
case "$ran_node_choice" in
  1) ran_node="sopnode-f1" ;;
  2) ran_node="sopnode-f2" ;;
  3) ran_node="sopnode-f3" ;;
  *) echo "Invalid ran node"; exit 1 ;;
esac

monitoring_enabled=false
monitor_node=""
if [[ "$core" != "oai" && "$ran" != "ueransim" ]]; then
  read -rp "Do you want to deploy a monitoring node? [y/N]: " mon_choice
  if [[ "$mon_choice" =~ ^[Yy]$ ]]; then
    monitoring_enabled=true
    echo "Select the node to deploy monitoring on:"
    echo "1) sopnode-f1"
    echo "2) sopnode-f2"
    echo "3) sopnode-f3"
    read -rp "Enter choice [1-3]: " mon_node_choice
    case "$mon_node_choice" in
      1) monitor_node="sopnode-f1" ;;
      2) monitor_node="sopnode-f2" ;;
      3) monitor_node="sopnode-f3" ;;
      *) echo "Invalid monitor node"; exit 1 ;;
    esac
  fi
fi

echo "Do you want to deploy using R2Lab or RFSIM?"
echo "1) r2lab"
echo "2) rfsim"
read -rp "Enter choice [1-2]: " platform_choice
case "$platform_choice" in
  1) platform="r2lab" ;;
  2) platform="rfsim" ;;
  *) echo "Invalid platform"; exit 1 ;;
esac

R2LAB_RU="$platform"
R2LAB_UES=()

if [[ "$platform" == "r2lab" ]]; then
  echo "Which RU do you want to use?"
  echo "1) jaguar"
  echo "2) panther"
  echo "3) n300"
  echo "4) n320"
  read -rp "Enter choice [1-4]: " ru_choice
  case "$ru_choice" in
    1) R2LAB_RU="jaguar" ;;
    2) R2LAB_RU="panther" ;;
    3) R2LAB_RU="n300" ;;
    4) R2LAB_RU="n320" ;;
    *) echo "Invalid RU choice"; exit 1 ;;
  esac

  echo "Which UEs do you want to use? (enter numbers separated by space)"
  echo "1) qhat01"
  echo "2) qhat02"
  echo "3) qhat03"
  echo "4) qhat10"
  echo "5) qhat11"
  read -rp "Enter choices: " ue_choices
  for choice in $ue_choices; do
    case "$choice" in
      1) R2LAB_UES+=("qhat01") ;;
      2) R2LAB_UES+=("qhat02") ;;
      3) R2LAB_UES+=("qhat03") ;;
      4) R2LAB_UES+=("qhat10") ;;
      5) R2LAB_UES+=("qhat11") ;;
      *) echo "Invalid UE choice: $choice"; exit 1 ;;
    esac
  done
fi

# Store the R2Lab username in a local file to avoid asking each time
R2LAB_CONFIG="./.r2lab_config"

if [[ -f "$R2LAB_CONFIG" ]]; then
  R2LAB_USERNAME=$(<"$R2LAB_CONFIG")
else
  echo "Enter your R2Lab username:"
  read -r R2LAB_USERNAME
  echo "$R2LAB_USERNAME" > "$R2LAB_CONFIG"
fi

# ========== Summary ==========
echo
echo "========== SUMMARY =========="
echo "Core:        $core on $core_node"
echo "RAN:         $ran on $ran_node"
[[ "$monitoring_enabled" == true ]] && echo "Monitoring:  enabled on $monitor_node" || echo "Monitoring:  disabled"
echo "Platform:    $platform"
[[ "$platform" == "r2lab" ]] && echo "RU:          $R2LAB_RU" && echo "UEs:         ${R2LAB_UES[*]}"
echo "============================="
echo

# Function to determine storage based on node
get_storage() {
  case "$1" in
    sopnode-f1 | sopnode-f2) echo "sda1" ;;
    sopnode-f3) echo "sdb2" ;;
    *) echo "unknown" ;;
  esac
}

get_ue_vars() {
  # usage: get_ue_vars <qhat>
  # relies on global $core and $ran already set
  : "${core:?core must be set}"; : "${ran:?ran must be set}"

  local qhat="${1,,}"
  local core_l="${core,,}"
  local ran_l="${ran,,}"

  local dnn upf_ip nssai

  # qhat → dnn
  case "$qhat" in
    qhat01|qhat03|qhat11) dnn="streaming" ;;
    qhat02|qhat10)        dnn="internet"  ;;
    *) echo "echo '❌ Unknown qhat: $qhat' >&2; return 1"; return 0 ;;
  esac

  # rules by core/ran
  if [[ "$core_l" == "oai" && "$ran_l" == "oai" ]]; then
    upf_ip="10.0.0.1"
    if [[ "$dnn" == "internet" ]]; then
      nssai="01.0xFFFFFF"
    else
      nssai="01.000001"
    fi

  elif [[ "$core_l" == "open5gs" && "$ran_l" == "oai" ]]; then
    if [[ "$dnn" == "internet" ]]; then
      upf_ip="10.41.0.1"; nssai="01.0xFFFFFF"
    else
      upf_ip="10.42.0.1"; nssai="01.000001"
    fi

  elif [[ "$core_l" == "open5gs" && "$ran_l" != "oai" ]]; then
    if [[ "$dnn" == "internet" ]]; then
      upf_ip="10.41.0.1"; nssai="01.000001"
    else
      upf_ip="10.42.0.1"; nssai="01.000002"
    fi

  else
    echo "echo '❌ Unsupported core/ran combo: core=$core ran=$ran' >&2; return 1"
    return 0
  fi

  # emit KEY=VALUE so caller can eval
  cat <<EOF
dnn=$dnn
upf_ip=$upf_ip
nssai=$nssai
EOF
}

# ========== Generate hosts.ini ==========
echo "Generating hosts.ini..."

cat > ./inventory/hosts.ini <<EOF
[webshell]
localhost ansible_connection=local

[core_node]
$core_node ansible_user=root nic_interface=ens2f1 ip=172.28.2.$(get_ip_suffix "$core_node") storage=$(get_storage "$core_node")

[ran_node]
$ran_node ansible_user=root nic_interface=ens2f1 ip=172.28.2.$(get_ip_suffix "$ran_node") storage=$(get_storage "$ran_node")
EOF

if [[ "$monitoring_enabled" == true ]]; then
cat >> ./inventory/hosts.ini <<EOF

[monitor_node]
$monitor_node ansible_user=root nic_interface=ens2f1 ip=172.28.2.$(get_ip_suffix "$monitor_node") storage=$(get_storage "$monitor_node")
EOF
fi

if [[ "$platform" == "r2lab" ]]; then
cat >> ./inventory/hosts.ini <<EOF

[faraday]
faraday.inria.fr ansible_user=$R2LAB_USERNAME rru=$R2LAB_RU conf=gnb.sa.band78.106prb.n310.7ds2u.conf

[qhats]
EOF
fi
# for ue in "${R2LAB_UES[@]}"; do
#   echo "$ue ansible_host=$ue ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' mode=mbim dnn=internet upf_ip=10.41.0.1 nssai=01.000001" >> ./inventory/hosts.ini
# done
# cat >> ./inventory/hosts.ini <<EOF


for ue in "${R2LAB_UES[@]}"; do
  # derive dnn/upf_ip/nssai from global core/ran + this UE
  eval "$(get_ue_vars "$ue")" || { echo "Failed to compute vars for $ue"; exit 1; }

  echo "$ue ansible_host=$ue ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' mode=mbim dnn=$dnn upf_ip=$upf_ip nssai=$nssai" \
    >> ./inventory/hosts.ini
done



cat >> ./inventory/hosts.ini <<EOF

[fit_nodes]
fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=2 fit_usrp=b210
EOF

cat >> ./inventory/hosts.ini <<EOF

[sopnodes:children]
core_node
ran_node
EOF

if [[ "$monitoring_enabled" == true ]]; then
  echo "monitor_node" >> ./inventory/hosts.ini
fi

cat >> ./inventory/hosts.ini <<EOF

[k8s_workers:children]
ran_node
EOF

if [[ "$monitoring_enabled" == true ]]; then
  echo "monitor_node" >> hosts.ini
fi

export RRU="$R2LAB_RU"
export monitoring_enabled="$monitoring_enabled"
export CORE="$core"
export RAN="$ran"

# Call appropriate deployment script
key="${core}-${ran}-${platform}"
script=""
case "$key" in
  oai-oai-rfsim)            script="deploy_oai_rfsim.sh" ;;
  oai-oai-r2lab)            script="deploy_oai_r2lab.sh" ;;
  open5gs-oai-rfsim)        script="deploy_open5gs_oai_rfsim.sh" ;;
  open5gs-oai-r2lab)        script="deploy_open5gs_oai_r2lab.sh" ;;
  open5gs-srsran-r2lab)     script="deploy_open5gs_srsRAN_r2lab.sh" ;;
  open5gs-srsran-rfsim)       script="deploy_open5gs_srsRAN_rfsim.sh" ;;
  open5gs-ueransim-rfsim)   script="deploy_open5gs_ueransim.sh" ;;
  *) echo "❌ Unknown deployment key: $key"; exit 1 ;;
esac

echo "Launching $script ..."
./deployments/$script
