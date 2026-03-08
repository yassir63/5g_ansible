from flask import Flask, jsonify, request
import redis
import os

app = Flask(__name__)

redis_host = os.getenv("REDIS_HOST", "redis")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def norm_hex(s: str, width: int) -> str:
    if not s:
        return ""
    s = str(s).strip().lower().replace("0x", "").replace(":", "")
    if not s:
        return ""
    return s.zfill(width)


def normalize_sst(sst: str | None) -> str:
    s = (sst or "").strip().lower()
    if not s or s in ("unknown", "none", "null"):
        return "01"
    return norm_hex(s, 2)


def normalize_sd(sd: str | None) -> str:
    s = (sd or "").strip().lower().replace(":", "")
    if not s or s in ("unknown", "none", "null"):
        return "ffffff"
    return norm_hex(s, 6)


def slice_id_from_sst_sd(sst: str, sd: str) -> str:
    sst_n = normalize_sst(sst)
    sd_n = normalize_sd(sd)
    return f"{sst_n}:{sd_n}"


def make_teid_args(ul_teid: str = "", dl_teid: str = "", slice_id: str = "") -> str:
    """
    gtp_latency_user supports:
      - single: 0xTEID@slice
      - pair:   0xUL:0xDL@slice
    """
    ul_teid = norm_hex(ul_teid, 8)
    dl_teid = norm_hex(dl_teid, 8)

    if not slice_id:
        slice_id = "01:ffffff"

    if ul_teid and dl_teid:
        return f"0x{ul_teid}:0x{dl_teid}@{slice_id}"
    if ul_teid:
        return f"0x{ul_teid}@{slice_id}"
    if dl_teid:
        return f"0x{dl_teid}@{slice_id}"
    return ""


def try_get_ue_record(imsi: str):
    """
    Preferred future-proof storage:
      ue:<imsi> hash contains ul_teid, dl_teid, sst, sd, ran_ue_id, ue_ip...
    """
    rec = rdb.hgetall(f"ue:{imsi}")
    return rec if rec else None


def get_teid_hash(teid: str):
    teid = norm_hex(teid, 8)
    return rdb.hgetall(f"teid:{teid}") or None


def find_ul_dl_from_teid_hashes_by_imsi(imsi: str):
    """
    Fallback: scan teid:* and look for hashes with field 'imsi' == imsi
    and field 'dir' in {UL,DL}. Also tries to recover ran_ue_id/sst/sd.
    """
    ul = dl = None
    ue_ip = None
    ran_ue_id = None
    sst = None
    sd = None

    keys = rdb.keys("teid:*")
    for k in keys:
        teid_hex = k.split("teid:", 1)[1]
        h = rdb.hgetall(k)
        if not h:
            continue
        if h.get("imsi") != imsi:
            continue

        direction = (h.get("dir") or "").upper()

        if not ue_ip:
            ue_ip = h.get("ue_ip")
        if not ran_ue_id:
            ran_ue_id = h.get("ran_ue_id")
        if not sst:
            sst = h.get("sst")
        if not sd:
            sd = h.get("sd")

        if direction == "UL":
            ul = norm_hex(teid_hex, 8)
        elif direction == "DL":
            dl = norm_hex(teid_hex, 8)

    return ul, dl, ue_ip, ran_ue_id, sst, sd


def build_inventory_rows(limit: int = 500):
    """
    Build clean inventory rows for the controller.

    Strategy:
      1) Prefer ran:<id> keys because they already pair UL/DL TEIDs.
      2) Enrich from teid:<ul> / teid:<dl>.
      3) Emit only rows that have BOTH ul_teid and dl_teid.
      4) Always normalize missing/unknown sd -> ffffff.
    """
    rows = []
    seen_pairs = set()

    ran_keys = sorted(rdb.keys("ran:*"))
    for ran_key in ran_keys:
        ran_id = ran_key.split("ran:", 1)[1]
        ran_info = rdb.hgetall(ran_key) or {}

        ul = norm_hex(ran_info.get("ul_teid", ""), 8)
        dl = norm_hex(ran_info.get("dl_teid", ""), 8)

        # Controller only wants real pairs
        if not ul or not dl:
            continue

        ul_h = get_teid_hash(ul) or {}
        dl_h = get_teid_hash(dl) or {}

        # Prefer IMSI from UL (Open5GS UL side usually carries the longer IMSI)
        imsi = (
            ul_h.get("imsi")
            or dl_h.get("imsi")
            or ran_info.get("imsi")
            or None
        )

        ue_ip = (
            ul_h.get("ue_ip")
            or dl_h.get("ue_ip")
            or ran_info.get("ue_ip")
            or None
        )

        sst = (
            ran_info.get("sst")
            or ul_h.get("sst")
            or dl_h.get("sst")
            or "01"
        )

        sd = (
            ran_info.get("sd")
            or ul_h.get("sd")
            or dl_h.get("sd")
            or "ffffff"
        )

        sst_n = normalize_sst(sst)
        sd_n = normalize_sd(sd)
        slice_id = slice_id_from_sst_sd(sst_n, sd_n)

        pair_key = (ul, dl, ran_id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        rows.append({
            "imsi": imsi,
            "ran_ue_id": ran_id,
            "ue_ip": ue_ip,
            "sst": sst_n,
            "sd": sd_n,
            "slice_id": slice_id,
            "ul_teid": f"0x{ul}",
            "dl_teid": f"0x{dl}",
            "teid_args": make_teid_args(ul, dl, slice_id),
            "source": "inventory -> ran:*",
        })

        if len(rows) >= limit:
            break

    return rows[:limit]


# -------------------------------------------------
# Basic lookup endpoints
# -------------------------------------------------

@app.route("/teid/<teid>")
def get_teid(teid):
    teid_n = norm_hex(teid, 8)
    result = rdb.hgetall(f"teid:{teid_n}")
    return jsonify(result or {"error": "TEID not found"})


@app.route("/all-teids")
def list_all_teids():
    limit = int(request.args.get("limit", 500))
    keys = sorted(rdb.keys("teid:*"))[:limit]
    entries = {k: rdb.hgetall(k) for k in keys}
    return jsonify({
        "count": limit,
        "cursor": 0,
        "entries": entries
    })


@app.route("/ip/<ue_ip>")
def get_by_ip(ue_ip):
    teid = rdb.get(f"ip:{ue_ip}")
    if not teid:
        return jsonify({"error": "UE IP not found"}), 404
    teid_n = norm_hex(teid, 8)
    data = rdb.hgetall(f"teid:{teid_n}")
    return jsonify({"teid": teid_n, "data": data})


@app.route("/imsi/<imsi>")
def get_by_imsi(imsi):
    teid = rdb.get(f"imsi:{imsi}")
    if not teid:
        return jsonify({"error": "IMSI not found"}), 404
    teid_n = norm_hex(teid, 8)
    data = rdb.hgetall(f"teid:{teid_n}")
    return jsonify({"teid": teid_n, "data": data})


# -------------------------------------------------
# Resolver endpoints
# -------------------------------------------------

@app.route("/resolve/ue/imsi/<imsi>")
def resolve_ue_by_imsi(imsi):
    # 1) Prefer ue:<imsi>
    rec = try_get_ue_record(imsi)
    if rec:
        sst = normalize_sst(rec.get("sst", "01"))
        sd = normalize_sd(rec.get("sd", "ffffff"))
        ul = norm_hex(rec.get("ul_teid", ""), 8)
        dl = norm_hex(rec.get("dl_teid", ""), 8)
        sid = slice_id_from_sst_sd(sst, sd)

        return jsonify({
            "imsi": imsi,
            "ran_ue_id": rec.get("ran_ue_id"),
            "ue_ip": rec.get("ue_ip"),
            "sst": sst,
            "sd": sd,
            "ul_teid": f"0x{ul}" if ul else None,
            "dl_teid": f"0x{dl}" if dl else None,
            "slice_id": sid,
            "last_seen": rec.get("last_seen"),
            "teid_args": make_teid_args(ul, dl, sid)
        })

    # 2) Fallback: scan teid:* by imsi
    ul, dl, ue_ip, ran_ue_id, sst, sd = find_ul_dl_from_teid_hashes_by_imsi(imsi)
    if ul or dl:
        sst_n = normalize_sst(sst)
        sd_n = normalize_sd(sd)
        sid = slice_id_from_sst_sd(sst_n, sd_n)

        return jsonify({
            "imsi": imsi,
            "ran_ue_id": ran_ue_id,
            "ue_ip": ue_ip,
            "sst": sst_n,
            "sd": sd_n,
            "ul_teid": f"0x{ul}" if ul else None,
            "dl_teid": f"0x{dl}" if dl else None,
            "slice_id": sid,
            "teid_args": make_teid_args(ul, dl, sid)
        })

    # 3) Last resort: imsi:<imsi> -> one teid only
    teid = rdb.get(f"imsi:{imsi}")
    if not teid:
        return jsonify({"error": "IMSI not found"}), 404

    teid_n = norm_hex(teid, 8)
    h = rdb.hgetall(f"teid:{teid_n}") or {}

    sst_n = normalize_sst(h.get("sst"))
    sd_n = normalize_sd(h.get("sd"))
    sid = slice_id_from_sst_sd(sst_n, sd_n)

    return jsonify({
        "imsi": imsi,
        "teid": f"0x{teid_n}",
        "data": h,
        "slice_id": sid,
        "teid_args": make_teid_args(teid_n, "", sid)
    })


@app.route("/resolve/slice")
def resolve_slice():
    sst = request.args.get("sst")
    sd = request.args.get("sd")

    if not sst:
        return jsonify({"error": "missing sst query param"}), 400

    sst_n = normalize_sst(sst)
    sd_n = normalize_sd(sd)
    sid = slice_id_from_sst_sd(sst_n, sd_n)

    inventory = build_inventory_rows(limit=500)
    ues = []
    teid_args_list = []

    for ue in inventory:
        if ue.get("sst") != sst_n:
            continue
        if ue.get("sd") != sd_n:
            continue

        ues.append(ue)
        if ue.get("teid_args"):
            teid_args_list.append(ue["teid_args"])

    return jsonify({
        "sst": sst_n,
        "sd": sd_n,
        "slice_id": sid,
        "ues": ues,
        "teid_args": " ".join(teid_args_list)
    })


# -------------------------------------------------
# Inventory endpoint used by controller
# -------------------------------------------------

@app.route("/inventory/ues")
def inventory_ues():
    limit = int(request.args.get("limit", 500))
    rows = build_inventory_rows(limit=limit)
    return jsonify({
        "count": len(rows),
        "limit": limit,
        "ues": rows
    })


# -------------------------------------------------
# Root
# -------------------------------------------------

@app.route("/")
def root():
    return jsonify({
        "status": "UE mapper API running",
        "endpoints": [
            "/teid/<teid>",
            "/all-teids",
            "/ip/<ue_ip>",
            "/imsi/<imsi>",
            "/resolve/ue/imsi/<imsi>",
            "/resolve/slice?sst=..&sd=..",
            "/inventory/ues"
        ]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

# from flask import Flask, jsonify
# import redis
# import os

# app = Flask(__name__)

# redis_host = os.getenv("REDIS_HOST", "redis")
# rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

# @app.route("/teid/<teid>")
# def get_teid(teid):
#     result = rdb.hgetall(f"teid:{teid}")
#     return jsonify(result or {"error": "TEID not found"})

# @app.route("/all-teids")
# def list_all_teids():
#     keys = rdb.keys("teid:*")
#     entries = {k: rdb.hgetall(k) for k in keys}
#     return jsonify(entries)

# @app.route("/ip/<ue_ip>")
# def get_by_ip(ue_ip):
#     teid = rdb.get(f"ip:{ue_ip}")
#     if not teid:
#         return jsonify({"error": "UE IP not found"})
#     data = rdb.hgetall(f"teid:{teid}")
#     return jsonify({"teid": teid, "data": data})

# @app.route("/imsi/<imsi>")
# def get_by_imsi(imsi):
#     teid = rdb.get(f"imsi:{imsi}")
#     if not teid:
#         return jsonify({"error": "IMSI not found"})
#     data = rdb.hgetall(f"teid:{teid}")
#     return jsonify({"teid": teid, "data": data})


# @app.route("/")
# def root():
#     return jsonify({"status": "UE mapper API running", "endpoints": ["/teid/<teid>", "/all-teids","/imsi/<imsi>","/ip/<ue_ip>"]})

# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=8080)
