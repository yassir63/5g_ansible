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
ansible-playbook -i inventory/default/hosts.ini \
  -e experiment_scenario_file=scenarios/my_self_contained_experiment.yml \
  playbooks/run_experiment.yml
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
```

Edit `scenarios/my_experiment.yml`, then run both files together:

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e experiment_scenario_file=scenarios/my_experiment.yml \
  -e experiment_artifacts_file=configs/artifacts/profiles/default_5g_observability.yml \
  playbooks/run_experiment.yml
```

The output is written to:

```text
results/experiment-<run_id>/
```

To test the mechanism without changing your paper scenarios, run the included
smoke-test scenario:

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e experiment_scenario_file=scenarios/experiment_examples/artifact_smoke_test.yml \
  -e experiment_artifacts_file=configs/artifacts/profiles/default_5g_observability.yml \
  playbooks/run_experiment.yml
```

It creates three short section windows and writes the command section output
under `section_logs/local_command/`.

For a real traffic example, run `qhat01` and `qhat03` uplink/downlink TCP iperf
at 40 Mb/s:

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e experiment_scenario_file=scenarios/experiment_examples/two_ue_iperf_40m.yml \
  -e experiment_artifacts_file=configs/artifacts/profiles/default_5g_observability.yml \
  -e target_server_host=sopnode-f2 \
  playbooks/run_experiment.yml
```

This creates separate artifact windows for server preparation, uplink traffic,
and downlink traffic. The per-section `section_logs/` directory contains the
command stdout/stderr plus fetched iperf tarballs for the uplink and downlink
sections.

There is also a direction-matrix example where every traffic combination is a
separate section:

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e experiment_scenario_file=scenarios/experiment_examples/two_ue_direction_matrix_40m.yml \
  -e experiment_artifacts_file=configs/artifacts/profiles/default_5g_observability.yml \
  -e target_server_host=sopnode-f2 \
  playbooks/run_experiment.yml
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
    enabled: false
  pcaps:
    enabled: false
```

Each section becomes a timeline window. If Prometheus splitting is enabled, the
runner creates:

```text
by_window/section/<section>/prometheus_timeseries.csv.gz
```

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
    namespaces: ["open5gs", "monarch"]
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

## Existing TCP And Validation Playbooks

The TCP paper and latency validation playbooks now share the same generic
timeline event recorder and default Prometheus query file.

Useful switches:

```bash
-e paper_collect_artifacts=false
-e validation_collect_artifacts=false
```

Specific switches still work:

```bash
-e paper_collect_prometheus=false
-e paper_collect_pod_logs=false
-e validation_capture_pcaps=false
-e validation_prometheus_queries_json=configs/artifacts/my_queries.json
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
```
