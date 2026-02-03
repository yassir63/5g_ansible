#!/usr/bin/env bash
#set -euo pipefail


############################
# CLI OPTIONS
############################

DRY_RUN=false
NO_RESERVATION=false
EXTRA_VARS_ARRAY=()

usage() {
  echo "Usage: $0 [options]"
  echo ""
  echo "-i, --inventory <name>   Create ./inventory/<name>/hosts.ini instead of the default one"
  echo "-p, --profile5g <name>   Use group_vars/all/5g_profile_<name>.yaml specific 5G profile"
  echo "-e <vars>                Extra ansible vars, e.g.:"
  echo "     -e \"oai_gnb_mode=cudu\" -e \"no_boot=true\""
  echo "--dry_run                Only print ansible commands"
  echo "--no-reservation         Skip node/R2lab reservations"
  echo "-h, --help               Show help"
}

run_cmd() {
  if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
    echo "🔹 Running: $*"
    "$@"
    local status=$?
    if [[ $status -ne 0 ]]; then
      echo "❌ Command failed with exit code $status: $*"
      # Optionnel : exit $status
    fi
    return $status
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -i|--inventory)
        shift
        inv="$1"
        inv_dir="./inventory/${inv}"
        inv_file="${inv_dir}/hosts.ini"

        if [[ ! -f "$inv_file" ]]; then
          read -rp "Inventory $inv_file does not exist. Create it? [y/N]: " c
          if [[ "$c" =~ ^[Yy]$ ]]; then
            mkdir -p "$inv_dir"
            : > "$inv_file"
          else
            exit 1
          fi
        fi

        NAME_INVENTORY="$inv"
        INVENTORY="$inv_file"
        ;;

      -p|--profile5g)
        shift
        prof="$1"
        file="group_vars/all/5g_profile_${prof}.yaml"
        [[ ! -f "$file" ]] && { echo "❌ 5G Profile ${prof} not found"; exit 1; }
        PROFILE_5G="$prof"
        ;;

      -e|--extra-vars)
	shift
	EXTRA_VARS_ARRAY+=("$1")
	;;
      
      --dry_run)
	DRY_RUN=true
	;;
      
      --no-reservation)
	NO_RESERVATION=true
	;;
      
      -h|--help)
	usage; exit 0
	;;
      
      *)
	echo "Unknown option $1"; usage; exit 1
	;;
    esac
    shift
  done
}

############################
# FUNCTIONS
############################

init_defaults_and_banner() {

RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
CYAN="\033[1;36m"
RESET="\033[0m"

DEFAULT_DURATION="120"
DEFAULT_CORE_NODE="sopnode-f2"
DEFAULT_RAN_NODE="sopnode-f3"
DEFAULT_MONITOR_NODE="sopnode-f1"

DEFAULT_PROFILE_5G="default"
DEFAULT_INVENTORY="default"

DEFAULT_CORE="open5gs"
DEFAULT_RAN="oai"
DEFAULT_PLATFORM="r2lab"
DEFAULT_RU="jaguar"
DEFAULT_LIST_UE="qhat01"

PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"

NAME_INVENTORY="${NAME_INVENTORY:-$DEFAULT_INVENTORY}"
INVENTORY="${INVENTORY:-./inventory/${NAME_INVENTORY}/hosts.ini}"

echo -e "${CYAN}\
    ____  ____ __    _   __ __       ____________   ____             __               ______            __
   / __ \/  _/   |  / | / /   |     / ____/ ____/  / __ \___  ____  / /___  __  __   /_  __/___  ____  / /   
  / / / // // /| | /  |/ / /| |    /___ \/ / __   / / / / _ \/ __ \/ / __ \/ / / /    / / / __ \/ __ \/ /    
 / /_/ // // ___ |/ /|  / ___ |   ____/ / /_/ /  / /_/ /  __/ /_/ / / /_/ / /_/ /    / / / /_/ / /_/ / /     
/_____/___/_/  |_/_/ |_/_/  |_|  /_____/\____/  /_____/\___/ .___/_/\____/\__, /    /_/  \____/\____/_/      
                                                          /_/            /____/                              
${RESET}"

}

############################
# USER INPUTS (UNCHANGED)
############################

collect_user_inputs() {

# ========== User Inputs ==========

# Select Core
# Make Open5Gs the default if the user just presses enter
echo ""
echo "Which CORE do you want to deploy? (default: ${DEFAULT_CORE})"
echo "1) OAI"
echo "2) Open5Gs"
read -rp "Enter choice [1-2]: " core_choice
if [[ -z "$core_choice" ]]; then
  core=${DEFAULT_CORE}
else
  case "${core_choice}" in
    1) core="oai" ;;
    2) core="open5gs" ;;
    *) echo "❌ Invalid choice"; exit 1 ;;
  esac
fi

# Select Core Node
# Make sopnode-f2 the default if the user just presses enter
echo ""
echo "Select the node to deploy CORE ($core) on (default: ${DEFAULT_CORE_NODE}):"
echo "1) sopnode-f1"
echo "2) sopnode-f2"
echo "3) sopnode-f3"
echo "4) sopnode-w3"
read -rp "Enter choice [1-4]: " core_node_choice
if [[ -z "${core_node_choice}" ]]; then
  core_node=${DEFAULT_CORE_NODE}
else
  case "${core_node_choice}" in
    1) core_node="sopnode-f1" ;;
    2) core_node="sopnode-f2" ;;
    3) core_node="sopnode-f3" ;;
    4) core_node="sopnode-w3" ;;
    *) echo "❌ Invalid core node"; exit 1 ;;
  esac
fi

# Select RAN
if [[ "$core" == "oai" ]]; then
  # If OAI core is selected, only OAI RAN is supported
  echo ""
  echo "ℹ️ Only OAI RAN is supported with OAI Core"
  ran="oai"
else
  # Make OAI RAN the default if the user just presses enter
  echo ""
  echo "Which RAN do you want to deploy? (default: ${DEFAULT_RAN})"
  echo "1) OAI"
  echo "2) srsRAN"
  echo "3) UERANSIM"
  read -rp "Enter choice [1-3]: " ran_choice
  if [[ -z "${ran_choice}" ]]; then
    ran=${DEFAULT_RAN}
  else
    case "${ran_choice}" in
      1) ran="oai" ;;
      2) ran="srsRAN" ;;
      3) ran="ueransim" ;;
      *) echo "❌ Invalid choice"; exit 1 ;;
    esac
  fi
fi

# Select RAN Node
# Make sopnode-f3 the default if the user just presses enter
echo ""
echo "Select the node to deploy RAN ($ran) on (default: ${DEFAULT_RAN_NODE}):"
echo "1) sopnode-f1"
echo "2) sopnode-f2"
echo "3) sopnode-f3"
read -rp "Enter choice [1-3]: " ran_node_choice
if [[ -z "${ran_node_choice}" ]]; then
  ran_node=${DEFAULT_RAN_NODE}
else
  case "${ran_node_choice}" in
    1) ran_node="sopnode-f1" ;;
    2) ran_node="sopnode-f2" ;;
    3) ran_node="sopnode-f3" ;;
    *) echo "❌ Invalid RAN node"; exit 1 ;;
  esac
fi

# Select Monitoring Node (only if not OAI core with UERANSIM RAN and if user wants it)
monitoring_enabled=false
monitor_node=""
if [[ "$core" != "oai" && "$ran" != "ueransim" ]]; then
  echo ""
  read -rp "Do you want to deploy a monitoring node? [y/N]: " mon_choice
  if [[ "$mon_choice" =~ ^[Yy]$ ]]; then
    # Select Monitoring Node
    # Make sopnode-f1 the default if the user just presses enter
    monitoring_enabled=true
    echo ""
    echo "Select the node to deploy Monitoring on (default: ${DEFAULT_MONITOR_NODE}):"
    echo "1) sopnode-f1"
    echo "2) sopnode-f2"
    echo "3) sopnode-f3"
    read -rp "Enter choice [1-3]: " monitor_node_choice
    if [[ -z "${monitor_node_choice}" ]]; then
      monitor_node=${DEFAULT_MONITOR_NODE}
    else
      case "${monitor_node_choice}" in
        1) monitor_node="sopnode-f1" ;;
        2) monitor_node="sopnode-f2" ;;
        3) monitor_node="sopnode-f3" ;;
        *) echo "❌ Invalid Monitoring node"; exit 1 ;;
      esac
    fi
  fi
fi

# Select Platform
# Make r2lab the default if the user just presses enter
echo ""
echo "Which PLATFORM do you want to deploy on? (default: ${DEFAULT_PLATFORM})"
echo "1) Real radio devices on the R2lab platform"
echo "2) Fake RAN only (e.g., rfsim)"
read -rp "Enter choice [1-2]: " platform_choice
if [[ -z "$platform_choice" ]]; then
  platform=${DEFAULT_PLATFORM}
else
  case "$platform_choice" in
    1) platform="r2lab" ;;
    2) platform="rfsim"; fhi72=false ;;
    *) echo "❌ Invalid choice"; exit 1 ;;
  esac
fi

R2LAB_RU="$platform" # if rfsim, RU is "rfsim"
R2LAB_UES=()

# If R2Lab platform is selected, ask for RU and UEs
if [[ "$platform" == "r2lab" ]]; then
  R2LAB_RUs=("benetel1" "benetel2" "jaguar" "panther" "n300" "n320")
  # Select RU
  # Make jaguar the default if the user just presses enter
  echo ""
  echo "Select the RU to use (default: ${DEFAULT_RU}):"
  for i in "${!R2LAB_RUs[@]}"; do
    echo "$((i + 1))) ${R2LAB_RUs[i]}"
  done
  read -rp "Enter your choice: " ru_choice
  if [[ -z "$ru_choice" ]]; then
    R2LAB_RU=${DEFAULT_RU}
  else
    if [[ "$ru_choice" -ge 1 && "$ru_choice" -le "${#R2LAB_RUs[@]}" ]]; then
      R2LAB_RU="${R2LAB_RUs[$((ru_choice - 1))]}"
    else
      echo "❌ Invalid RU choice: $ru_choice"
      exit 1
    fi
  fi
  echo "RU is $R2LAB_RU"
  case "${R2LAB_RU}" in
      "benetel1"|"benetel2")
	  echo "Currently Benetel scenarios mandates OAI core and OAI ran on sopnode-f3, enforcing parameters..."
	  core="oai"
	  ran="oai"
	  ran_node="sopnode-f3"
	  fhi72=true
          ;;
      *)
	  fhi72=false
	  ;;
  esac

  QHATS=("qhat01" "qhat02" "qhat03" "qhat10" "qhat11")
  # Select UEs
  # Allow multiple selections
  # Make qhat01 the default if the user just presses enter
  echo ""
  echo "Select the UEs to use (you can select multiple separated by spaces, default: ${DEFAULT_LIST_UE}):"
  for i in "${!QHATS[@]}"; do
    echo "$((i + 1))) ${QHATS[i]}"
  done
  read -rp "Enter your choices: " -a ue_choices
  if [[ "${#ue_choices[@]}" -eq 0 ]]; then
    R2LAB_UES=("${DEFAULT_LIST_UE}")
  else
    for choice in "${ue_choices[@]}"; do
      if [[ "$choice" -ge 1 && "$choice" -le "${#QHATS[@]}" ]]; then
        R2LAB_UES+=("${QHATS[$((choice - 1))]}")
      else
        echo "❌ Invalid UE choice: $choice"
        exit 1
      fi
    done
  fi
fi

# Store the R2Lab slice name (usename) as well as email and passowrd for future use
R2LAB_CONFIG="./.r2lab_config"
if [[ -f "$R2LAB_CONFIG" ]]; then
  source "$R2LAB_CONFIG"
else
  echo ""
  read -rp "Enter your R2Lab username (slice name): " R2LAB_USERNAME
  read -rp "Enter your R2Lab email: " R2LAB_EMAIL
  read -rsp "Enter your R2Lab password: " R2LAB_PASSWORD
  echo
  cat > "$R2LAB_CONFIG" <<EOF
R2LAB_USERNAME="$R2LAB_USERNAME"
R2LAB_EMAIL="$R2LAB_EMAIL"
R2LAB_PASSWORD="$R2LAB_PASSWORD"
EOF
  chmod 600 "$R2LAB_CONFIG"
fi
}

############################
# OPTIONAL SCENARIOS
############################

optional_scenarios() {

# ========== Optional Scenarios ==========
# Available scenarios:
# 1) Default Iperf Test whithout interference. Will run only on one UE, assumed to be already connected to the network (only if R2Lab platform is used, and at least one UE is selected).
# 2) Parallel Iperf Test without interference. Will run one the first 4 UEs, assumed to be already connected to the network (only if R2Lab platform is used, and at least 4 UEs are selected).
# 3) RFSIM Iperf Test. Will run on 2 OAI-NR UEs simulated on RFSIM (only if RFSIM platform is used and RAN is OAI).
# 4) Interference Test. Will run only on one UE, assumed to be already connected to the network (only if R2Lab platform is used, and at least one UE is selected).

# Based on the selected variables, ask the user if they want to run one of the optional scenarios after deployment. (Only one scenario can be selected).

run_scenario=false
# Ask the user if they want to run an optional scenario
echo ""
read -rp "Do you want to run an optional scenario after deployment? [y/N]: " scenario_choice
if [[ "$scenario_choice" =~ ^[Yy]$ ]]; then
  echo ""
  echo "Select the scenario to run:"
  options=()
  if [[ "$platform" == "r2lab" && "${#R2LAB_UES[@]}" -ge 1 ]]; then
    options+=("Default Iperf Test (without interference)")
  fi
  if [[ "$platform" == "r2lab" && "${#R2LAB_UES[@]}" -ge 4 ]]; then
    options+=("Parallel Iperf Test (without interference)")
  fi
  if [[ "$platform" == "rfsim" && "$ran" == "oai" ]]; then
    options+=("RFSIM Iperf Test")
  fi
  if [[ "$platform" == "r2lab" && "${#R2LAB_UES[@]}" -ge 1 ]]; then
    options+=("Interference Test")
  fi

  for i in "${!options[@]}"; do
    echo "$((i+1))) ${options[$i]}"
  done

  read -rp "Enter your choice: " scenario_choice

  if [[ "$scenario_choice" =~ ^[0-9]+$ ]] &&
    ((scenario_choice >= 1 && scenario_choice <= ${#options[@]})); then
    scenario="${options[$((scenario_choice - 1))]}"
    echo "Selected scenario: $scenario"
    run_scenario=true
  else
    echo "❌ Invalid choice"
  fi
fi

# ========== Iperf Tests Setup (without interference) ==========
# For the normal iperf tests without interference, we do not need any additional user inputs, since the UEs are assumed to be already connected to the network after deployment.
# We sill use the run_iperf_test.sh script to run the selected iperf test scenario after deployment.
run_iperf_test=false
if [[ "$run_scenario" == true && ( "$scenario" == "Default Iperf Test (without interference)" || "$scenario" == "Parallel Iperf Test (without interference)" || "$scenario" == "RFSIM Iperf Test" ) ]]; then
  run_iperf_test=true
fi
}

############################
# INTERFERENCE SETUP
############################

interference_setup() {

# ========== Interference Test Setup ==========
run_interference_test=false
# If the user selected the Interference Test scenario, ask for additional parameters
if [[ "$run_scenario" == true && "$scenario" == "Interference Test" ]]; then
  run_interference_test=true
  USRPs=("n300" "n320" "b210" "b205mini")
  # Remove the RU used for RAN from the list of available USRPs for interference if it is a USRP
  for i in "${!USRPs[@]}"; do
    if [[ "${USRPs[i]}" == "$R2LAB_RU" ]]; then
      unset 'USRPs[i]'
    fi
  done
  echo ""
  echo "Select the USRP to use for interference generation:"
  for i in "${!USRPs[@]}"; do
    echo "$((i+1))) ${USRPs[$i]}"
  done
  read -rp "Enter your choice: " choice
  if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#USRPs[@]} )); then
    noise_usrp="${USRPs[$((choice-1))]}"
    echo "Selected USRP: $noise_usrp"
  else
    echo "❌ Invalid choice"
    exit 1
  fi
  VIZ_USRPs=("b210" "b205mini")
  # Remove the interference USRP from the list of available USRPs and ask user to select one for spectrum visualization (if wanted)
  for i in "${!VIZ_USRPs[@]}"; do
    if [[ "${VIZ_USRPs[i]}" == "$noise_usrp" ]]; then
      unset 'VIZ_USRPs[i]'
    fi
  done
  echo ""
  read -rp "Do you want to setup spectrum visualization using a second USRP? [y/N]: " viz_choice
  if [[ "$viz_choice" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Select the USRP to use for spectrum visualization:"
    for i in "${!VIZ_USRPs[@]}"; do
      echo "$((i+1))) ${VIZ_USRPs[$i]}"
    done
    read -rp "Enter your choice: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#VIZ_USRPs[@]} )); then
      viz_usrp="${VIZ_USRPs[$((choice-1))]}"
      echo "Selected USRP for visualization: $viz_usrp"
    else
      echo "❌ Invalid choice"
      exit 1
    fi
  fi
  # Set MODE for interference test to TDD if OAI RAN is used, FDD if srsRAN RAN is used
  if [[ "$ran" == "oai" ]]; then
    echo "Setting MODE to TDD for interference test"
    echo ""
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
    echo ""
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
}


############################
# PRINT SUMMARY
############################

print_summary() {

echo
echo "========== SUMMARY =========="
echo "Core:        $core on ${core_node}"
echo "RAN:         $ran on ${ran_node}"
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
if [[ "$run_iperf_test" == true ]]; then
  echo "Iperf Test: enabled"
  echo "  Scenario: $scenario"
  case "$scenario" in
    "Default Iperf Test (without interference)")
      echo "Will run iperf on ${R2LAB_UES[0]} for 5 minutes in downlink then uplink (10 minutes in total for the scenario)"
      ;;
    "Parallel Iperf Test (without interference)")
      echo "Will run a bidirectional iperf on ${R2LAB_UES[0]}, ${R2LAB_UES[1]}, ${R2LAB_UES[2]} and ${R2LAB_UES[3]} respectively for 5 minutes each, with an in-between wait time of 100 seconds (10 minutes in total for the scenario)"
      ;;
    "RFSIM Iperf Test")
      echo "Will run iperf on OAI-NR-UE1 then OAI-NR-UE2 for 200 seconds each with an in-between wait time of 100 seconds in downlink then uplink (10 minutes in total for the scenario)"
      ;;
  esac
fi

echo "============================="
echo  
}

############################
# HELPER FUNCTIONS
############################

# ========== Helper Functions ==========
# Function to determine IP suffix based on node
get_ip_suffix() {
  case "$1" in
    sopnode-f1) echo "76" ;;
    sopnode-f2) echo "77" ;;
    sopnode-f3) echo "95" ;;
    sopnode-w3) echo "71" ;;
    *) echo "XX" ;;
  esac
}

# Function to determine storage based on node
get_storage() {
  case "$1" in
    sopnode-f1 | sopnode-f2 | sopnode-w3) echo "sda1" ;;
    sopnode-f3) echo "sdb2" ;;
    *) echo "❌ unknown" ;;
  esac
}

# Function to determine NIC
get_nic() {
  case "$1" in
      sopnode-f1 | sopnode-f2)
	  echo "ens2f1" ;;
      sopnode-f3)
	  case "$R2LAB_RU" in
	      "benetel1"|"benetel2")
		  echo "ens15f1np1" ;;
	      *)
		  echo "ens15f1" ;;
	  esac ;;
      sopnode-w3)
	  echo "enp59s0f1np1" ;;
      *) echo "❌ unknown"
  esac
}

# Function to get fit info from usrp id
get_fit_info() {
  case "$1" in
    b210) echo "fit02 2 b210" ;;
    b205mini) echo "fit08 8 b205" ;;
    *) echo "" ;; # n300/n320 -> no direct fit node
  esac
}



############################
# INVENTORY GENERATION
############################

generate_inventory() {

echo "Generating ${INVENTORY}..."

# Build faraday line (may include interference params)
faraday_opts="faraday.inria.fr ansible_user=$R2LAB_USERNAME"
if [[ "${run_interference_test:-}" == true ]]; then
  # add interference params
  # Use the actual noise USRP id for faraday if it's an RU (n300/n320), otherwise use "fit" for b210/b205 variants
  if [[ "$noise_usrp" == "n300" || "$noise_usrp" == "n320" ]]; then
    faraday_interference_usrp="$noise_usrp"
  else
    faraday_interference_usrp="fit"
  fi
  faraday_opts="$faraday_opts interference_usrp=$faraday_interference_usrp gain=$GAIN noise_bandwidth=$NOISE_BANDWIDTH"
  if [[ "${MODE:-}" == "TDD" ]]; then
    faraday_opts="$faraday_opts freq=$FREQ"
  else
    faraday_opts="$faraday_opts freq_ul=$FREQ_UL freq_dl=$FREQ_DL"
  fi
fi

cat > "$INVENTORY" <<EOF
[webshell]
localhost ansible_connection=local

[core_node]
${core_node} ansible_user=root nic_interface=$(get_nic "${core_node}") ip=172.28.2.$(get_ip_suffix "${core_node}") storage=$(get_storage "${core_node}")

[ran_node]
${ran_node} ansible_user=root nic_interface=$(get_nic "${ran_node}") ip=172.28.2.$(get_ip_suffix "${ran_node}") storage=$(get_storage "${ran_node}") boot_mode=live

[monitor_node]
EOF

if [[ "$monitoring_enabled" == true ]]; then
    cat >> "$INVENTORY" <<EOF
${monitor_node} ansible_user=root nic_interface=$(get_nic "${monitor_node}") ip=172.28.2.$(get_ip_suffix "${monitor_node}") storage=$(get_storage "${monitor_node}")
EOF
fi

if [[ "$platform" == "r2lab" ]]; then
cat >> "$INVENTORY" <<EOF

[faraday]
$faraday_opts

[qhats]
EOF
fi

if [[ "$platform" == "r2lab" ]]; then
    for ue in "${R2LAB_UES[@]}"; do
	echo "$ue ansible_host=$ue ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' mode=mbim" >> "$INVENTORY"
    done
fi

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
  local name="$1" num="$2" usrp="$3"
  fit_lines+=("$name ansible_host=$name ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=$num fit_usrp=$usrp")
}

if [[ "${run_interference_test:-}" == true ]]; then
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
    cat >> "$INVENTORY" <<EOF

[fit_nodes]
# no FIT nodes required for n300/n320-only interference. Add fit nodes if you want visualization.
# Example:
# fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=2 fit_usrp=b210
EOF
  else
    cat >> "$INVENTORY" <<EOF

[fit_nodes]
EOF
    for line in "${fit_lines[@]}"; do
      echo "$line" >> "$INVENTORY"
    done
  fi

else
  # not running interference test: keep original default fit02 entry (as in previous script)
  cat >> "$INVENTORY" <<EOF

#[fit_nodes]
#fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=$R2LAB_USERNAME@faraday.inria.fr' fit_number=2 fit_usrp=b210
EOF
fi

cat >> "$INVENTORY" <<EOF

[sopnodes:children]
core_node
ran_node
EOF
if [[ "$monitoring_enabled" == true ]]; then
  echo "monitor_node" >> "$INVENTORY"
fi

cat >> "$INVENTORY" <<EOF

[k8s_workers:children]
ran_node
EOF
if [[ "$monitoring_enabled" == true ]]; then
  echo "monitor_node" >> "$INVENTORY"
fi


# Append useful variables
cat >> "$INVENTORY" <<EOF

[all:vars]
# ---- CORE / RAN type ----
core="$core"
ran="$ran"

# ---- Node aliases ----
core_node_name="${core_node}"
ran_node_name="${ran_node}"
EOF
if [[ "$monitoring_enabled" == true ]]; then
    cat >> "$INVENTORY" <<EOF
monitor_node_name="${monitor_node}"
EOF
fi
cat >> "$INVENTORY" <<EOF
faraday_node_name="faraday.inria.fr"

# ---- RRU information ----
rru="${R2LAB_RU}"

# ---- RRU families ----
fhi72=${fhi72}
aw2s=$( [[ "${R2LAB_RU}" == "jaguar" || "${R2LAB_RU}" == "panther" ]] && echo true || echo false )

# ---- hosts variants for RAN ----
f3_ran=$( [[ "${ran_node}" == "sopnode-f3" ]] && echo true || echo false )

# ---- Other boolean parameters
# bridge_enabled is true if OVS bridge required between core_node and ran_node
bridge_enabled=$( [[ "${fhi72}" == "false" ]] && echo true || echo false )
monitoring_enabled=${monitoring_enabled}
EOF

}

############################
# RESERVATIONS
############################

reserve_nodes() {
  [[ "$NO_RESERVATION" == true ]] && return

  # ========== Reserve Nodes on SLICES ==========
  # Create a calendar entry for the required nodes with the command: 
  # pos calendar create -d <duration in minutes> -s "now" <node/nodes separated by space>
  # Keep the outputed reservation ID to delete it later if needed.
  # Try to reserve for 2 hours (120 minutes) by default, if it fails, try with 1 hour (60 minutes)
  # If it still fails, ask the user if they want to ignore and continue (not recommended) or exit the script.
  echo ""
  echo "Reserving nodes on SLICES..."
  nodes_to_reserve=("${core_node}" "${ran_node}")
  if [[ "$monitoring_enabled" == true ]]; then
    nodes_to_reserve+=("${monitor_node}")
  fi
  # Remove duplicates
  nodes_to_reserve=($(printf "%s\n" "${nodes_to_reserve[@]}" | sort -u))
  reservation_id=""
  slices_reserved=false
  duration_minutes="${DEFAULT_DURATION}"

  # Try to reserve 
  echo "Trying to reserve nodes: ${nodes_to_reserve[*]} for $duration_minutes minutes..."
  reservation_output=$(pos calendar create -d "${duration_minutes}" -s "now" "${nodes_to_reserve[@]}" 2>&1)
  reservation_exit_code=$?

  echo "DEBUG:: just after pos calendar create, before if"
  if [[ $reservation_exit_code -ne 0 || "$reservation_output" == "-1" || -z "${reservation_output}" ]]; then
    # If it fails, try with 60 minutes
    echo "❌ Reservation for ${duration_minutes} minutes failed. Trying to reserve for 60 minutes..."
    duration_minutes=60
    reservation_output=$(pos calendar create -d "$duration_minutes" -s "now" "${nodes_to_reserve[@]}" 2>&1)
    reservation_exit_code=$?

    if [[ $reservation_exit_code -ne 0 || "$reservation_output" == "-1" || -z "${reservation_output}" ]]; then
      echo "❌ Reservation for 60 minutes failed too."
      echo "Error details: $reservation_output"
      read -rp "Do you want to ignore the reservation failure and continue? [y/N]: " ignore_choice
      if [[ ! "$ignore_choice" =~ ^[Yy]$ ]]; then
        echo "Exiting script."
        exit 1
      else
        echo "⚠️ Ignoring reservation failure and continuing..."
        slices_reserved=false
      fi
    else
      # The output is the reservation ID
      reservation_id="$reservation_output"
      echo "✅ Reservation successful. Reservation ID: $reservation_id. Reserved for $duration_minutes minutes."
      slices_reserved=true
    fi
  else
    # The output is the reservation ID
    reservation_id="$reservation_output"
    echo "✅ Reservation successful. Reservation ID: $reservation_id. Reserved for $duration_minutes minutes."
    slices_reserved=true
  fi
}



reserve_r2lab() {
[[ "$NO_RESERVATION" == true ]] && return

## ========== Reserve R2Lab if needed ==========
# If R2Lab platform is selected, reserve the testbed with the command:
# rhubarbe book <start(HH:MM)> <end(HH:MM)> -e <email> -p <password> -s <slice name> -v
# Reserve only if slices were reserved successfully and use the same duration.
if [[ "$platform" == "r2lab" && "$slices_reserved" == true ]]; then
  echo "Reserving R2Lab testbed..."
  start_time=$(date +"%Y-%m-%dT%H:%M")
  end_time=$(date -d "+$duration_minutes minutes" +"%Y-%m-%dT%H:%M")
  rhubarbe_output=$(ssh "${R2LAB_USERNAME}"@faraday.inria.fr "rhubarbe book '${start_time}' '${end_time}' -e '${R2LAB_EMAIL}' -p '${R2LAB_PASSWORD}' -s '${R2LAB_USERNAME}' -v; echo EXIT_CODE:\$?" 2>&1)

  # Extract the exit code from the output
  exit_code=$(echo "$rhubarbe_output" | grep "EXIT_CODE:" | cut -d: -f2)
  rhubarbe_output=$(echo "$rhubarbe_output" | grep -v "EXIT_CODE:")

  if [[ "$exit_code" -ne 0 ]]; then
    echo "❌ R2Lab reservation failed."
    echo "Error details: $rhubarbe_output"
    read -rp "Do you want to ignore the R2Lab reservation failure and continue? [y/N]: " ignore_r2lab_choice
    if [[ ! "$ignore_r2lab_choice" =~ ^[Yy]$ ]]; then
      # If R2Lab reservation fails and the user does not want to ignore, exit the script and delete the slices reservation
      # Using the command: pos calendar delete --id <reservation_id> <node/nodes separated by space>
      echo "Deleting sopnodes reservation with ID: $reservation_id ..."
      delete_output=$(pos calendar delete --id "$reservation_id" "${nodes_to_reserve[@]}" 2>&1)
      if [[ $? -ne 0 ]]; then
        echo "❌ Failed to delete sopnodes reservation."
        echo "Error details: $delete_output"
      else
        echo "Sopnodes reservation deleted successfully."
      fi
      echo "Exiting script."
      exit 1
    else
      echo "Ignoring R2Lab reservation failure and continuing..."
    fi
  else
    echo "✅ R2Lab reservation successful from $start_time to $end_time."
  fi
fi


}

############################
# DEPLOYMENT
############################

deploy() {

  ANSIBLE_EXTRA_ARGS=()
  ANSIBLE_EXTRA_ARGS+=(-e "fiveg_profile=${PROFILE_5G}")

  for ev in "${EXTRA_VARS_ARRAY[@]:-}"; do
    ANSIBLE_EXTRA_ARGS+=(-e "$ev")
  done
  
  echo "Launching deployment..."

  run_cmd ansible-galaxy install -r collections/requirements.yml

  if [[ "$platform" == "r2lab" ]]; then
      echo "ansible-playbook -i $INVENTORY ${ANSIBLE_EXTRA_ARGS[@]} playbooks/deploy_r2lab.yml &"
      run_cmd ansible-playbook -i "$INVENTORY" \
        "${ANSIBLE_EXTRA_ARGS[@]}" \
        playbooks/deploy_r2lab.yml 2>&1 | tee logs-r2lab.txt &
  fi

  echo "ansible-playbook -i $INVENTORY ${ANSIBLE_EXTRA_ARGS[@]} playbooks/deploy.yml"

  run_cmd ansible-playbook -i "$INVENTORY" \
    "${ANSIBLE_EXTRA_ARGS[@]}" \
    playbooks/deploy.yml 2>&1 | tee logs.txt

  echo ""
  echo "=========================================="
  echo "========== Deployment Completed =========="
  echo "=========================================="
  echo ""
}

############################
# SCENARIOS
############################

run_scenarios() {

# <<< YOUR ORIGINAL SCENARIO BLOCK UNCHANGED >>>
# ========== Run Optional Scenario ==========
# Use the run_iperf_test.sh for all scenarios, using the right flag, since it can also handle the interference.
# echo "  -s           Use OAI rfsim iperf test playbook"
# echo "  -d           Use default iperf test playbook"
# echo "  -p           Use parallel iperf test playbook"
# echo "  -i           Use interference iperf test playbook"
if [[ "$run_scenario" == true ]]; then
  echo "Running $scenario"
  case "$scenario" in
    "Default Iperf Test (without interference)")
      ./run_iperf_test.sh -d
      ;;
    "Parallel Iperf Test (without interference)")
      ./run_iperf_test.sh -p
      ;;
    "RFSIM Iperf Test")
      ./run_iperf_test.sh -s
      ;;
    "Interference Test")
      ./run_iperf_test.sh -i
      ;;
    *)
      echo "❌ Unknown iperf test scenario: $scenario"
      exit 1
      ;;
  esac
  echo ""
  echo "=========================================="
  echo "========== Scenario Completed =========="
  echo "=========================================="
  echo ""
fi

}

############################
# ACCESS INFO
############################

show_access_info() {

# ========== End of Script ==========
# Note: The user is responsible for deleting the reservations after use if needed.
# Show the commands to run to connect to the Grafana dashboard if monitoring is enabled.
if [[ "$monitoring_enabled" == true ]]; then
  echo ""
  echo "To access the Grafana Dashboard, follow these chained SSH port forwarding steps: "
  echo "Step 1: On your local machine, SSH into Duckburg with port forwarding: "
  echo ""
  # Show command to connect to Duckburg with user's username using whoami
  echo "ssh -L 8888:localhost:8888 -p 10022 $(whoami)@duckburg.net.in.tum.de"
  echo ""
  echo "Step 2: From Duckburg, SSH into the monitoring node with port forwarding: "
  echo ""
  echo "ssh -L 8888:localhost:32005 root@${monitor_node}"
  echo ""
  echo "Step 3: Now open your browser and go to http://localhost:8888 to access Grafana, using these credentials: "
  echo ""
  echo "Username: admin"
  echo "Password: monarch-operator"
  echo ""
fi

# Also show the commands to connect to the visualization USRP if interference test with visualization is enabled. (VNC viewer)
if [[ "$run_interference_test" == true && -n "${viz_usrp:-}" ]]; then
  echo ""
  echo ""
  echo "=========================================="
  echo ""
  echo "To access the Spectrum Visualization VNC session, launch SSH port forwarding and connect with a VNC viewer: "
  echo "Step 1: On your local machine, launch SSH tunnel with port forwarding: "
  # Get fit node name from (if viz_usrp is b210 -> fit02, if b205mini -> fit08)
  if [[ "$viz_usrp" == "b210" ]]; then
    fit_node="fit02"
  else
    fit_node="fit08"
  fi
  echo ""
  echo "ssh -t ${R2LAB_USERNAME}@faraday.inria.fr -L 5901:127.0.0.1:5901 ssh root@${fit_node} -L 5901:127.0.0.1:5901"
  echo ""
  echo "Step 2: Open your VNC viewer and connect to localhost:1 (using password: 1234567890)"
  echo ""
  echo "Note: to rerun this interference scenario, do: "
  echo ""
  echo "export MODE=${MODE}"
  echo "./run_iperf_test.sh -i --no-setup"
  echo ""
fi

}

############################
# MAIN
############################

parse_args "$@"

init_defaults_and_banner
collect_user_inputs
optional_scenarios
interference_setup
print_summary
generate_inventory
reserve_nodes
reserve_r2lab
deploy
run_scenarios
show_access_info

echo "✅ All done!"
