"""
alarms_blueprint.py
--------------------
Price-alarm add-on for your Trading Journal Flask app.

USAGE IN YOUR MAIN app.py:

    from alarms_blueprint import alarms_bp, start_alarm_worker

    app.register_blueprint(alarms_bp)
    start_alarm_worker()   # call this once, e.g. right before app.run(...)

SETUP:
1. pip install requests   (Flask you already have)
2. Install the "ntfy" app on your phone (App Store / Google Play).
3. In the app, subscribe to a topic name only you know
   (e.g. "zafar-btc-alerts-7f2k9" -- avoid guessable names, ntfy topics
   are public by name).
4. Set that topic as an environment variable NTFY_TOPIC, or edit the
   default below.
"""

import json
import os
import threading
import time
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request

alarms_bp = Blueprint("alarms", __name__, url_prefix="/api/alarms")

# ---------- Config ----------
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "ahad-trades")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
CHECK_INTERVAL_SECONDS = 30
DATA_FILE = os.path.join(os.path.dirname(__file__), "alarms_data.json")

# Ticker -> CoinGecko id. Add more coins here as you need them.
COIN_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "LTC": "litecoin",
}

_lock = threading.Lock()
_worker_started = False


# ---------- Storage ----------
def load_alarms():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_alarms(alarms):
    with open(DATA_FILE, "w") as f:
        json.dump(alarms, f, indent=2)


# ---------- Price fetching ----------
def get_current_prices(coin_ids):
    if not coin_ids:
        return {}
    ids_param = ",".join(sorted(set(coin_ids)))
    url = "https://api.coingecko.com/api/v3/simple/price"
    resp = requests.get(url, params={"ids": ids_param, "vs_currencies": "usd"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return {coin_id: data[coin_id]["usd"] for coin_id in data}


# ---------- Notifications ----------
def send_phone_alert(coin_symbol, target_price, direction, current_price):
    title = f"{coin_symbol} hit your target!"
    message = (
        f"{coin_symbol} is now ${current_price:,.4f} "
        f"({'above' if direction == 'above' else 'below'} your target of ${target_price:,.4f})"
    )
    try:
        requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"[alarms] Failed to send notification: {e}")


# ---------- Background worker ----------
def _price_check_loop():
    while True:
        try:
            with _lock:
                alarms = load_alarms()
            active = [a for a in alarms if not a["triggered"]]

            if active:
                coin_ids = [a["coin_id"] for a in active]
                prices = get_current_prices(coin_ids)

                changed = False
                for alarm in active:
                    price = prices.get(alarm["coin_id"])
                    if price is None:
                        continue
                    hit = (
                        (alarm["direction"] == "above" and price >= alarm["target_price"])
                        or (alarm["direction"] == "below" and price <= alarm["target_price"])
                    )
                    if hit:
                        send_phone_alert(alarm["coin_symbol"], alarm["target_price"], alarm["direction"], price)
                        alarm["triggered"] = True
                        alarm["triggered_at"] = datetime.utcnow().isoformat()
                        alarm["triggered_price"] = price
                        changed = True

                if changed:
                    with _lock:
                        save_alarms(alarms)

        except Exception as e:
            print(f"[alarms] Error during price check: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


def start_alarm_worker():
    """Call this once from your main app, before app.run()."""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    t = threading.Thread(target=_price_check_loop, daemon=True)
    t.start()


# ---------- Routes ----------
@alarms_bp.route("", methods=["GET"])
def list_alarms():
    with _lock:
        alarms = load_alarms()
    return jsonify(alarms)


@alarms_bp.route("", methods=["POST"])
def add_alarm():
    body = request.get_json(force=True)
    symbol = (body.get("coin_symbol") or "").upper().strip()
    target_price = body.get("target_price")
    direction = body.get("direction")

    if symbol not in COIN_ID_MAP:
        return jsonify({"error": f"Unknown coin '{symbol}'. Add it to COIN_ID_MAP in alarms_blueprint.py."}), 400
    if direction not in ("above", "below"):
        return jsonify({"error": "direction must be 'above' or 'below'"}), 400
    try:
        target_price = float(target_price)
    except (TypeError, ValueError):
        return jsonify({"error": "target_price must be a number"}), 400

    alarm = {
        "id": int(time.time() * 1000),
        "coin_symbol": symbol,
        "coin_id": COIN_ID_MAP[symbol],
        "target_price": target_price,
        "direction": direction,
        "triggered": False,
        "created_at": datetime.utcnow().isoformat(),
    }

    with _lock:
        alarms = load_alarms()
        alarms.append(alarm)
        save_alarms(alarms)

    return jsonify(alarm), 201


@alarms_bp.route("/<int:alarm_id>", methods=["DELETE"])
def delete_alarm(alarm_id):
    with _lock:
        alarms = load_alarms()
        alarms = [a for a in alarms if a["id"] != alarm_id]
        save_alarms(alarms)
    return jsonify({"ok": True})
