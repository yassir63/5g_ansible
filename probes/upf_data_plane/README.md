# UPF data-plane probe

This external probe runs as a privileged **ephemeral container** in a UPF pod.
It does not enter, patch, or otherwise modify the UPF process or its source
code. It sees the pod network namespace and exports low-cardinality Prometheus
metrics at `/metrics`.

The probe resolves two local interfaces:

- **N3** comes from the UPF pod's Multus `network-status` annotation after
  matching a configured network name. `UPF_PROBE_N3_INTERFACE` is a deliberate
  override for an unusual deployment.
- **N6** is the single default route in the UPF pod network namespace.
  `UPF_PROBE_N6_INTERFACE` is the corresponding override.

It exports Linux `/proc/net/dev` counters for each resolved path: receive and
transmit bytes, packets, errors, and drops. It also opens a passive raw socket
on the resolved N3 interface and counts frames carrying UDP port 2152. This is
evidence that the locally selected interface carries GTP-U; it is not a
packet-loss measurement and it does not correlate individual UE packets.

No UE, IMSI, IP address, TEID, packet payload, or arbitrary interface label is
exported. `upf_probe_path_info` records the selected interface and discovery
method, while `upf_probe_path_ready`, `upf_probe_gtpu_capture_active`, and
`upf_probe_gtpu_seen` make an unresolved or inactive observation explicit.
An idle N3 can legitimately leave `upf_probe_gtpu_seen` at zero.

## Enable after building the image

Build from the repository root after all probe changes are ready:

```bash
docker build -f probes/upf_data_plane/Dockerfile -t REGISTRY/upf-data-plane-probe:TAG .
```

Then set, for example:

```yaml
upf_data_plane_probe_enabled: true
upf_data_plane_probe_image: REGISTRY/upf-data-plane-probe:TAG
```

The current Open5GS and free5GC profiles use the `n3network` Multus network
name. The role discovers the actual interface from the live pod annotation;
it does not assume that the interface is named `n3`. Override the network name
or either interface only for a deployment that differs:

```yaml
upf_data_plane_probe_n3_network_names: ["my-n3-network"]
upf_data_plane_probe_n3_interface: ""
upf_data_plane_probe_n6_interface: ""
```

The role labels only the selected UPF pods and creates a headless Service that
selects those labels. This avoids assuming a vendor-specific UPF pod label.
Ephemeral containers cannot be edited in place: change the probe image or
configuration by recreating the UPF pod before rerunning the monitoring role.
