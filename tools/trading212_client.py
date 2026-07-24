"""
Trading 212 Public API client.

Docs: https://docs.trading212.com/api
Auth: HTTP Basic Auth -- Authorization: Basic base64(API_KEY:API_SECRET).
Older keys without a secret fall back to the legacy raw-key header scheme.
Known beta quirk: POST /orders/market is NOT idempotent -- retrying a timed-out
request can duplicate a live order. place_market_order() below guards against
this with a client-side order log keyed on an idempotency token; it will
refuse to resend an order it already has a pending/unknown-outcome record for.

Usage as a script:
    python tools/trading212_client.py account
    python tools/trading212_client.py cash
    python tools/trading212_client.py portfolio
    python tools/trading212_client.py buy AAPL_US_EQ 2
    python tools/trading212_client.py sell AAPL_US_EQ 2
"""
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URLS = {
    "demo": "https://demo.trading212.com",
    "live": "https://live.trading212.com",
}

ORDER_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "order_log.json"


class Trading212Error(RuntimeError):
    pass


class Trading212Client:
    def __init__(self, api_key: str = None, api_secret: str = None, mode: str = None):
        self.api_key = api_key or os.environ.get("TRADING212_API_KEY")
        self.api_secret = api_secret or os.environ.get("TRADING212_API_SECRET")
        self.mode = (mode or os.environ.get("TRADING212_API_MODE", "demo")).lower()
        if not self.api_key:
            raise Trading212Error("TRADING212_API_KEY not set (check your .env)")
        if self.mode not in BASE_URLS:
            raise Trading212Error(f"TRADING212_API_MODE must be 'demo' or 'live', got {self.mode!r}")
        self.base_url = BASE_URLS[self.mode]
        self.session = requests.Session()

        if self.api_secret:
            token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
            auth_header = f"Basic {token}"
        else:
            # legacy keys issued before the secret-key scheme
            auth_header = self.api_key

        self.session.headers.update({
            "Authorization": auth_header,
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, retries: int = 3, **kwargs):
        url = f"{self.base_url}{path}"
        last_err = None
        for attempt in range(retries):
            resp = self.session.request(method, url, timeout=20, **kwargs)
            if resp.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise Trading212Error(f"{method} {path} -> {resp.status_code}: {resp.text}")
            return resp.json() if resp.text else {}
        raise Trading212Error(f"{method} {path} rate-limited after {retries} retries: {last_err}")

    # --- Account ---
    def get_account_summary(self):
        return self._request("GET", "/api/v0/equity/account/summary")

    def get_cash(self):
        return self._request("GET", "/api/v0/equity/account/cash")

    # --- Portfolio ---
    def get_portfolio(self):
        return self._request("GET", "/api/v0/equity/positions")

    def get_position(self, ticker: str):
        return self._request("GET", f"/api/v0/equity/positions/{ticker}")

    # --- Instruments ---
    def get_instruments(self):
        return self._request("GET", "/api/v0/equity/metadata/instruments")

    # --- Orders ---
    def get_orders(self):
        return self._request("GET", "/api/v0/equity/orders")

    def get_order(self, order_id):
        return self._request("GET", f"/api/v0/equity/orders/{order_id}")

    def place_market_order(self, ticker: str, quantity: float, idempotency_key: str = None):
        """
        quantity > 0 = buy, quantity < 0 = sell.
        idempotency_key: pass a stable string (e.g. "2026-07-24-AAPL-buy") so a
        crashed/retried call doesn't double-submit. Checked against the local
        order log before hitting the API.
        """
        log = _load_order_log()
        key = idempotency_key or str(uuid.uuid4())
        if key in log:
            raise Trading212Error(
                f"Refusing to place order: idempotency key {key!r} already used "
                f"(logged result: {log[key]}). Use a new key if this is intentional."
            )
        log[key] = {"status": "submitting", "ticker": ticker, "quantity": quantity}
        _save_order_log(log)

        try:
            result = self._request("POST", "/api/v0/equity/orders/market", json={
                "ticker": ticker,
                "quantity": quantity,
            })
        except Trading212Error as e:
            log[key] = {"status": "error", "ticker": ticker, "quantity": quantity, "error": str(e)}
            _save_order_log(log)
            raise

        log[key] = {"status": "submitted", "ticker": ticker, "quantity": quantity, "result": result}
        _save_order_log(log)
        return result


def _load_order_log():
    if ORDER_LOG_PATH.exists():
        return json.loads(ORDER_LOG_PATH.read_text())
    return {}


def _save_order_log(log):
    ORDER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDER_LOG_PATH.write_text(json.dumps(log, indent=2))


def _main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    client = Trading212Client()
    cmd = sys.argv[1]

    if cmd == "account":
        print(json.dumps(client.get_account_summary(), indent=2))
    elif cmd == "cash":
        print(json.dumps(client.get_cash(), indent=2))
    elif cmd == "portfolio":
        print(json.dumps(client.get_portfolio(), indent=2))
    elif cmd == "instruments":
        print(json.dumps(client.get_instruments(), indent=2))
    elif cmd == "orders":
        print(json.dumps(client.get_orders(), indent=2))
    elif cmd in ("buy", "sell"):
        ticker, qty = sys.argv[2], float(sys.argv[3])
        signed_qty = qty if cmd == "buy" else -qty
        key = f"cli-{cmd}-{ticker}-{int(time.time())}"
        print(json.dumps(client.place_market_order(ticker, signed_qty, idempotency_key=key), indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _main()
