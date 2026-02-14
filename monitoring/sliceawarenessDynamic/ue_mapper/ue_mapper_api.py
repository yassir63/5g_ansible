from flask import Flask, jsonify
import redis
import os

app = Flask(__name__)

redis_host = os.getenv("REDIS_HOST", "redis")
rdb = redis.Redis(host=redis_host, port=6379, decode_responses=True)

@app.route("/teid/<teid>")
def get_teid(teid):
    result = rdb.hgetall(f"teid:{teid}")
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
        return jsonify({"error": "UE IP not found"})
    data = rdb.hgetall(f"teid:{teid}")
    return jsonify({"teid": teid, "data": data})

@app.route("/imsi/<imsi>")
def get_by_imsi(imsi):
    teid = rdb.get(f"imsi:{imsi}")
    if not teid:
        return jsonify({"error": "IMSI not found"})
    data = rdb.hgetall(f"teid:{teid}")
    return jsonify({"teid": teid, "data": data})


@app.route("/")
def root():
    return jsonify({"status": "UE mapper API running", "endpoints": ["/teid/<teid>", "/all-teids","/imsi/<imsi>","/ip/<ue_ip>"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
