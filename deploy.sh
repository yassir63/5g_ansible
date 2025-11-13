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

# ========== Interference Test Setup ==========
run_interference_test=false
# If at least one UE on R2Lab is used, give the option to run an interference test
if [[ "$platform" == "r2lab" && "${#R2LAB_UES[@]}" -gt 0 ]]; then
  read -rp "Do you want to run an interference test after deployment? [y/N]: " interference_choice
  if [[ "$interference_choice" =~ ^[Yy]$ ]]; then
    run_interference_test=true
    USRPs=("n300" "n320" "b210" "b205mini")
    # Remove the RU used for RAN from the list of available USRPs for interference if it is a USRP
    for i in "${!USRPs[@]}"; do
      if [[ "${USRPs[i]}" == "$R2LAB_RU" ]]; then
        unset 'USRPs[i]'
      fi
    done
    echo "Select the USRP to use for interference generation:"
    select noise_usrp in "${USRPs[@]}"; do
      if [[ -n "$noise_usrp" ]]; then
        echo "Selected USRP: $noise_usrp"
        break
      else
        echo "Invalid choice. Please try again."
      fi
    done
    VIZ_USRPs=("b210" "b205mini")
    # Remove the interference USRP from the list of available USRPs and ask user to select one for spectrum visualization (if wanted)
    for i in "${!VIZ_USRPs[@]}"; do
      if [[ "${VIS_USRPs[i]}" == "$noise_usrp" ]]; then
        unset 'VIZ_USRPs[i]'
      fi
    done
    read -rp "Do you want to setup spectrum visualization using a second USRP? [y/N]: " viz_choice
    if [[ "$viz_choice" =~ ^[Yy]$ ]]; then
      echo "Select the USRP to use for spectrum visualization:"
      select viz_usrp in "${VIZ_USRPs[@]}"; do
        if [[ -n "$viz_usrp" ]]; then
          echo "Selected USRP for visualization: $viz_usrp"
          break
        else
          echo "Invalid choice. Please try again."
        fi
      done
    fi
    # Set MODE for interference test to TDD if OAI RAN is used, FDD if srsRAN RAN is used
    if [[ "$ran" == "oai" ]]; then
      echo "Setting MODE to TDD for interference test"
      export MODE="TDD"
      # Ask user for interference parameters and export them: FREQ, GAIN, NOISE_BANDWIDTH (defaults are 3411.22M, 110, 20M)
      read -rp "Enter interference frequency [default: 3411.22M]: " freq_input
      FREQ="${freq_input:-3411.22M}"
      read -rp "Enter interference gain in dB [default: 110]: " gain_input
      GAIN="${gain_input:-110}"
      read -rp "Enter noise bandwidth in Hz [default: 20M]: " bw_input
      NOISE_BANDWIDTH="${bw_input:-20M}"
      export FREQ GAIN NOISE_BANDWIDTH
    else
      echo "Setting MODE to FDD for interference test"
      export MODE="FDD"
      # Ask user for interference parameters and export them: FREQ_UL, FREQ_DL, GAIN, NOISE_BANDWIDTH (defaults are 1747.5M, 1842.5M, 110, 5M)
      read -rp "Enter interference uplink frequency [default: 1747.5M]: " freq_ul_input
      FREQ_UL="${freq_ul_input:-1747.5M}"
      read -rp "Enter interference downlink frequency [default: 1842.5M]: " freq_dl_input
      FREQ_DL="${freq_dl_input:-1842.5M}"
      read -rp "Enter interference gain in dB [default: 110]: " gain_input
      GAIN="${gain_input:-110}"
      read -rp "Enter noise bandwidth in Hz [default: 5M]: " bw_input
      NOISE_BANDWIDTH="${bw_input:-5M}"
      export FREQ_UL FREQ_DL GAIN NOISE_BANDWIDTH
    fi
  fi
fi

# ========== Summary ==========
echo
echo "========== SUMMARY =========="
echo "Core:        $core on $core_node"
echo "RAN:         $ran on $ran_node"
[[ "$monitoring_enabled" == true ]] && echo "Monitoring:  enabled on $monitor_node" || echo "Monitoring:  disabled"
echo "Platform:    $platform"
[[ "$platform" == "r2lab" ]] && echo "RU:          $R2LAB_RU" && echo "UEs:         ${R2LAB_UES[*]}"
if [[ "$run_interference_test" == true ]]; then
  echo "Interference Test: enabled"
  echo "  Interference USRP: $noise_usrp"
  [[ -n "${viz_usrp:-}" ]] && echo "  Visualization USRP: $viz_usrp"
  echo "  MODE: $MODE"
  if [[ "$MODE" == "TDD" ]]; then
    echo "  FREQ: $FREQ"
  else
    echo "  FREQ_UL: $FREQ_UL"
    echo "  FREQ_DL: $FREQ_DL"
  fi
  echo "  GAIN: $GAIN"
  echo "  NOISE_BANDWIDTH: $NOISE_BANDWIDTH"
fi
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

# Build faraday line (may include interference params)
faraday_opts="faraday.inria.fr ansible_user=$R2LAB_USERNAME rru=$R2LAB_RU"
if [[ "${run_interference_test:-}" == true ]]; then
  # add interference params
  faraday_opts="$faraday_opts interference_usrp=$noise_usrp gain=$GAIN noise_bandwidth=$NOISE_BANDWIDTH"
  if [[ "${MODE:-}" == "TDD" ]]; then
    faraday_opts="$faraday_opts freq=$FREQ"
  else
    faraday_opts="$faraday_opts freq_ul=$FREQ_UL freq_dl=$FREQ_DL"
  fi
fi
# keep conf on a separate var so it's easy to change
faraday_conf="conf=gnb.sa.band78.106prb.n310.7ds2u.conf"

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
$faraday_opts $faraday_conf

[qhats]
EOF
fi

for ue in "${R2LAB_UES[@]}"; do
  # derive dnn/upf_ip/nssai from global core/ran + this UE
  eval "$(get_ue_vars "$ue")" || { echo "Failed to compute vars for $ue"; exit 1; }

  echo "$ue ansible_host=$ue ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' mode=mbim dnn=$dnn upf_ip=$upf_ip nssai=$nssai" \
    >> ./inventory/hosts.ini
done

# Build fit_nodes section.
# Rules:
# - If no interference test: keep the original default fit02 (b210).
# - If interference test:
#   - If noise_usrp is b210 -> primary=fit02
#   - If noise_usrp is b205mini -> primary=fit08
#   - If noise_usrp is n300/n320 and viz_usrp requested:
#       ensure fitnodes has two slots: first = the "other" fit node, second = the viz fit node
#   - If both noise and viz are b210/b205mini, first = noise, second = viz
#
# Map: b210 -> fit02 (fit_number=2, fit_usrp=b210)
#      b205mini -> fit08 (fit_number=8, fit_usrp=b205)
# (we use fit_usrp=b205 for b205mini as in examples)

fit_lines=()
append_fit() {
  local name="$1" num="$2 usrp="$3
  fit_lines+=("$name ansible_host=$name ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=$num fit_usrp=$usrp")
}

if [[ "${run_interference_test:-}" == true ]]; then
  # helper to get fit info from usrp id
  get_fit_info() {
    case "$1" in
      b210) echo "fit02 2 b210" ;;
      b205mini) echo "fit08 8 b205" ;;
      *) echo "" ;; # n300/n320 -> no direct fit node
    esac
  }

  noise_info="$(get_fit_info "$noise_usrp")"
  viz_info="$(get_fit_info "${viz_usrp:-}")"

  # If noise has a fit mapping, use it as primary
  if [[ -n "$noise_info" ]]; then
    read -r n_name n_num n_usrp <<<"$noise_info"
    # if viz is set and maps to a fit, and it's different, add viz as second
    if [[ -n "$viz_info" ]]; then
      read -r v_name v_num v_usrp <<<"$viz_info"
      # ensure primary != viz; if they are equal (shouldn't happen), swap with the other
      if [[ "$n_name" == "$v_name" ]]; then
        # pick the other available fit as secondary if possible
        if [[ "$n_name" == "fit02" ]]; then
          append_fit "fit02" 2 b210
          append_fit "fit08" 8 b205
        else
          append_fit "fit08" 8 b205
          append_fit "fit02" 2 b210
        fi
      else
        append_fit "$n_name" "$n_num" "$n_usrp"
        append_fit "$v_name" "$v_num" "$v_usrp"
      fi
    else
      # only noise fit present
      append_fit "$n_name" "$n_num" "$n_usrp"
    fi

  else
    # noise is n300/n320 (no fit mapping)
    if [[ -n "$viz_info" ]]; then
      # we need two slots, put the OTHER fit first and the viz fit second
      read -r v_name v_num v_usrp <<<"$viz_info"
      if [[ "$v_name" == "fit02" ]]; then
        append_fit "fit08" 8 b205
        append_fit "$v_name" "$v_num" "$v_usrp"
      else
        append_fit "fit02" 2 b210
        append_fit "$v_name" "$v_num" "$v_usrp"
      fi
    else
      # noise is n300/n320 and no viz requested -> do not add fit nodes (noise is RU-based)
      # To preserve previous behavior, we still add a commented example entry (no active fit nodes)
      :
    fi
  fi

  # If after all we have no fit_lines, still add a default example like original script did
  if [[ "${#fit_lines[@]}" -eq 0 ]]; then
    # no fit nodes to declare (e.g., n300/n320 noise only & no viz) -> add a commented example
    cat >> ./inventory/hosts.ini <<EOF

[fit_nodes]
# no FIT nodes required for n300/n320-only interference. Add fit nodes if you want visualization.
# Example:
# fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=2 fit_usrp=b210
EOF
  else
    cat >> ./inventory/hosts.ini <<EOF

[fit_nodes]
EOF
    for line in "${fit_lines[@]}"; do
      echo "$line" >> ./inventory/hosts.ini
    done
  fi

else
  # not running interference test: keep original default fit02 entry (as in previous script)
  cat >> ./inventory/hosts.ini <<EOF

[fit_nodes]
fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=2 fit_usrp=b210
EOF
fi

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
  echo "monitor_node" >> ./inventory/hosts.ini
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

echo "Deployment completed."

# Call interference test script if requested
if [[ "${run_interference_test:-}" == true ]]; then
  echo "Launching interference test script ..."
  ./scenarios/run_interference_test.sh
fi
