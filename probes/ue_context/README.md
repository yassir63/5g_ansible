# UE context history sampler

This external collector records sampled `/inventory/ues` history. The generic
experiment runner starts and stops it; it does not change the UE mapper or the
network functions. It requires only Python's standard library and, when using
the Kubernetes service proxy, `kubectl` with permission to access `services/proxy`.

Configure it through `collect.ue_context` in an artifact profile. See
[experiment artifacts](../../docs/experiment_artifacts.md#setup-snapshots-and-ue-context-history).

For direct API access it also runs independently:

```bash
python3 probes/ue_context/probe.py \
  --url http://MAPPER_HOST:PORT \
  --run-id development-test \
  --out-dir /tmp/ue-history-test \
  --stop-file /tmp/ue-history-test/STOP \
  --interval-seconds 2 --max-seconds 600
```

Creating the stop file or sending SIGTERM/SIGINT requests a final snapshot and
clean exit. Each run needs a fresh output directory; existing history is never
overwritten. `max_seconds` bounds the sampling schedule, with a final request
allowed to finish afterward. This limits orphaned sampling after a controller
disconnect. There is no background Prometheus server in this artifact sampler;
the existing Prometheus exporters and Loki continue to provide live monitoring.

Outputs:

- `history.jsonl`: run ID, sequence, request start/completion times, duration,
  status, inventory rows, and possible truncation flag for each observation.
- `ready.json`: the first observation, including an error if the mapper was not
  reachable. Its existence means polling began, not that the mapper is healthy.
- `summary.json`: number of observations/errors, sampling settings and stop reason.

An error record has no UE list. A successful empty response has `ues: []`.
Responses at the requested limit are flagged as possibly truncated. Neither
failed nor potentially truncated observations establish that a UE disconnected.
This API has no pagination guarantee; increase the limit if appropriate and
verify inventory completeness on the deployment.

This is sampled state, not an attach/detach event log: short-lived changes between
polls may be missed. Preserve both request times when joining with other data.
The mapper's RAN UE identifier must not be assumed to be the radio RNTI.
