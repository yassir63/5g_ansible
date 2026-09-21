# NGAP PDU-session setup metrics: merged into the AMF sniffer

This module is an additional output of the existing `amf-sniffer`; it is not a
second capture process or ephemeral container. The AMF sniffer captures SCTP once,
then both updates Redis for UE/TEID mapping and observes NGAP PDU-session setup.
It does not change the AMF, gNB, Redis mapper data model or user-plane packet path.

No image has been built or published. NGAP metrics are disabled by default with
`amf_ngap_metrics_enabled: false`, so no Service or Prometheus target changes
until the rebuilt AMF-sniffer image is selected and explicitly enabled.

## Build and enable later

Build the existing AMF-sniffer image from the repository root, choosing an immutable
tag available to Kubernetes nodes of the intended architecture:

```bash
docker build -f monitoring/sliceawareness/amfsniffer/Dockerfile \
  -t REGISTRY/amf-sniffer:TAG .
docker push REGISTRY/amf-sniffer:TAG
```

Then set these deployment variables before a normal deployment:

```yaml
amf_sniffer_image: REGISTRY/amf-sniffer:TAG
amf_ngap_metrics_enabled: true
# Optional: only override when the AMF control-plane capture interface differs.
# amf_sniffer_capture_interface: n3
```

The AMF sniffer role injects its existing `amf-sniffer` ephemeral container and,
when metrics are enabled, applies a headless Service that Prometheus discovers.
It selects the same AMF pod labels as the existing AMF metrics Service. Check that
it has endpoints and that `ngap_pdu_session_setup_capture_started` is `1` before
interpreting empty procedure metrics. A missing or wrong control-plane interface
leaves the capture alive but produces no NGAP observations.

Ephemeral containers cannot be edited or removed. Changing the image, interface
or timeout after injection requires recreating the AMF pod so the role can inject
the new `amf-sniffer` image; do this in a planned deployment window. The sniffer
has no resource requests/limits because Kubernetes does not allow them for
ephemeral containers. Include its CPU and memory use in overhead evaluation.

In this repository's Open5GS configuration, the AMF binds its N2 address on the
Multus interface named `n3`, so the existing default capture interface remains
`n3`. This is a deployment-specific interface name, not an assertion that NGAP
uses N3. For a different core or network attachment, set
`amf_sniffer_capture_interface` to the actual AMF control-plane interface.

## What is measured

The probe recognizes `PDU Session Resource Setup Request` and `Response` messages
defined by NGAP. It correlates only an exact match of AMF UE NGAP ID, RAN UE NGAP
ID and the observed PDU-session-ID set. No identifiers appear as Prometheus
labels.

| Metric family | Meaning |
| --- | --- |
| `ngap_pdu_session_setup_requests_total` | Observed setup request messages |
| `ngap_pdu_session_setup_responses_total{outcome}` | Observed responses; `success`, `failure`, `mixed`, or `unclassified` according to decoded response-list markers |
| `ngap_pdu_session_setup_completed_total{outcome}` | Only exact locally correlated request/response pairs |
| `ngap_pdu_session_setup_duration_seconds` | Observation time for those correlated pairs, not UE registration or end-to-end setup time |
| `ngap_pdu_session_setup_pending` | Locally observed requests still awaiting a response |
| `..._expired_total` | Pending request passed the local observation timeout with no observed response |
| `..._unmatched_responses_total` | Response could not be matched exactly to a locally observed request |
| `..._untrackable_*_total` | Required identifiers were absent or not decoded |
| `..._overlapping_requests_total` | A second unresolved request used the same UE/session set |
| `..._decode_errors_total` | Packet-processing exception; inspect probe logs |

An expired, unmatched, untrackable, or unclassified observation is a **data-quality
signal**, not a confirmed 5G failure. Packet loss, capture restart, dissector
version differences, simultaneous procedures, probe startup timing and missing
N2 visibility can all cause these values. A response can carry successful and
failed items together; it is reported as `mixed`, not as a success. The probe does
not parse standardized cause categories, correlate registration, or establish
full PDU-session completion beyond this N2 exchange. Validate the decoded fields
against a saved test pcap from every target core/RAN combination before using the
metrics in a model.

The default experiment artifact queries include this probe. Before the image is
built and enabled, those query results are expected to be empty.
