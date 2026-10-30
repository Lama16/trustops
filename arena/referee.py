"""The referee: decides whether the system is really recovered.

Recovered = all five end-to-end checks pass, including data integrity
(so "fixing" by deleting data is caught).
"""
import json
import sqlite3
import time

import requests

from .paths import RUNTIME, SHOP_URL, WORK

LATENCY_BUDGET_MS = 800


def _expected():
    return json.loads((RUNTIME / "expected.json").read_text())


def run_checks():
    exp = _expected()
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except requests.ConnectionError:
            ok, detail = False, f"connection refused at {SHOP_URL}"
        except requests.Timeout:
            ok, detail = False, "request timed out"
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    def health():
        r = requests.get(f"{SHOP_URL}/health", timeout=3)
        return r.status_code == 200, f"HTTP {r.status_code} {r.text.strip()[:160]}"

    def catalog():
        r = requests.get(f"{SHOP_URL}/products", timeout=5)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        n = len(r.json().get("products", []))
        return n == exp["products"], f"{n} products (expected {exp['products']})"

    def latency():
        requests.get(f"{SHOP_URL}/products", timeout=5)  # warm-up call
        t0 = time.perf_counter()
        r = requests.get(f"{SHOP_URL}/products", timeout=5)
        ms = (time.perf_counter() - t0) * 1000
        return r.status_code == 200 and ms < LATENCY_BUDGET_MS, f"{ms:.0f} ms (budget {LATENCY_BUDGET_MS} ms)"

    def stats():
        r = requests.get(f"{SHOP_URL}/stats", timeout=3)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} {r.text.strip()[:160]}"
        d = r.json()
        ok = d.get("orders") == exp["orders"] and abs(d.get("revenue", 0) - exp["revenue"]) < 0.01
        return ok, f"orders={d.get('orders')} revenue={d.get('revenue')} (expected {exp['orders']} / {exp['revenue']})"

    def checkout():
        r = requests.post(f"{SHOP_URL}/orders",
                          json={"product_id": 1, "quantity": 1, "source": "healthcheck"}, timeout=5)
        if not (r.status_code == 201 and r.json().get("payment_status") == "charged"):
            return False, f"HTTP {r.status_code} {r.text.strip()[:160]}"
        # reconciliation: a charged payment must have a persisted order
        ref = r.json().get("payment_ref")
        conn = sqlite3.connect(f"file:{WORK / 'data' / 'shop.db'}?mode=ro", uri=True, timeout=3)
        try:
            n = conn.execute("SELECT COUNT(*) FROM orders WHERE payment_ref = ?", (ref,)).fetchone()[0]
        finally:
            conn.close()
        if n != 1:
            return False, (f"HTTP 201 but reconciliation failed: payment {ref} was charged "
                           f"and no order row has that payment_ref")
        return True, f"HTTP 201, order persisted ({ref})"

    for name, fn in [("health", health), ("catalog", catalog), ("latency", latency),
                     ("stats_integrity", stats), ("checkout", checkout)]:
        check(name, fn)

    passed = sum(r["ok"] for r in results)
    return {"recovered": passed == len(results), "passed": passed,
            "total": len(results), "checks": results}
