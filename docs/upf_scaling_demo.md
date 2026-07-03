# Elastic UPF Scaling Demo

This demo branch implements the first scaffold for a closed-loop UPF scaling
demonstration on Open5GS.

The demo is intentionally framed as **elastic orchestration for new or
re-established PDU sessions**, not seamless 3GPP UPF session migration. Open5GS
does not move an active PFCP/GTP-U session from one UPF to another. The demo
therefore scales a secondary UPF path and emits an explicit event telling the
operator/controller to reconnect selected UEs or release/re-establish their PDU
sessions.

## What Is Implemented Now

- A Prometheus-driven controller script:
  `scripts/demo/upf_scaling_controller.py`
- A wrapper playbook:
  `playbooks/run_upf_scaling_demo.yml`
- Dry-run by default.
- Scale-out and scale-in hysteresis.
- JSONL event log suitable for Grafana annotations or later notebook analysis.

## Default Control Loop

1. Query Prometheus for primary UPF CPU.
2. Optionally query latency.
3. If CPU or latency exceeds the scale-out threshold, scale configured
   secondary targets to 1 replica.
4. Emit `ue_reestablishment_required=true`.
5. When CPU/latency remain below lower thresholds for the stable period, scale
   the configured secondary targets back down.

Default scale targets are:

```text
deployment/upf2
deployment/smf2
```

These are placeholders for the existing Open5GS slice-2 path. For a polished
demo, the next step is to make this target a true secondary UPF for the same
DNN/slice or to use a demo profile that intentionally moves selected UEs to the
secondary slice during controlled re-establishment.

## Dry-Run Test

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e prometheus_url=http://localhost:30095 \
  playbooks/run_upf_scaling_demo.yml
```

This does not scale anything. It writes artifacts under:

```text
results/upf-scaling-demo-<run_id>/
```

## Apply Mode

Only use this after verifying the target deployments exist and the query works.

```bash
ansible-playbook -i inventory/default/hosts.ini \
  -e prometheus_url=http://localhost:30095 \
  -e demo_apply=true \
  -e demo_iterations=0 \
  playbooks/run_upf_scaling_demo.yml
```

## Useful Checks

```bash
kubectl -n open5gs get deploy,pods,svc | grep -E 'upf|smf'
curl 'http://localhost:30095/api/v1/query?query=up'
```

For the default CPU query:

```bash
curl --get 'http://localhost:30095/api/v1/query' \
  --data-urlencode 'query=sum(rate(container_cpu_usage_seconds_total{namespace="open5gs",pod=~".*upf1.*",container!="POD",container!=""}[30s]))'
```

## Next Implementation Steps

1. Add an Open5GS demo profile that cleanly distinguishes primary and secondary
   UPF placement.
2. Add explicit SMF/UPF patching or Kustomize overlays for secondary UPF
   activation.
3. Add UE reconnection automation for UERANSIM first, then R2Lab/COTS UEs.
4. Export controller state as Prometheus metrics.
5. Add Grafana panels and annotations for scale-out and scale-in events.
