#!/usr/bin/env python3
"""Capture NGAP PDU-session TEIDs and merge them into the UE mapper store."""

from __future__ import annotations

import os
import queue
import re
import signal
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any


RAN_UE_ID_FIELDS = ("ran_ue_ngap_id",)
AMF_UE_ID_FIELDS = ("amf_ue_ngap_id",)
GTP_TEID_FIELDS = ("gtp_teid",)
SST_FIELDS = ("sst",)
SD_FIELDS = ("sd",)
SUCI_SUPI_FORMAT_FIELDS = ("nas_5gs_mm_suci_supi_fmt",)
SUCI_SCHEME_FIELDS = ("nas_5gs_mm_suci_scheme_id",)
SUCI_MSIN_FIELDS = ("nas_5gs_mm_suci_msin",)
MCC_FIELDS = ("e212_mcc",)
MNC_FIELDS = ("e212_mnc",)

REQUEST_MARKERS = (
    "pdusessionresourcesetuprequest",
    "pdusessionresourcesetuprequest_element",
    "pdusessionresourcesetuplistcxtreq",
    "pdusessionresourcesetupitemcxtreq",
    "pdusessionresourcesetuplisthoreq",
    "pdusessionresourcesetupitemhoreq",
    "pdusessionresourcesetuplistsureq",
    "pdusessionresourcesetupitemsureq",
    "pdusessionresourcesetuprequesttransfer",
    "pdusessionresourcesetuprequesttransfer_element",
)
RESPONSE_MARKERS = (
    "pdusessionresourcesetupresponse",
    "pdusessionresourcesetupresponse_element",
    "pdusessionresourcesetuplistcxtres",
    "pdusessionresourcesetupitemcxtres",
    "pdusessionresourcesetuplistsures",
    "pdusessionresourcesetupitemsures",
    "pdusessionresourcesetupresponsetransfer",
    "pdusessionresourcesetupresponsetransfer_element",
)
REQUEST_CONTEXT_MARKERS = (
    "initialcontextsetuprequest",
    "initialcontextsetuprequest_element",
    "initialcontextsetuprequestvalue",
    "initialcontextsetuprequestvalue_element",
)
RESPONSE_CONTEXT_MARKERS = (
    "initialcontextsetupresponse",
    "initialcontextsetupresponse_element",
    "initialcontextsetupresponsevalue",
    "initialcontextsetupresponsevalue_element",
    "pathswitchrequestacknowledge",
    "pathswitchrequestacknowledge_element",
    "pathswitchrequestacknowledgetransfer",
)
RELEASE_MARKERS = (
    "uecontextreleasecommand",
    "uecontextreleasecommand_element",
    "uecontextreleasecomplete",
    "uecontextreleasecomplete_element",
    "uecontextreleaserequest",
    "uecontextreleaserequest_element",
)
PROCEDURE_CODE_FIELDS = (
    "procedureCode",
    "procedure_code",
)
INITIATING_MESSAGE_FIELDS = (
    "initiatingMessage",
    "initiatingMessage_element",
    "initiatingMessagevalue",
    "initiatingMessagevalue_element",
    "proc.imsg",
)
SUCCESSFUL_OUTCOME_FIELDS = (
    "successfulOutcome",
    "successfulOutcome_element",
    "successfulOutcomevalue",
    "successfulOutcome_value_element",
    "proc.sout",
)
UNSUCCESSFUL_OUTCOME_FIELDS = (
    "unsuccessfulOutcome",
    "unsuccessfulOutcome_element",
    "unsuccessfulOutcomevalue",
    "unsuccessfulOutcome_value_element",
    "proc.uout",
)
PDU_SESSION_RESOURCE_SETUP_PROCEDURE_CODE = 29


def normalized_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def value_texts(value: Any, *, raw_first: bool = False) -> list[tuple[str, bool]]:
    """Return PyShark display and raw representations without losing their base."""
    if value is None:
        return []
    if isinstance(value, bool):
        return [(str(int(value)), False)]
    if isinstance(value, int):
        return [(str(value), False)]
    if isinstance(value, (bytes, bytearray)):
        return [(bytes(value).hex(), True)]

    display_attrs = ("show", "showname_value")
    raw_attrs = ("raw_value", "hex_value")
    attrs = raw_attrs + display_attrs if raw_first else display_attrs + raw_attrs
    candidates: list[tuple[str, bool]] = []
    seen: set[tuple[str, bool]] = set()

    for attr in attrs:
        try:
            candidate = getattr(value, attr, None)
        except Exception:
            continue
        if candidate is None or candidate is value:
            continue
        text = str(candidate).strip()
        item = (text, attr in raw_attrs)
        if text and item not in seen:
            candidates.append(item)
            seen.add(item)

    if candidates:
        return candidates

    text = str(value).strip()
    item = (text, False)
    if text and item not in seen:
        candidates.append(item)
        seen.add(item)
    return candidates


def normalize_teid(value: Any) -> str | None:
    for text, _is_raw in value_texts(value, raw_first=True):
        compact = text.strip().lower().replace(":", "").replace("-", "")
        if compact.startswith("0x"):
            compact = compact[2:]
        if re.fullmatch(r"[0-9a-f]+", compact):
            try:
                teid = int(compact, 16)
            except ValueError:
                continue
            if 0 < teid <= 0xFFFFFFFF:
                return f"{teid:08x}"
    return None


def normalize_integer(value: Any) -> int | None:
    if value is None:
        return None

    def parse_display_text(text: str) -> int | None:
        compact = text.strip().lower().replace("_", "")
        if not compact:
            return None
        try:
            if re.fullmatch(r"[+-]?\d+", compact):
                number = int(compact, 10)
                return number if number >= 0 else None
            if re.fullmatch(r"[+-]?0x[0-9a-f]+", compact):
                number = int(compact, 16)
                return number if number >= 0 else None
        except ValueError:
            return None
        return None

    def parse_raw_text(text: str) -> int | None:
        compact = text.strip().lower().replace("_", "")
        if not compact:
            return None
        if compact.startswith("0x"):
            compact = compact[2:]
        if not re.fullmatch(r"[0-9a-f]+", compact):
            return None
        try:
            return int(compact, 16)
        except ValueError:
            return None

    for attr in ("show", "showname_value"):
        try:
            candidate = getattr(value, attr, None)
        except Exception:
            continue
        if candidate is None or candidate is value:
            continue
        parsed = parse_display_text(str(candidate))
        if parsed is not None:
            return parsed

    try:
        int_value = getattr(value, "int_value", None)
    except Exception:
        int_value = None
    if isinstance(int_value, int) and int_value >= 0:
        return int_value

    for attr in ("raw_value", "hex_value"):
        try:
            candidate = getattr(value, attr, None)
        except Exception:
            continue
        if candidate is None or candidate is value:
            continue
        parsed = parse_raw_text(str(candidate))
        if parsed is not None:
            return parsed

    if isinstance(value, (bytes, bytearray)):
        parsed = parse_raw_text(bytes(value).hex())
        if parsed is not None:
            return parsed
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, bool):
        return int(value)

    for text, _is_raw in value_texts(value):
        parsed = parse_display_text(text)
        if parsed is not None:
            return parsed
    return None


def normalize_hex_field(value: Any, width: int) -> str | None:
    limit = (1 << (width * 4)) - 1
    for text, _is_raw in value_texts(value, raw_first=True):
        compact = text.strip().lower().replace(":", "").replace("-", "")
        if compact.startswith("0x"):
            compact = compact[2:]
        if not re.fullmatch(r"[0-9a-f]+", compact):
            continue
        try:
            number = int(compact, 16)
        except ValueError:
            continue
        if 0 <= number <= limit:
            return f"{number:0{width}x}"
    return None


def normalize_digit_field(value: Any, widths: set[int]) -> str | None:
    for text, _is_raw in value_texts(value):
        compact = re.sub(r"\D", "", text)
        if len(compact) in widths:
            return compact
    return None


def flatten_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[Any] = []
        for item in value:
            out.extend(flatten_values(item))
        return out
    all_fields = getattr(value, "all_fields", None)
    if all_fields:
        return flatten_values(all_fields)
    return [value]


def layer_field_names(layer: Any) -> list[str]:
    names = getattr(layer, "field_names", None) or []
    return [str(name) for name in names]


def field_values(layer: Any, name: str) -> list[Any]:
    out: list[Any] = []
    seen_scalars: set[tuple[type[Any], Any]] = set()
    seen_objects: set[int] = set()

    def append_values(values: list[Any]) -> None:
        for item in values:
            if isinstance(item, (str, int, bool, bytes, bytearray)):
                key = (type(item), bytes(item) if isinstance(item, (bytes, bytearray)) else item)
                if key in seen_scalars:
                    continue
                seen_scalars.add(key)
            else:
                identity = id(item)
                if identity in seen_objects:
                    continue
                seen_objects.add(identity)
            out.append(item)

    try:
        append_values(flatten_values(getattr(layer, name, None)))
    except Exception:
        pass

    getter = getattr(layer, "get_field_values", None)
    if getter is not None:
        try:
            append_values(flatten_values(getter(name)))
        except Exception:
            pass
    return out


def semantic_field_values(layer: Any, aliases: tuple[str, ...]) -> list[Any]:
    out: list[Any] = []
    visited: set[str] = set()
    targets = {normalized_name(alias) for alias in aliases}
    for name in layer_field_names(layer):
        normalized = normalized_name(name)
        if not any(normalized == target or normalized.endswith(target) for target in targets):
            continue
        visited.add(name)
        out.extend(field_values(layer, name))

    for name in aliases:
        if name not in visited:
            out.extend(field_values(layer, name))
    return out


def first_integer_field(layer: Any, aliases: tuple[str, ...]) -> int | None:
    for value in semantic_field_values(layer, aliases):
        normalized = normalize_integer(value)
        if normalized is not None:
            return normalized
    return None


def all_ngap_teids(layer: Any) -> list[str]:
    values = semantic_field_values(layer, GTP_TEID_FIELDS)
    out: list[str] = []
    for value in values:
        teid = normalize_teid(value)
        if teid and teid not in out:
            out.append(teid)
    return out


def marker_present(
    layer: Any,
    available: set[str],
    markers: tuple[str, ...],
) -> bool:
    for marker in markers:
        normalized_marker = normalized_name(marker)
        if any(normalized_marker in name for name in available):
            return True
        try:
            if getattr(layer, marker, None) is not None:
                return True
        except Exception:
            pass
    return False


def classify_ngap_message(layer: Any) -> str:
    available = {normalized_name(name) for name in layer_field_names(layer)}
    if marker_present(layer, available, RELEASE_MARKERS):
        return "release"

    request = marker_present(layer, available, REQUEST_MARKERS)
    response = marker_present(layer, available, RESPONSE_MARKERS)
    if not request and not response:
        request = marker_present(layer, available, REQUEST_CONTEXT_MARKERS)
        response = marker_present(layer, available, RESPONSE_CONTEXT_MARKERS)
    if request and response:
        return "ambiguous"
    if request:
        return "request"
    if response:
        return "response"

    procedure_code = first_integer_field(layer, PROCEDURE_CODE_FIELDS)
    if procedure_code == PDU_SESSION_RESOURCE_SETUP_PROCEDURE_CODE:
        has_initiating = marker_present(layer, available, INITIATING_MESSAGE_FIELDS)
        has_successful = marker_present(layer, available, SUCCESSFUL_OUTCOME_FIELDS)
        has_unsuccessful = marker_present(
            layer,
            available,
            UNSUCCESSFUL_OUTCOME_FIELDS,
        )
        if has_initiating and not has_successful and not has_unsuccessful:
            return "request"
        if has_successful and not has_initiating:
            return "response"

    try:
        layer_text = normalized_name(str(layer))
    except Exception:
        return "other"
    if any(normalized_name(marker) in layer_text for marker in RELEASE_MARKERS):
        return "release"
    request = any(
        normalized_name(marker) in layer_text
        for marker in REQUEST_MARKERS + REQUEST_CONTEXT_MARKERS
    )
    response = any(
        normalized_name(marker) in layer_text
        for marker in RESPONSE_MARKERS + RESPONSE_CONTEXT_MARKERS
    )
    if request and response:
        return "ambiguous"
    if request:
        return "request"
    if response:
        return "response"
    return "other"


def packet_ngap_layers(packet: Any) -> list[Any]:
    """Return all NGAP layers exposed by PyShark for a packet.

    Under burst load, SCTP can coalesce several NGAP PDUs into one Ethernet/IP
    frame. Older PyShark access patterns such as packet["ngap"] only expose a
    single layer, which loses the extra UE IDs and TEIDs from the same packet.
    Prefer the multi-layer API when available, then fall back to packet.layers
    and finally the historical single-layer access.
    """

    layers: list[Any] = []
    seen: set[int] = set()

    def append(layer: Any) -> None:
        if layer is None:
            return
        identity = id(layer)
        if identity in seen:
            return
        seen.add(identity)
        layers.append(layer)

    getter = getattr(packet, "get_multiple_layers", None)
    if getter is not None:
        try:
            for layer in getter("ngap") or []:
                append(layer)
        except Exception:
            pass

    if not layers:
        try:
            for layer in getattr(packet, "layers", None) or []:
                if normalized_name(getattr(layer, "layer_name", "")) == "ngap":
                    append(layer)
        except Exception:
            pass

    if not layers:
        try:
            if "ngap" in packet:
                append(packet["ngap"])
        except Exception:
            pass

    if not layers:
        try:
            append(getattr(packet, "ngap", None))
        except Exception:
            pass

    return layers


class SnifferStats:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def increment(self, name: str, amount: int = 1) -> int:
        with self._lock:
            self._counts[name] += amount
            return self._counts[name]

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


class SessionTracker:
    """Join request and response packets by either RAN or AMF UE NGAP ID."""

    def __init__(self) -> None:
        self.by_ran: dict[int, dict[str, Any]] = {}
        self.by_amf: dict[int, dict[str, Any]] = {}

    def get(self, ran_id: int | None, amf_id: int | None) -> dict[str, Any]:
        ran_session = self.by_ran.get(ran_id) if ran_id is not None else None
        amf_session = self.by_amf.get(amf_id) if amf_id is not None else None

        if (
            ran_session is not None
            and amf_id is not None
            and ran_session.get("AMF_UE_ID") not in {None, amf_id}
            and amf_session is None
        ):
            self._forget(ran_session)
            ran_session = None
        if (
            amf_session is not None
            and ran_id is not None
            and amf_session.get("RAN_UE_ID") not in {None, ran_id}
            and ran_session is None
        ):
            self._forget(amf_session)
            amf_session = None

        candidates: list[dict[str, Any]] = []
        if ran_session is not None:
            candidates.append(ran_session)
        if amf_session is not None and all(
            amf_session is not item for item in candidates
        ):
            candidates.append(amf_session)

        session = candidates[0] if candidates else {}
        for other in candidates[1:]:
            self._merge(session, other)

        if ran_id is not None:
            self.by_ran = {
                key: value
                for key, value in self.by_ran.items()
                if value is not session or key == ran_id
            }
            session["RAN_UE_ID"] = ran_id
            self.by_ran[ran_id] = session
        if amf_id is not None:
            self.by_amf = {
                key: value
                for key, value in self.by_amf.items()
                if value is not session or key == amf_id
            }
            session["AMF_UE_ID"] = amf_id
            self.by_amf[amf_id] = session
        return session

    def release(self, ran_id: int | None, amf_id: int | None) -> dict[str, Any] | None:
        session = None
        if ran_id is not None:
            session = self.by_ran.get(ran_id)
        if session is None and amf_id is not None:
            session = self.by_amf.get(amf_id)
        if session is None:
            return None

        self._forget(session)
        return session

    def _forget(self, session: dict[str, Any]) -> None:
        self.by_ran = {
            key: value for key, value in self.by_ran.items() if value is not session
        }
        self.by_amf = {
            key: value for key, value in self.by_amf.items() if value is not session
        }

    def _merge(self, primary: dict[str, Any], secondary: dict[str, Any]) -> None:
        if primary is secondary:
            return
        for key, value in secondary.items():
            primary.setdefault(key, value)
        for key, value in list(self.by_ran.items()):
            if value is secondary:
                self.by_ran[key] = primary
        for key, value in list(self.by_amf.items()):
            if value is secondary:
                self.by_amf[key] = primary


@dataclass
class RedisEvent:
    action: str
    payload: dict[str, Any]


class RedisWriter:
    def __init__(self, client: Any, stats: SnifferStats, max_queue: int = 8192) -> None:
        self.client = client
        self.stats = stats
        self.events: queue.Queue[RedisEvent | None] = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._run, name="redis-writer", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.events.put(None)
        self.thread.join(timeout=10)

    def store(self, session: dict[str, Any]) -> None:
        self._enqueue(RedisEvent("store", dict(session)))

    def cleanup(self, ran_id: int, session: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"ran_ue_id": ran_id}
        if session is not None:
            payload["ul_teid"] = normalize_teid(session.get("UL_TEID"))
            payload["dl_teid"] = normalize_teid(session.get("DL_TEID"))
        self._enqueue(RedisEvent("cleanup", payload))

    def _enqueue(self, event: RedisEvent) -> None:
        try:
            self.events.put_nowait(event)
            self.stats.increment("redis_events_queued")
        except queue.Full:
            self.stats.increment("redis_queue_drops")
            print(
                "[ERROR][AMF] Redis event queue full; dropping mapper update",
                file=sys.stderr,
                flush=True,
            )

    def _run(self) -> None:
        while True:
            event = self.events.get()
            try:
                if event is None:
                    return
                self._execute_with_retry(event)
            finally:
                self.events.task_done()

    def _execute_with_retry(self, event: RedisEvent) -> None:
        for attempt in range(1, 4):
            try:
                if event.action == "store":
                    self._store(event.payload)
                else:
                    self._cleanup(event.payload)
                return
            except Exception as exc:
                self.stats.increment("redis_errors")
                if attempt == 3:
                    print(
                        f"[ERROR][AMF] Redis {event.action} failed after 3 attempts: {exc!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return
                time.sleep(0.05 * attempt)

    def _store(self, session: dict[str, Any]) -> None:
        ran = str(session["RAN_UE_ID"])
        ul = normalize_teid(session.get("UL_TEID"))
        dl = normalize_teid(session.get("DL_TEID"))
        if not ul or not dl:
            return

        sst = str(session.get("SST") or "unknown")
        sd = str(session.get("SD") or "unknown")
        base = {"ran_ue_id": ran, "sst": sst, "sd": sd}
        if session.get("AMF_UE_ID") is not None:
            base["amf_ue_id"] = str(session["AMF_UE_ID"])
        if session.get("IMSI"):
            base["imsi"] = str(session["IMSI"])

        old_ran = self.client.hgetall(f"ran:{ran}") or {}
        stale_teids = {
            old_ran.get("ul_teid"),
            old_ran.get("dl_teid"),
        } - {None, "", ul, dl}
        for stale_teid in stale_teids:
            stale_key = f"teid:{stale_teid}"
            if self.client.hget(stale_key, "ran_ue_id") == ran:
                self.client.delete(stale_key)
                self.stats.increment("redis_stale_teids_removed")

        pipeline = self.client.pipeline(transaction=False)
        pipeline.hset(f"teid:{ul}", mapping={**base, "dir": "UL"})
        pipeline.hset(f"teid:{dl}", mapping={**base, "dir": "DL"})
        ran_mapping = {"ul_teid": ul, "dl_teid": dl, "sst": sst, "sd": sd}
        if session.get("AMF_UE_ID") is not None:
            ran_mapping["amf_ue_id"] = str(session["AMF_UE_ID"])
        if session.get("IMSI"):
            ran_mapping["imsi"] = str(session["IMSI"])
        pipeline.hset(f"ran:{ran}", mapping=ran_mapping)
        pipeline.execute()
        self.stats.increment("redis_pairs_written")
        print(
            f"[OK][AMF] ran_ue_id={ran} UL={ul} DL={dl} sst={sst} sd={sd} "
            f"imsi={session.get('IMSI', '-')}",
            flush=True,
        )

    def _cleanup(self, payload: dict[str, Any]) -> None:
        ran_id = int(payload["ran_ue_id"])
        ran_key = f"ran:{ran_id}"
        ran_info = self.client.hgetall(ran_key) or {}
        expected_pair = (
            normalize_teid(payload.get("ul_teid")),
            normalize_teid(payload.get("dl_teid")),
        )
        current_pair = (
            normalize_teid(ran_info.get("ul_teid")),
            normalize_teid(ran_info.get("dl_teid")),
        )
        pair_mismatch = any(
            expected is not None
            and current is not None
            and expected != current
            for expected, current in zip(expected_pair, current_pair)
        )
        if pair_mismatch:
            self.stats.increment("redis_cleanup_reused_ran_skipped")
            print(
                f"[CLEANUP-SKIP][AMF] ran_ue_id={ran_id} "
                f"expected={expected_pair} current={current_pair}",
                flush=True,
            )
            return

        keys = [ran_key]
        if ran_info.get("ul_teid"):
            ul_key = f"teid:{ran_info['ul_teid']}"
            owner = self.client.hget(ul_key, "ran_ue_id")
            if owner in {None, str(ran_id)}:
                keys.append(ul_key)
        if ran_info.get("dl_teid"):
            dl_key = f"teid:{ran_info['dl_teid']}"
            owner = self.client.hget(dl_key, "ran_ue_id")
            if owner in {None, str(ran_id)}:
                keys.append(dl_key)
        self.client.delete(*keys)
        self.stats.increment("redis_sessions_removed")
        print(
            f"[CLEANUP][AMF] ran_ue_id={ran_id} "
            f"ul={ran_info.get('ul_teid', '-')} dl={ran_info.get('dl_teid', '-')}",
            flush=True,
        )


class NgapProcessor:
    def __init__(self, writer: RedisWriter, stats: SnifferStats) -> None:
        self.writer = writer
        self.stats = stats
        self.sessions = SessionTracker()

    def process(self, layer: Any) -> None:
        self.stats.increment("ngap_packets")
        ran_id = first_integer_field(layer, RAN_UE_ID_FIELDS)
        amf_id = first_integer_field(layer, AMF_UE_ID_FIELDS)
        if ran_id is None and amf_id is None:
            self.stats.increment("packets_without_ue_id")
            return

        message_kind = classify_ngap_message(layer)
        if message_kind == "release":
            session = self.sessions.release(ran_id, amf_id)
            cleanup_ran = session.get("RAN_UE_ID") if session is not None else None
            if cleanup_ran is not None and session is not None:
                self.writer.cleanup(int(cleanup_ran), session)
            else:
                self.stats.increment("release_packets_without_session")
            self.stats.increment("release_packets")
            return

        if message_kind == "ambiguous":
            self.stats.increment("ambiguous_setup_packets")
            return

        teids = all_ngap_teids(layer)
        session = self.sessions.get(ran_id, amf_id)
        identity_changed = self._capture_identity(layer, session)
        self._capture_slice(layer, session)
        if message_kind not in {"request", "response"}:
            inferred_kind = self._infer_setup_message_kind(session, teids)
            if inferred_kind is None:
                if identity_changed:
                    self._emit_if_complete(session)
                self.stats.increment("other_ngap_packets")
                return
            message_kind = inferred_kind

        if not teids:
            self.stats.increment("setup_packets_without_teid")
            return
        if len(teids) > 1:
            self.stats.increment("setup_packets_multiple_teids")

        # The request carries the UPF-side TEID used for uplink traffic.
        if message_kind == "request":
            new_ul = self._select_teid(
                teids,
                avoid={normalize_teid(session.get("DL_TEID"))},
            )
            old_ul = normalize_teid(session.get("UL_TEID"))
            if old_ul and old_ul != new_ul:
                session.pop("DL_TEID", None)
                session.pop("_emitted_signature", None)
            session["UL_TEID"] = new_ul
            self.stats.increment("setup_requests")

        # The response carries the gNB-side TEID used for downlink traffic.
        if message_kind == "response":
            session["DL_TEID"] = self._select_teid(
                teids,
                avoid={normalize_teid(session.get("UL_TEID"))},
            )
            self.stats.increment("setup_responses")
            self._emit_if_complete(session)

    def _capture_identity(self, layer: Any, session: dict[str, Any]) -> bool:
        if session.get("IMSI"):
            return False

        supi_format = first_integer_field(layer, SUCI_SUPI_FORMAT_FIELDS)
        scheme = first_integer_field(layer, SUCI_SCHEME_FIELDS)
        if supi_format != 0 or scheme != 0:
            return False

        mcc = self._first_digit_field(layer, MCC_FIELDS, {3})
        mnc = self._first_digit_field(layer, MNC_FIELDS, {2, 3})
        msin = self._first_digit_field(layer, SUCI_MSIN_FIELDS, set(range(1, 11)))
        if not mcc or not mnc or not msin:
            self.stats.increment("incomplete_null_suci")
            return False

        imsi = f"{mcc}{mnc}{msin}"
        if not 5 <= len(imsi) <= 15:
            self.stats.increment("invalid_imsi_length")
            return False

        session["IMSI"] = imsi
        self.stats.increment("imsi_captured")
        return True

    def _first_digit_field(
        self,
        layer: Any,
        aliases: tuple[str, ...],
        widths: set[int],
    ) -> str | None:
        for value in semantic_field_values(layer, aliases):
            normalized = normalize_digit_field(value, widths)
            if normalized is not None:
                return normalized
        return None

    def _infer_setup_message_kind(
        self,
        session: dict[str, Any],
        teids: list[str],
    ) -> str | None:
        if not teids:
            return None

        known_ul = normalize_teid(session.get("UL_TEID"))
        known_dl = normalize_teid(session.get("DL_TEID"))
        if known_ul and not known_dl and any(teid != known_ul for teid in teids):
            self.stats.increment("inferred_setup_responses")
            return "response"
        return None

    def _select_teid(self, teids: list[str], avoid: set[str | None]) -> str:
        for teid in teids:
            if teid not in avoid:
                return teid
        return teids[0]

    def _capture_slice(self, layer: Any, session: dict[str, Any]) -> None:
        if "SST" not in session:
            values = semantic_field_values(layer, SST_FIELDS)
            for value in values:
                sst = normalize_hex_field(value, 2)
                if sst is not None:
                    session["SST"] = sst
                    break
        if "SD" not in session:
            values = semantic_field_values(layer, SD_FIELDS)
            for value in values:
                sd = normalize_hex_field(value, 6)
                if sd is not None:
                    session["SD"] = sd
                    break

    def _emit_if_complete(self, session: dict[str, Any]) -> None:
        ran_id = session.get("RAN_UE_ID")
        ul = normalize_teid(session.get("UL_TEID"))
        dl = normalize_teid(session.get("DL_TEID"))
        if ran_id is None or not ul or not dl:
            return
        signature = (ul, dl, session.get("IMSI"))
        if session.get("_emitted_signature") == signature:
            return
        session["UL_TEID"] = ul
        session["DL_TEID"] = dl
        session["_emitted_signature"] = signature
        self.writer.store(session)
        self.stats.increment("complete_pairs")


def report_stats(stats: SnifferStats, writer: RedisWriter, interval: int) -> None:
    while True:
        time.sleep(interval)
        values = stats.snapshot()
        values["redis_queue_depth"] = writer.events.qsize()
        body = " ".join(f"{key}={values[key]}" for key in sorted(values))
        print(f"[STATS][AMF] {body}", flush=True)


def should_log_error(count: int) -> bool:
    return count <= 10 or count & (count - 1) == 0


def main() -> int:
    interface = os.getenv("amf_sniffer_iface") or (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not interface:
        print(
            "No interface provided. Set amf_sniffer_iface or pass it as an argument.",
            file=sys.stderr,
        )
        return 2

    import pyshark
    import redis

    redis_host = os.getenv("REDIS_HOST", "redis.open5gs.svc.cluster.local")
    stats_interval = max(1, int(os.getenv("AMF_SNIFFER_STATS_INTERVAL", "10")))

    client = redis.Redis(
        host=redis_host,
        port=6379,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=15,
    )

    stats = SnifferStats()
    writer = RedisWriter(client, stats)
    writer.start()
    processor = NgapProcessor(writer, stats)
    reporter = threading.Thread(
        target=report_stats,
        args=(stats, writer, stats_interval),
        name="stats-reporter",
        daemon=True,
    )
    reporter.start()

    capture = pyshark.LiveCapture(
        interface=interface,
        bpf_filter="sctp",
    )
    stopping = False

    def stop_capture(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        try:
            capture.close()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, stop_capture)
    signal.signal(signal.SIGINT, stop_capture)
    print(
        f"[START][AMF] interface={interface} redis={redis_host}:6379",
        flush=True,
    )

    try:
        for packet in capture.sniff_continuously():
            if stopping:
                break
            stats.increment("captured_packets")
            try:
                ngap_layers = packet_ngap_layers(packet)
                if not ngap_layers:
                    stats.increment("non_ngap_packets")
                    continue
                if len(ngap_layers) > 1:
                    stats.increment("multi_ngap_packets")
                    stats.increment("extra_ngap_layers", len(ngap_layers) - 1)
                for layer in ngap_layers:
                    processor.process(layer)
            except Exception as exc:
                count = stats.increment("packet_errors")
                if should_log_error(count):
                    number = getattr(packet, "number", "?")
                    print(
                        f"[ERROR][AMF] packet={number} count={count} error={exc!r}",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        try:
            capture.close()
        except Exception:
            pass
        writer.stop()
        values = stats.snapshot()
        values["redis_queue_depth"] = writer.events.qsize()
        body = " ".join(f"{key}={values[key]}" for key in sorted(values))
        print(f"[STOP][AMF] {body}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
