# Automated 5G Deployment And Monitoring With Ansible

![setup](images/setup.png)

This repository provides a fully automated [Ansible](https://www.ansible.com/) framework for deploying and monitoring a sliced 5G network using Kubernetes. It is intended to be run from the [SLICES platform Webshell](https://post-5g-web.slices-ri.eu/), assuming you can obtain:

- A valid [SLICES account](https://doc.slices-ri.eu/).
- A valid reservation for the machines `sopnode-f1`, `sopnode-f2`, and `sopnode-f3` (from [Duckburg](https://duckburg.net.cit.tum.de/)).
- A valid [R2Lab account and reservation](https://r2lab.inria.fr/tuto-010-registration.md).

> First version of this script was developed in June 2025 by Ziyad Mabrouk as part of his internship at [Inria Sophia Antipolis](https://www.inria.fr/en/inria-centre-universite-cote-azur), under the supervision of: Thierry Turletti, Chadi Barakat, and Walid Dabbous.

---

## 🚀 Quick Start

From the SLICES Webshell:

```bash
git clone https://github.com/sopnode/5g_ansible.git
cd 5g_ansible
./deploy.sh
```

This will:
- Allocate and reset the reserved machines using `pos`.
- Install all required packages.
- Set up a Kubernetes cluster across the nodes.
- Deploy the **Open5GS Core** (with slice support via [sopnode/open5gs-k8s](https://github.com/sopnode/open5gs-k8s)).
- Deploy the **Monarch monitoring framework** ([Ziyad-Mabrouk/5g-monarch](https://github.com/Ziyad-Mabrouk/5g-monarch), forked from [niloysh/5g-monarch](https://github.com/niloysh/5g-monarch)).
- Clean up and configure R2Lab resources: RRU, UEs, Fit Nodes.
- Deploy the **OAI RAN stack** ([sopnode/oai5g-rru](https://github.com/sopnode/oai5g-rru)).

> **Note:** This will only prepare the UEs, but will not connect them. That is done via one of the test scenarios below. However, you can uncomment the last section of the `playbooks/deploy.yml` file so that all the UEs in the `[qhats]` group of the `inventory/hosts.ini` will be connected to the 5G network.

---

## Available Scenarios
The `deploy.sh` can be used to configure and deploy a scenario (iperf or interference). If such a scenario option is selected, the `deploy.sh`  script will execute, once the 5G CORE+CN deployement is ready, another script `run_scenario.sh` that can also be run manually, provided that the inventory is already configured for the target scenario.

#### Command Overview

```bash

./run_scenario.sh -h
Usage: ./run_scenario.sh [-d|-i] [--no-setup] [--inventory=name] [-e vars] [--dry-run]

-d                       Deploy the default iperf scenario
-i                       Deploy the interference scenario
--no-setup               Do not run the setup, --use this option if R2lab devices already up and running
-e <vars>                Extra ansible vars, e.g., -e "nb_ues=5" -e "duration=20"
--inventory <name>       Use ./inventory/<name>/hosts.ini inventory instead of the default one
--dry-run                Only print ansible commands
-h, --help               Show help

```

Use this script when repeating the same test without redoing the necessary setup.

In the current version, two scenarios are available

### 1. Default Iperf Scenario

```bash
./run_scenario.sh [-d] [--no-setup]
```
- Connect all UEs defined in `[qhats]` host group.
- Each UE runs downlink then uplink iperf3 test *separately*.
> **Note:** This is the baseline scenario used to for basic connectivity testing and benchmarking.


### 2. Interference Scenario with Spectrum Visualization

```bash
./run_iperf_test.sh -i [--no-setup]
```
- Connect the first UE.
- Spectrum visualization using `uhd_fft` on the first fit node.
- Noise generation via second fit node (`interference_usrp=fit`) or specified USRP (`n300` or `n320`).
- Noise generation is active 100 seconds after the UE performs bidirectional iperf3.


---


## Inventory Configuration

The deployment is driven by `inventory/hosts.ini`, where you define:

- Core, RAN, and Monitoring nodes
- Which node acts as Kubernetes master/worker
- R2Lab Faraday jump-host and remote UEs
- RRU to use
- Other scenario-specific parameters ...

### Example Snippet:

```ini
[webshell]
localhost ansible_connection=local

[core_node]
sopnode-w3 ansible_user=root nic_interface=enp59s0f1np1 ip=172.28.2.71 storage=sda1

[ran_node]
sopnode-f3 ansible_user=root nic_interface=ens15f1 ip=172.28.2.95 storage=sdb2 boot_mode=live

[monitor_node]

#[fit_nodes]
#fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_sopnode@faraday.inria.fr' fit_number=2 fit_usrp=b210

[sopnodes:children]
core_node
ran_node

[k8s_workers:children]
ran_node

[all:vars]
# ---- CORE / RAN type ----
core="open5gs"
ran="oai"

# ---- Node aliases ----
core_node_name="sopnode-w3"
ran_node_name="sopnode-f3"
faraday_node_name="faraday.inria.fr"

# ---- RRU information ----
rru="rfsim"

# ---- RRU families ----
fhi72=false
aw2s=false

# ---- hosts variants for RAN ----
f3_ran=true

# ---- Other boolean parameters
# bridge_enabled is true if OVS bridge required between core_node and ran_node
bridge_enabled=true
monitoring_enabled=false

```

**IMPORTANT:** Update the `ansible_user` under `[faraday]` to match your actual R2Lab slice name.


Note that the framework now uses a 5G profile that defines all 5G core parameters that can be changed with the -p option of the *deploy.sh* deployment script. By default the script runs with -p default option that corresponds to the profile located at *group_vars/all/5g_profile_default.yaml*. This 5G profile is used by the framework to configure the different types of cores (Open5GS, OAI) and ran (srsRAN, OAI) used in the target scenario. 



```
5g_ansible $ cat group_vars/all/5g_profile_default.yaml


plmn:
  mcc: "001"
  mnc: "01"
  tac: "1"

dnns:
  - name: internet
    pdu_type: IPV4
  - name: streaming
    pdu_type: IPV4
  - name: ims
    pdu_type: IPV4V6

slices:
  - name: slice1
    dnn: internet
    sst: "1"
    sd: "EMPTY"
    qos:
      five_qi: "9"
      arp:
        priority_level: "8"
        preempt_cap: "NOT_PREEMPT"
        preempt_vuln: "PREEMPTABLE"
      priority_level: "1"
    bandwidth:
      uplink: "20Mbps"
      downlink: "40Mbps"
    ip_prefix: "12.1.1"

  - name: slice2
    dnn: streaming
    sst: "1"
    sd: "100000"
    qos:
      five_qi: "5"
      arp:
        priority_level: "1"
        preempt_cap: "NOT_PREEMPT"
        preempt_vuln: "PREEMPTABLE"
      priority_level: "1"
    bandwidth:
      uplink: "30Mbps"
      downlink: "50Mbps"
    ip_prefix: "14.1.1"

security:
  full_key: "fec86ba6eb707ed08905757b1bb44b8f"
  opc: "C42449363BBAD02B66D16BC975D77CC1"

ues:
  qhat01:
    imsi_suffix: "0000000006"
    slice: slice1
  qhat02:
    imsi_suffix: "0000000007"
    slice: slice2
  qhat03:
    imsi_suffix: "0000000008"
    slice: slice1
  qhat10:
    imsi_suffix: "0000000009"
    slice: slice1
  qhat11:
    imsi_suffix: "0000000010"
    slice: slice1
  qhat20:
    imsi_suffix: "0000000011"
    slice: slice1
  qhat21:
    imsi_suffix: "0000000012"
    slice: slice1
  qhat22:
    imsi_suffix: "0000000013"
    slice: slice1
  qhat23:
    imsi_suffix: "0000000014"
    slice: slice1
  UE18:
    imsi_suffix: "0000001121"  # used for OAI RFSIM UE
    slice: slice1
  UE19:
    imsi_suffix: "0000001122"  # used for OAI RFSIM UE2
    slice: slice2
  UE20:
    imsi_suffix: "0000001123"  # used for OAI RFSIM UE3
    slice: slice1
```


---

## Monitoring Dashboard Access [TBD]

After deployment, instructions will be printed to your terminal with the SSH command required to access the **Monarch monitoring dashboard**.

**Note:** To access Duckburg nodes from your local machine, you must configure your local environment to connect to the SLICES infrastructure. See:
👉 [SLICES CLI and SSH access guide](https://doc.slices-ri.eu/SupportingServices/slicescli.html)

---

## Customization Tips

- You may assign any node for any role (core, ran, monitor) **except** that AW2S RAN can only be deployed on `sopnode-f1`, `sopnode-f2` or `sopnode-f3`.
- The UPF and DNN of each UE are defined in [this configuration of the Open5gs core](https://github.com/Ziyad-Mabrouk/open5gs-k8s/blob/main/mongo-tools/generate-data.py) and should not be changed.
- Supported RRUs are: `n300`, `n320`, `jaguar` and `panther`.
- The entire `[fit_nodes]` group, as well as the frequency, gain (g) and sample rate / bandwidth covered by the noise generator (s) specified in `[faraday]`, are only relevant to the interference test scenario and are not needed otherwise.
- For the interference scenario, the first fist node (`groups[fit_nodes][0]`) is used for spectrum visualization. The USRP used for noise generation (`interference_usrp`) can be `=fit`(meaning the second fit node's USRP will be used as noise generator), or `=n300` / `=n320`).

---

## Troubleshooting

- Ensure you have SSH access from Webshell to `faraday.inria.fr` **and** from your local machine to the Webshell environment.
- Always verify your reservations are active in the [Duckburg Calendar](https://duckburg.net.cit.tum.de/calendar) and [R2Lab](https://r2lab.inria.fr/run.md).
- This code relies heavily on the setup and configuration of SLICES and R2Lab, thus any changes/problems related to these testbeds can impact experiments.

## Data Persistence

All of Monarch's monitoring data is automatically persisted to the block storage device specified under the storage variable in your `hosts.ini` file (e.g., `sda1` for `sopnode-f1`). This device is mounted under `/mnt/data`, and all monitoring data flushed by Prometheus to the MinIO bucket is stored under `/mnt/data/minio/monarch-thanos/`.

**Note on Prometheus persistence:** Prometheus writes all incoming metrics data to an in-memory *head block* and only flushes this data to disk every **2 hours**. At the moment, this interval cannot be changed in our setup. As a result:
> ⚠️ If the monitor node is terminated or redeployed before 2 hours have passed, any collected data will be lost.

To ensure metrics are actually written to disk and persist across redeployments, you must wait **at least 2 hours** after starting data collection, in which case extending your reservation is needed. Once this threshold is reached, Prometheus will flush the in-memory block to the permanent on-disk block that survives restarts.

As long as the same monitor node and mounted storage are reused, previously flushed data will remain available in the monitoring dashboard.