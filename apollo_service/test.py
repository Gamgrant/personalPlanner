from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/apollo-webhook", methods=["POST"])
def apollo_webhook():
    resp = request.get_json(force=True, silent=True) or {}
    print("Status:", resp.status_code)

    try:
        print("Response JSON:", resp.json())
    except Exception:
        print("Response Text:", resp.text)
    
    # TODO: store phone numbers where you want (e.g. DB or Google Sheets)
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(port=5000)

