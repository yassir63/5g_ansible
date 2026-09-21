"""Passive, low-cardinality NGAP PDU-session setup metrics for the AMF sniffer."""

from __future__ import annotations

import re
import threading
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


def _normal(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _values(layer, names: tuple[str, ...]) -> list[object]:
    values = []
    for name in names:
        try:
            found = layer.get_field_values(name)
            if not isinstance(found, (list, tuple)):
                found = [found]
            values.extend(value for value in found if value is not None)
        except (AttributeError, KeyError, TypeError):
            pass
        try:
            value = getattr(layer, name)
            if isinstance(value, (list, tuple)):
                values.extend(value)
            elif value is not None:
                values.append(value)
        except AttributeError:
            pass
    return values


def _positive_int(value, maximum: int) -> str:
    try:
        text = str(value).strip().split()[0]
        number = int(text, 0)
    except (IndexError, TypeError, ValueError):
        return ""
    return str(number) if 0 < number <= maximum else ""


def _layer_text(layer) -> str:
    fields = getattr(layer, "field_names", []) or []
    return _normal(" ".join(map(str, fields)) + " " + str(layer))


def _message_kind(layer) -> str:
    text = _layer_text(layer)
    if "pdusessionresourcesetuprequest" in text:
        return "request"
    if "pdusessionresourcesetupresponse" in text:
        return "response"
    return ""


def _response_outcome(layer) -> str:
    text = _layer_text(layer)
    failed = "pdusessionresourcefailedtosetuplistsures" in text
    successful = "pdusessionresourcesetuplistsures" in text
    if successful and failed:
        return "mixed"
    if successful:
        return "success"
    if failed:
        return "failure"
    return "unclassified"


def _session_ids(layer) -> frozenset[str]:
    ids = {
        _positive_int(value, 255)
        for value in _values(layer, ("pDUSessionID", "pdu_session_id", "pdusessionid"))
    }
    ids.discard("")
    return frozenset(ids)


def _ue_key(layer) -> str:
    amf = next((value for value in (
        _positive_int(item, (1 << 40) - 1)
        for item in _values(layer, ("aMF_UE_NGAP_ID", "amf_ue_ngap_id"))
    ) if value), "")
    ran = next((value for value in (
        _positive_int(item, (1 << 40) - 1)
        for item in _values(layer, ("rAN_UE_NGAP_ID", "ran_ue_ngap_id"))
    ) if value), "")
    return f"{amf}:{ran}" if amf and ran else ""


class PduSessionSetupTracker:
    """Correlate only unambiguous request/response pairs without UE labels."""

    def __init__(self, pending_timeout_seconds: float = 30, registry=None, clock=time.monotonic):
        if pending_timeout_seconds <= 0:
            raise ValueError("pending timeout must be positive")
        self.timeout = pending_timeout_seconds
        self.clock = clock
        self.lock = threading.Lock()
        self.pending: dict[tuple[str, frozenset[str]], float] = {}
        self.registry = registry if registry is not None else CollectorRegistry()
        self.registry.register(_ExpiryCollector(self))
        self.requests = Counter("ngap_pdu_session_setup_requests", "Observed PDU-session setup request messages.", registry=self.registry)
        self.responses = Counter("ngap_pdu_session_setup_responses", "Observed setup response messages by parsed response-list outcome.", ["outcome"], registry=self.registry)
        self.completed = Counter("ngap_pdu_session_setup_completed", "Unambiguously correlated request/response pairs by parsed response-list outcome.", ["outcome"], registry=self.registry)
        self.duration = Histogram("ngap_pdu_session_setup_duration_seconds", "N2 request-to-response observation time for unambiguously correlated pairs.", ["outcome"], registry=self.registry)
        self.expired = Counter("ngap_pdu_session_setup_expired", "Pending requests without an observed response before the local observation timeout.", registry=self.registry)
        self.unmatched = Counter("ngap_pdu_session_setup_unmatched_responses", "Responses without one unambiguous locally observed request.", registry=self.registry)
        self.untrackable_requests = Counter("ngap_pdu_session_setup_untrackable_requests", "Request messages missing the UE-pair or PDU-session identifiers needed for correlation.", registry=self.registry)
        self.untrackable_responses = Counter("ngap_pdu_session_setup_untrackable_responses", "Response messages missing the UE-pair or PDU-session identifiers needed for correlation.", registry=self.registry)
        self.overlapping_requests = Counter("ngap_pdu_session_setup_overlapping_requests", "Request messages overlapping an unresolved identical UE/session set.", registry=self.registry)
        self.decode_errors = Counter("ngap_pdu_session_setup_decode_errors", "Packet-processing exceptions in this probe.", registry=self.registry)
        self.capture_started = Gauge("ngap_pdu_session_setup_capture_started", "One after capture initialization succeeds in this probe process.", registry=self.registry)
        self.last_packet = Gauge("ngap_pdu_session_setup_last_packet_timestamp_seconds", "Unix time of the most recent NGAP packet decoded by this probe.", registry=self.registry)
        self.pending_gauge = Gauge("ngap_pdu_session_setup_pending", "Locally observed request messages awaiting a response.", registry=self.registry)
        self.pending_gauge.set(0)
        self.capture_started.set(0)

    def _expire_locked(self, now: float) -> None:
        stale = [key for key, started in self.pending.items() if now - started >= self.timeout]
        for key in stale:
            self.pending.pop(key, None)
            self.expired.inc()
        self.pending_gauge.set(len(self.pending))

    def expire(self) -> None:
        with self.lock:
            self._expire_locked(self.clock())

    def mark_capture_started(self) -> None:
        self.capture_started.set(1)

    def record_decode_error(self) -> None:
        self.decode_errors.inc()

    def observe_layer(self, layer, observed_at: float | None = None) -> str:
        now = self.clock() if observed_at is None else observed_at
        self.last_packet.set(time.time())
        kind = _message_kind(layer)
        if not kind:
            return "ignored"
        ue_key = _ue_key(layer)
        sessions = _session_ids(layer)
        with self.lock:
            self._expire_locked(now)
            if kind == "request":
                self.requests.inc()
                if not ue_key or not sessions:
                    self.untrackable_requests.inc()
                    return "untrackable_request"
                key = (ue_key, sessions)
                if key in self.pending:
                    self.overlapping_requests.inc()
                    return "overlapping_request"
                self.pending[key] = now
                self.pending_gauge.set(len(self.pending))
                return "request"
            outcome = _response_outcome(layer)
            self.responses.labels(outcome).inc()
            if not ue_key or not sessions:
                self.untrackable_responses.inc()
                return "untrackable_response"
            key = (ue_key, sessions)
            started = self.pending.pop(key, None)
            if started is None:
                self.unmatched.inc()
                return "unmatched_response"
            self.pending_gauge.set(len(self.pending))
            self.completed.labels(outcome).inc()
            self.duration.labels(outcome).observe(max(0, now - started))
            return outcome


class _ExpiryCollector:
    """Run expiry before registered counters are rendered for a scrape."""

    def __init__(self, tracker: PduSessionSetupTracker):
        self.tracker = tracker

    def collect(self):
        self.tracker.expire()
        return []

    def describe(self):
        return []
