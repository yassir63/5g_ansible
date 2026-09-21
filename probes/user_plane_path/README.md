# User-plane path probe

This external probe runs as an ephemeral container beside a gNB or UPF. It
does not enter, patch, or otherwise modify the network-function process or its
source code. It sees the pod network namespace and exports low-cardinality
Prometheus metrics at `/metrics`.

The probe resolves the N3 interface from the pod's Multus `network-status`
annotation after matching a configured network name. An explicit N3 override
is available for an unusual deployment. At a UPF, it additionally resolves N6
as the single default route in the pod network namespace; a gNB observes N3
only.

It exports Linux `/proc/net/dev` counters for each resolved path: receive and
transmit bytes, packets, errors, and drops. It also opens a passive raw socket
on N3 and counts frames carrying UDP port 2152. This is evidence that the
selected interface carries GTP-U; it is not a packet-loss measurement and does
not correlate individual UE packets.

Every metric has a static `anchor=gnb|upf` label. It does not export UE
identifiers, IP addresses, TEIDs, or packet payloads. The
`user_plane_probe_path_info` metric records the selected local interface and
discovery method, while `user_plane_probe_path_ready`,
`user_plane_probe_gtpu_capture_active`, and `user_plane_probe_gtpu_seen`
make an unresolved or inactive observation explicit. An idle N3 can
legitimately leave `user_plane_probe_gtpu_seen` at zero.

## Enable after building the image

Build from the repository root after all probe changes are ready:

```bash
docker build -f probes/user_plane_path/Dockerfile -t REGISTRY/user-plane-path-probe:TAG .
```

Then set the same image for each anchor you want to observe:

```yaml
upf_data_plane_probe_enabled: true
upf_data_plane_probe_image: REGISTRY/user-plane-path-probe:TAG
gnb_data_plane_probe_enabled: true
gnb_data_plane_probe_image: REGISTRY/user-plane-path-probe:TAG
```

The current Open5GS, free5GC, srsRAN, OAI, and UERANSIM profiles use the
`n3network` Multus name. Each role discovers the actual interface from the
live pod annotation; it does not assume that the interface is named `n3`.
Override the network name or interface only for a deployment that differs:

```yaml
upf_data_plane_probe_n3_network_names: ["my-n3-network"]
upf_data_plane_probe_n3_interface: ""
upf_data_plane_probe_n6_interface: ""
gnb_data_plane_probe_n3_network_names: ["my-n3-network"]
gnb_data_plane_probe_n3_interface: ""
```

The roles label only their selected pods and create headless Services that
select those labels. This avoids assuming vendor-specific pod labels.
Ephemeral containers cannot be edited in place: change the probe image or
configuration by recreating the target pod before rerunning the monitoring
role.
