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

- Alert-driven UPF scaling integrated into the existing Kopf controller:
  `monitoring/controllers/kopf/k8scontroller.py`
- An optional in-controller Alertmanager webhook:
  `http://kopf-controller-alerts.<namespace>.svc:<port>/alert`
- A Prometheus-driven standalone controller script for local/offline dry runs:
  `scripts/demo/upf_scaling_controller.py`
- A wrapper playbook for the standalone dry-run path:
  `playbooks/run_upf_scaling_demo.yml`
- Dry-run by default.
- Scale-out and scale-in hysteresis.
- JSONL event log suitable for Grafana annotations or later notebook analysis.

The production/demo path should use the Kopf controller. The standalone script
is kept as a local harness for testing the decision logic without rebuilding the
controller image.

## Kopf Controller Mode

UPF scaling is disabled unless explicitly enabled:

```yaml
upf_scaling_enabled: "1"
```

Useful controller variables:

```yaml
alert_webhook_enabled: "1"
alert_webhook_port: "5000"
upf_scaling_enabled: "1"
upf_scaling_namespace: "open5gs"
upf_scaling_scale_out_alerts: "UPFOverload,HighUPFCPU,HighUPFLatency,LowGTPThroughput"
upf_scaling_targets: "deployment/upf2,deployment/smf2"
upf_scaling_scale_out_replicas: "1"
upf_scaling_scale_in_replicas: "0"
upf_scaling_stable_seconds: "60"
```

When `alert_webhook_enabled=1`, Alertmanager can post to:

```text
http://kopf-controller-alerts.open5gs.svc:5000/alert
```

The controller writes the latest Alertmanager payload to `/tmp/alert.json`, reads
that file in its normal Kopf reconciliation loop, scales the configured
deployments, and records its small state machine in `/tmp/upf_scaling_state.json`.

An alert triggers scale-out when either:

- its `alertname` is listed in `upf_scaling_scale_out_alerts`; or
- it has label `upf_scaling_action: scale_out`.

Example Prometheus alert label:

```yaml
labels:
  severity: warning
  upf_scaling_action: scale_out
```

The scale-out event intentionally logs:

```text
Controlled UE/PDU session re-establishment is required.
```

That line is part of the demo truth: this is not live UPF migration.

## Controller Loop

1. Alertmanager posts the latest alert payload to the Kopf webhook.
2. The Kopf timer reads the alert payload and its local scaling state.
3. If a scale-out alert is firing, scale configured
   secondary targets to 1 replica.
4. Emit `ue_reestablishment_required=true`.
5. When no scale-out alert is firing for the stable period, scale
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

The standalone script still queries Prometheus directly. It is only a local
decision-loop harness; the deployed demo should use the Kopf/Alertmanager path.

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
