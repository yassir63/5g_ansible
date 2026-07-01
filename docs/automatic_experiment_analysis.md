# Automatic Experiment Analysis

This workflow runs the experiment and creates a supervisor-facing Jupyter
notebook automatically.

The notebook is always named:

```text
experiment_analysis.ipynb
```

It loads the run artifacts and shows:

- iperf throughput and retransmissions
- all exported Prometheus metrics
- direct latency time series
- latency CDF, tail CCDF, and box plots
- UPF minus gNB latency gap
- P50/P95/P99 latency over time
- same-packet RTT/gap/pair-rate/rejection plots
- MAC throughput and radio context such as MCS, BLER, HARQ, SNR, PRACH
- monitoring overhead: CPU and memory
- pcap RTT validation if `pcap_tcp_rtt.csv` exists

## Full TCP Paper Run

Use this when you want `deploy.sh` to deploy the system and then run the TCP
paper scenarios.

Interactive mode:

```bash
./deploy.sh
```

When prompted for an optional scenario, choose **TCP paper scenarios**. The
script then asks:

- which TCP paper scenarios to run
- iperf target server
- iperf duration override, optional

It will state that `experiment_analysis.ipynb` is created automatically.

Flag mode:

```bash
./deploy.sh --tcp-paper all --target-server sopnode-w3
```

Run only selected TCP scenarios:

```bash
./deploy.sh \
  --tcp-paper 01_decomp_baseline_all_ues,02_decomp_far_ue_radio \
  --target-server sopnode-w3
```

Run only the UPF CPU stress decomposition scenario:

```bash
./deploy.sh \
  --tcp-paper 03_decomp_upf_cpu_stress \
  --target-server sopnode-w3
```

The result directory is:

```text
results/tcp-paper-YYYYMMDD_HHMMSS/
```

Open:

```text
results/tcp-paper-YYYYMMDD_HHMMSS/experiment_analysis.ipynb
```

## Full Latency Validation Run

Use this when you want `deploy.sh` to deploy the system and then run the
validation pipeline.

Interactive mode:

```bash
./deploy.sh
```

When prompted for an optional scenario, choose **Latency validation pipeline**.
The script then asks:

- which validation scenarios to run
- iperf target server
- validation iperf duration override, optional

It will state that `experiment_analysis.ipynb` is created automatically.

Flag mode:

```bash
./deploy.sh --validation near --target-server sopnode-w3
```

Run all validation scenarios:

```bash
./deploy.sh --validation all --target-server sopnode-w3
```

Run selected validation scenarios:

```bash
./deploy.sh \
  --validation v01_candidate_signal_baseline,v03_controlled_delay \
  --target-server sopnode-w3
```

The result directory is:

```text
results/latency-validation-YYYYMMDD_HHMMSS/
```

Open:

```text
results/latency-validation-YYYYMMDD_HHMMSS/experiment_analysis.ipynb
```

## Scenario-Only Mode

Use this when the deployment is already running and you only want to run a new
experiment plus notebook generation.

```bash
./deploy.sh -n --scenario-only --tcp-paper all --target-server sopnode-w3
```

Validation-only:

```bash
./deploy.sh -n --scenario-only --validation near --target-server sopnode-w3
```

`-n` means “reuse the previous inventory and deployment environment”.

## Prometheus URL

By default, the playbooks resolve Prometheus from the `monitor_node` inventory
entry:

```text
http://<monitor_node_ip>:30095
```

Override it when needed:

```bash
./deploy.sh \
  --tcp-paper all \
  --target-server sopnode-w3 \
  --prometheus-url http://172.28.2.76:30095
```

Prometheus is exported at `1s` step using:

```text
scripts/validation/paper_prometheus_queries.json
```

## Duration

Override iperf duration:

```bash
./deploy.sh \
  --tcp-paper 01_decomp_baseline_all_ues \
  --target-server sopnode-w3 \
  --duration 180
```

For validation:

```bash
./deploy.sh \
  --validation near \
  --target-server sopnode-w3 \
  --duration 120
```

## Manual Export for an Existing Run

If you already ran an experiment and only need Prometheus export plus notebook:

```bash
scripts/validation/export_paper_prometheus_1s.sh \
  --prometheus-url http://172.28.2.76:30095 \
  --start START_EPOCH \
  --end END_EPOCH \
  --out-dir results/my-existing-run
```

For the May 1 run from `15:40` to `16:48` Europe/Paris:

```bash
scripts/validation/export_paper_prometheus_1s.sh \
  --prometheus-url http://172.28.2.76:30095 \
  --start 1777642800 \
  --end 1777646880 \
  --out-dir paper_artifacts/prometheus_1s_2026-05-01_1540_1648
```

This writes:

```text
prometheus_timeseries_1s.csv
prometheus_export_summary.json
experiment_analysis.ipynb
```

## Recommended Supervisor Workflow

1. Run a TCP scenario or validation run with `deploy.sh`.
2. Open the generated `experiment_analysis.ipynb`.
3. Run all cells.
4. Review the metric inventory first.
5. Discuss the paper-candidate sections:
   direct latency, CDF/CCDF, box plots, UPF-gNB gap, same-packet validation,
   throughput/radio context, and overhead.
6. Decide whether another experiment needs extra UEs, longer duration, a
   controlled delay, or pcap collection.
