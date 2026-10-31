#!/usr/bin/env python3
"""TrustOps live console: watch one incident unfold in the browser.

  python -m console.server            # then open http://127.0.0.1:8090
  python -m console.server --port 8090

Scenarios are picked in the page (or with ?scenario=A&autostart=1).
Needs ANTHROPIC_API_KEY (or another provider, see fixer/llm.py).
"""
import argparse
import json
import queue
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from arena import faults
from fixer.round import fixer_factory, run_round
from fixer.trust import TrustLedger

STATIC = Path(__file__).parent / "static"

# A ledger matching the end of the 72-incident evaluation: what the agent had earned by then.
EARNED = {"config": (7, 1, "success"), "data": (5, 0, "success"), "code": (4, 0, "success"),
          "dependency": (2, 0, "success"), "secrets": (2, 0, "success"), "permissions": (2, 0, "success"),
          "workflow": (2, 0, "success"), "schema": (2, 1, "failure"), "performance": (1, 0, "success")}

SCENARIOS = {
    "A": {"title": "Two faults at once", "faults": ["config_wrong_db_path", "payments_down"], "ledger": "new"},
    "B": {"title": "A fault type it has earned", "faults": ["stats_code_bug"], "ledger": "earned"},
    "C": {"title": "Outside its control", "faults": ["payments_vendor_outage"], "ledger": "earned"},
}

app = Flask(__name__, static_folder=None)
subscribers, lock = [], threading.Lock()
state = {"running": False}


def publish(kind, data):
    msg = json.dumps({"kind": kind, "data": data, "t": time.time()}, default=str)
    with lock:
        for q in list(subscribers):
            q.put(msg)


def make_ledger(kind):
    led = TrustLedger()
    if kind == "earned":
        for c, (s, f, last) in EARNED.items():
            led.stats[c] = {"success": s, "failure": f, "last": last}
    return led


def run(scenario, provider=None, model=None):
    sc = SCENARIOS[scenario]
    ledger = make_ledger(sc["ledger"])
    publish("start", {"scenario": scenario, "title": sc["title"], "ledger": ledger.snapshot()})
    try:
        row, result, actions = run_round(sc["faults"], "earned", ledger, fixer_factory("llm", provider, model),
                                         on_event=publish)
        truth = [f"{f.id} ({f.category})" for f in faults.resolve(sc["faults"])]
        publish("done", {"row": row, "ledger": ledger.snapshot(), "truth": truth})
    except Exception as exc:  # surface errors in the page instead of a silent stall
        publish("error", {"message": f"{type(exc).__name__}: {exc}"})
    finally:
        state["running"] = False


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.post("/run/<scenario>")
def start(scenario):
    if scenario not in SCENARIOS:
        return jsonify(error="unknown scenario"), 404
    if state["running"]:
        return jsonify(error="an incident is already running"), 409
    state["running"] = True
    threading.Thread(target=run, args=(scenario,), daemon=True).start()
    return jsonify(ok=True)


@app.get("/events")
def events():
    q = queue.Queue()
    with lock:
        subscribers.append(q)

    def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with lock:
                subscribers.remove(q)
    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()
    print(f"TrustOps console on http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, threaded=True)
