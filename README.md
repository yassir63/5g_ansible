# Automated 5G Deployment & Monitoring with Ansible

![setup](images/setup.png)

This repository provides a fully automated [Ansible](https://www.ansible.com/) framework for deploying and monitoring a sliced 5G network on Kubernetes. It is intended to be run from the [SLICES platform Webshell](https://post-5g-web.slices-ri.eu/), assuming you have:

- A valid [SLICES account](https://doc.slices-ri.eu/).
- A valid reservation for the machines `sopnode-f1`, `sopnode-f2`, and `sopnode-f3` (from [Duckburg](https://duckburg.net.cit.tum.de/)).
- A valid [R2Lab account and reservation](https://r2lab.inria.fr/tuto-010-registration.md).

It supports:

- **R2Lab deployments** (Open5GS core + OAI on R2Lab resources).
- **RF Simulation** (no R2Lab devices).
- **Multiple RAN combos**: OAI gNB (rfsim), srsRAN gNB (ZMQ), UERANSIM gNB/UE.

This work was done by **Yassir Amami** and **Ziyad Mabrouk** as part of our mission at [Inria Sophia Antipolis](https://www.inria.fr/en/inria-centre-universite-cote-azur).

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
├─ iperfTests/                 # Iperf test scenarios & runners (moved here)
│  ├─ run_iperf_test.sh        # Default/parallel/interference test runner
│  └─ run_rfsim_iperf_test.sh  # RFSim iperf tests
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
./deploy.sh r2lab
```

This will:

- Allocate and reset reserved machines via `pos`.
- Install required packages.
- Set up a Kubernetes cluster across nodes.
- Deploy **Open5GS Core** (with slice support via the `open5gs-k8s` dependency).
- Deploy the **Monarch** monitoring stack which allows the monitoring of the Core as well as the OAI gNB or the srsRAN gNB for now.
- Prepare R2Lab resources (RRU, UEs, Fit Nodes) as required.

> **Note:** By default, UEs are prepared but not necessarily connected. Use one of the **iperf test scenarios** below to exercise the system, or adjust the last section of the relevant deploy playbook if you want automatic attach.

---

## 🧭 Unified Deployment Launcher

Use the top-level `deploy.sh` to select a deployment target explicitly:

```bash
./deploy.sh <target>
```

Available **targets**:

- `r2lab` → `deployments/deploy.sh`
  - Runs **Open5GS core + OAI** on **R2Lab**.
- `oai_rfsim` → `deployments/deploy_rfsim_oai_core.sh`
  - Runs **OAI core + OAI gNB** with **RFSIM** (emulated).
- `open5gs_rfsim` → `deployments/deploy_open5gs_rfsim.sh`
  - Runs **Open5GS core + OAI gNB** with **RFSIM** (emulated).
- `open5gs_srsran` → `deployments/deploy_open5gs_srsRAN.sh`
  - Runs **Open5GS core + srsRAN gNB + UEs** (**ZMQ** emulated).
- `open5gs_ueransim` → `deployments/deploy_open5gs_ueransim.sh`
  - Runs **Open5GS core + UERANSIM gNB + emulated UEs**.

Examples:

```bash
./deploy.sh r2lab
./deploy.sh oai_rfsim
./deploy.sh open5gs_rfsim
./deploy.sh open5gs_srsran
./deploy.sh open5gs_ueransim
```

> The wrapper is **explicit only** (no extra options forwarded). If you need to pass Ansible variables later, run the underlying playbooks directly or extend the wrapper.

---

## 📶 Iperf Test Scenarios

After deployment, choose one of the following scenarios. The helper script is now under `iperfTests/`. ( works only for the R2Lab scenario for now )

#### Command Overview

```bash
cd iperfTests
./run_iperf_test.sh [OPTION] [--no-setup]
```

- `-d`: Run the default iperf test
- `-p`: Run the parallel iperf test
- `-i`: Run the interference test
- `--no-setup`: Skip the setup phase (useful for repeat runs)

### 1) Default Iperf Test

```bash
cd iperfTests
./run_iperf_test.sh -d
```

- Connects UEs defined in `[qhats]`.
- Each UE runs downlink then uplink iperf3 separately.

### 2) Parallel Iperf Tests

```bash
cd iperfTests
./run_iperf_test.sh -p
```

- Connects the *first 4* UEs.
- Each UE runs bidirectional iperf3 for 400s, staggered by 100s.

### 3) Interference Test + Spectrum Visualization

```bash
cd iperfTests
./run_iperf_test.sh -i
```

- Connects the first UE.
- Spectrum visualization (`uhd_fft`) on the first fit node.
- Noise generation via second fit node (`interference_usrp=fit`) or specific USRP (`n300`/`n320`).
- Noise starts 100s after UE begins bidirectional iperf3.

#### RF Simulation Iperf

```bash
cd iperfTests
./run_rfsim_iperf_test.sh
```

- Runs uplink then downlink iperf on RFSim UEs (slice 1 & 2).

> Use `--no-setup` to skip connection & device config when re-running a scenario without changes.

---

## 🛰 RF Simulation Mode (no R2Lab)

To run the setup **without** R2Lab devices using RF Simulation only:

```bash
./deploy.sh open5gs_rfsim
# then
cd iperfTests && ./run_rfsim_iperf_test.sh
```

The playbooks will create NR-UE pods (e.g., `oai-nr-ue`, `oai-nr-ue2`, `oai-nr-ue3`) on Kubernetes.

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

> **IMPORTANT:** set `ansible_user` in `[faraday]` to your actual R2Lab slice name.

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

- Ensure SSH access from Webshell → `faraday.inria.fr` and from your local machine → Webshell.
- Verify reservations in the Duckburg and R2Lab calendars.
- Testbeds evolve; changes on SLICES/R2Lab can affect experiments.

---

## 💾 Data Persistence

Monarch’s data is persisted to the block device specified by `storage` in `hosts.ini`, mounted under `/mnt/data`. MinIO stores Thanos blocks in `/mnt/data/minio/monarch-thanos/`.

**Prometheus flushing behavior:** data is flushed from in-memory head to disk every **\~2 hours**. If the monitor node is terminated before a flush, that interim data is lost. After the first flush, data survives restarts as long as the same node and storage are reused.

---

## 🙏 Acknowledgments

- Inria Sophia Antipolis — DIANA Team
- Upstream components: Open5GS, OAI, srsRAN, UERANSIM, Monarch

