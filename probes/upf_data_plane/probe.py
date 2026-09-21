"""Passive UPF N3/N6 interface and GTP-U observation probe.

The probe deliberately knows nothing about a UPF implementation.  The Ansible
role passes the Multus network-status annotation from the target pod; this
module resolves the configured N3 network from that annotation and resolves
N6 from the pod network namespace's default route.  Explicit interface
overrides always win.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server


LOG = logging.getLogger("upf_data_plane_probe")
ETH_P_ALL = 0x0003
ETH_P_IP = 0x0800
ETH_P_IPV6 = 0x86DD
ETH_P_8021Q = 0x8100
ETH_P_8021AD = 0x88A8
PACKET_OUTGOING = 4
GTPU_PORT = 2152


@dataclass(frozen=True)
class PathDiscovery:
    """A locally discovered path, without claiming 3GPP topology semantics."""

    interface: str
    source: str


@dataclass(frozen=True)
class InterfaceCounters:
    receive_bytes: int
    receive_packets: int
    receive_errors: int
    receive_drops: int
    transmit_bytes: int
    transmit_packets: int
    transmit_errors: int
    transmit_drops: int


def _short_network_name(name: object) -> str:
    return str(name or "").strip().rsplit("/", 1)[-1]


def discover_n3_interface(
    network_status: str,
    network_names: Iterable[str],
    override: str = "",
) -> PathDiscovery:
    """Resolve N3 only from an explicit override or matching Multus status."""

    if str(override).strip():
        return PathDiscovery(str(override).strip(), "override")

    expected = {_short_network_name(name) for name in network_names if str(name).strip()}
    if not expected:
        return PathDiscovery("", "network_name_not_configured")

    try:
        records = json.loads(network_status)
    except (TypeError, ValueError):
        return PathDiscovery("", "network_status_invalid")
    if not isinstance(records, list):
        return PathDiscovery("", "network_status_invalid")

    matches = {
        str(record.get("interface", "")).strip()
        for record in records
        if isinstance(record, dict)
        and _short_network_name(record.get("name")) in expected
        and str(record.get("interface", "")).strip()
    }
    if len(matches) == 1:
        return PathDiscovery(next(iter(matches)), "network_status")
    if not matches:
        return PathDiscovery("", "network_not_attached")
    return PathDiscovery("", "network_status_ambiguous")


def discover_n6_interface(route_table: str, override: str = "") -> PathDiscovery:
    """Resolve N6 as the single default route in the UPF pod namespace."""

    if str(override).strip():
        return PathDiscovery(str(override).strip(), "override")

    interfaces = set()
    for line in str(route_table or "").splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        try:
            route_is_up = int(fields[3], 16) & 0x1
        except ValueError:
            continue
        if route_is_up:
            interfaces.add(fields[0])
    if len(interfaces) == 1:
        return PathDiscovery(next(iter(interfaces)), "default_route")
    if not interfaces:
        return PathDiscovery("", "default_route_missing")
    return PathDiscovery("", "default_route_ambiguous")


def parse_interface_counters(text: str) -> dict[str, InterfaceCounters]:
    """Parse the stable Linux /proc/net/dev ABI without an iproute dependency."""

    counters = {}
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        interface, values_text = line.split(":", 1)
        values = values_text.split()
        if len(values) < 12:
            continue
        try:
            values = [int(value) for value in values]
        except ValueError:
            continue
        counters[interface.strip()] = InterfaceCounters(
            receive_bytes=values[0],
            receive_packets=values[1],
            receive_errors=values[2],
            receive_drops=values[3],
            transmit_bytes=values[8],
            transmit_packets=values[9],
            transmit_errors=values[10],
            transmit_drops=values[11],
        )
    return counters


def is_gtpu_frame(frame: bytes) -> bool:
    """Recognize Ethernet IPv4/IPv6 UDP frames carrying the standard GTP-U port."""

    if len(frame) < 14:
        return False
    ether_type = struct.unpack("!H", frame[12:14])[0]
    offset = 14
    while ether_type in (ETH_P_8021Q, ETH_P_8021AD):
        if len(frame) < offset + 4:
            return False
        ether_type = struct.unpack("!H", frame[offset + 2:offset + 4])[0]
        offset += 4

    if ether_type == ETH_P_IP:
        if len(frame) < offset + 20:
            return False
        version_ihl = frame[offset]
        if version_ihl >> 4 != 4:
            return False
        header_length = (version_ihl & 0x0F) * 4
        if header_length < 20 or len(frame) < offset + header_length + 8:
            return False
        if frame[offset + 9] != socket.IPPROTO_UDP:
            return False
        udp_offset = offset + header_length
    elif ether_type == ETH_P_IPV6:
        if len(frame) < offset + 48 or frame[offset] >> 4 != 6:
            return False
        # Extension headers are intentionally not guessed.  The common N3 path
        # is direct IPv6/UDP; a non-direct packet remains observable in /proc.
        if frame[offset + 6] != socket.IPPROTO_UDP:
            return False
        udp_offset = offset + 40
    else:
        return False

    source_port, destination_port = struct.unpack("!HH", frame[udp_offset:udp_offset + 4])
    return source_port == GTPU_PORT or destination_port == GTPU_PORT


class GtpUCaptureWorker:
    """Small AF_PACKET observer for evidence that the discovered N3 carries GTP-U."""

    def __init__(self, interface: str, probe: "UPFDataPlaneProbe"):
        self.interface = interface
        self.probe = probe
        self.stop_event = threading.Event()
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.active = False

    def start(self) -> bool:
        packet_family = getattr(socket, "AF_PACKET", None)
        if packet_family is None:
            self.probe.record_error("gtpu_capture_unsupported")
            LOG.warning("AF_PACKET is unavailable; GTP-U capture cannot run on %s", self.interface)
            return False
        try:
            raw_socket = socket.socket(packet_family, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
            raw_socket.bind((self.interface, 0))
            raw_socket.settimeout(1.0)
        except OSError as error:
            self.probe.record_error("gtpu_capture_open")
            LOG.warning("cannot open GTP-U observer on %s: %s", self.interface, error)
            return False
        self.socket = raw_socket
        self.active = True
        self.probe.capture_active.set(1)
        self.thread = threading.Thread(target=self._run, name="gtpu-observer", daemon=True)
        self.thread.start()
        return True

    def _run(self) -> None:
        assert self.socket is not None
        while not self.stop_event.is_set():
            try:
                frame, address = self.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as error:
                if not self.stop_event.is_set():
                    self.probe.record_error("gtpu_capture_read")
                    LOG.warning("GTP-U observer stopped on %s: %s", self.interface, error)
                break
            if is_gtpu_frame(frame):
                packet_type = address[2] if len(address) > 2 else -1
                direction = "tx" if packet_type == PACKET_OUTGOING else "rx"
                self.probe.record_gtpu(direction, len(frame))
        self.active = False
        self.probe.capture_active.set(0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            self.socket.close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class UPFDataPlaneProbe:
    """Export low-cardinality UPF path counters and discovery evidence."""

    def __init__(
        self,
        network_status: str,
        n3_network_names: Iterable[str],
        n3_interface_override: str = "",
        n6_interface_override: str = "",
        proc_net_dev_path: str = "/proc/net/dev",
        proc_route_path: str = "/proc/net/route",
        registry=None,
    ):
        self.network_status = network_status
        self.n3_network_names = tuple(n3_network_names)
        self.n3_interface_override = n3_interface_override
        self.n6_interface_override = n6_interface_override
        self.proc_net_dev_path = proc_net_dev_path
        self.proc_route_path = proc_route_path
        self.registry = registry if registry is not None else CollectorRegistry()
        self.n3 = PathDiscovery("", "not_discovered")
        self.n6 = PathDiscovery("", "not_discovered")
        self.capture: GtpUCaptureWorker | None = None
        self.last_capture_attempt = 0.0

        self.path_ready = Gauge("upf_probe_path_ready", "One when a path interface was resolved locally.", ["path"], registry=self.registry)
        self.path_info = Gauge("upf_probe_path_info", "Resolved interface and local discovery method for a path.", ["path", "interface", "source"], registry=self.registry)
        self.interface_available = Gauge("upf_probe_interface_available", "One when the resolved interface is present in /proc/net/dev.", ["path"], registry=self.registry)
        self.rx_bytes = Gauge("upf_probe_interface_receive_bytes_total", "Kernel receive-byte counter for the resolved interface.", ["path"], registry=self.registry)
        self.rx_packets = Gauge("upf_probe_interface_receive_packets_total", "Kernel receive-packet counter for the resolved interface.", ["path"], registry=self.registry)
        self.rx_errors = Gauge("upf_probe_interface_receive_errors_total", "Kernel receive-error counter for the resolved interface.", ["path"], registry=self.registry)
        self.rx_drops = Gauge("upf_probe_interface_receive_drops_total", "Kernel receive-drop counter for the resolved interface.", ["path"], registry=self.registry)
        self.tx_bytes = Gauge("upf_probe_interface_transmit_bytes_total", "Kernel transmit-byte counter for the resolved interface.", ["path"], registry=self.registry)
        self.tx_packets = Gauge("upf_probe_interface_transmit_packets_total", "Kernel transmit-packet counter for the resolved interface.", ["path"], registry=self.registry)
        self.tx_errors = Gauge("upf_probe_interface_transmit_errors_total", "Kernel transmit-error counter for the resolved interface.", ["path"], registry=self.registry)
        self.tx_drops = Gauge("upf_probe_interface_transmit_drops_total", "Kernel transmit-drop counter for the resolved interface.", ["path"], registry=self.registry)
        self.collection_success = Gauge("upf_probe_collection_success", "One when all resolved path counters were read in the latest collection.", registry=self.registry)
        self.last_collection = Gauge("upf_probe_last_collection_timestamp_seconds", "Unix timestamp of the most recent interface-counter collection.", registry=self.registry)
        self.errors = Counter("upf_probe_collection_errors", "Probe errors by local operation.", ["operation"], registry=self.registry)
        self.capture_active = Gauge("upf_probe_gtpu_capture_active", "One when the AF_PACKET GTP-U observer is active.", registry=self.registry)
        self.gtpu_seen = Gauge("upf_probe_gtpu_seen", "One after the local observer has seen at least one N3 GTP-U packet.", registry=self.registry)
        self.gtpu_packets = Counter("upf_probe_gtpu_packets", "N3 GTP-U packets seen by the local raw-socket observer.", ["direction"], registry=self.registry)
        self.gtpu_bytes = Counter("upf_probe_gtpu_bytes", "Ethernet-frame bytes of N3 GTP-U packets seen by the local raw-socket observer.", ["direction"], registry=self.registry)
        self.gtpu_last_packet = Gauge("upf_probe_gtpu_last_packet_timestamp_seconds", "Unix timestamp of the most recently observed N3 GTP-U packet.", registry=self.registry)
        self.collection_success.set(0)
        self.capture_active.set(0)
        self.gtpu_seen.set(0)

    def record_error(self, operation: str) -> None:
        self.errors.labels(operation).inc()

    def record_gtpu(self, direction: str, frame_bytes: int) -> None:
        self.gtpu_packets.labels(direction).inc()
        self.gtpu_bytes.labels(direction).inc(frame_bytes)
        self.gtpu_seen.set(1)
        self.gtpu_last_packet.set(time.time())

    def discover_paths(self) -> None:
        try:
            with open(self.proc_route_path, encoding="utf-8") as route_file:
                route_table = route_file.read()
        except OSError as error:
            self.record_error("route_read")
            LOG.warning("cannot read %s: %s", self.proc_route_path, error)
            route_table = ""
        self.n3 = discover_n3_interface(self.network_status, self.n3_network_names, self.n3_interface_override)
        self.n6 = discover_n6_interface(route_table, self.n6_interface_override)
        for path, discovery in (("n3", self.n3), ("n6", self.n6)):
            self.path_ready.labels(path).set(1 if discovery.interface else 0)
            self.path_info.labels(path, discovery.interface or "unknown", discovery.source).set(1)

    def _read_interface_counters(self) -> dict[str, InterfaceCounters]:
        try:
            with open(self.proc_net_dev_path, encoding="utf-8") as dev_file:
                return parse_interface_counters(dev_file.read())
        except OSError as error:
            self.record_error("proc_net_dev_read")
            LOG.warning("cannot read %s: %s", self.proc_net_dev_path, error)
            return {}

    def _export_path_counters(self, path: str, interface: str, counters: dict[str, InterfaceCounters]) -> bool:
        values = counters.get(interface)
        if values is None:
            self.interface_available.labels(path).set(0)
            return False
        self.interface_available.labels(path).set(1)
        self.rx_bytes.labels(path).set(values.receive_bytes)
        self.rx_packets.labels(path).set(values.receive_packets)
        self.rx_errors.labels(path).set(values.receive_errors)
        self.rx_drops.labels(path).set(values.receive_drops)
        self.tx_bytes.labels(path).set(values.transmit_bytes)
        self.tx_packets.labels(path).set(values.transmit_packets)
        self.tx_errors.labels(path).set(values.transmit_errors)
        self.tx_drops.labels(path).set(values.transmit_drops)
        return True

    def ensure_gtpu_capture(self) -> None:
        if not self.n3.interface or self.capture is not None:
            return
        now = time.monotonic()
        if now - self.last_capture_attempt < 10:
            return
        self.last_capture_attempt = now
        candidate = GtpUCaptureWorker(self.n3.interface, self)
        if candidate.start():
            self.capture = candidate

    def refresh(self) -> None:
        if self.n3.source == "not_discovered" or self.n6.source == "not_discovered":
            self.discover_paths()
        counters = self._read_interface_counters()
        paths = (("n3", self.n3), ("n6", self.n6))
        available = [self._export_path_counters(path, result.interface, counters) for path, result in paths if result.interface]
        self.collection_success.set(1 if available and all(available) and len(available) == 2 else 0)
        self.last_collection.set(time.time())
        self.ensure_gtpu_capture()

    def stop(self) -> None:
        if self.capture is not None:
            self.capture.stop()


def _json_list_from_env(name: str) -> list[str]:
    raw = os.environ.get(name, "[]")
    try:
        values = json.loads(raw)
    except ValueError:
        LOG.warning("%s is not valid JSON; ignoring it", name)
        return []
    if not isinstance(values, list):
        LOG.warning("%s must be a JSON list; ignoring it", name)
        return []
    return [str(value) for value in values]


def main() -> int:
    logging.basicConfig(level=os.environ.get("UPF_PROBE_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("UPF_PROBE_METRICS_PORT", "9103"))
    interval = float(os.environ.get("UPF_PROBE_COLLECTION_INTERVAL_SECONDS", "5"))
    if not 1 <= port <= 65535:
        raise ValueError("UPF_PROBE_METRICS_PORT must be between 1 and 65535")
    if interval <= 0:
        raise ValueError("UPF_PROBE_COLLECTION_INTERVAL_SECONDS must be positive")

    probe = UPFDataPlaneProbe(
        network_status=os.environ.get("UPF_PROBE_NETWORK_STATUS_JSON", ""),
        n3_network_names=_json_list_from_env("UPF_PROBE_N3_NETWORK_NAMES"),
        n3_interface_override=os.environ.get("UPF_PROBE_N3_INTERFACE", ""),
        n6_interface_override=os.environ.get("UPF_PROBE_N6_INTERFACE", ""),
    )
    stopping = threading.Event()

    def stop_handler(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    probe.refresh()
    start_http_server(port, registry=probe.registry)
    LOG.info("UPF data-plane probe listening on port %s", port)
    while not stopping.wait(interval):
        probe.refresh()
    probe.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
