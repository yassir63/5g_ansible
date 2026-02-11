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
After running `deploy.sh`, choose one of the available scenarios to run based on your experiment setup.

#### Command Overview
```bash
./run_iperf.sh 
```

Use this script when repeating the same test without redoing the necessary setup.

### 1. Default Iperf Test
```bash
./run_iperf_test.sh 
```
- Connect all UEs defined in `[qhats]` host group.
- Each UE runs downlink then uplink iperf3 test *separately*.
> **Note:** This is the baseline scenario used to for basic connectivity testing and benchmarking.

### 2. Parallel Iperf Tests (TBD)
```bash
./run_iperf_test.sh -p
```
- Connect the *first 4* UEs.
- Each UE runs bidirectional iperf3 test for 400 seconds, sequentially spaced by 100 seconds.

### 3. Interference Test with Spectrum Visualization (TBD)
```bash
./run_iperf_test.sh -i
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

> **IMPORTANT:** Update the `ansible_user` under `[faraday]` to match your actual R2Lab slice name.

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