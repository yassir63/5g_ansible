#!/usr/bin/env bash
#set -euo pipefail


############################
# CLI OPTIONS
############################

DRY_RUN=false
NO_RESERVATION=false
EXTRA_VARS_ARRAY=()
SKIP_INPUTS=false
SCENARIO_ONLY=false
REQUESTED_EXPERIMENT_MODE=""
REQUESTED_TCP_PAPER_SCENARIOS=""
REQUESTED_VALIDATION_SCENARIOS=""
REQUESTED_TARGET_SERVER=""
REQUESTED_PROMETHEUS_URL=""
REQUESTED_EXPERIMENT_DURATION=""
REQUESTED_VALIDATION_TCP_BITRATE=""
REQUESTED_VALIDATION_MTU_PING_SIZE=""
REQUESTED_MONITORING_LOKI=""

SCENARIO_RFSIM="Iperf RFSIM scenario without interference"
SCENARIO_R2LAB="Iperf R2lab scenario without interference"
SCENARIO_R2LAB_INTERFERENCE="Iperf R2lab scenario with interference"
SCENARIO_R2LAB_MULTI="Iperf R2lab scenario without interference, with multiple simultaneous UEs"
SCENARIO_R2LAB_PING="Ping R2lab scenario without interference, with multiple simultaneous UEs"
REQUESTED_CHURN_SUBSCRIBERS=""
REQUESTED_CHURN_COUNTS=""
CHURN_RUNS_VIA_DEPLOY=false

usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "-n, --no-input           Skip prompts and use the previous deployment setup"
    echo "                         (i.e. ./inventory/<name>/hosts.ini and ./inventory/<name>/.deployment.env)."
    echo "                         Use -i to select deployment setup other than default"
    echo "-i, --inventory <name>   Create ./inventory/<name>/hosts.ini instead of the default one"
    echo "-p, --profile5g <name>   Use group_vars/all/5g_profile_<name>.yaml specific 5G profile"
    echo "-e <vars>                Extra ansible vars, e.g.:"
    echo "     -e \"oai_gnb_mode=cudu\" -e \"no_boot=true\""
    echo "--dry-run                Only print ansible commands"
    echo "-r, --no-reservation     Skip node/R2lab reservations"
    echo "--no-auto-start          Only configure iperf scenario, don't start it after 5G deployment"
    echo "--scenario-only          Skip reservation/deployment and only run the selected scenario workflow"
    echo "--tcp-paper <names>      Run TCP paper scenarios and create experiment_analysis.ipynb"
    echo "                         <names> can be all or a comma-separated scenario list"
    echo "                         Current TCP paper scenarios:"
    echo "                         01_decomp_baseline_all_ues"
    echo "                         02_decomp_far_ue_radio"
    echo "                         03_decomp_upf_cpu_stress"
    echo "                         04_decomp_target_server_netem_delay"
    echo "                         05_fit02_interference_near_ul_dl"
    echo "                         06_fit28_spatial_control_near_ul_dl"
    echo "--validation <names>     Run latency validation and create paper figures/notebooks"
    echo "                         <names> can be all, v01_candidate_signal_baseline,"
    echo "                         v02_icmp_ping_correctness, v03_controlled_delay,"
    echo "                         v04_tc_pass_baseline, v05_tcp_icmp_parallel_median,"
    echo "                         v06_tcp_icmp_parallel_mtu_ping,"
    echo "                         or a comma-separated validation scenario list"
    echo "--target-server <node>   Bare-metal target server for iperf, e.g. sopnode-w3"
    echo "--prometheus-url <url>   Override Prometheus URL only if needed, e.g. http://172.28.2.76:30095"
    echo "--duration <seconds>     Override TCP scenario/validation traffic duration"
    echo "--validation-tcp-bitrate <rate>  Override validation TCP cap, e.g. 30Mb or 0"
    echo "--validation-mtu-ping-size <bytes>  Override v06 ICMP payload size; 1472 gives a 1500-byte IPv4 packet"
    echo "--with-loki              Keep Grafana Loki enabled with monitoring (default)"
    echo "--no-loki                Disable Grafana Loki log collection"
    echo "--ueransim-churn         Deploy/run the Open5GS UERANSIM attach/detach churn scenario"
    echo "--churn-subscribers <n> Number of generated churn subscribers"
    echo "--churn-counts <list>   Space-separated waves, e.g. \"10 50 100 200\""
    echo "-h, --help               Show help"
}


run_cmd() {
    if [[ "$DRY_RUN" == true ]]; then
      echo -e "\e[33m[DRY-RUN]\e[0m $*"
    else
      echo -e "\e[34m🔹 Running:\e[0m $*"

      # force colors for Ansible and similar tools
      ANSIBLE_FORCE_COLOR=true PY_COLORS=1 "$@"
      local status=$?

      if [[ $status -ne 0 ]]; then
        echo -e "\e[31m❌ Command failed with exit code $status:\e[0m $*"
#        exit $status  # optional
      fi

      return $status
    fi
}

run_logged_cmd() {
    local log_file="$1"
    shift

    if [[ "$DRY_RUN" == true ]]; then
      run_cmd "$@"
      return $?
    fi

    run_cmd "$@" 2>&1 | tee "$log_file"
    return "${PIPESTATUS[0]}"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -n|--no-input)
          SKIP_INPUTS=true
          ;;

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
        
        --dry-run)
          DRY_RUN=true
          ;;
        
        -r|--no-reservation)
          NO_RESERVATION=true
          ;;
        
        --no-auto-start)
          START_SCENARIO=false
          ;;

        --scenario-only)
          SCENARIO_ONLY=true
          NO_RESERVATION=true
          ;;

        --tcp-paper)
          shift
          REQUESTED_EXPERIMENT_MODE="tcp-paper"
          REQUESTED_TCP_PAPER_SCENARIOS="${1:-all}"
          ;;

        --validation)
          shift
          REQUESTED_EXPERIMENT_MODE="validation"
          REQUESTED_VALIDATION_SCENARIOS="${1:-all}"
          ;;

        --ueransim-churn)
          REQUESTED_EXPERIMENT_MODE="ueransim-churn"
          ;;

        --churn-subscribers)
          shift
          REQUESTED_CHURN_SUBSCRIBERS="${1:-}"
          ;;

        --churn-counts)
          shift
          REQUESTED_CHURN_COUNTS="${1:-}"
          ;;

        --target-server)
          shift
          REQUESTED_TARGET_SERVER="${1:-}"
          ;;

        --prometheus-url)
          shift
          REQUESTED_PROMETHEUS_URL="${1:-}"
          ;;

        --duration)
          shift
          REQUESTED_EXPERIMENT_DURATION="${1:-}"
          ;;

        --validation-tcp-bitrate)
          shift
          REQUESTED_VALIDATION_TCP_BITRATE="${1:-}"
          ;;

        --validation-mtu-ping-size)
          shift
          REQUESTED_VALIDATION_MTU_PING_SIZE="${1:-}"
          ;;

        --with-loki)
          REQUESTED_MONITORING_LOKI="true"
          ;;

        --no-loki)
          REQUESTED_MONITORING_LOKI="false"
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

normalize_validation_tcp_bitrate() {
    local value="${1:-}"
    value="${value//[[:space:]]/}"

    if [[ -z "$value" || "$value" == "0" ]]; then
      printf '%s' "$value"
    elif [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      printf '%sMb' "$value"
    elif [[ "$value" =~ ^[0-9]+([.][0-9]+)?[kKmMgGtT]$ ]]; then
      printf '%sb' "$value"
    else
      printf '%s' "$value"
    fi
}

extra_var_value() {
    local key="$1"
    local ev clean_ev
    for ev in "${EXTRA_VARS_ARRAY[@]:-}"; do
      clean_ev="${ev#--}"
      if [[ "$clean_ev" == "$key="* ]]; then
        printf '%s' "${clean_ev#*=}"
        return 0
      fi
    done
    return 1
}

extra_var_defined() {
    extra_var_value "$1" >/dev/null
}

append_cli_extra_vars() {
    local ev clean_ev churn_counts_value
    for ev in "${EXTRA_VARS_ARRAY[@]:-}"; do
      [[ -z "$ev" ]] && continue
      clean_ev="${ev#--}"
      if [[ "$clean_ev" == "ueransim_churn_counts="* ]]; then
        churn_counts_value="${clean_ev#*=}"
        ANSIBLE_EXTRA_ARGS+=(-e "{\"ueransim_churn_counts\":\"${churn_counts_value}\"}")
      else
        ANSIBLE_EXTRA_ARGS+=(-e "$clean_ev")
      fi
    done
}

############################
# FUNCTIONS
############################

init_defaults_and_banner() {

    #RED="\033[0;31m"
    #GREEN="\033[0;32m"
    #YELLOW="\033[1;33m"
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
    DEFAULT_RU="n300"
    DEFAULT_LIST_UE="qhat01"
    DEFAULT_UERANSIM_CHURN_REPO_URL="https://github.com/yassir63/open5gs-k8s.git"
    DEFAULT_UERANSIM_CHURN_REPO_BRANCH="ueransim-churn"
    DEFAULT_UERANSIM_CHURN_SUBSCRIBERS="200"
    DEFAULT_UERANSIM_CHURN_COUNTS="10 50 100 200"
    DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS="60"
    DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS="30"

    PROFILE_5G="${PROFILE_5G:-$DEFAULT_PROFILE_5G}"
  
    START_SCENARIO="${START_SCENARIO:-true}"

    NAME_INVENTORY="${NAME_INVENTORY:-$DEFAULT_INVENTORY}"
    INVENTORY="${INVENTORY:-./inventory/${NAME_INVENTORY}/hosts.ini}"
    DEPLOYMENT_ENV="${DEPLOYMENT_ENV:-./inventory/${NAME_INVENTORY}/.deployment.env}"
    if [[ "$SKIP_INPUTS" == true ]]; then
      if [[ -f "$DEPLOYMENT_ENV" ]]; then
        source "$DEPLOYMENT_ENV"
        if [[ "${monitoring_enabled:-false}" == true ]]; then
          monitoring_loki_enabled=true
          if [[ "${REQUESTED_MONITORING_LOKI:-}" == "false" ]]; then
            monitoring_loki_enabled=false
          fi
        else
          monitoring_loki_enabled=false
        fi
      else
        echo "❌ The deployment variables were not set. Run the script with prompting enabled or manualy ensure that the $DEPLOYMENT_ENV file is configured correctly."
        exit 1
      fi
    fi

    R2LAB_CONFIG="./.r2lab_config"

    DISTINCT_IPERF_SERVER=false
    DIR_LOGS="${DIR_LOGS:-LOGS}"
    mkdir -p ${DIR_LOGS}

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
    echo "3) Free5gc"
    read -rp "Enter choice [1-3]: " core_choice
    if [[ -z "${core_choice}" ]]; then
      core=${DEFAULT_CORE}
    else
      case "${core_choice}" in
        1) core="oai" ;;
        2) core="open5gs" ;;
        3) core="free5gc" ;;
        *) echo "❌ Invalid choice"; exit 1 ;;
      esac
    fi

    # Select Core Node
    # Make sopnode-f2 the default if the user just presses enter
    echo ""
    if [[ "${core_choice}" == 3 ]]; then
      echo "Warning: with free5gc core, deploy core and ran on 2 different nodes"
    fi
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

    # Select RAN Node
    # Make sopnode-f3 the default if the user just presses enter
    echo ""
    echo "Select the node to deploy RAN ($ran) on (default: ${DEFAULT_RAN_NODE}):"
    echo "1) sopnode-f1"
    echo "2) sopnode-f2"
    echo "3) sopnode-f3"
    echo "4) sopnode-w3"
    read -rp "Enter choice [1-4]: " ran_node_choice
    if [[ -z "${ran_node_choice}" ]]; then
      ran_node=${DEFAULT_RAN_NODE}
    else
      case "${ran_node_choice}" in
        1) ran_node="sopnode-f1" ;;
        2) ran_node="sopnode-f2" ;;
        3) ran_node="sopnode-f3" ;;
        4) ran_node="sopnode-w3" ;;
        *) echo "❌ Invalid RAN node"; exit 1 ;;
      esac
    fi
    if [[ "$core" == "free5gc" && "${core_node}" == "${ran_node}" ]]; then
      echo "❌ Invalid choice: with free5gc, use a different node for RAN."
      exit 1
    fi

    # Select Monitoring
    # Monitoring now deploys a lightweight Prometheus/Grafana stack. The legacy
    # Monarch roles remain in the repository, but deploy.sh no longer enables
    # the full Monarch stack.
    monitoring_enabled=false
    monitoring_loki_enabled=false
    monarch=false
    monitor_node=""
    if [[ "$core" == "open5gs" || "$ran" != "ueransim" ]]; then
      echo ""
      if [[ "$core" == "open5gs" && "$ran" == "ueransim" ]]; then
        echo "Monitoring is required for UERANSIM churn CPU, memory, and UE-mapper measurements."
      fi
      read -rp "Do you want to deploy monitoring? [y/N]: " mon_choice
      if [[ "$mon_choice" =~ ^[Yy]$ ]]; then
        monitoring_enabled=true
        monarch=false
        echo ""
        echo "Select the node to deploy lightweight Prometheus/Grafana on (default: ${DEFAULT_MONITOR_NODE}):"
        echo "1) sopnode-f1"
        echo "2) sopnode-f2"
        echo "3) sopnode-f3"
        echo "4) sopnode-w3"
        read -rp "Enter choice [1-4]: " monitor_node_choice
        if [[ -z "${monitor_node_choice}" ]]; then
          monitor_node=${DEFAULT_MONITOR_NODE}
        else
          case "${monitor_node_choice}" in
            1) monitor_node="sopnode-f1" ;;
            2) monitor_node="sopnode-f2" ;;
            3) monitor_node="sopnode-f3" ;;
            4) monitor_node="sopnode-w3" ;;
            *) echo "❌ Invalid Monitoring node"; exit 1 ;;
          esac
        fi
        monitoring_loki_enabled=true
        if [[ "${REQUESTED_MONITORING_LOKI:-}" == "false" ]]; then
          monitoring_loki_enabled=false
        fi
      fi
    fi

    # Select Platform
    # Make r2lab the default if the user just presses enter
    if [[ "$ran" != "ueransim" ]]; then
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
    else
      platform="rfsim"; fhi72=false
    fi

    R2LAB_RU="$platform" # if rfsim, RU is "rfsim"
    R2LAB_UES=()

    # If R2Lab platform is selected, ask for RU and UEs
    if [[ "$platform" == "r2lab" ]]; then
      if [[ "$ran" == "oai" ]]; then
        R2LAB_RUs=("benetel1" "benetel2" "jaguar" "panther" "n300" "n320")
      else # $ran == "srsRAN" for now, only n3xx and benetel RUs supported
        R2LAB_RUs=("n300" "n320" "benetel1" "benetel2")
      fi
      # Select RU
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
          if [[ "${ran_node}" != "sopnode-f3" ]]; then
            echo "❌ Invalid server used for RAN pods with benetel RU, only sopnode-f3 currently supported"
            exit 1
	  fi
          fhi72=true
          ;;
        *)
          fhi72=false
          ;;
      esac

      QHATS=("qhat01" "qhat02" "qhat03" "qhat10" "qhat11" "qhat21" "qhat22")
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

    # Store the R2Lab slice name (usename) as well as email and password for future use
    R2LAB_CONFIG="./.r2lab_config"
    if [[ -f "${R2LAB_CONFIG}" ]]; then
      source "${R2LAB_CONFIG}"
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
    cat > "$DEPLOYMENT_ENV" <<EOF
core="$core"
ran="$ran"
core_node="$core_node"
ran_node="$ran_node"
platform="$platform"
monitoring_enabled="$monitoring_enabled"
monitoring_loki_enabled="$monitoring_loki_enabled"
monarch="$monarch"
monitor_node="$monitor_node"
EOF
}

############################
# OPTIONAL SCENARIOS
############################

optional_scenarios() {

    # ========== Optional Scenarios ==========
    # Available scenarios:
    # - Iperf R2lab scenario without interference. Will run only on one UE, assumed to be already connected to the network (only if R2Lab platform is used, and at least one UE is selected).
    # - Iperf RFSIM scenario without interference. Will run on 2 OAI-NR UEs simulated on RFSIM (only if RFSIM platform is used and RAN is OAI).
    # - Iperf R2lab scenario with interference. Will run only on one UE, assumed to be already connected to the network (only if R2Lab platform is used, and at least one UE is selected).
    # - Iperf R2lab scenario with multiple UEs.  Will first test each UE individually, then all UEs simultaneously.  Tests both uplink and downlink, with TCP and UDP.
    # - Ping R2lab scenario with multiple UEs.  Will first test from each UE individually, then all UEs simultaneously.

    # Based on the selected variables, ask the user if they want to run one of the optional scenarios after deployment. (Only one scenario can be selected).

    run_scenario=false
    DISTINCT_IPERF_SERVER=false
    iperf_server_node=""
    paper_scenario_names="all"
    validation_scenario_names="all"
    paper_prometheus_url=""
    validation_prometheus_url=""
    paper_duration_override=""
    validation_duration_override=""
    validation_tcp_bitrate_override=""
    validation_mtu_ping_size_override=""
    requires_iperf_server=true
    ueransim_churn_repo_url="${DEFAULT_UERANSIM_CHURN_REPO_URL}"
    ueransim_churn_repo_branch="${DEFAULT_UERANSIM_CHURN_REPO_BRANCH}"
    ueransim_churn_subscribers="${DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}"
    ueransim_churn_counts="${DEFAULT_UERANSIM_CHURN_COUNTS}"
    ueransim_churn_settle_seconds="${DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}"
    ueransim_churn_between_seconds="${DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}"

    if churn_override="$(extra_var_value ueransim_churn_repo_url)"; then
      ueransim_churn_repo_url="$churn_override"
    fi
    if churn_override="$(extra_var_value ueransim_churn_repo_branch)"; then
      ueransim_churn_repo_branch="$churn_override"
    fi
    if churn_override="$(extra_var_value ueransim_churn_subscribers)"; then
      ueransim_churn_subscribers="$churn_override"
    fi
    if churn_override="$(extra_var_value ueransim_churn_counts)"; then
      ueransim_churn_counts="$churn_override"
    fi
    if churn_override="$(extra_var_value ueransim_churn_settle_seconds)"; then
      ueransim_churn_settle_seconds="$churn_override"
    fi
    if churn_override="$(extra_var_value ueransim_churn_between_seconds)"; then
      ueransim_churn_between_seconds="$churn_override"
    fi
    if [[ -n "${REQUESTED_CHURN_SUBSCRIBERS:-}" ]]; then
      ueransim_churn_subscribers="$REQUESTED_CHURN_SUBSCRIBERS"
    fi
    if [[ -n "${REQUESTED_CHURN_COUNTS:-}" ]]; then
      ueransim_churn_counts="$REQUESTED_CHURN_COUNTS"
    fi

    TCP_PAPER_UES=("qhat01" "qhat02" "qhat03")
    TCP_PAPER_SCENARIOS=(
      "01_decomp_baseline_all_ues"
      "02_decomp_far_ue_radio"
      "03_decomp_upf_cpu_stress"
      "04_decomp_target_server_netem_delay"
      "05_fit02_interference_near_ul_dl"
      "06_fit28_spatial_control_near_ul_dl"
    )
    VALIDATION_SCENARIOS=(
      "v01_candidate_signal_baseline"
      "v02_icmp_ping_correctness"
      "v05_tcp_icmp_parallel_median"
      "v06_tcp_icmp_parallel_mtu_ping"
      "v03_controlled_delay"
      "v04_tc_pass_baseline"
    )

    if [[ -n "${REQUESTED_EXPERIMENT_MODE:-}" ]]; then
      if [[ "$REQUESTED_EXPERIMENT_MODE" != "ueransim-churn" && "$platform" != "r2lab" ]]; then
        echo "❌ Automated paper/validation workflows currently require platform=r2lab."
        exit 1
      fi

      run_scenario=true
      case "$REQUESTED_EXPERIMENT_MODE" in
        "tcp-paper")
          scenario="TCP paper scenarios"
          paper_scenario_names="${REQUESTED_TCP_PAPER_SCENARIOS:-all}"
          echo "TCP paper scenario names: ${paper_scenario_names}"

          tcp_paper_required_ues=()
          add_tcp_paper_required_ue() {
            local ue="$1"
            if ! printf '%s\n' "${tcp_paper_required_ues[@]}" | grep -qx "$ue"; then
              tcp_paper_required_ues+=("$ue")
            fi
          }

          if [[ "$paper_scenario_names" == "all" ]]; then
            for required_ue in "${TCP_PAPER_UES[@]}"; do
              add_tcp_paper_required_ue "$required_ue"
            done
          else
            IFS=',' read -ra selected_tcp_paper_scenarios_for_ues <<< "$paper_scenario_names"
            for selected_tcp_paper_scenario in "${selected_tcp_paper_scenarios_for_ues[@]}"; do
              case "$selected_tcp_paper_scenario" in
                "01_decomp_baseline_all_ues"|"02_decomp_far_ue_radio"|"03_decomp_upf_cpu_stress"|"04_decomp_target_server_netem_delay"|"05_fit02_interference_near_ul_dl"|"06_fit28_spatial_control_near_ul_dl")
                  add_tcp_paper_required_ue "qhat01"
                  add_tcp_paper_required_ue "qhat02"
                  add_tcp_paper_required_ue "qhat03"
                  ;;
                *)
                  echo "❌ Unknown TCP paper scenario: $selected_tcp_paper_scenario"
                  exit 1
                  ;;
              esac
            done
          fi

          for required_ue in "${tcp_paper_required_ues[@]}"; do
            if ! printf '%s\n' "${R2LAB_UES[@]}" | grep -qx "$required_ue"; then
              R2LAB_UES+=("$required_ue")
            fi
          done
          echo "TCP paper scenario selected; ensuring required UEs are in inventory: ${tcp_paper_required_ues[*]}"
          ;;

        "validation")
          scenario="Latency validation pipeline"
          validation_scenario_names="${REQUESTED_VALIDATION_SCENARIOS:-all}"
          validation_tcp_bitrate_override="${REQUESTED_VALIDATION_TCP_BITRATE:-${VALIDATION_TCP_BITRATE:-}}"
          validation_mtu_ping_size_override="${REQUESTED_VALIDATION_MTU_PING_SIZE:-${VALIDATION_MTU_PING_SIZE:-}}"
          if [[ -n "${validation_mtu_ping_size_override}" && ! "${validation_mtu_ping_size_override}" =~ ^[0-9]+$ ]]; then
            echo "❌ Invalid MTU-sized ping payload: ${validation_mtu_ping_size_override}"
            exit 1
          fi
          echo "Latency validation scenario names: ${validation_scenario_names}"

          validation_required_ues=()
          add_validation_required_ue() {
            local ue="$1"
            if ! printf '%s\n' "${validation_required_ues[@]}" | grep -qx "$ue"; then
              validation_required_ues+=("$ue")
            fi
          }

          if [[ "$validation_scenario_names" == "all" ]]; then
            add_validation_required_ue "qhat01"
            add_validation_required_ue "qhat02"
          else
            IFS=',' read -ra selected_validation_scenarios_for_ues <<< "$validation_scenario_names"
            for selected_validation_scenario in "${selected_validation_scenarios_for_ues[@]}"; do
              case "$selected_validation_scenario" in
                "v01_candidate_signal_baseline")
                  add_validation_required_ue "qhat01"
                  add_validation_required_ue "qhat02"
                  ;;
                "v02_icmp_ping_correctness"|"v03_controlled_delay"|"v04_tc_pass_baseline"|"v05_tcp_icmp_parallel_median"|"v06_tcp_icmp_parallel_mtu_ping")
                  add_validation_required_ue "qhat01"
                  ;;
                *)
                  echo "❌ Unknown validation scenario: $selected_validation_scenario"
                  exit 1
                  ;;
              esac
            done
          fi

          for required_ue in "${validation_required_ues[@]}"; do
            if ! printf '%s\n' "${R2LAB_UES[@]}" | grep -qx "$required_ue"; then
              R2LAB_UES+=("$required_ue")
            fi
          done
          echo "Latency validation selected; ensuring required UEs are in inventory: ${validation_required_ues[*]}"
          ;;

        "ueransim-churn")
          if [[ "$core" != "open5gs" || "$ran" != "ueransim" ]]; then
            echo "❌ UERANSIM churn requires core=open5gs and ran=ueransim."
            exit 1
          fi
          scenario="UERANSIM attach/detach churn"
          requires_iperf_server=false
          monitoring_enabled=true
          monarch=false
          monitor_node="${monitor_node:-$DEFAULT_MONITOR_NODE}"
          if [[ ! "$ueransim_churn_subscribers" =~ ^[0-9]+$ || "$ueransim_churn_subscribers" -lt 1 ]]; then
            echo "❌ Invalid churn subscriber count: $ueransim_churn_subscribers"
            exit 1
          fi
          if [[ ! "$ueransim_churn_counts" =~ ^[0-9]+([[:space:]]+[0-9]+)*$ ]]; then
            echo "❌ Invalid churn counts: $ueransim_churn_counts"
            exit 1
          fi
          churn_max_count=0
          for churn_count in $ueransim_churn_counts; do
            if (( churn_count > churn_max_count )); then
              churn_max_count="$churn_count"
            fi
          done
          if (( churn_max_count > ueransim_churn_subscribers )); then
            echo "❌ Largest churn wave (${churn_max_count}) exceeds generated subscribers (${ueransim_churn_subscribers})."
            exit 1
          fi
          echo "UERANSIM churn selected."
          echo "Monitoring node: ${monitor_node}"
          echo "Kopf controller, packet-pairer, and eBPF latency probes: disabled"
          echo "Subscribers: ${ueransim_churn_subscribers}"
          echo "Attach counts: ${ueransim_churn_counts}"
          ;;

        *)
          echo "❌ Unknown requested experiment mode: ${REQUESTED_EXPERIMENT_MODE}"
          exit 1
          ;;
      esac

      if [[ "$requires_iperf_server" == true ]]; then
        iperf_server_node="${REQUESTED_TARGET_SERVER:-sopnode-w3}"
        echo "iperf server node: ${iperf_server_node}"
        if [[ "${iperf_server_node}" == "${core_node}" || \
              "${iperf_server_node}" == "${ran_node}" || \
              ( -n "${monitor_node}" && "${iperf_server_node}" == "${monitor_node}" ) ]]; then
          echo "iperf server already part of inventory, no need to add it."
        else
          DISTINCT_IPERF_SERVER=true
          echo "iperf server ${iperf_server_node} will be added in the inventory."
        fi
      fi

      cat >> "$DEPLOYMENT_ENV" <<EOF
run_scenario="$run_scenario"
scenario="$scenario"
iperf_server_node="$iperf_server_node"
paper_scenario_names="$paper_scenario_names"
validation_scenario_names="$validation_scenario_names"
paper_prometheus_url="${REQUESTED_PROMETHEUS_URL:-}"
validation_prometheus_url="${REQUESTED_PROMETHEUS_URL:-}"
paper_duration_override="${REQUESTED_EXPERIMENT_DURATION:-}"
validation_duration_override="${REQUESTED_EXPERIMENT_DURATION:-}"
validation_tcp_bitrate_override="${REQUESTED_VALIDATION_TCP_BITRATE:-${VALIDATION_TCP_BITRATE:-}}"
validation_mtu_ping_size_override="${REQUESTED_VALIDATION_MTU_PING_SIZE:-${VALIDATION_MTU_PING_SIZE:-}}"
requires_iperf_server="$requires_iperf_server"
ueransim_churn_repo_url="$ueransim_churn_repo_url"
ueransim_churn_repo_branch="$ueransim_churn_repo_branch"
ueransim_churn_subscribers="$ueransim_churn_subscribers"
ueransim_churn_counts="$ueransim_churn_counts"
ueransim_churn_settle_seconds="$ueransim_churn_settle_seconds"
ueransim_churn_between_seconds="$ueransim_churn_between_seconds"
EOF
      return
    fi

    # Ask the user if they want to run an optional scenario after deployment
    echo ""
    read -rp "Do you want to run an optional scenario after deployment? [y/N]: " scenario_choice
    if [[ "$scenario_choice" =~ ^[Yy]$ ]]; then
      echo ""
      echo "Select the scenario to run:"
      options=()
      if [[ "$platform" == "r2lab" && "${#R2LAB_UES[@]}" -ge 1 ]]; then
        options+=("$SCENARIO_R2LAB")
        options+=("$SCENARIO_R2LAB_INTERFERENCE")
        options+=("$SCENARIO_R2LAB_MULTI")
        options+=("$SCENARIO_R2LAB_PING")
      fi
      if [[ "$platform" == "rfsim" ]]; then
        options+=("$SCENARIO_RFSIM")
      fi
      if [[ "$platform" == "r2lab" ]]; then
        options+=("TCP paper scenarios")
      fi
      if [[ "$platform" == "r2lab" ]]; then
        options+=("Latency validation pipeline")
      fi
      if [[ "$core" == "open5gs" && "$ran" == "ueransim" ]]; then
        options+=("UERANSIM attach/detach churn")
      else
        echo "Note: UERANSIM churn is available only with Open5GS core and UERANSIM RAN."
        echo "Current selection: core=${core}, ran=${ran}"
      fi
      
      for i in "${!options[@]}"; do
        echo "$((i+1))) ${options[$i]}"
      done

      read -rp "Confirm your choice: " scenario_choice

      if [[ "$scenario_choice" =~ ^[0-9]+$ ]] && ((scenario_choice >= 1 && scenario_choice <= ${#options[@]})); then
        scenario="${options[$((scenario_choice - 1))]}"
        echo "Selected scenario: $scenario"
        run_scenario=true
      else
        echo "❌ Invalid choice"
      fi

      # ========== Iperf Tests Setup (without interference) ==========
      # Simply use the run_iperf_test.sh script to run the selected iperf test scenario after deployment.

      if [[ "$run_scenario" == true ]]; then
        if [[ "$scenario" == "TCP paper scenarios" ]]; then
          echo ""
          echo "Select TCP paper scenario(s) to run (default: all):"
          echo "0) all TCP paper scenarios"
          for i in "${!TCP_PAPER_SCENARIOS[@]}"; do
            echo "$((i + 1))) ${TCP_PAPER_SCENARIOS[i]}"
          done
          read -rp "Enter choices separated by spaces [0-${#TCP_PAPER_SCENARIOS[@]}]: " -a tcp_paper_choices
          if [[ "${#tcp_paper_choices[@]}" -eq 0 || "${tcp_paper_choices[0]}" == "0" ]]; then
            paper_scenario_names="all"
          else
            selected_tcp_paper_scenarios=()
            for choice in "${tcp_paper_choices[@]}"; do
              if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#TCP_PAPER_SCENARIOS[@]} )); then
                selected_tcp_paper_scenarios+=("${TCP_PAPER_SCENARIOS[$((choice - 1))]}")
              else
                echo "❌ Invalid TCP paper scenario choice: $choice"
                exit 1
              fi
            done
            paper_scenario_names=$(IFS=,; echo "${selected_tcp_paper_scenarios[*]}")
          fi
          echo "TCP paper scenario names: ${paper_scenario_names}"

          tcp_paper_required_ues=()
          add_tcp_paper_required_ue() {
            local ue="$1"
            if ! printf '%s\n' "${tcp_paper_required_ues[@]}" | grep -qx "$ue"; then
              tcp_paper_required_ues+=("$ue")
            fi
          }

          if [[ "$paper_scenario_names" == "all" ]]; then
            for required_ue in "${TCP_PAPER_UES[@]}"; do
              add_tcp_paper_required_ue "$required_ue"
            done
          else
            IFS=',' read -ra selected_tcp_paper_scenarios_for_ues <<< "$paper_scenario_names"
            for selected_tcp_paper_scenario in "${selected_tcp_paper_scenarios_for_ues[@]}"; do
              case "$selected_tcp_paper_scenario" in
                "01_decomp_baseline_all_ues"|"02_decomp_far_ue_radio"|"03_decomp_upf_cpu_stress"|"04_decomp_target_server_netem_delay"|"05_fit02_interference_near_ul_dl"|"06_fit28_spatial_control_near_ul_dl")
                  add_tcp_paper_required_ue "qhat01"
                  add_tcp_paper_required_ue "qhat02"
                  add_tcp_paper_required_ue "qhat03"
                  ;;
              esac
            done
          fi

          for required_ue in "${tcp_paper_required_ues[@]}"; do
            if ! printf '%s\n' "${R2LAB_UES[@]}" | grep -qx "$required_ue"; then
              R2LAB_UES+=("$required_ue")
            fi
          done
          echo "TCP paper scenario selected; ensuring required UEs are in inventory: ${tcp_paper_required_ues[*]}"
          echo "This workflow will export Prometheus at 1s and create experiment_analysis.ipynb automatically."
          echo ""
          read -rp "TCP paper iperf duration in seconds [default: 300]: " paper_duration_input
          if [[ -n "${paper_duration_input}" ]]; then
            if [[ "$paper_duration_input" =~ ^[0-9]+$ ]]; then
              paper_duration_override="$paper_duration_input"
            else
              echo "❌ Invalid duration: $paper_duration_input"
              exit 1
            fi
          fi
          DEFAULT_IPERF_SERVER_NODE="sopnode-w3"
        elif [[ "$scenario" == "Latency validation pipeline" ]]; then
          echo ""
          echo "Select latency validation scenario(s) to run:"
          echo "0) all validation scenarios"
          for i in "${!VALIDATION_SCENARIOS[@]}"; do
            echo "$((i + 1))) ${VALIDATION_SCENARIOS[i]}"
          done
          read -rp "Enter choices separated by spaces [0-${#VALIDATION_SCENARIOS[@]}]: " -a validation_choices
          if [[ "${#validation_choices[@]}" -eq 0 || "${validation_choices[0]}" == "0" ]]; then
            validation_scenario_names="all"
          else
            selected_validation_scenarios=()
            for choice in "${validation_choices[@]}"; do
              if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#VALIDATION_SCENARIOS[@]} )); then
                selected_validation_scenarios+=("${VALIDATION_SCENARIOS[$((choice - 1))]}")
              else
                echo "❌ Invalid validation scenario choice: $choice"
                exit 1
              fi
            done
            validation_scenario_names=$(IFS=,; echo "${selected_validation_scenarios[*]}")
          fi
          echo "Latency validation scenario names: ${validation_scenario_names}"

          validation_required_ues=()
          add_validation_required_ue() {
            local ue="$1"
            if ! printf '%s\n' "${validation_required_ues[@]}" | grep -qx "$ue"; then
              validation_required_ues+=("$ue")
            fi
          }

          if [[ "$validation_scenario_names" == "all" ]]; then
            add_validation_required_ue "qhat01"
            add_validation_required_ue "qhat02"
          else
            IFS=',' read -ra selected_validation_scenarios_for_ues <<< "$validation_scenario_names"
            for selected_validation_scenario in "${selected_validation_scenarios_for_ues[@]}"; do
              case "$selected_validation_scenario" in
                "v01_candidate_signal_baseline")
                  add_validation_required_ue "qhat01"
                  add_validation_required_ue "qhat02"
                  ;;
                "v02_icmp_ping_correctness"|"v03_controlled_delay"|"v04_tc_pass_baseline"|"v05_tcp_icmp_parallel_median"|"v06_tcp_icmp_parallel_mtu_ping")
                  add_validation_required_ue "qhat01"
                  ;;
                *)
                  echo "❌ Unknown validation scenario: $selected_validation_scenario"
                  exit 1
                  ;;
              esac
            done
          fi

          for required_ue in "${validation_required_ues[@]}"; do
            if ! printf '%s\n' "${R2LAB_UES[@]}" | grep -qx "$required_ue"; then
              R2LAB_UES+=("$required_ue")
            fi
          done
          echo "Latency validation selected; ensuring required UEs are in inventory: ${validation_required_ues[*]}"
          echo "This workflow will export Prometheus at 1s and create experiment_analysis.ipynb automatically."
          echo ""
          read -rp "Validation traffic duration in seconds [default: 120; ICMP uses ping_count instead]: " validation_duration_input
          if [[ -n "${validation_duration_input}" ]]; then
            if [[ "$validation_duration_input" =~ ^[0-9]+$ ]]; then
              validation_duration_override="$validation_duration_input"
            else
              echo "❌ Invalid duration: $validation_duration_input"
              exit 1
            fi
          fi
          read -rp "Validation TCP bitrate cap [default: 30Mb; use 0 for iperf unlimited]: " validation_tcp_bitrate_input
          if [[ -n "${validation_tcp_bitrate_input}" ]]; then
            validation_tcp_bitrate_override="$(normalize_validation_tcp_bitrate "$validation_tcp_bitrate_input")"
            echo "Validation TCP bitrate cap normalized to: ${validation_tcp_bitrate_override}"
          fi
          read -rp "MTU-sized ICMP ping payload for v06 in bytes [default: 1472, IPv4 packet 1500]: " validation_mtu_ping_size_input
	          if [[ -n "${validation_mtu_ping_size_input}" ]]; then
	            if [[ "$validation_mtu_ping_size_input" =~ ^[0-9]+$ ]]; then
	              validation_mtu_ping_size_override="$validation_mtu_ping_size_input"
	            else
	              echo "❌ Invalid MTU-sized ping payload: $validation_mtu_ping_size_input"
	              exit 1
	            fi
	          fi
	          DEFAULT_IPERF_SERVER_NODE="sopnode-w3"
	        elif [[ "$scenario" == "UERANSIM attach/detach churn" ]]; then
	          requires_iperf_server=false
	          echo ""
	          echo "UERANSIM churn selected."
	          echo "This will deploy Open5GS from ${DEFAULT_UERANSIM_CHURN_REPO_URL} branch ${DEFAULT_UERANSIM_CHURN_REPO_BRANCH}"
	          echo "and run attach/detach waves against the ueransim-ue-churn StatefulSet."
	          echo "Each UE container pauses immediately before nr-ue, then the full wave is released as one attach burst."
	          echo "It will collect Prometheus CPU/memory overhead, pod logs, and UE-mapper API snapshots."
	          echo "Kopf controller, packet-pairer, and eBPF latency probes are disabled for churn."
	          echo ""

	          if [[ "${monitoring_enabled:-false}" != true || -z "${monitor_node:-}" ]]; then
	            monitoring_enabled=true
	            monarch=false
	            echo "UERANSIM churn overhead needs a monitoring node for Prometheus/cAdvisor."
	            echo "Select the node to deploy lightweight Prometheus/Grafana on (default: ${DEFAULT_MONITOR_NODE}):"
	            echo "1) sopnode-f1"
	            echo "2) sopnode-f2"
	            echo "3) sopnode-f3"
	            echo "4) sopnode-w3"
	            read -rp "Enter choice [1-4]: " churn_monitor_node_choice
	            if [[ -z "${churn_monitor_node_choice}" ]]; then
	              monitor_node=${DEFAULT_MONITOR_NODE}
	            else
	              case "${churn_monitor_node_choice}" in
	                1) monitor_node="sopnode-f1" ;;
	                2) monitor_node="sopnode-f2" ;;
	                3) monitor_node="sopnode-f3" ;;
	                4) monitor_node="sopnode-w3" ;;
	                *) echo "❌ Invalid Monitoring node"; exit 1 ;;
	              esac
	            fi
	          fi

	          read -rp "Number of churn subscribers to create [default: ${DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}]: " churn_subscribers_input
	          ueransim_churn_subscribers="${churn_subscribers_input:-$DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}"
	          if [[ ! "$ueransim_churn_subscribers" =~ ^[0-9]+$ || "$ueransim_churn_subscribers" -lt 1 ]]; then
	            echo "❌ Invalid churn subscriber count: $ueransim_churn_subscribers"
	            exit 1
	          fi

	          read -rp "UE attach counts to test [default: ${DEFAULT_UERANSIM_CHURN_COUNTS}]: " churn_counts_input
	          ueransim_churn_counts="${churn_counts_input:-$DEFAULT_UERANSIM_CHURN_COUNTS}"
	          if [[ ! "$ueransim_churn_counts" =~ ^[0-9[:space:]]+$ ]]; then
	            echo "❌ Invalid churn counts: $ueransim_churn_counts"
	            exit 1
	          fi

	          read -rp "Seconds to hold each attached wave [default: ${DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}]: " churn_settle_input
	          ueransim_churn_settle_seconds="${churn_settle_input:-$DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}"
	          if [[ ! "$ueransim_churn_settle_seconds" =~ ^[0-9]+$ ]]; then
	            echo "❌ Invalid settle seconds: $ueransim_churn_settle_seconds"
	            exit 1
	          fi

	          read -rp "Seconds to wait between waves after detach [default: ${DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}]: " churn_between_input
	          ueransim_churn_between_seconds="${churn_between_input:-$DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}"
	          if [[ ! "$ueransim_churn_between_seconds" =~ ^[0-9]+$ ]]; then
	            echo "❌ Invalid between-wave seconds: $ueransim_churn_between_seconds"
	            exit 1
	          fi

	          churn_max_count=0
	          for churn_count in $ueransim_churn_counts; do
	            if (( churn_count > churn_max_count )); then
	              churn_max_count="$churn_count"
	            fi
	          done
	          if (( churn_max_count > ueransim_churn_subscribers )); then
	            echo "❌ Largest churn wave (${churn_max_count}) exceeds generated subscribers (${ueransim_churn_subscribers})."
	            exit 1
	          fi

	          echo ""
	          echo "Confirmed UERANSIM churn configuration:"
	          echo "  Subscribers: ${ueransim_churn_subscribers}"
	          echo "  Attach waves: ${ueransim_churn_counts}"
	          echo "  Hold each wave: ${ueransim_churn_settle_seconds}s"
	          echo "  Wait after detach: ${ueransim_churn_between_seconds}s"
	        else
	          DEFAULT_IPERF_SERVER_NODE=${core_node}
	        fi
	        if [[ "$requires_iperf_server" == true ]]; then
	          echo "By default, iperf will run between UEs and the selected bare-metal target server, i.e., ${DEFAULT_IPERF_SERVER_NODE}"
	          echo ""
	          echo "Select the target node to deploy iperf servers : by default, ${DEFAULT_IPERF_SERVER_NODE}:"
	          echo "1) sopnode-f1"
	          echo "2) sopnode-f2"
	          echo "3) sopnode-f3"
	          echo "4) sopnode-w3"
	          read -rp "Enter choice [1-4]: " iperf_server_choice
	          if [[ -z "${iperf_server_choice}" ]]; then
	            iperf_server_node=${DEFAULT_IPERF_SERVER_NODE}
	          else
	            case "${iperf_server_choice}" in
	              1) iperf_server_node="sopnode-f1" ;;
	              2) iperf_server_node="sopnode-f2" ;;
	              3) iperf_server_node="sopnode-f3" ;;
	              4) iperf_server_node="sopnode-w3" ;;
	              *) echo "❌ Invalid iperf target server choice"; exit 1 ;;
	            esac
	          fi

	          echo "iperf server node: ${iperf_server_node}"
	          if [[ "${iperf_server_node}" == "${core_node}" || \
	                "${iperf_server_node}" == "${ran_node}" || \
	                ( -n "${monitor_node}" && "${iperf_server_node}" == "${monitor_node}" ) ]]; then
	            echo "iperf server already part of inventory, no need to add it."
	          else
	            DISTINCT_IPERF_SERVER=true
	            echo "iperf server ${iperf_server_node} will be added in the inventory."
	          fi
	        fi
	      fi
	    fi
    cat >> "$DEPLOYMENT_ENV" <<EOF
run_scenario="$run_scenario"
scenario="$scenario"
iperf_server_node="$iperf_server_node"
paper_scenario_names="$paper_scenario_names"
validation_scenario_names="$validation_scenario_names"
paper_prometheus_url="$paper_prometheus_url"
validation_prometheus_url="$validation_prometheus_url"
paper_duration_override="$paper_duration_override"
validation_duration_override="$validation_duration_override"
validation_tcp_bitrate_override="$validation_tcp_bitrate_override"
validation_mtu_ping_size_override="$validation_mtu_ping_size_override"
requires_iperf_server="$requires_iperf_server"
ueransim_churn_repo_url="$ueransim_churn_repo_url"
ueransim_churn_repo_branch="$ueransim_churn_repo_branch"
ueransim_churn_subscribers="$ueransim_churn_subscribers"
ueransim_churn_counts="$ueransim_churn_counts"
ueransim_churn_settle_seconds="$ueransim_churn_settle_seconds"
ueransim_churn_between_seconds="$ueransim_churn_between_seconds"
EOF
}

############################
# INTERFERENCE SETUP
############################

interference_setup() {

    # ========== Interference Test Setup ==========
    run_interference_test=false
    if [[ "$run_scenario" == true && "$scenario" == "$SCENARIO_R2LAB_INTERFERENCE" ]]; then
      run_interference_test=true
      USRPs=("n300" "n320" "b210" "b205mini")

      # Remove the RU used for RAN from the list of available USRPs
      NEW_USRPs=()
      for u in "${USRPs[@]}"; do
        if [[ "$u" != "$R2LAB_RU" && -n "$u" ]]; then
          NEW_USRPs+=("$u")
        fi
      done
      USRPs=("${NEW_USRPs[@]}")

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
        # Clean up VIZ_USRPs array to remove possible ""
        VIZ_USRPs=($(printf "%s\n" "${VIZ_USRPs[@]}" | grep -v '^$'))
        echo ""
        echo "Select the USRP to use for spectrum visualization:"
        for idx in "${!VIZ_USRPs[@]}"; do
          echo "$((idx+1))) ${VIZ_USRPs[$idx]}"
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
        # Ask user for interference parameters and export them: FREQ, GAIN, NOISE_BANDWIDTH (defaults are 3411.22M, 110, 15M)
        read -rp "Enter interference frequency [default: 3411.22M]: " freq_input
        FREQ="${freq_input:-3411.22M}"
        read -rp "Enter interference gain in dB [default: 110]: " gain_input
        GAIN="${gain_input:-110}"
        read -rp "Enter noise bandwidth in Hz [default: 15M]: " bw_input
        NOISE_BANDWIDTH="${bw_input:-15M}"
        export FREQ GAIN NOISE_BANDWIDTH
      else # $ran == "srsRAN" # Now srsRAN gNB config is TDD. band 78
        echo "Setting MODE to TDD for interference test"
        echo ""
        export MODE="TDD"
        # Ask user for interference parameters and export them: FREQ, GAIN, NOISE_BANDWIDTH (defaults are 3411.22M, 110, 15M)
        read -rp "Enter interference frequency [default: 3600.00M]: " freq_input
        FREQ="${freq_input:-3600.00M}"
        read -rp "Enter interference gain in dB [default: 110]: " gain_input
        GAIN="${gain_input:-110}"
        read -rp "Enter noise bandwidth in Hz [default: 15M]: " bw_input
        NOISE_BANDWIDTH="${bw_input:-15M}"
        export FREQ GAIN NOISE_BANDWIDTH
        #export MODE="FDD"
        ## Ask user for interference parameters and export them: FREQ_UL, FREQ_DL, GAIN, NOISE_BANDWIDTH (defaults are 1747.5M, 1842.5M, 110, 5M)
        #read -rp "Enter interference uplink frequency [default: 1747.5M]: " freq_ul_input
        #FREQ_UL="${freq_ul_input:-1747.5M}"
        #read -rp "Enter interference downlink frequency [default: 1842.5M]: " freq_dl_input
        #FREQ_DL="${freq_dl_input:-1842.5M}"
        #read -rp "Enter interference gain in dB [default: 110]: " gain_input
        #GAIN="${gain_input:-110}"
        #read -rp "Enter noise bandwidth in Hz [default: 5M]: " bw_input
        #NOISE_BANDWIDTH="${bw_input:-5M}"
        #export FREQ_UL FREQ_DL GAIN NOISE_BANDWIDTH
      fi
    fi
    cat >> "$DEPLOYMENT_ENV" <<EOF
run_interference_test="$run_interference_test"
viz_usrp="$viz_usrp"
EOF
}


############################
# PRINT SUMMARY
############################

print_summary() {

    echo
    echo "========== SUMMARY =========="
    echo "Core:        $core on ${core_node}"
    echo "RAN:         $ran on ${ran_node}"
    if [[ "$monitoring_enabled" == true ]]; then
      if [[ -n "$monitor_node" ]]; then
        echo "Monitoring:  enabled on $monitor_node"
      else
        echo "Monitoring:  enabled (automatic mode)"
      fi
      echo "Logs:        $([[ "${monitoring_loki_enabled:-false}" == true ]] && echo "Loki enabled" || echo "Loki disabled")"
      echo "Monarch:     disabled (legacy roles kept, not deployed)"
    else
      echo "Monitoring:  disabled"
      echo "Monarch:     false"
    fi
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
	    if [[ "${run_scenario}" == true ]]; then
	      if [[ "$scenario" == "UERANSIM attach/detach churn" ]]; then
	        echo "Scenario:    enabled"
	      else
	        echo "Iperf Test: enabled"
	      fi
	      echo "  Scenario: $scenario"
      case "$scenario" in
        "$SCENARIO_R2LAB")
          echo "Will run iperf in a sequential way on ${R2LAB_UES[0]} for 30 seconds in downlink then uplink (use the iperf_duration and iperf_sleep ansible parameters to change the default values (in s))"
        ;;
        "$SCENARIO_RFSIM")
          echo "Will run iperf sequentially OAI-NR-UE1, OAI-NR-UE2 and OAI-NR-UE3 for 30 seconds each with an in-between wait time of 5 seconds in downlink then uplink (use the iperf_duration and iperf_sleep ansible parameters to change the default values (in s))"
        ;;
        "$SCENARIO_R2LAB_INTERFERENCE")
          echo "Will run iperf with interference (to explain further)"
        ;;
        "TCP paper scenarios")
          echo "Will run selected TCP paper scenario(s): ${paper_scenario_names:-all}. UEs are left connected at the end."
          echo "Artifacts will include iperf JSON logs, 1s Prometheus CSV, and experiment_analysis.ipynb."
          [[ -n "${paper_duration_override:-}" ]] && echo "  Duration override: ${paper_duration_override}s"
          [[ -n "${paper_prometheus_url:-}" ]] && echo "  Prometheus URL override: ${paper_prometheus_url}"
        ;;
	        "Latency validation pipeline")
	          echo "Will run latency validation scenario(s): ${validation_scenario_names:-all}. UEs are left connected at the end."
	          echo "Artifacts will include iperf JSON logs, 1s Prometheus CSV, optional pcaps, and experiment_analysis.ipynb."
	          [[ -n "${validation_duration_override:-}" ]] && echo "  Duration override: ${validation_duration_override}s"
	          [[ -n "${validation_tcp_bitrate_override:-}" ]] && echo "  TCP bitrate override: ${validation_tcp_bitrate_override}"
	          [[ -n "${validation_mtu_ping_size_override:-}" ]] && echo "  v06 MTU-sized ping payload override: ${validation_mtu_ping_size_override} bytes"
	          [[ -n "${validation_prometheus_url:-}" ]] && echo "  Prometheus URL override: ${validation_prometheus_url}"
	        ;;
        "$SCENARIO_R2LAB_MULTI")
          echo "Will run iperf on each UE individually (${R2LAB_UES[0]}), and then all UEs simultaneously.  Will test uplink and downlink for both TCP and UDP.  Each test lasts 30s (use the iperf_duration and iperf_sleep ansible parameters to change the default values (in s))"
        ;;
        "$SCENARIO_R2LAB_PING")
          echo "Will run ping from each UE individually (${R2LAB_UES[0]}), and then all UEs simultaneously.  Each test lasts 30s (use the ping_duration and ping_sleep ansible parameters to change the default values (in s))"
	        ;;
	        "UERANSIM attach/detach churn")
	          echo "Will deploy UERANSIM churn from ${ueransim_churn_repo_url:-$DEFAULT_UERANSIM_CHURN_REPO_URL} branch ${ueransim_churn_repo_branch:-$DEFAULT_UERANSIM_CHURN_REPO_BRANCH}."
	          if [[ "$START_SCENARIO" == true ]]; then
	            echo "Will run attach/detach waves during deploy with counts: ${ueransim_churn_counts:-$DEFAULT_UERANSIM_CHURN_COUNTS}."
	          else
	            echo "Will configure churn deployment; attach/detach waves are not auto-started."
	          fi
	          echo "Attach mode: synchronized nr-ue launch after every UE container reaches the shared start gate."
	          echo "Subscribers: ${ueransim_churn_subscribers:-$DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}"
	          echo "Kopf controller, packet-pairer, and eBPF latency probes: disabled"
	          echo "Settle seconds: ${ueransim_churn_settle_seconds:-$DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}; between waves: ${ueransim_churn_between_seconds:-$DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}"
	        ;;
	      esac
	      if [[ "${requires_iperf_server:-true}" == true ]]; then
	        echo "iperf server will run on the bare-metal ${iperf_server_node} server."
	      fi
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
      *) echo "❌ unknown" ;;
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

    if [[ "${monitoring_enabled}" == true && -n "${monitor_node}" ]]; then
      cat >> "$INVENTORY" <<EOF
${monitor_node} ansible_user=root nic_interface=$(get_nic "${monitor_node}") ip=172.28.2.$(get_ip_suffix "${monitor_node}") storage=$(get_storage "${monitor_node}")
EOF
    fi

    if [[ "${DISTINCT_IPERF_SERVER}" == true ]]; then
      cat >> "$INVENTORY" <<EOF

[iperf_server_node]
${iperf_server_node} ansible_user=root nic_interface=$(get_nic "${iperf_server_node}") ip=172.28.2.$(get_ip_suffix "${iperf_server_node}") storage=$(get_storage "${iperf_server_node}")
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
          read -r v_name v_num v_usrp <<<"$viz_info"
          if [[ "$v_name" == "fit02" ]]; then
            append_fit "$v_name" "$v_num" "$v_usrp"
          else
            append_fit "$v_name" "$v_num" "$v_usrp"
          fi
        else
          # noise is n300/n320 and no viz requested -> do not add fit nodes (noise is RU-based)
          # To preserve previous behavior, we still add a commented example entry (no active fit nodes)
          : # i.e., nop
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
    if [[ "$monitoring_enabled" == true && -n "${monitor_node}" ]]; then
      echo "monitor_node" >> "$INVENTORY"
    fi
    if [[ "${DISTINCT_IPERF_SERVER}" == true ]]; then
      echo "iperf_server_node" >> "$INVENTORY"
    fi

    cat >> "$INVENTORY" <<EOF

[k8s_workers:children]
ran_node
EOF
    if [[ "${monitoring_enabled}" == true && -n "${monitor_node}" ]]; then
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

    if [[ "$monitoring_enabled" == true && -n "${monitor_node}" ]]; then
      cat >> "$INVENTORY" <<EOF
monitor_node_name="${monitor_node}"
EOF
    fi

    if [[ "${DISTINCT_IPERF_SERVER}" == true ]]; then
      cat >> "$INVENTORY" <<EOF
iperf_server_node_name="${iperf_server_node}"
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
bridge_enabled=$( [[ "${ran_node}" != "${core_node}" ]] && echo true || echo false )
monitoring_enabled=${monitoring_enabled}
monitoring_loki_enabled=${monitoring_loki_enabled:-false}
monarch=${monarch}
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
    if [[ "$monitoring_enabled" == true && -n "${monitor_node}" ]]; then
      nodes_to_reserve+=("${monitor_node}")
    fi
    if [[ "${DISTINCT_IPERF_SERVER}" == true ]]; then
      nodes_to_reserve+=("${iperf_server_node}")
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
      # Round current time down to nearest 10 minutes
      S=$(date +'%H%M')
      START="${S:0:2}:${S:2:1}0"
      # Start time in ISO format
      start_time=$(date +"%Y-%m-%dT$START")
      # Convert start_time to epoch timestamp (portable code)
      start_epoch=$(date -j -f "%Y-%m-%dT%H:%M" "$start_time" "+%s" 2>/dev/null || date -d "$start_time" "+%s")
      # Calculate end epoch by adding duration in minutes
      end_epoch=$((start_epoch + duration_minutes * 60))
      # Convert end epoch back to ISO format (portable)
      end_time=$(date -r "$end_epoch" "+%Y-%m-%dT%H:%M" 2>/dev/null || date -d "@$end_epoch" "+%Y-%m-%dT%H:%M")
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

    ANSIBLE_EXTRA_ARGS=(-e "fiveg_profile=${PROFILE_5G}")
    append_cli_extra_vars

    if [[ "${monitoring_enabled:-false}" == true ]] && ! extra_var_defined "monitoring_loki_enabled"; then
      ANSIBLE_EXTRA_ARGS+=(-e "monitoring_loki_enabled=${monitoring_loki_enabled:-true}")
    fi

    if [[ "${scenario:-}" == "UERANSIM attach/detach churn" ]]; then
      extra_var_defined "open5gs_repo_url_override" || ANSIBLE_EXTRA_ARGS+=(-e "open5gs_repo_url_override=${ueransim_churn_repo_url:-$DEFAULT_UERANSIM_CHURN_REPO_URL}")
      extra_var_defined "open5gs_repo_branch_override" || ANSIBLE_EXTRA_ARGS+=(-e "open5gs_repo_branch_override=${ueransim_churn_repo_branch:-$DEFAULT_UERANSIM_CHURN_REPO_BRANCH}")
      ANSIBLE_EXTRA_ARGS+=(-e "ueransim_deploy_mode=churn")
      ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_initial_replicas=0")
      extra_var_defined "churn_capture_amf_pcap" || ANSIBLE_EXTRA_ARGS+=(-e "churn_capture_amf_pcap=true")
      extra_var_defined "ueransim_churn_subscribers" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_subscribers=${ueransim_churn_subscribers:-$DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}")
      extra_var_defined "ueransim_churn_counts" || ANSIBLE_EXTRA_ARGS+=(
        -e "{\"ueransim_churn_counts\":\"${ueransim_churn_counts:-$DEFAULT_UERANSIM_CHURN_COUNTS}\"}"
      )
      extra_var_defined "ueransim_churn_settle_seconds" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_settle_seconds=${ueransim_churn_settle_seconds:-$DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}")
      extra_var_defined "ueransim_churn_between_seconds" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_between_seconds=${ueransim_churn_between_seconds:-$DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}")
      if [[ "$START_SCENARIO" == true ]]; then
        ANSIBLE_EXTRA_ARGS+=(-e "ueransim_run_churn_after_deploy=true")
        CHURN_RUNS_VIA_DEPLOY=true
      else
        ANSIBLE_EXTRA_ARGS+=(-e "ueransim_run_churn_after_deploy=false")
        CHURN_RUNS_VIA_DEPLOY=false
      fi
    fi

    echo "Launching deployment..."

    run_cmd ansible-galaxy install -r collections/requirements.yml

    if [[ "$platform" == "r2lab" ]]; then
      echo "ansible-playbook -i $INVENTORY ${ANSIBLE_EXTRA_ARGS[@]} playbooks/deploy_r2lab.yml &"
      run_cmd ansible-playbook -i "$INVENTORY" \
        "${ANSIBLE_EXTRA_ARGS[@]}" \
        playbooks/deploy_r2lab.yml 2>&1 | tee ${DIR_LOGS}/logs-r2lab.txt &
    fi

    echo "ansible-playbook -i $INVENTORY ${ANSIBLE_EXTRA_ARGS[@]} playbooks/deploy.yml"

    run_cmd ansible-playbook -i "$INVENTORY" \
      "${ANSIBLE_EXTRA_ARGS[@]}" \
      playbooks/deploy.yml 2>&1 | tee ${DIR_LOGS}/logs.txt


    echo ""
    echo "=========================================="
    echo "========== Deployment Completed =========="
    echo "=========================================="
    echo ""
}


############################
# SCENARIOS
############################

run_scenario() {

    ANSIBLE_EXTRA_ARGS=(-e "fiveg_profile=${PROFILE_5G}")
    append_cli_extra_vars

    if [[ -n "${paper_prometheus_url:-}" ]]; then
      ANSIBLE_EXTRA_ARGS+=(-e "paper_prometheus_url=${paper_prometheus_url}")
    fi

    if [[ -n "${validation_prometheus_url:-}" ]]; then
      ANSIBLE_EXTRA_ARGS+=(-e "validation_prometheus_url=${validation_prometheus_url}")
    fi

    if [[ -n "${paper_duration_override:-}" ]]; then
      ANSIBLE_EXTRA_ARGS+=(-e "paper_duration=${paper_duration_override}")
    fi

    if [[ -n "${validation_duration_override:-}" ]]; then
      ANSIBLE_EXTRA_ARGS+=(-e "validation_duration=${validation_duration_override}")
    fi

    if [[ -n "${validation_tcp_bitrate_override:-}" ]]; then
      validation_tcp_bitrate_override="$(normalize_validation_tcp_bitrate "$validation_tcp_bitrate_override")"
      ANSIBLE_EXTRA_ARGS+=(-e "{\"validation_tcp_bitrate\":\"${validation_tcp_bitrate_override}\"}")
    fi

    if [[ -n "${validation_mtu_ping_size_override:-}" ]]; then
      if [[ ! "${validation_mtu_ping_size_override}" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid MTU-sized ping payload: ${validation_mtu_ping_size_override}"
        exit 1
      fi
      ANSIBLE_EXTRA_ARGS+=(-e "validation_mtu_ping_size=${validation_mtu_ping_size_override}")
    fi

    if [[ -n "${REQUESTED_PROMETHEUS_URL:-}" && -z "${paper_prometheus_url:-}" && -z "${validation_prometheus_url:-}" ]]; then
      ANSIBLE_EXTRA_ARGS+=(-e "paper_prometheus_url=${REQUESTED_PROMETHEUS_URL}")
      ANSIBLE_EXTRA_ARGS+=(-e "validation_prometheus_url=${REQUESTED_PROMETHEUS_URL}")
    fi

    if [[ -n "${REQUESTED_EXPERIMENT_DURATION:-}" && -z "${paper_duration_override:-}" && -z "${validation_duration_override:-}" ]]; then
      case "${REQUESTED_EXPERIMENT_MODE:-}" in
        "tcp-paper")
          ANSIBLE_EXTRA_ARGS+=(-e "paper_duration=${REQUESTED_EXPERIMENT_DURATION}")
          ;;
        "validation")
          ANSIBLE_EXTRA_ARGS+=(-e "validation_duration=${REQUESTED_EXPERIMENT_DURATION}")
          ;;
        *)
          ANSIBLE_EXTRA_ARGS+=(-e "paper_duration=${REQUESTED_EXPERIMENT_DURATION}")
          ANSIBLE_EXTRA_ARGS+=(-e "validation_duration=${REQUESTED_EXPERIMENT_DURATION}")
          ;;
      esac
    fi

    if [[ -n "${REQUESTED_VALIDATION_TCP_BITRATE:-}" ]]; then
      REQUESTED_VALIDATION_TCP_BITRATE="$(normalize_validation_tcp_bitrate "$REQUESTED_VALIDATION_TCP_BITRATE")"
      ANSIBLE_EXTRA_ARGS+=(-e "{\"validation_tcp_bitrate\":\"${REQUESTED_VALIDATION_TCP_BITRATE}\"}")
    fi

	    if [[ -n "${REQUESTED_VALIDATION_MTU_PING_SIZE:-}" && -z "${validation_mtu_ping_size_override:-}" ]]; then
	      if [[ ! "${REQUESTED_VALIDATION_MTU_PING_SIZE}" =~ ^[0-9]+$ ]]; then
	        echo "❌ Invalid MTU-sized ping payload: ${REQUESTED_VALIDATION_MTU_PING_SIZE}"
	        exit 1
	      fi
	      ANSIBLE_EXTRA_ARGS+=(-e "validation_mtu_ping_size=${REQUESTED_VALIDATION_MTU_PING_SIZE}")
	    fi

	    if [[ "${scenario:-}" == "UERANSIM attach/detach churn" ]]; then
	      ANSIBLE_EXTRA_ARGS+=(-e "ueransim_deploy_mode=churn")
	      extra_var_defined "churn_capture_amf_pcap" || ANSIBLE_EXTRA_ARGS+=(-e "churn_capture_amf_pcap=true")
	      extra_var_defined "ueransim_churn_subscribers" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_subscribers=${ueransim_churn_subscribers:-$DEFAULT_UERANSIM_CHURN_SUBSCRIBERS}")
	      extra_var_defined "ueransim_churn_counts" || ANSIBLE_EXTRA_ARGS+=(
	        -e "{\"ueransim_churn_counts\":\"${ueransim_churn_counts:-$DEFAULT_UERANSIM_CHURN_COUNTS}\"}"
	      )
	      extra_var_defined "ueransim_churn_settle_seconds" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_settle_seconds=${ueransim_churn_settle_seconds:-$DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS}")
	      extra_var_defined "ueransim_churn_between_seconds" || ANSIBLE_EXTRA_ARGS+=(-e "ueransim_churn_between_seconds=${ueransim_churn_between_seconds:-$DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS}")
	    fi

    if [[ -n "${REQUESTED_EXPERIMENT_MODE:-}" ]]; then
      run_scenario=true
      iperf_server_node="${REQUESTED_TARGET_SERVER:-${iperf_server_node:-sopnode-w3}}"
      case "$REQUESTED_EXPERIMENT_MODE" in
        "tcp-paper")
          scenario="TCP paper scenarios"
          paper_scenario_names="${REQUESTED_TCP_PAPER_SCENARIOS:-${paper_scenario_names:-all}}"
          ;;
        "validation")
          scenario="Latency validation pipeline"
          validation_scenario_names="${REQUESTED_VALIDATION_SCENARIOS:-${validation_scenario_names:-all}}"
          ;;
        "ueransim-churn")
          scenario="UERANSIM attach/detach churn"
          ;;
      esac
    fi

    if [[ "$run_scenario" == true ]]; then
      if [[ "$START_SCENARIO" == true ]]; then
        echo "Running $scenario"
        scenario_status=0
        case "$scenario" in
          "Iperf R2lab scenario without interference"|"Iperf RFSIM scenario without interference")
            run_logged_cmd "${DIR_LOGS}/logs-scenario_iperf.txt" \
              ./run_scenario.sh -d --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"
            scenario_status=$?
            ;;
          "Iperf R2lab scenario with interference")
            run_logged_cmd "${DIR_LOGS}/logs-scenario_interference.txt" \
              ./run_scenario.sh -i --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"
            scenario_status=$?
            ;;
          "TCP paper scenarios")
            run_logged_cmd "${DIR_LOGS}/logs-scenario_tcp-paper.txt" \
              ansible-playbook -i "$INVENTORY" \
              "${ANSIBLE_EXTRA_ARGS[@]}" \
              -e "target_server_host=${iperf_server_node}" \
              -e "paper_scenario_names=${paper_scenario_names:-all}" \
              playbooks/run_tcp_paper_scenarios.yml
            scenario_status=$?
            ;;
	          "Latency validation pipeline")
	            extra_var_defined "validation_extract_pcap_rtt" || ANSIBLE_EXTRA_ARGS+=(-e "validation_extract_pcap_rtt=false")
	            extra_var_defined "validation_install_tshark" || ANSIBLE_EXTRA_ARGS+=(-e "validation_install_tshark=false")
	            extra_var_defined "validation_create_paper_figures" || ANSIBLE_EXTRA_ARGS+=(-e "validation_create_paper_figures=true")
            extra_var_defined "validation_compress_pcaps" || ANSIBLE_EXTRA_ARGS+=(-e "validation_compress_pcaps=true")
            extra_var_defined "validation_compress_prometheus_csv" || ANSIBLE_EXTRA_ARGS+=(-e "validation_compress_prometheus_csv=true")
            if [[ -z "${validation_tcp_bitrate_override:-}" && -z "${REQUESTED_VALIDATION_TCP_BITRATE:-}" ]] && ! extra_var_defined "validation_tcp_bitrate"; then
              ANSIBLE_EXTRA_ARGS+=(-e '{"validation_tcp_bitrate":"30Mb"}')
            fi
            run_logged_cmd "${DIR_LOGS}/logs-scenario_latency-validation.txt" \
              ansible-playbook -i "$INVENTORY" \
              "${ANSIBLE_EXTRA_ARGS[@]}" \
              -e "target_server_host=${iperf_server_node}" \
	              -e "validation_scenario_names=${validation_scenario_names:-all}" \
	              playbooks/run_latency_validation.yml
	            scenario_status=$?
            ;;
          "$SCENARIO_R2LAB"|"$SCENARIO_RFSIM")
            run_cmd ./run_scenario.sh -d --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"  2>&1 | tee ${DIR_LOGS}/logs-scenario_iperf.txt
            ;;
          "$SCENARIO_R2LAB_INTERFERENCE")
            run_cmd ./run_scenario.sh -i --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"  2>&1 | tee ${DIR_LOGS}/logs-scenario_interference.txt
            ;;
          "$SCENARIO_R2LAB_MULTI")
            run_cmd ./run_scenario.sh -m --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"  2>&1 | tee ${DIR_LOGS}/logs-scenario_iperf_multi.txt
            ;;
          "$SCENARIO_R2LAB_PING")
            run_cmd ./run_scenario.sh --ping --inventory="${NAME_INVENTORY}" \
              "${ANSIBLE_EXTRA_ARGS[@]}"  2>&1 | tee ${DIR_LOGS}/logs-scenario_ping.txt
	            ;;
	          "UERANSIM attach/detach churn")
	            if [[ "$SCENARIO_ONLY" != true && "$CHURN_RUNS_VIA_DEPLOY" == true ]]; then
	              echo "UERANSIM churn already ran from deploy.yml because it was selected in deploy.sh."
	              scenario_status=0
	            else
	              churn_console_log="${TMPDIR:-/tmp}/5g_ansible_logs/logs-scenario_ueransim-churn.txt"
	              mkdir -p "$(dirname "$churn_console_log")"
	              echo "UERANSIM churn console log: ${churn_console_log}"
	              run_logged_cmd "$churn_console_log" \
	                ansible-playbook -i "$INVENTORY" \
	                "${ANSIBLE_EXTRA_ARGS[@]}" \
	                playbooks/run_ueransim_churn.yml
	              scenario_status=$?
	            fi
	            ;;
	          *)
	            echo "❌ Unknown scenario: $scenario"
	            exit 1
            ;;
        esac
        if [[ "$scenario_status" -ne 0 ]]; then
          echo ""
          echo "=========================================="
          echo "============ Scenario Failed ============"
          echo "=========================================="
          echo ""
          return "$scenario_status"
        fi
        echo ""
        echo "=========================================="
        echo "========== Scenario Completed =========="
        echo "=========================================="
        echo ""
      else
        echo ""
        echo "Scenario $scenario with MANUAL start mode selected"
        if [[ "$scenario" == "TCP paper scenarios" ]]; then
          echo "Just launch:"
          echo "ansible-playbook -i ${INVENTORY} -e fiveg_profile=${PROFILE_5G} -e target_server_host=${iperf_server_node} -e paper_scenario_names=${paper_scenario_names:-all} playbooks/run_tcp_paper_scenarios.yml"
          [[ -n "${paper_duration_override:-}" ]] && echo "  add: -e paper_duration=${paper_duration_override}"
          [[ -n "${paper_prometheus_url:-}" ]] && echo "  add: -e paper_prometheus_url=${paper_prometheus_url}"
	        elif [[ "$scenario" == "Latency validation pipeline" ]]; then
	          echo "Just launch:"
	          echo "ansible-playbook -i ${INVENTORY} -e fiveg_profile=${PROFILE_5G} -e target_server_host=${iperf_server_node} -e validation_scenario_names=${validation_scenario_names:-all} playbooks/run_latency_validation.yml"
	          [[ -n "${validation_duration_override:-}" ]] && echo "  add: -e validation_duration=${validation_duration_override}"
	          [[ -n "${validation_tcp_bitrate_override:-}" ]] && echo "  add: -e validation_tcp_bitrate=${validation_tcp_bitrate_override}"
	          [[ -n "${validation_mtu_ping_size_override:-}" ]] && echo "  add: -e validation_mtu_ping_size=${validation_mtu_ping_size_override}"
	          [[ -n "${validation_prometheus_url:-}" ]] && echo "  add: -e validation_prometheus_url=${validation_prometheus_url}"
	        elif [[ "$scenario" == "UERANSIM attach/detach churn" ]]; then
	          echo "Just launch:"
	          echo "ansible-playbook -i ${INVENTORY} -e fiveg_profile=${PROFILE_5G} -e ueransim_deploy_mode=churn -e \"ueransim_churn_counts=${ueransim_churn_counts:-$DEFAULT_UERANSIM_CHURN_COUNTS}\" -e ueransim_churn_settle_seconds=${ueransim_churn_settle_seconds:-$DEFAULT_UERANSIM_CHURN_SETTLE_SECONDS} -e ueransim_churn_between_seconds=${ueransim_churn_between_seconds:-$DEFAULT_UERANSIM_CHURN_BETWEEN_SECONDS} playbooks/run_ueransim_churn.yml"
	        else
	          echo "Just launch ./run_scenario.sh to start it !"
	        fi
        echo ""
      fi
    fi
}


############################
# ACCESS INFO
############################

show_access_info() {

    # ========== End of Script ==========
    # Note: The user is responsible for deleting the reservations after use if needed.
    # Show the commands to run to connect to the Grafana dashboard if monitoring is enabled.
    if [[ "$monitoring_enabled" == true && -n "${monitor_node}" ]]; then
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
      echo "Password: admin"
      if [[ "${monitoring_loki_enabled:-false}" == true ]]; then
        echo ""
        echo "Loki is available inside Grafana as the 'Loki' datasource for Kubernetes pod logs."
        echo "For direct Loki API access, forward port 31000 from ${monitor_node} if needed."
      fi
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
      echo "./run_scenario.sh -i --no-setup"
      echo ""
    fi

}

############################
# MAIN
############################

parse_args "$@"

init_defaults_and_banner
if [[ "$SKIP_INPUTS" == true ]]; then
  echo "Skipped User Inputs"
  echo "Using $INVENTORY as inventory"
  if [[ -z "${core:-}" ]]; then
    core="$(awk -F= '$1 == "core" {gsub(/"/, "", $2); print $2; exit}' "$INVENTORY")"
  fi
  if [[ -z "${ran:-}" ]]; then
    ran="$(awk -F= '$1 == "ran" {gsub(/"/, "", $2); print $2; exit}' "$INVENTORY")"
  fi
  if [[ -f "$R2LAB_CONFIG" ]]; then
    source "$R2LAB_CONFIG"
  elif [[ "${platform:-}" == "r2lab" ]]; then
    echo "R2lab config doesn't exist. Exiting."
    exit 1
  fi
  if [[ -n "${REQUESTED_EXPERIMENT_MODE:-}" ]]; then
    optional_scenarios
  fi
else
  collect_user_inputs
  optional_scenarios
  interference_setup
  print_summary
  generate_inventory
fi
reserve_nodes
reserve_r2lab
if [[ "$SCENARIO_ONLY" == true ]]; then
  echo "Scenario-only mode selected: skipping reservation and deployment."
else
  deploy
fi
SCENARIO_STATUS=0
run_scenario || SCENARIO_STATUS=$?
show_access_info

if [[ "$SCENARIO_STATUS" -ne 0 ]]; then
  echo "❌ Finished with scenario failure."
  exit "$SCENARIO_STATUS"
fi

echo "✅ All done!"
