# User-plane path probe

This external probe runs as an ephemeral container beside a gNB or UPF. It
does not enter, patch, or otherwise modify the network-function process or its
source code. It sees the pod network namespace and exports low-cardinality
Prometheus metrics at `/metrics`.

The deployment role passes the pod's Multus `network-status` annotation to the
probe. For a gNB, an N3-like attachment or interface name is a provisional
candidate. A pod-wide passive observer can confirm that candidate or select a
different interface when it sees valid GTP-U there. No traffic is required for
deployment: an idle pod stays provisional or unknown. If GTP-U appears on more
than one interface, the automatic result becomes ambiguous. Explicit network
names or an interface override take precedence over automatic selection. At a
UPF, N3 still uses a configured network name and N6 uses the single default
route in the pod network namespace.

It exports Linux `/proc/net/dev` counters for each resolved path: receive and
transmit bytes, packets, errors, and drops. Its passive raw socket recognizes
UDP port 2152 with a GTPv1-U header. At a gNB it listens across the pod's
interfaces to discover N3; at a UPF it listens on the configured N3 interface.
This is not a packet-loss measurement and does not correlate UE packets.

Every metric has a static `anchor=gnb|upf` label. It does not export UE
identifiers, IP addresses, TEIDs, or packet payloads. The
`user_plane_probe_path_info` records the selected local interface and discovery
method. `user_plane_probe_path_ready` means a candidate interface is selected;
`user_plane_probe_path_confirmed` means valid GTP-U was observed on it.
`user_plane_probe_gtpu_capture_active` and `user_plane_probe_gtpu_seen` show
whether capture is active and whether any valid GTP-U has appeared. An idle N3
can legitimately remain unconfirmed.

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

The gNB role does not use vendor-specific network names. Override the network
name or interface only when automatic selection remains ambiguous. The UPF
role still needs an N3 network name when several secondary networks exist:

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
