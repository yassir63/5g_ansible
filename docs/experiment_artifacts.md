# Generic Experiment Artifact Collection

This repository includes a generic experiment runner for collecting and slicing
observability artifacts around any user-defined scenario.

The runner does not need to understand what your experiment means. You define
named sections, and the runner records when each section starts and ends. After
the experiment, it can export Prometheus/Grafana-query data, split the CSV by
sections, collect Kubernetes pod logs, and optionally collect short pcaps.

## Quick Start

There are two supported styles.

Style A: self-contained scenario file. Put both the experiment sections and
the `collect` / `pcap` artifact settings in one YAML file:

```bash
./deploy.sh -n --scenario-only \
  --experiment scenarios/my_self_contained_experiment.yml \
  --experiment-artifacts none
```

Style B: separated experiment and artifact profile. This keeps the scenario
focused on what runs and the artifact profile focused on what is collected:

```bash
cp scenarios/experiment_templates/basic_sections.yml scenarios/my_experiment.yml
```

Choose an artifact profile:

```text
configs/artifacts/profiles/default_5g_observability.yml
configs/artifacts/profiles/pod_logs_and_pcaps.yml
configs/artifacts/profiles/churn_observability.yml
```

Edit `scenarios/my_experiment.yml`, then run both files together:

```bash
./deploy.sh -n --scenario-only \
  --experiment scenarios/my_experiment.yml \
  --experiment-artifacts default_5g_observability
```

The output is written to:

```text
results/experiment-<run_id>/
```

To test the mechanism without changing existing scenarios, run the included
smoke-test scenario:

```bash
./deploy.sh -n --scenario-only \
  --experiment uesim_artifact_validation \
  --experiment-artifacts default_5g_observability
```

It creates three short section windows and writes the command section output
under `section_logs/local_command/`.

Preview the merged experiment/artifact configuration without deploying or
running traffic:

```bash
./deploy.sh -n --scenario-only \
  --experiment uesim_artifact_validation \
  --experiment-artifacts default_5g_observability \
  --duration 60 \
  --dry-run-experiment
```

This loads the scenario YAML, merges the artifact profile, validates the
schema, prints the resolved sections and collection settings, then stops before
creating results, running sections, starting pcaps, collecting pod logs, or
exporting Prometheus.

The same schema validation runs before every real generic experiment. It checks
the structure the runner depends on:

- scenario and artifact files must be YAML maps;
- `sections` must be a list when present;
- each section needs a unique non-empty `name`;
- `runner.type` must be `command`, `playbook`, `tasks`, or `pause`;
- command runners need `runner.command`;
- playbook/task runners need `runner.file`;
- collection blocks must be maps or booleans where supported;
- pcap targets must be valid host or pod targets.

For a real traffic example, run `qhat01` and `qhat03` uplink/downlink TCP iperf
at 40 Mb/s:

```bash
./deploy.sh -n --scenario-only \
  --experiment two_ue_iperf_40m \
  --experiment-artifacts default_5g_observability \
  --target-server sopnode-f2
```

This creates separate artifact windows for server preparation, uplink traffic,
and downlink traffic. The per-section `section_logs/` directory contains the
command stdout/stderr plus fetched iperf tarballs for the uplink and downlink
sections.

There is also a direction-matrix example where every traffic combination is a
separate section:

```bash
./deploy.sh -n --scenario-only \
  --experiment two_ue_direction_matrix_40m \
  --experiment-artifacts default_5g_observability \
  --target-server sopnode-f2
```

It includes:

- `qhat01_ul_qhat03_dl`
- `qhat01_dl_qhat03_ul`
- `qhat01_ul_only`
- `qhat01_dl_only`
- `qhat03_ul_only`
- `qhat03_dl_only`

The `qhat01_ul_qhat03_dl` section enables pcap only for that section. The
`qhat01_dl_qhat03_ul` section disables the per-section Prometheus split, while
the full-run Prometheus export remains available.

## UERANSIM Churn As A Generic Experiment

UERANSIM attach/detach churn is also expressed as a generic experiment.
Select the churn scenario and artifact profile like this:

```bash
./deploy.sh \
  --experiment ueransim_churn \
  --experiment-artifacts churn_observability \
  -e "ueransim_churn_counts=10 50 100 200"
```

The churn waves run as the `ueransim_churn` section inside
`playbooks/run_experiment.yml`. Detailed churn artifacts are written under:

```text
results/experiment-<run_id>/section_logs/ueransim_churn/churn_results/
```

That directory includes churn summaries, UE mapper snapshots, optional AMF
pcaps, pod logs, and the churn playbook timeline.

## Scenario Structure

The experiment scenario file describes what happens and when:

```yaml
name: my_experiment
title: My experiment

sections:
  - name: baseline
    runner:
      type: pause
      seconds: 60

  - name: workload
    runner:
      type: command
      command: "./scripts/run_my_workload.sh"
```

The artifact profile file describes what to collect:

```yaml
collect:
  enabled: true
  prometheus:
    enabled: true
    url: "http://127.0.0.1:30095"
    queries_file: "configs/artifacts/default_prometheus_queries.json"
  split_by_windows: true
  pod_logs:
    enabled: true
    namespaces: auto
  pcaps:
    enabled: false
```

Pod logs are collected by running `kubectl` on one control host. By default,
the runner uses the `[monitor_node]` inventory host when present, then falls
back to the first `[core_node]`. It auto-detects a usable kubeconfig from
`$HOME/.kube/config`, `/root/.kube/config`, or `/etc/kubernetes/admin.conf`.
It connects as `root` by default because the cluster kubeconfig is commonly
root-owned on lab nodes.

With `namespaces: auto`, the collector lists pods across the cluster, keeps
application/observability namespaces such as `open5gs`, `oai`, `free5gc`, and
`monitoring`, skips Kubernetes infrastructure namespaces, and falls back to
`default` only when no application namespace with pods is found. To force a
specific set of namespaces, pass a YAML list or a comma-separated string:

```yaml
collect:
  pod_logs:
    enabled: true
    namespaces: ["open5gs", "monitoring"]
```

If your working Kubernetes context is on another machine, override it at
runtime:

```bash
./deploy.sh -n --scenario-only \
  --experiment uesim_artifact_validation \
  --experiment-artifacts default_5g_observability \
  -e experiment_pod_log_control_host=sopnode-f2
```

If that host uses a specific kubeconfig path, also pass:

```bash
-e experiment_kubeconfig=/path/to/kubeconfig
```

If the control host needs a different SSH user, pass:

```bash
-e experiment_pod_log_remote_user=<user>
```

Each section becomes a timeline window. If Prometheus splitting is enabled, the
runner creates:

```text
by_window/section/<section>/prometheus_timeseries.csv.gz
```

If artifact collection is enabled and no `sections` are defined, the runner
creates one default section:

```text
full_run
```

In interactive mode, `deploy.sh` asks how long this default window should last.
For non-interactive runs, the fallback is 60 seconds. Override the duration
with:

```bash
-e experiment_default_section_seconds=120
```

or set `default_section_seconds` in the scenario YAML.

## Setup Snapshots And UE Context History

The `default_5g_observability` profile now enables both collectors. Other profiles
and self-contained scenarios retain their previous behavior unless enabled:

```yaml
collect:
  enabled: true
  # Optional; defaults to the pod-log control host (monitor node, then core).
  control_host: sopnode-f1
  setup:
    enabled: true
    # Optional; defaults to core, RAN, monitor and traffic-server inventory hosts.
    hosts: [sopnode-f1, sopnode-w3]
  ue_context:
    enabled: true
    namespace: monitoring
    service: ue-mapper-api
    interval_seconds: 2
    timeout_seconds: 5
    max_seconds: 7200
    limit: 5000
    # Optional API base URL reachable from control_host; bypasses the service proxy.
    # url: http://MAPPER_HOST:PORT
```

Set either `enabled` to `false` to disable it; `collect.enabled: false` overrides
both. Dry runs validate and show the configuration without creating directories,
connecting to hosts, or starting a sampler. Hosts need Python 3; the control host
also needs kubectl and a working kubeconfig. Existing `experiment_kubeconfig`
applies to both collectors. Auto mode uses KUBECONFIG when set, otherwise the
first readable file among the user's kubeconfig, root's kubeconfig and
`/etc/kubernetes/admin.conf`. Set an explicit path if multiple contexts exist.

The sampler uses the Kubernetes service proxy for the mapper's port 80. It
does not need a new NodePort or a long-running port-forward. For a different
service port, provide a directly reachable `url`.

The runner writes:

```text
setup/
  before/{hosts,cluster,deployment}.json
  after/{hosts,cluster,deployment}.json
ue_context/
  history.jsonl
  ready.json
  summary.json
  collection_status.json
experiment_status.json
```

Host snapshots contain CPU/NUMA information from lscpu, memory, OS/kernel,
interfaces, routes and clock status. Cluster snapshots contain Kubernetes
versions, node capacity, pod placement, requested resources, container image IDs,
network attachments, service selectors and ConfigMap fingerprints. Missing tools
or inaccessible hosts are recorded as collection errors, not healthy results.
`hosts.json` and `cluster.json` retain Ansible return metadata; their `stdout`
fields contain the collector's JSON snapshot. Each command has collection times.

`deployment.json` records the source revision, tracked modifications, selected
RAN/core/RU/profile, traffic-server host and selected profile sections (PLMN,
DNNs, slices, UE assignments). It excludes the profile's security section and
does not archive Kubernetes Secrets or container environment values. ConfigMaps
are fingerprinted rather than copied because they may contain credentials.
The selected profile describes source settings, not necessarily the exact
effective runtime radio configuration; use image IDs and configuration
fingerprints to identify changes and preserve relevant sanitized config elsewhere
when required. `experiment_metadata.json` remains the source for resolved section
and traffic-runner settings. Explicitly add external UE/traffic hosts to `hosts`
when they are not in the default groups. Inventory SSH settings are reused.

Setup collection happens outside individual workload sections. UE sampling starts
before the first section and stops after the last, with a final observation. A
failed section still triggers sampler shutdown, history retrieval, final setup
collection and normal artifact collection; the playbook then reports the original
failure. `experiment_status.json` reports workload failure separately from
collection status. A killed Ansible controller or unreachable machine can prevent
cleanup; the sampler has a maximum duration and remote files are retained when
retrieval fails. Inspect `collection_status.json` before assuming completeness.

Sample times are observation times, not exact attach/detach times. Missing or
possibly truncated responses must not be interpreted as UE departures. Raise the
maximum duration for campaigns longer than two hours and verify cross-host clock
alignment before correlating the history with metrics and logs.

This addition does not add RAN export queries, an LLM, or a live UE-context metrics
endpoint. Those remain separate follow-up steps. Source configuration and
fault-revealing section names are evaluation context, not automatic model inputs.

## Runner Types

Run a shell command:

```yaml
runner:
  type: command
  command: "./scripts/run_traffic.sh --duration 120"
```

Run an existing playbook:

```yaml
runner:
  type: playbook
  file: playbooks/my_scenario.yml
  extra_vars:
    duration: 120
```

Include a task file:

```yaml
runner:
  type: tasks
  file: tasks/my_section.yml
```

Pause and collect a window:

```yaml
runner:
  type: pause
  seconds: 60
```

## Artifact Switches

Disable all heavy artifact collection:

```yaml
collect:
  enabled: false
```

Collect only Prometheus data:

```yaml
collect:
  enabled: true
  prometheus:
    enabled: true
    url: "http://127.0.0.1:30095"
  split_by_windows: true
  pod_logs:
    enabled: false
  pcaps:
    enabled: false
```

Collect pod logs:

```yaml
collect:
  pod_logs:
    enabled: true
    namespaces: ["open5gs", "monitoring"]
    since: "4h"
    tail_lines: 5000
    include_previous: true
```

## Prometheus/Grafana Queries

The default query file is:

```text
configs/artifacts/default_prometheus_queries.json
```

You can point to another file:

```yaml
collect:
  prometheus:
    enabled: true
    queries_file: "configs/artifacts/my_queries.json"
```

Or define queries inline:

```yaml
collect:
  prometheus:
    enabled: true
    queries:
      - name: direct_mean_ms_5s
        query: >
          (sum(rate(gtp_teid_latency_observed_ns_sum[5s])) by (imsi, ue_ip, slice, probe_role, mode)
          /
          sum(rate(gtp_teid_latency_observed_ns_count[5s])) by (imsi, ue_ip, slice, probe_role, mode))
          / 1000000
```

The CSV format is the same one consumed by the existing Grafana-style analysis
tools: `query_name`, `timestamp`, `value`, `metric_json`, and common labels.

## Pcaps

Pcaps are disabled by default because they can be large and may contain packet
payloads.

Enable pcaps globally:

```yaml
collect:
  pcaps:
    enabled: true

pcap:
  strict: false
  auto_install_tcpdump: true
  capture_seconds: 30
  filter: ""
```

If pcap collection is enabled and no targets are specified, the runner captures
all packets on `interface: any` for the first `[core_node]` and first
`[ran_node]` in the inventory, bounded by `capture_seconds`. Add a BPF filter
when you want a protocol-specific capture.

Override targets:

```yaml
pcap:
  targets:
    - name: core_n3
      type: host
      host: sopnode-f1
      interface: any
      filter: ""

    - name: upf_pod_n3
      type: pod
      namespace: open5gs
      pod_regex: upf
      container_regex: upf
      interface: any
      filter: "udp port 2152"
```

Per-section override:

```yaml
sections:
  - name: measured_traffic
    pcap:
      enabled: true
      capture_seconds: 60
    runner:
      type: playbook
      file: playbooks/my_traffic.yml
```

Disable the split Prometheus CSV for one section:

```yaml
sections:
  - name: traffic_without_section_prometheus
    collect:
      prometheus:
        enabled: false
    runner:
      type: command
      command: "./run_traffic.sh"
```

When `tcpdump` is missing, the runner tries package managers in this order when
available: `apt-get`, `dnf`, `yum`, `microdnf`, `apk`.

Default behavior:

```yaml
pcap:
  strict: false
```

If tcpdump cannot be installed, the experiment continues and the status is
written to:

```text
pcaps/pcap_summary.json
```

Strict behavior:

```yaml
pcap:
  strict: true
```

If a configured pcap target cannot start, the section fails before the workload
continues.

## Existing TCP Scenario Playbook

The TCP scenario playbook uses the same generic timeline event recorder and
default Prometheus query file as generic experiments.

Useful switches:

```bash
-e paper_collect_artifacts=false
```

Specific switches still work:

```bash
-e paper_collect_prometheus=false
-e paper_collect_pod_logs=false
-e paper_prometheus_queries_json=configs/artifacts/my_queries.json
```

## Templates

Templates live in:

```text
scenarios/experiment_templates/
```

Start with:

```text
basic_sections.yml
playbook_sections.yml
artifacts_only_observation.yml
```
