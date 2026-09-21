# UE mapper metrics collector

This is the probe-specific Prometheus collector used by the existing UE mapper
API. It reads bounded Redis mapper context and exports low-cardinality context
coverage metrics. It does not establish UE activity, attach events, or mapping
freshness, and it does not export IMSI, IP, RNTI, or TEID values as labels.

The deployed API service remains in `monitoring/sliceawareness/ue_mapper`.
Its new observability collector lives here under `probes/`. Build the mapper
image from the repository root:

```bash
docker build -f monitoring/sliceawareness/ue_mapper/Dockerfile \
  -t REGISTRY/ue-mapper-api:TAG .
```

Then select the tag with `ue_mapper_api_image` and set
`ue_mapper_metrics_enabled: true`.
