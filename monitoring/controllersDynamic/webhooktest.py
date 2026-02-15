# from flask import Flask, request, jsonify

# app = Flask(__name__)

# @app.route("/alert", methods=["POST"])
# def alert_handler():
#     data = request.get_json()
#     alerts = data.get("alerts", [])

#     for alert in alerts:
#         name = alert.get("labels", {}).get("alertname")
#         status = alert.get("status")
#         if name == "LowGTPThroughput" and status == "firing":
#             print("🚨 ALERT: Low throughput detected!")

#     return jsonify({"status": "ok"}), 200

# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5000)


from flask import Flask, request, jsonify
import json

app = Flask(__name__)

@app.route("/alert", methods=["POST"])
def alert_handler():
    data = request.get_json()
    print("🚨 ALERT: Low throughput detected!")
    print(json.dumps(data, indent=2))

    # Write to alert file for kopf to pick up
    with open("/tmp/alert.json", "w") as f:
        json.dump(data, f)

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)