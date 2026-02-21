⚠️ This repository is no longer maintained and is used for testing purposes.
The actively maintained/stable version is now available at our lab's official repo:
[sopnode/5g_ansible](https://github.com/sopnode/5g_ansible)

This original repository is preserved for reproducibility of the published papers:
- Paper 1 (2026) : Toward Real-Time RAN Observability in Open-Source 5G Systems
- Paper 2 (2026) : Demonstrating Real-Time RAN Observability in Open-Source 5G: Controlled Interference Scenario

---
# Automated 5G Deployment & Monitoring with Ansible

![setup](images/setup.png)

This repository provides a fully automated [Ansible](https://www.ansible.com/) framework for deploying and monitoring a sliced 5G network on Kubernetes. It is intended to be run from the [SLICES platform Webshell](https://post-5g-web.slices-ri.eu/), assuming you have:

- A valid [SLICES account](https://doc.slices-ri.eu/SupportingServices/getanaccount.html).
- A valid [R2Lab account](https://r2lab.inria.fr/tuto-010-registration.md).
- Working SSH connection from your Duckburg to R2LAB. For that, the public SSH key of your Duckburg should be [uploaded to R2LAB](https://r2lab.inria.fr/tuto-010-registration.md).

It supports:

- **R2Lab deployments** (Open5GS core + OAI/srsRAN on R2Lab resources).
- **RF Simulation** (no R2Lab devices).
- **Multiple RAN combos**: OAI gNB/UE, srsRAN gNB/UE, UERANSIM gNB/UE.

This work was done by **Yassir Amami** and **Ziyad Mabrouk** as part of our mission at [Inria Sophia Antipolis](https://www.inria.fr/en/inria-centre-universite-cote-azur).

---



## 🔍 Explore the Codebases

This repository integrates and builds upon several open-source projects to provide a comprehensive 5G deployment and monitoring framework. If you want to dive deeper or customize the components, feel free to explore the following codebases:

- [**/5g_ansible**](https://github.com/yassir63/5g_ansible): Automates the full testbed setup and orchestrates the execution of experimental scenarios. ( This exact Repo !)
- [**/oai-cn5g-fed**](https://github.com/yassir63/oai-cn5g-fed): OpenAirInterface 5G Core Network Helm charts.
- [**/oai5g-rru**](https://github.com/yassir63/oai5g-rru): OpenAirInterface 5G Core Network Deployment on SophiaNode/R2lab using Helm Charts and nepi-ng.
- [**/open5gs-k8s**](https://github.com/yassir63/open5gs-k8s): 5G Open5GS Core network deployment on Kubernetes.
- [**/5g-monarch**](https://github.com/Ziyad-Mabrouk/5g-monarch): Extends Monarch for RAN metrics and new KPIs.
- [**/flexric**](https://github.com/Ziyad-Mabrouk/flexric): Modified FlexRIC with new E2SM for adaptive log control.
- [**/oai-gnb-log-parser**](https://github.com/Ziyad-Mabrouk/oai-gnb-log-parser): Lightweight Prometheus exporter for OAI gNB logs.
- [**/openairinterface5g**](https://github.com/Ziyad-Mabrouk/openairinterface5g): Modified OAI gNB with E2SM support for monitoring interval control.
- [**/srsran-helm**](https://github.com/Ziyad-Mabrouk/srsran-helm): Dockerfiles and Kubernetes deployment manifests for srsRAN, srsUE, and Telegraf-based monitoring integration.

Feel free to fork these repositories and make your own changes to suit your experimentation needs. Contributions and feedback are always welcome!

---

## 📁 Repository Layout

```
.
├─ deployments/                # Deployment launchers (see below)
│  ├─ deploy.sh                # R2Lab: Open5GS core + OAI on R2Lab
│  ├─ deploy_rfsim_oai_core.sh # OAI core + OAI gNB (RFSIM emulated)
│  ├─ deploy_open5gs_rfsim.sh  # Open5GS core + OAI gNB (RFSIM emulated)
│  ├─ deploy_open5gs_srsRAN.sh # Open5GS core + srsRAN gNB + UEs (ZMQ emulated)
│  └─ deploy_open5gs_ueransim.sh # Open5GS core + UERANSIM gNB + emulated UEs
├─ scenarios/                  # Iperf test scenarios
│  └─ run_iperf_test.sh        # Default/parallel/interference/rfsim test runner
├─ playbooks/                  # Ansible playbooks
├─ inventory/                  # hosts.ini and group_vars
├─ collections/                # Ansible collections
├─ roles/                      # Ansible roles
├─ images/                     # figures/screenshots
└─ deploy.sh                   # Unified launcher (new)
```

---

## 🚀 Quick Start

From the SLICES Webshell:

```bash
git clone https://github.com/yassir63/5g_ansible.git
cd 5g_ansible
./deploy.sh
```

This will provide you with an interactive menu which depending on your choices will : 

- Allocate and reset reserved resources.
- Install required packages.
- Set up a Kubernetes cluster across nodes.
- Deploy **5G Core** and **5G gNB**.
- Deploy the **Monarch** monitoring stack if specified which allows the monitoring of the Core, the gNB as well as machine performance and energy usage.
- Prepare R2Lab resources (RRU, UEs, Fit Nodes) as required.

> **Note:** By default, UEs are prepared and connected for all scenarios. Use one of the **iperf test scenarios** below to exercise the system, or adjust the last section of the relevant deploy playbook if you don't want your UEs to automatically attach.

---

## 🧭 Unified Deployment Launcher

Use the top-level `deploy.sh` to interactively configure and deploy your 5G network. The script provides a menu-driven interface to select the core, RAN, platform, and other deployment options.

### Interactive Deployment

Run the script without arguments to start the interactive menu:

```bash
./deploy.sh
```

The script will guide you through the following steps:
- Select the **Core Network** (OAI or Open5GS).
- Select the **RAN** (OAI, srsRAN, or UERANSIM).
- Choose the deployment nodes (`sopnode-f1`, `sopnode-f2`, or `sopnode-f3`).
- Optionally enable **monitoring** and select a monitoring node.
- Choose the deployment platform (`R2Lab` or `RFSIM`).
- Configure additional options such as interference tests (if applicable).

### Example

Interactive deployment:

```bash
./deploy.sh
```

> **Note:** The interactive menu allows for more customization, such as enabling monitoring or configuring interference tests. For advanced use cases, you can also modify the underlying playbooks or extend the wrapper script.

---

## 📶 Iperf Test Scenarios

After deployment, choose one of the following scenarios. The helper script is under `scenarios/`.

#### Command Overview

```bash
./scenarios/run_iperf_test.sh [OPTION] [--no-setup]
```

- `-d`: Runs the default iperf test
- `-p`: Runs the parallel iperf test
- `-i`: Runs the interference test
- `-s`: Runs RFSim iperf test
- `--no-setup`: Skips the setup phase (useful for repeat runs)

### 1) Default Iperf Test

```bash
./scenarios/run_iperf_test.sh -d
```

- Connects the *first* UE defined in `[qhats]`.
- The UE runs iperf for 5 minutes on downlink then uplink (10 minutes in total for the scenario).

### 2) Parallel Iperf Test

```bash
./scenarios/run_iperf_test.sh -p
```

- Connects the *first 4* UEs defined in `[qhats]`.
- Each UE runs bidirectional iperf3 for 5 minutes, staggered by 100s. (10 minutes in total for the scenario).

### 3) Interference Test + Spectrum Visualization

```bash
./scenarios/run_iperf_test.sh -i
```

- Connects the *first* UE.
- Spectrum visualization (`uhd_fft`) on the first fit node.
- Noise generation via second fit node (`interference_usrp=fit`) or specific USRP (`n300`/`n320`).
- Noise starts 100s after UE begins a 5-minutes-long bidirectional iperf3.
> **Note:** This scenario requires the user to export a `MODE` environment variable that takes either `TDD` if OAI RAN is deployed, or `FDD` if srsRAN is deployed (based on the gNB configuration we set).

### 4) RF Simulation Iperf Test

```bash
./scenarios/run_iperf_test.sh -s
```

- Runs iperf on downlink then uplink, on RFSim UEs OAI-NR-UE1 then OAI-NR-UE2, for 200s each, staggered by 100s (10 minutes in total for the scenario).

> Use `--no-setup` to skip device config when re-running a scenario without changes.

---

## 🧾 Inventory Configuration

All deployments are driven by `inventory/hosts.ini`:

- Core, RAN, Monitoring nodes
- Kubernetes master/worker roles
- R2Lab Faraday jump-host & remote UEs
- RRU model
- Scenario-specific parameters

### Example

```ini
[webshell]
localhost ansible_connection=local

[core_node]
sopnode-f3 ansible_user=root nic_interface=ens15f1 ip=172.28.2.95 storage=sdb1

[ran_node]
sopnode-f2 ansible_user=root nic_interface=ens2f1 ip=172.28.2.77 storage=sda1

[monitor_node]
sopnode-f1 ansible_user=root nic_interface=ens2f1 ip=172.28.2.76 storage=sda1

[faraday]
faraday.inria.fr ansible_user=<your_r2lab_user> rru=jaguar interference_usrp=n320 freq=3411.22M g=110 s=20M conf=gnb.sa.band78.51prb.aw2s.ddsuu.20MHz.conf

[qhats]
qhat01 ansible_host=qhat01 ansible_user=root ansible_ssh_common_args='-o ProxyJump=<your_r2lab_user>@faraday.inria.fr' mode=mbim dnn=internet upf_ip=10.41.0.1
qhat02 ansible_host=qhat02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=<your_r2lab_user>@faraday.inria.fr' mode=mbim dnn=streaming upf_ip=10.42.0.1
qhat03 ansible_host=qhat03 ansible_user=root ansible_ssh_common_args='-o ProxyJump=<your_r2lab_user>@faraday.inria.fr' mode=mbim dnn=internet upf_ip=10.41.0.1

[fit_nodes]
fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=<your_r2lab_user>@faraday.inria.fr' fit_number=2 fit_usrp=b210

[sopnodes:children]
core_node
ran_node
monitor_node

[k8s_workers:children]
ran_node
monitor_node
```

> **IMPORTANT:** This file is dynamiccaly composed by your entries within the interactive deployment script. You will be asked during the first time to speicify once your actual R2LAB slice name (username), email, and password, if you ever decide to use it.

---

## 📊 Monitoring Dashboard Access

After deployment, the terminal prints the SSH command to access the **Monarch** dashboard.

**Tip:** To access Duckburg nodes from your local machine, configure SLICES CLI/SSH per the official guide.

---

## 🛠 Customization Notes

- You can assign any node to any role (core/ran/monitor), except AW2S RAN is limited to `sopnode-f1..f3`.
- Single-node deployment is possible (not recommended for performance).
- UE DNN/UPF mapping is defined by the Open5GS core data generator; don’t change unless you know what you’re doing.
- Supported RRUs: `n300`, `n320`, `jaguar`, `panther`.
- The gNB `conf` file is mandatory (see `ran-config/conf/` in `oai5g-rru`).
- Interference scenario uses the first fit node for spectrum viz; the second fit node (or `n300`/`n320`) as noise source.

---

## 🧰 Troubleshooting

- Ensure SSH access from Duckburg to R2LAB (`faraday.inria.fr)` and from your local machine to Duckburg.
- Testbeds evolve; changes on SLICES/R2Lab can affect experiments.

---

## 💾 Data Persistence

Monarch’s data is persisted to the block device specified by `storage` in `hosts.ini`, mounted under `/mnt/data`. MinIO stores Thanos blocks in `/mnt/data/minio/monarch-thanos/`.

**Prometheus flushing behavior:** data is flushed from in-memory head to disk every **\~2 hours**. If the monitor node is terminated before a flush, that interim data is lost. After the first flush, data survives restarts as long as the same node and storage are reused.

---

## 🙏 Acknowledgments

- Inria Sophia Antipolis — DIANA Team
- Upstream components: Open5GS, OAI, srsRAN, UERANSIM, Monarch

