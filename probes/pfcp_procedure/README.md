# PFCP Session Establishment metrics

This probe is an additional output of the existing `smf-sniffer`; it is not a
second packet capture or ephemeral container. The SMF sniffer captures PFCP
once on UDP port 8805, continues its existing UE/TEID mapping work, and also
observes PFCP Session Establishment Request (`50`) and Response (`51`) messages.

For an exact local correlation, the probe requires the PFCP sequence number and
the two private IP endpoints observed in the packet. Those endpoints are held
only in process memory as a correlation key and are never emitted as Prometheus
labels. The response `Cause` is grouped as `accepted`, `rejected`, or `unknown`.
The timing is a local observation time across N4, not PDU-session completion or
end-to-end user-plane latency.

Expired, unmatched, untrackable, overlapping, and decode-error counts are
capture or correlation quality signals. They are not confirmed session failures.

## Build and enable later

Build from the repository root after all probe changes are ready:

```bash
docker build -f probes/smf_sniffer/Dockerfile \
  -t REGISTRY/smf-sniffer:TAG .
docker push REGISTRY/smf-sniffer:TAG
```

Then select the image and enable metrics:

```yaml
smf_sniffer_image: REGISTRY/smf-sniffer:TAG
smf_pfcp_metrics_enabled: true
```

For Open5GS, the default capture interface is `n4`; other deployments default
to `eth0`. Override only when necessary:

```yaml
smf_sniffer_capture_interface: ""
smf_pfcp_metrics_port: 9104
smf_pfcp_pending_timeout_seconds: 30
```

The role labels only the selected SMF pods and exposes a headless Prometheus
Service for those labels. An existing `smf-sniffer` ephemeral container cannot
be upgraded in place. Recreate the SMF pod before enabling this probe so the
role can inject the selected image.
