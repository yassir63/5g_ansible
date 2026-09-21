# Monitoring preparation for diagnosis experiments

The first deployment should collect enough evidence to compare healthy traffic,
UPF resource contention, and target-server or transport delay. These changes
extend the existing experiment runner and artifact exports.

## Prepared before deployment

The pinned kube-prometheus-stack chart is 51.9.4. Its default cAdvisor drop rule
removes `container_cpu_cfs_throttled_seconds_total`. The monitoring values now
retain that metric while preserving the other upstream drop rules. Kubelet
scraping and kube-state-metrics were already enabled. Node-exporter was disabled
and is now enabled, including the pressure and schedstat collectors.

Node-exporter and kube-state-metrics are scraped every 5 seconds with a 4-second
timeout. Existing probe and kubelet scrape intervals are unchanged. These
infrastructure settings can be overridden with `monitoring_infrastructure_interval`
and `monitoring_infrastructure_scrape_timeout` (timeout must not exceed interval).
Set `monitoring_node_exporter_enabled=false` if another managed node-exporter
already covers the hosts; then verify that its targets are scraped.

| Evidence | Source | Prepared change |
| --- | --- | --- |
| Container CPU throttled time and fraction of throttled periods | Kubelet/cAdvisor | Retain throttled time and export both measures |
| CPU, memory and I/O pressure; aggregate scheduler waiting time; runnable tasks | Node-exporter | Enable host collection and export queries |
| CPU modes including softirq, available memory, disk activity and free storage | Node-exporter | Export queries |
| Host and pod interface traffic and drops; host interface errors | Node-exporter/cAdvisor | Export queries with interface labels |
| Restarts, OOM evidence, readiness, waiting/termination reasons | kube-state-metrics/cAdvisor | Export state and counters |
| CPU/memory requests and limits; node allocatable resources | kube-state-metrics | Export resource context |
| Pod UID, container identity and images, pod-to-node placement, host identity | kube-state-metrics/node-exporter | Export correlation context |
| Scrape availability, duration, collector success and host clock indicators | Prometheus/node-exporter | Export data-quality evidence |
| Latency, UE/slice labels, traffic and RAN metrics | Existing probes/exporters | Existing queries retained |
| Component logs | Existing Loki/Promtail | Existing live collection retained; optional read-only ingestion check |

The new queries are in `configs/artifacts/default_prometheus_queries.json`, so
the `default_5g_observability` artifact profile includes them automatically.
Other profiles with their own query files, such as churn, are unchanged. All
Prometheus labels remain available in the exported CSV's `metric_json` column.

## One deployment, followed by a coverage check

1. Deploy from a checkout containing these changes with monitoring enabled.
2. Allow at least two minutes of scrapes. Run normal traffic with the intended
   latency probes active; packet-dependent metrics need actual traffic.
3. From a machine that can reach the monitor node, run:

```bash
python3 scripts/artifacts/check_observability.py \
  --prometheus-url http://MONITOR_HOST:30095 \
  --loki-url http://MONITOR_HOST:31000 \
  --out results/observability_check.json
```

This only reads APIs. It checks the last five minutes of the same queries used
by artifact exports, inventories active scrape targets, and optionally checks
for a recent Loki log entry. It requires only Python's standard library. A
successful check does not certify diagnostic completeness: inspect the report
for coverage of every intended node, pod, interface and probe role.

Statuses distinguish finite samples (including valid zeros), empty results,
non-finite values, mixed finite/non-finite values, and query errors. Empty OOM or
termination-reason metrics can be normal when no such event occurred. Missing
latency series can mean idle traffic or inactive probes. Do not fill missing
measurements with zeros. Exit status 2 indicates API/query errors or unhealthy
discovered targets; status 0 means the checks completed, not that all desired
measurements exist. Empty target discovery also needs investigation.

Monitoring settings can subsequently be updated on the existing cluster without
running the full infrastructure deployment:

```bash
ansible-playbook -i inventory/default/hosts.ini playbooks/update_monitoring.yml
```

This runs the existing monitoring role, including Prometheus, Grafana and Loki
configuration. Keep the same inventory and variable overrides used for the
original deployment. It does not redeploy the 5G core or RAN. Export-query changes
only require rerunning collection while the source data remains in retention.

## What the live deployment must establish

- Node-exporter reaches every intended Kubernetes node. A traffic server or
  physical UE outside the cluster needs separate host monitoring if its resource
  state is required; the DaemonSet does not cover external hosts.
- CPU pressure requires kernel PSI support. Scheduler counters may depend on
  kernel scheduling statistics being enabled. Collector success and a zero
  counter alone do not prove that the kernel is generating useful statistics.
- CFS throttling depends on runtime/cgroup support and workload configuration.
  The throttled-period fraction is not a CPU utilization percentage. Aggregate
  scheduler waiting time is not packet latency and can exceed one second per
  second across CPUs/tasks.
- cAdvisor network measurements describe network namespaces, often through the
  pod sandbox. Do not filter out `container="POD"` for these measurements or sum
  duplicate network-namespace series as independent interfaces.
- Host/pod counters do not guarantee coverage of Multus, SR-IOV VFs, OVS or DPDK
  paths. Identify the actual N2/N3/N6 interfaces and check where counters change.
  Driver, VF, queue or OVS statistics may require a later dedicated collector.
- Last-termination reason is state, not a timestamped event. Interpret it with
  restart counters and logs. Missing resource-limit series may mean no limit was
  configured. Preserve this distinction from zero limits.
- Host clock indicators do not prove cross-host synchronization or PTP accuracy.
  Verify synchronization before aligning logs and metrics across nodes.
- Scraping or exporting every second does not imply the source refreshes every
  second. New infrastructure rate queries use one-minute windows and smooth short
  events; retain event timestamps and raw source metrics for finer analysis.
- Check scrape duration, exporter resource use and disk growth during a pilot.
  Increased monitoring must itself be included in overhead measurements.

## Next evidence to add only when the experiment needs it

### Prepared for the next deployment

- **Pending image build:** the UE-mapper API now has `/metrics`. Build/push a new
  mapper image, select it with `ue_mapper_api_image`, and only then enable
  `ue_mapper_metrics_enabled`. Scraping remains disabled by default. See the
  [mapper checklist](../monitoring/sliceawareness/ue_mapper/README.md).
- **No image build needed:** artifact queries now include the srsRAN metrics
  already referenced by `monitoring-dashboard-srsran.json`: UL/DL bitrate,
  successful/failed transmissions, MCS, rank, CQI, PUCCH/PUSCH SNR and DL buffer
  occupancy. The same queries automatically enter the availability check.

These RAN queries retain every source label, including RNTI and exporter identity
when provided. They do not join RNTI to the mapper's NGAP RAN UE ID: those identifiers
are not interchangeable. They also do not assume the transmission values are
cumulative counters or that buffer/bitrate units match a particular exporter
version. Preserve the raw values; verify units, update interval and counter reset
semantics against the deployed exporter before computing BLER or training a model.
Idle or unsupported metrics remain missing, not zero. OAI and UERANSIM deployments
are not expected to provide these srsRAN series. PRB and HARQ-specific metrics still
need source verification; this change does not instrument the RAN implementation.

The N2 probe still needs live validation for observable procedure outcomes and
durations. RAN evidence should reuse the existing implementation-specific
exporters/logs (for example BLER, HARQ, MCS, PRB use and radio quality where
available). Neither is replaced by host metrics.

For a diagnosis dataset, record verified fault type, location, actual start/end,
severity, traffic settings and repeated run identity. Keep injection commands and
fault-revealing scenario names out of model inputs. The generic runner already
records windows and artifacts; validate a complete baseline/fault/recovery run
before collecting a campaign.

The default observability artifact profile archives setup snapshots before and
after a run and polls UE-mapper inventory every two seconds during the workload.
See [experiment artifacts](experiment_artifacts.md#setup-snapshots-and-ue-context-history)
for configuration and completeness checks. The collector archives Kubernetes
logs; exporting Loki windows remains follow-up work. Preserve relevant logs
before retention expires for reproducible offline diagnosis.

## Upstream references

- [Pinned chart values](https://github.com/prometheus-community/helm-charts/blob/kube-prometheus-stack-51.9.4/charts/kube-prometheus-stack/values.yaml)
- [Node-exporter collectors](https://github.com/prometheus/node_exporter#collectors)
- [cAdvisor metrics](https://github.com/google/cadvisor/blob/master/docs/storage/prometheus.md)
