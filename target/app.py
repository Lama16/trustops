"""ShopLite: the target system the Fixer agent has to keep alive.

Endpoints: /health, /products, /stats, /orders (POST), /metrics
Dependencies: SQLite database, payments provider (payments.py), slow upstream catalog.
"""
import logging
import os
import sqlite3
import sys
import threading
import time
from collections import deque

import requests
import yaml
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

BASE = os.path.dirname(os.path.abspath(__file__))
STARTED_AT = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [shop] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("shop")

try:
    with open(os.path.join(BASE, "config.yaml")) as f:
        CFG = yaml.safe_load(f)
except Exception as exc:  # a broken config crashes the service at startup, like in real life
    log.critical("could not load config.yaml: %s", exc)
    sys.exit(2)

DB_PATH = os.path.join(BASE, CFG["database"]["path"])
PAYMENTS_URL = CFG["payments"]["url"]
PAYMENTS_TIMEOUT = CFG["payments"].get("timeout_seconds", 2)
CACHE_ENABLED = CFG["catalog"].get("cache_enabled", True)
UPSTREAM_LATENCY = CFG["catalog"].get("upstream_latency_ms", 1500) / 1000.0
PAYMENTS_TOKEN = os.environ.get("PAYMENTS_TOKEN")

app = Flask(__name__)

_lock = threading.Lock()
_recent = deque(maxlen=500)  # (path, status, latency_ms)
_cache = {"data": None, "hits": 0, "misses": 0}


# ---------------------------------------------------------------- database
def connect():
    # mode=rw: never silently create a new empty database
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=rw", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------- telemetry
@app.before_request
def _start_timer():
    g.t0 = time.perf_counter()


@app.after_request
def _record(resp):
    if request.path != "/metrics":
        ms = (time.perf_counter() - g.get("t0", time.perf_counter())) * 1000
        with _lock:
            _recent.append((request.path, resp.status_code, ms))
    return resp


@app.errorhandler(Exception)
def _unhandled(exc):
    if isinstance(exc, HTTPException):
        return exc
    log.exception("unhandled error on %s %s", request.method, request.path)
    return jsonify(error=type(exc).__name__, message=str(exc)), 500


# ---------------------------------------------------------------- catalog
def _fetch_catalog_upstream():
    time.sleep(UPSTREAM_LATENCY)  # simulated slow upstream catalog service
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, name, unit_price, stock FROM products ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_catalog():
    if CACHE_ENABLED and _cache["data"] is not None:
        _cache["hits"] += 1
        return _cache["data"]
    _cache["misses"] += 1
    data = _fetch_catalog_upstream()
    if CACHE_ENABLED:
        _cache["data"] = data
    return data


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    checks = {}
    try:
        db().execute("SELECT 1 FROM products LIMIT 1").fetchone()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
    ok = all(v == "ok" for v in checks.values())
    return jsonify(status="ok" if ok else "degraded", checks=checks), (200 if ok else 503)


@app.get("/products")
def products():
    return jsonify(products=get_catalog())


@app.get("/stats")
def stats():
    rows = db().execute(
        "SELECT quantity, unit_price FROM orders WHERE source != 'healthcheck' ORDER BY id"
    ).fetchall()
    revenue = 0.0
    for o in rows:
        revenue += o["quantity"] * o["unit_price"]
    return jsonify(orders=len(rows), revenue=round(revenue, 2))


@app.post("/orders")
def create_order():
    body = request.get_json(silent=True) or {}
    product_id = body.get("product_id")
    quantity = int(body.get("quantity", 1))
    source = body.get("source", "web")

    product = db().execute(
        "SELECT id, unit_price FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    if product is None:
        return jsonify(error="unknown product"), 404
    amount = round(product["unit_price"] * quantity, 2)

    try:
        resp = requests.post(
            f"{PAYMENTS_URL}/charge",
            json={"amount": amount},
            headers={"Authorization": f"Bearer {PAYMENTS_TOKEN or ''}"},
            timeout=PAYMENTS_TIMEOUT,
        )
    except requests.RequestException as exc:
        log.error("payments provider unreachable at %s: %s", PAYMENTS_URL, exc)
        return jsonify(error="payment_unavailable"), 502
    if resp.status_code != 200:
        log.error("payment rejected (HTTP %s): %s", resp.status_code, resp.text.strip())
        return jsonify(error="payment_failed", upstream_status=resp.status_code), 502

    ref = resp.json().get("ref")
    cur = db().execute(
        "INSERT INTO orders (product_id, quantity, unit_price, source, payment_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (product_id, quantity, product["unit_price"], source, ref),
    )
    db().commit()
    return jsonify(order_id=cur.lastrowid, payment_status="charged", payment_ref=ref, amount=amount), 201


@app.get("/metrics")
def metrics():
    with _lock:
        data = list(_recent)
    endpoints = {}
    for path, status, ms in data:
        e = endpoints.setdefault(path, {"requests": 0, "errors_5xx": 0, "lat": []})
        e["requests"] += 1
        e["errors_5xx"] += status >= 500
        e["lat"].append(ms)
    out = {}
    for path, e in endpoints.items():
        lat = sorted(e.pop("lat"))
        out[path] = {
            **e,
            "error_rate": round(e["errors_5xx"] / e["requests"], 3),
            "p50_ms": round(lat[len(lat) // 2], 1),
            "p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1),
        }
    return jsonify(
        uptime_seconds=round(time.time() - STARTED_AT, 1),
        endpoints=out,
        catalog_cache={"hits": _cache["hits"], "misses": _cache["misses"]},
    )


# ---------------------------------------------------------------- startup
if __name__ == "__main__":
    log.info("ShopLite starting (db=%s, payments=%s)", CFG["database"]["path"], PAYMENTS_URL)
    if not PAYMENTS_TOKEN:
        log.warning("PAYMENTS_TOKEN is not set in the environment; checkout will be rejected")
    try:
        c = connect()
        c.execute("SELECT 1").fetchone()
        c.close()
    except Exception as exc:
        log.error("database check failed for %s: %s", CFG["database"]["path"], exc)
    if CACHE_ENABLED:
        try:
            _cache["data"] = _fetch_catalog_upstream()
            log.info("catalog cache warmed (%d products)", len(_cache["data"]))
        except Exception as exc:
            log.error("catalog warm-up failed: %s", exc)
    log.info("listening on %s:%s", CFG["app"]["host"], CFG["app"]["port"])
    app.run(host=CFG["app"]["host"], port=CFG["app"]["port"], threaded=True)
