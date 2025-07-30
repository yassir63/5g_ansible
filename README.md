# Automated 5G Deployment And Monitoring With Ansible

This repository provides a fully automated [Ansible](https://www.ansible.com/) framework for deploying and monitoring a sliced 5G network using Kubernetes. It is intended to be run from the [SLICES platform Webshell](https://post-5g-web.slices-ri.eu/), assuming you have:

- A valid [SLICES account](https://doc.slices-ri.eu/).
- A valid reservation for the machines `sopnode-f1`, `sopnode-f2`, and `sopnode-f3` (from [Duckburg](https://duckburg.net.cit.tum.de/)).
- A valid [R2Lab account and reservation](https://r2lab.inria.fr/tuto-010-registration.md).

> This work was developed as part of my internship at [Inria Sophia Antipolis](https://www.inria.fr/en/inria-centre-universite-cote-azur), under the supervision of: Thierry Turletti, Chadi Barakat, and Walid Dabbous.

---

## 🚀 Quick Start

From the SLICES Webshell:

```bash
git clone https://github.com/Ziyad-Mabrouk/5g_ansible.git
cd 5g_ansible
./deploy.sh
```

This will:
- Allocate and reset the reserved machines using `pos`.
- Install all required packages.
- Set up a Kubernetes cluster across the nodes.
- Deploy the **Open5GS Core** (with slice support via [Ziyad-Mabrouk/open5gs-k8s](https://github.com/Ziyad-Mabrouk/open5gs-k8s)).
- Deploy the **Monarch monitoring framework** ([Ziyad-Mabrouk/5g-monarch](https://github.com/Ziyad-Mabrouk/5g-monarch), forked from [niloysh/5g-monarch](https://github.com/niloysh/5g-monarch)).
- Clean up and configure R2Lab resources: RRU, UEs, Fit Nodes.
- Deploy the **OAI RAN stack** with **FlexRIC and gNB** ([Ziyad-Mabrouk/oai5g-rru](https://github.com/Ziyad-Mabrouk/oai5g-rru/tree/gen-cn2)).

> **Note:** This will only prepare the UEs, but will not connect them. That is done via one of the test scenarios below. However, you can uncomment the last section of the `playbooks/deploy.yml` file so that all the UEs in the `[qhats]` group of the `inventory/hosts.ini` will be connected to the 5G network.

---

## Available Scenarios
After running `deploy.sh`, choose one of the available scenarios to run based on your experiment setup.

#### Command Overview
```bash
./run_iperf_test.sh [OPTION] [--no-setup]
```
- `-d`: Run the default iperf test
- `-p`: Run the parallel iperf test
- `-i`: Run the interference test
- `--no-setup`: Optional. Skip the setup phase and only run the test playbook.
Use this when repeating the same test without redoing the necessary setup.

### 1. Default Iperf Test
```bash
./run_iperf_test.sh -d
```
- Connect all UEs defined in `[qhats]` host group.
- Each UE runs downlink then uplink iperf3 test *separately*.
> **Note:** This is the baseline scenario used to for basic connectivity testing and benchmarking.

### 2. Parallel Iperf Tests
```bash
./run_iperf_test.sh -p
```
- Connect the *first 4* UEs.
- Each UE runs bidirectional iperf3 test for 400 seconds, sequentially spaced by 100 seconds.

### 3. Interference Test with Spectrum Visualization
```bash
./run_iperf_test.sh -i
```
- Connect the first UE.
- Spectrum visualization using `uhd_fft` on the first fit node.
- Noise generation via second fit node (`interference_usrp=fit`) or specified USRP (`n300` or `n320`).
- Noise generation is active 100 seconds after the UE performs bidirectional iperf3.

#### Re-running a Test without Setup
To skip the setup phase (e.g., when UEs and USRPs are already configured), use the `--no-setup` flag:
```bash
./run_iperf_test.sh -i --no-setup
```
This avoids reconnecting UEs and reconfiguring the noise generator, and only re-runs the test logic.

---

## RF Simulation Mode

To run the setup **without any R2Lab devices**, using RF Simulation only, run:

```bash
./deploy_rfsim.sh
```

No changes to `inventory/hosts.ini` are required. The playbook not use Faraday and UE configurations and instead it will install 3 NR-UEs as Kubernetes pods (`oai-nr-ue`, `oai-nr-ue2`, `oai-nr-ue3`). Each uses the same IMSI as qhat01, qhat02, and qhat03 respectively.

Afer that, to run the RFSIM iperf test:
```bash
./run_rfsim_iperf_test.sh
```
This will run uplink, then downlink iperf test for slice 1 (`oai-nr-ue`) and slice 2 (`oai-nr-ue2`) separately.

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
# SLICES Webshell
[webshell]
localhost ansible_connection=local

# SLICES Deployment Nodes
[sopnodes]
sopnode-f1 ansible_user=root nic_interface=ens2f0 ip=172.28.2.76 storage=sda1
sopnode-f2 ansible_user=root nic_interface=ens2f1 ip=172.28.2.77 storage=sda1
sopnode-f3 ansible_user=root nic_interface=ens15f0 ip=172.28.2.95 storage=sdb1

# Kubernetes Configuration
[k8s_workers]
sopnode-f1
sopnode-f2

[core_node]
sopnode-f3  # Acts as Kubernetes master + Open5GS Core node

[ran_node]
sopnode-f2  # Only f1 and f2 are supported for RAN deployements with AW2S RRUS (jaguar and panther)

[monitor_node]
sopnode-f1  # Monarch monitoring stack

# R2Lab Section
[faraday]
faraday.inria.fr ansible_user=inria_ubinet01 rru=jaguar interference_usrp=n320 freq=3411.22M g=110 s=20M conf=...

[qhats]
qhat01 ansible_host=qhat01 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_ubinet01@faraday.inria.fr' mode=mbim dnn=internet upf_ip=10.41.0.1
qhat02 ansible_host=qhat02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_ubinet01@faraday.inria.fr' mode=mbim dnn=streaming upf_ip=10.42.0.1

[fit_nodes]
fit02 ansible_host=fit02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_ubinet01@faraday.inria.fr' fit_number=2 fit_usrp=b210
```

> **IMPORTANT:** Update the `ansible_user` under `[faraday]` to match your actual R2Lab slice name.

---

## Monitoring Dashboard Access

After deployment, instructions will be printed to your terminal with the SSH command required to access the **Monarch monitoring dashboard**.

**Note:** To access Duckburg nodes from your local machine, you must configure your local environment to connect to the SLICES infrastructure. See:
👉 [SLICES CLI and SSH access guide](https://doc.slices-ri.eu/SupportingServices/slicescli.html)

---

## Customization Tips

- You may assign any node for any role (core, ran, monitor) **except** that AW2S RAN must can only be deployed on `sopnode-f1` or `sopnode-f2`.
- You can run everything on a single node (not recommended for performance).
- The UPF and DNN of each UE are defined in [this configuration of the Open5gs core](https://github.com/Ziyad-Mabrouk/open5gs-k8s/blob/main/mongo-tools/generate-data.py) and should not be changed.
- Supported RRUs are: `n300`, `n320`, `jaguar` and `panther`.
- The specific gNB configuration file is mandatory and must be specified via the `conf` variable in the `hosts.ini` file (under the `[faraday]` group). Only config files available in the [`ran-config/conf/`](https://github.com/Ziyad-Mabrouk/oai5g-rru/tree/gen-cn2/ran-config/conf) directory of the `oai5g-rru` repository are supported.
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