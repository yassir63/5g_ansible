from flask import Flask, jsonify, request
import redis
import os
import zlib
import time

app = Flask(__name__)

redis_host = os.getenv("REDIS_HOST", "redis")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

def norm_hex(s: str, width: int) -> str:
    if not s:
        return ""
    s = s.lower().replace("0x", "")
    return s.zfill(width)

def slice_id_from_sst_sd(sst: str, sd: str) -> int:
    key = f"{sst}:{sd}".encode()
    return zlib.crc32(key) & 0xffffffff

def make_teid_args(ul_teid: str = "", dl_teid: str = "", slice_id: int = 0):
    """
    gtp_latency_user supports:
      - single: 0xTEID@slice
      - pair:   0xUL:0xDL@slice
    """
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

def find_ul_dl_from_teid_hashes_by_imsi(imsi: str):
    """
    Fallback: scan teid:* and look for hashes with field 'imsi' == imsi
    and field 'dir' in {UL,DL}.
    """
    ul = dl = None
    ue_ip = None
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
        if direction == "UL":
            ul = norm_hex(teid_hex, 8)
        elif direction == "DL":
            dl = norm_hex(teid_hex, 8)
    return ul, dl, ue_ip

def get_teid_hash(teid: str):
    teid = norm_hex(teid, 8)
    return rdb.hgetall(f"teid:{teid}") or None

@app.route("/teid/<teid>")
def get_teid(teid):
    teid_n = norm_hex(teid, 8)
    result = rdb.hgetall(f"teid:{teid_n}")
    return jsonify(result or {"error": "TEID not found"})

@app.route("/all-teids")
def list_all_teids():
    keys = rdb.keys("teid:*")
    entries = {k: rdb.hgetall(k) for k in keys}
    return jsonify(entries)

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

# ---------------------------
# NEW: resolver endpoints
# ---------------------------

@app.route("/resolve/ue/imsi/<imsi>")
def resolve_ue_by_imsi(imsi):
    # 1) Prefer ue:<imsi> (best model)
    rec = try_get_ue_record(imsi)
    if rec:
        sst = norm_hex(rec.get("sst", ""), 2)
        sd  = norm_hex(rec.get("sd", ""), 6)
        ul  = norm_hex(rec.get("ul_teid", ""), 8)
        dl  = norm_hex(rec.get("dl_teid", ""), 8)
        sid = slice_id_from_sst_sd(sst, sd) if (sst and sd) else 0

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

    # 2) Fallback: scan teid:* hashes if they contain "imsi" field
    ul, dl, ue_ip = find_ul_dl_from_teid_hashes_by_imsi(imsi)
    if ul or dl:
        # slice unknown in this fallback unless you also store it in teid hash
        # If you DO store sst/sd in teid hash, you can enhance this easily.
        return jsonify({
            "imsi": imsi,
            "ue_ip": ue_ip,
            "sst": None,
            "sd": None,
            "ul_teid": f"0x{ul}" if ul else None,
            "dl_teid": f"0x{dl}" if dl else None,
            "slice_id": 0,
            "teid_args": make_teid_args(ul, dl, 0)
        })

    # 3) Last resort: your existing imsi:<imsi> -> one teid only
    teid = rdb.get(f"imsi:{imsi}")
    if not teid:
        return jsonify({"error": "IMSI not found"}), 404

    teid_n = norm_hex(teid, 8)
    h = rdb.hgetall(f"teid:{teid_n}") or {}
    # direction is whatever stored; we just return single-teid args
    return jsonify({
        "imsi": imsi,
        "teid": f"0x{teid_n}",
        "data": h,
        "slice_id": 0,
        "teid_args": make_teid_args(teid_n, "", 0)
    })

@app.route("/resolve/slice")
def resolve_slice():
    sst = request.args.get("sst")
    sd = request.args.get("sd")
    if not sst or not sd:
        return jsonify({"error": "missing sst or sd query param"}), 400

    sst = norm_hex(sst, 2)
    sd  = norm_hex(sd, 6)
    sid = slice_id_from_sst_sd(sst, sd)

    # Preferred: slice:<sst>:<sd> is a SET of IMSIs
    imsis = sorted(list(rdb.smembers(f"slice:{sst}:{sd}")))
    ues = []
    teid_args_list = []

    for imsi in imsis:
        rec = try_get_ue_record(imsi)
        if not rec:
            continue
        ul = norm_hex(rec.get("ul_teid", ""), 8)
        dl = norm_hex(rec.get("dl_teid", ""), 8)
        arg = make_teid_args(ul, dl, sid)
        if not arg:
            continue
        ues.append({
            "imsi": imsi,
            "ran_ue_id": rec.get("ran_ue_id"),
            "ue_ip": rec.get("ue_ip"),
            "ul_teid": f"0x{ul}" if ul else None,
            "dl_teid": f"0x{dl}" if dl else None,
            "last_seen": rec.get("last_seen"),
            "teid_args": arg
        })
        teid_args_list.append(arg)

    return jsonify({
        "sst": sst,
        "sd": sd,
        "slice_id": sid,
        "ues": ues,
        "teid_args": " ".join(teid_args_list)
    })

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
            "/resolve/slice?sst=..&sd=.."
        ]
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)