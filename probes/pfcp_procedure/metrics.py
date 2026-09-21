"""Passive, low-cardinality PFCP Session Establishment metrics."""

from __future__ import annotations

import threading
import time

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


PFCP_SESSION_ESTABLISHMENT_REQUEST = 50
PFCP_SESSION_ESTABLISHMENT_RESPONSE = 51
PFCP_REQUEST_ACCEPTED = 1
PFCP_SEQUENCE_MAX = (1 << 24) - 1


def response_outcome(cause) -> str:
    """Map a parsed PFCP Cause value to an intentionally small outcome set."""

    if cause is None:
        return "unknown"
    text = str(cause).strip().lower()
    if not text:
        return "unknown"
    if "request accepted" in text or text == "accepted":
        return "accepted"
    try:
        return "accepted" if int(text, 0) == PFCP_REQUEST_ACCEPTED else "rejected"
    except ValueError:
        return "rejected"


def _sequence(value) -> int | None:
    try:
        sequence = int(value)
    except (TypeError, ValueError):
        return None
    return sequence if 0 <= sequence <= PFCP_SEQUENCE_MAX else None


class PFCPSessionEstablishmentTracker:
    """Correlate strict local PFCP Session Establishment request/response pairs."""

    def __init__(self, pending_timeout_seconds: float = 30, registry=None, clock=time.monotonic):
        if pending_timeout_seconds <= 0:
            raise ValueError("pending timeout must be positive")
        self.timeout = pending_timeout_seconds
        self.clock = clock
        self.lock = threading.Lock()
        # Peer addresses are correlation-only state and never Prometheus labels.
        self.pending: dict[tuple[str, int], float] = {}
        self.registry = registry if registry is not None else CollectorRegistry()
        self.registry.register(_ExpiryCollector(self))
        self.requests = Counter("pfcp_session_establishment_requests", "Observed PFCP Session Establishment Request messages.", registry=self.registry)
        self.responses = Counter("pfcp_session_establishment_responses", "Observed PFCP Session Establishment Response messages by parsed Cause outcome.", ["outcome"], registry=self.registry)
        self.completed = Counter("pfcp_session_establishment_completed", "Exactly correlated local PFCP Session Establishment exchanges by parsed Cause outcome.", ["outcome"], registry=self.registry)
        self.duration = Histogram("pfcp_session_establishment_duration_seconds", "Local PFCP request-to-response observation time for exactly correlated exchanges.", ["outcome"], registry=self.registry)
        self.expired = Counter("pfcp_session_establishment_expired", "Locally observed requests without a response before the observation timeout.", registry=self.registry)
        self.unmatched = Counter("pfcp_session_establishment_unmatched_responses", "Responses without one exact locally observed request.", registry=self.registry)
        self.untrackable_requests = Counter("pfcp_session_establishment_untrackable_requests", "Requests without valid sequence and peer data for local correlation.", registry=self.registry)
        self.untrackable_responses = Counter("pfcp_session_establishment_untrackable_responses", "Responses without valid sequence and peer data for local correlation.", registry=self.registry)
        self.overlapping_requests = Counter("pfcp_session_establishment_overlapping_requests", "Requests overlapping an unresolved local peer/sequence pair.", registry=self.registry)
        self.decode_errors = Counter("pfcp_session_establishment_decode_errors", "Packet-processing exceptions in this probe.", registry=self.registry)
        self.capture_started = Gauge("pfcp_session_establishment_capture_started", "One after PFCP capture initialization succeeds in this probe process.", registry=self.registry)
        self.last_packet = Gauge("pfcp_session_establishment_last_packet_timestamp_seconds", "Unix time of the most recently observed relevant PFCP packet.", registry=self.registry)
        self.pending_gauge = Gauge("pfcp_session_establishment_pending", "Locally observed requests awaiting a response.", registry=self.registry)
        self.capture_started.set(0)
        self.pending_gauge.set(0)

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

    def observe(self, message_type, sequence, peer_key: str, cause=None, observed_at: float | None = None) -> str:
        """Observe one parsed PFCP message; anything other than 50/51 is ignored."""

        try:
            message_type = int(message_type)
        except (TypeError, ValueError):
            return "ignored"
        if message_type not in (PFCP_SESSION_ESTABLISHMENT_REQUEST, PFCP_SESSION_ESTABLISHMENT_RESPONSE):
            return "ignored"

        now = self.clock() if observed_at is None else observed_at
        self.last_packet.set(time.time())
        sequence = _sequence(sequence)
        peer_key = str(peer_key or "").strip()
        with self.lock:
            self._expire_locked(now)
            if message_type == PFCP_SESSION_ESTABLISHMENT_REQUEST:
                self.requests.inc()
                if sequence is None or not peer_key:
                    self.untrackable_requests.inc()
                    return "untrackable_request"
                key = (peer_key, sequence)
                if key in self.pending:
                    self.overlapping_requests.inc()
                    return "overlapping_request"
                self.pending[key] = now
                self.pending_gauge.set(len(self.pending))
                return "request"

            outcome = response_outcome(cause)
            self.responses.labels(outcome).inc()
            if sequence is None or not peer_key:
                self.untrackable_responses.inc()
                return "untrackable_response"
            started = self.pending.pop((peer_key, sequence), None)
            if started is None:
                self.unmatched.inc()
                return "unmatched_response"
            self.pending_gauge.set(len(self.pending))
            self.completed.labels(outcome).inc()
            self.duration.labels(outcome).observe(max(0, now - started))
            return outcome


class _ExpiryCollector:
    """Expire pending observations immediately before a Prometheus scrape."""

    def __init__(self, tracker: PFCPSessionEstablishmentTracker):
        self.tracker = tracker

    def collect(self):
        self.tracker.expire()
        return []

    def describe(self):
        return []
