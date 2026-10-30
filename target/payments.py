"""Mock payments provider: the external (vendor) dependency ShopLite needs for checkout.

This is a third-party service: on-call engineers can restart the local connector,
but they cannot change the vendor's behaviour (outages, revoked tokens).
"""
import logging
import os
import sys
import time
import uuid
from collections import deque

from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [payments] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("payments")

EXPECTED_TOKEN = "tok_live_7f3a9c"
VENDOR_STATE = os.environ.get("VENDOR_STATE_DIR", "")
CHARGES = deque(maxlen=200)

app = Flask(__name__)


def _vendor(flag):
    return bool(VENDOR_STATE) and os.path.exists(os.path.join(VENDOR_STATE, flag))


@app.get("/health")
def health():
    if _vendor("outage"):
        return jsonify(status="maintenance"), 503
    return jsonify(status="ok")


@app.post("/charge")
def charge():
    if _vendor("outage"):
        log.warning("charge refused: provider-side scheduled maintenance in progress")
        return jsonify(error="provider_maintenance",
                       message="Payments provider is under maintenance. ETA unknown."), 503
    if request.headers.get("Authorization", "") != f"Bearer {EXPECTED_TOKEN}":
        log.warning("rejected charge: invalid or missing API token")
        return jsonify(error="unauthorized"), 401
    if _vendor("token_revoked"):
        log.warning("rejected charge: API token has been revoked by the account owner")
        return jsonify(error="token_revoked",
                       message="This API token was revoked. A new token must be issued from the provider dashboard."), 401
    body = request.get_json(silent=True) or {}
    ref = "pay_" + uuid.uuid4().hex[:10]
    amount = float(body.get("amount", 0))
    CHARGES.append({"ref": ref, "amount": amount, "at": time.strftime("%H:%M:%S")})
    log.info("charged %.2f SAR ref=%s", amount, ref)
    return jsonify(status="charged", ref=ref, amount=body.get("amount"))


@app.get("/charges")
def charges():
    """Provider dashboard: most recent successful charges."""
    return jsonify(charges=list(CHARGES)[-50:])


if __name__ == "__main__":
    port = int(os.environ.get("PAYMENTS_PORT", 5051))
    log.info("payments provider listening on 127.0.0.1:%s", port)
    app.run(host="127.0.0.1", port=port, threaded=True)
