# UE-mapper metrics: prepared, image build pending

The API now serves `/metrics` using the
[Prometheus Python custom collector](https://prometheus.github.io/client_python/collector/custom/).
The probe-specific collector code lives in `probes/ue_mapper_metrics/`.
Existing lookup and inventory responses are unchanged. Metrics read Redis directly
and include incomplete stored contexts that `/inventory/ues` filters out.

## Build later, before enabling scraping

No image has been built or published for this change. The deployment still uses
the existing image and `ue_mapper_metrics_enabled: false` by default.

Before the next deployment:

1. Build and push the Dockerfile in `monitoring/sliceawareness/ue_mapper` from
   the repository root with a new immutable tag appropriate for the deployment
   machines' architecture.
2. Set `ue_mapper_api_image` to that tag and `ue_mapper_metrics_enabled: true`.
3. Apply the sliceawareness role through your deployment workflow. The generic
   `update_monitoring.yml` playbook does not deploy the sliceawareness API.
4. Verify `/metrics`, then run the observability check during traffic.

Only the Service is annotated for scraping, avoiding duplicate pod/service
series. When applying the standalone `ue_mapper_deployment.yaml`, update its image
and add Service annotations for `/metrics`, port `80`, and scraping explicitly;
the Ansible flag applies only to the role template.

## Meaning of the metrics

| Metric | Meaning |
| --- | --- |
| `ue_mapper_context_records` | Number of stored `ran:*` records |
| `ue_mapper_context_records_by_slice` | Stored contexts per observed slice |
| `ue_mapper_identified_ues_by_slice` | Distinct known IMSIs in those contexts per slice |
| `ue_mapper_paired_contexts_by_slice` | Contexts containing both UL and DL TEID values |
| `ue_mapper_context_missing_fields` | Contexts missing IMSI, UE IP, slice, TEIDs, or referenced TEID hashes |
| `ue_mapper_collection_success` | Complete bounded scan succeeded |
| `ue_mapper_collection_busy` | Another collection prevented this one from starting |
| `ue_mapper_redis_up` | Redis operations succeeded during collection; also zero when busy |
| `ue_mapper_collection_duration_seconds` | Collection time |

There are no IMSI, IP, RNTI or TEID labels. The slice and missing-field labels
are aggregate dimensions. Unknown slice information is not assigned to a
default slice. Unknown SD may mean an absent SD, not necessarily bad data.

These are stored mapping counts, **not verified active UE or PDU-session counts**.
The AMF writer normally creates `ran:*` only after obtaining both TEIDs; UEs
without any stored context cannot be counted here. `ran:*` is currently keyed
only by RAN UE ID, so identifier collisions across gNBs are an existing limitation.
No claim is made that a missing field is a network failure. Redis reads are not
an atomic snapshot, and disappearing records can occur during attach/release.

SCAN replaces blocking KEYS for the metrics path; duplicate scan keys are
deduplicated and hash reads are pipelined. Collection is limited to 5,000 keys,
100 scan calls and a three-second processing budget, with one-second Redis socket
timeouts (an in-flight operation may extend the budget). Concurrent collections
do not start a second scan. An incomplete/failed scan omits context counts rather
than publishing partial counts or false zeros. A successful empty scan reports
zero total records; per-slice series disappear when the slice is no longer present.

Collection success does not establish freshness: existing writers do not supply
a reliable last-update timestamp. Freshness and attach/release event counters
require separate writer instrumentation and are not implemented here.

The generic artifact profile includes these queries. Until the new image is built
and scraping enabled, empty mapper query results are expected.
