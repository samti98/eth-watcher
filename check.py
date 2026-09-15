"""
ETH wallet watcher -> Telegram notifications.
Each run checks every 20s for ~8 min (GitHub Actions starts a run every 5 min).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from decimal import Decimal

STATE_FILE = "state.json"
API = "https://api.etherscan.io/v2/api"

WALLETS = [w.strip().lower() for w in os.environ["WALLETS"].split(",") if w.strip()]
ETHERSCAN_KEY = os.environ["ETHERSCAN_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]


def http_get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=30) as r:
        return json.load(r)


def etherscan(action, address, startblock):
    for attempt in range(3):
        data = http_get(API, {
            "chainid": 1, "module": "account", "action": action,
            "address": address, "startblock": startblock, "endblock": 99999999,
            "sort": "asc", "apikey": ETHERSCAN_KEY,
        })
        if data.get("status") == "1":
            return data["result"]
        if "No transactions found" in data.get("message", ""):
            return []
        if "rate limit" in str(data.get("result", "")).lower():
            time.sleep(1.5)
            continue
        raise RuntimeError(f"Etherscan error: {data}")
    raise RuntimeError("Etherscan rate limit, try next run")


def latest_block():
    data = http_get(API, {"chainid": 1, "module": "proxy",
                          "action": "eth_blockNumber", "apikey": ETHERSCAN_KEY})
    return int(data["result"], 16)


def telegram(text):
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", body, timeout=30)


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def check_once(state):
    changed = False

    for wallet in WALLETS:
        if wallet not in state:
            state[wallet] = latest_block()
            changed = True
            telegram(f"👀 Now watching <code>{wallet}</code>")
            continue

        start = state[wallet] + 1
        txs = etherscan("txlist", wallet, start) + etherscan("txlistinternal", wallet, start)
        txs.sort(key=lambda t: int(t["blockNumber"]))

        for tx in txs:
            if tx.get("isError") == "1" or int(tx["value"]) == 0:
                continue
            eth = Decimal(tx["value"]) / Decimal(10**18)
            incoming = tx["to"].lower() == wallet
            arrow = "🟢 Received" if incoming else "🔴 Sent"
            other = tx["from"] if incoming else tx["to"]
            kind = " (internal)" if "type" in tx else ""
            telegram(
                f"{arrow} <b>{eth.normalize():f} ETH</b>{kind}\n"
                f"Wallet: <code>{short(wallet)}</code>\n"
                f"{'From' if incoming else 'To'}: <code>{other}</code>\n"
                f"<a href=\"https://etherscan.io/tx/{tx['hash']}\">View on Etherscan</a>"
            )

        if txs:
            state[wallet] = max(int(t["blockNumber"]) for t in txs)
            changed = True

    return changed


INTERVAL = int(os.environ.get("INTERVAL", "20"))
RUN_FOR = int(os.environ.get("RUN_FOR", "480"))


def main():
    state = load_state()
    end = time.time() + RUN_FOR
    errors = 0
    while True:
        try:
            if check_once(state):
                with open(STATE_FILE, "w") as f:
                    json.dump(state, f, indent=2)
                print("new activity, state saved")
        except Exception as e:
            errors += 1
            print(f"check failed: {e}", file=sys.stderr)
        if time.time() + INTERVAL >= end:
            break
        time.sleep(INTERVAL)
    print(f"done, {errors} failed checks")


if __name__ == "__main__":
    main()
