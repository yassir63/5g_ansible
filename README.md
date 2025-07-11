# Automated 5G Deployment And Monitoring With Ansible

This repository provides a fully automated [Ansible](https://www.ansible.com/) framework for deploying and monitoring a sliced 5G network using Kubernetes. It is intended to be run from the [SLICES platform Webshell](https://post-5g-web.slices-ri.eu/), assuming you have:

- A valid [SLICES account](https://doc.slices-ri.eu/).
- A valid reservation for the machines `sopnode-f1`, `sopnode-f2`, and `sopnode-f3` (from [Duckburg](https://duckburg.net.cit.tum.de/)).
- A valid [R2Lab account and reservation](https://r2lab.inria.fr/tuto-010-registration.md).

> This work was developed as part of my internship at [Inria Sophia Antipolis](https://www.inria.fr/en/inria-centre-universite-cote-azur), under the supervision of: Thierry Turletti, Chadi Barakat, and Walid Dabbous.

---

## 🚀 Quick Start

From the SLICES Webshell, clone this repo and simply run:

```bash
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

---

## Inventory Configuration

The deployment is driven by `inventory/hosts.ini`, where you define:

- Core, RAN, and Monitoring nodes
- Which node acts as Kubernetes master/worker
- R2Lab Faraday jump-host and remote UEs
- RRU to use

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
sopnode-f2  # Only f1 and f2 supported for RAN

[monitor_node]
sopnode-f1  # Monarch monitoring stack

# R2Lab Jump-host and UEs
[faraday]
faraday.inria.fr ansible_user=inria_ubinet01 rru=jaguar freq=3401.22M g=110

[qhats]
qhat01 ansible_host=qhat01 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_ubinet01@faraday.inria.fr' mode=mbim dnn=internet ip=10.41.0.2 upf_ip=10.41.0.1
qhat02 ansible_host=qhat02 ansible_user=root ansible_ssh_common_args='-o ProxyJump=inria_ubinet01@faraday.inria.fr' mode=mbim dnn=streaming ip=10.42.0.2 upf_ip=10.42.0.1
```

> **Note:** Update the `ansible_user` under `[faraday]` to match your actual R2Lab slice name.

---

## RRU Configuration

- Supported RRUs: `n300`, `n320` (use same gNB config) or `jaguar`, `panther` (use different gNB config).
- Frequency is specified in `[faraday]` and only affects interference test scenarios (not needed otherwise).

---

## RF Simulation Mode

To run the setup **without any R2Lab devices**, using RF Simulation only:

```bash
./deploy_rfsim.sh
```

> No changes to `inventory/hosts.ini` are required. The script will skip Faraday and UE configurations and install three OAI-NR-UEs instead (with the same imsi as qhat01, qhat02 and qhat03 respectively).

---

## Monitoring Dashboard Access

After deployment, instructions will be printed to your terminal with the SSH command required to access the **Monarch monitoring dashboard**.

**Note:** To access Duckburg nodes from your local machine, you must configure your local environment to connect to the SLICES infrastructure. See:
👉 [SLICES CLI and SSH access guide](https://doc.slices-ri.eu/SupportingServices/slicescli.html)

---

## Playbooks & Scenarios

Playbook YAML files define the logic for each deployment step and usage scenario. For UE-related experiments, ensure that:
- The Quectel UEs are correctly configured.
- The `inventory/hosts.ini` file correctly maps DNN, mode, and IP settings per UE.

---

## Customization Tips

- You may assign any node for any role (core, ran, monitor) **except** that RAN must be deployed on `sopnode-f1` or `sopnode-f2`.
- You can run everything on a single node (not recommended for performance).
- UPF, DNN, IPs, and RRU frequency are defined in the `[configuration of the Open5gs core](https://github.com/Ziyad-Mabrouk/open5gs-k8s/blob/main/mongo-tools/generate-data.py).

---

## Troubleshooting

- Ensure you have SSH access from Webshell to `faraday.inria.fr` **and** from your local machine to the Webshell environment.
- Always verify your reservations are active in the [Duckburg Calendar](https://duckburg.net.cit.tum.de/calendar) and [R2Lab](https://r2lab.inria.fr/run.md).

