#!/usr/bin/env python3
"""Live demo for the video: one incident, the agent's reasoning on screen, trust decisions visible.

  python demo.py --fault config_wrong_db_path env_token_renamed --strategy earned \
                 --ledger results/<run>/ledger_earned.json
  python demo.py --fixer runbook --fault payments_down        # no API key needed
"""
import argparse
import json
import random

from arena import faults
from fixer.round import fixer_factory, run_round
from fixer.trust import TrustLedger

C = {"dim": "\033[2m", "b": "\033[1m", "red": "\033[31m", "grn": "\033[32m", "yel": "\033[33m",
     "cyn": "\033[36m", "mag": "\033[35m", "x": "\033[0m"}


def show_checks(rep, title):
    print(f"\n{C['b']}{title}{C['x']}")
    for c in rep["checks"]:
        mark = f"{C['grn']}PASS{C['x']}" if c["ok"] else f"{C['red']}FAIL{C['x']}"
        print(f"  {mark}  {c['check']:<16} {C['dim']}{c['detail'][:90]}{C['x']}")


def on_event(kind, data):
    if kind == "symptoms":
        show_checks(data, "🚨 PAGE — production smoke tests")
    elif kind == "thought":
        print(f"\n{C['cyn']}🧠 {data}{C['x']}")
    elif kind == "call" and data["tool"] not in ("submit_report",):
        args = {k: v for k, v in data["args"].items() if k not in ("category", "reason")}
        print(f"{C['dim']}  → {data['tool']}({json.dumps(args)[:110]}){C['x']}")
    elif kind == "gate":
        style = {"auto": (C["grn"], "⚡ AUTONOMOUS"), "approved": (C["yel"], "🙋 HUMAN APPROVED"),
                 "rejected": (C["red"], "⛔ HUMAN REJECTED")}[data["mode"]]
        print(f"  {style[0]}{C['b']}{style[1]}{C['x']} {data['tool']} [{data['category']}] "
              f"{C['dim']}{data['reason'][:100]}{C['x']}")
    elif kind == "result":
        if "PermissionError" in data["out"] or "only single read-only" in data["out"]:
            print(f"  {C['mag']}{C['b']}🛡 GUARDRAIL{C['x']} {C['mag']}{data['out'][:140]}{C['x']}")
    elif kind == "breaker":
        print(f"\n  {C['mag']}{C['b']}🔌 CIRCUIT BREAKER{C['x']} {C['mag']}{data}{C['x']}")
    elif kind == "escalation":
        tag = "forced by harness" if data.get("forced") else "agent's own decision"
        print(f"\n{C['yel']}{C['b']}📟 ESCALATED TO ON-CALL HUMAN{C['x']} {C['dim']}({tag}){C['x']}")
        for k in ("suspected_cause", "category", "evidence", "actions_taken", "recommended_next_step"):
            if data.get(k):
                print(f"  {C['yel']}{k.replace('_', ' ')}:{C['x']} {str(data[k])[:150]}")
    elif kind == "report":
        print(f"\n{C['b']}📝 Incident report{C['x']}")
        for rc in data.get("root_causes", []):
            print(f"  • [{rc.get('category')}] {rc.get('component')}: {rc.get('evidence', '')[:110]}")
        print(f"  {data.get('summary', '')}")
    elif kind == "verdict":
        show_checks(data, "⚖️  Independent referee")
        msg = f"{C['grn']}{C['b']}RECOVERED{C['x']}" if data["recovered"] else f"{C['red']}{C['b']}NOT RECOVERED{C['x']}"
        print(f"\n  {msg} ({data['passed']}/{data['total']})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fault", nargs="+", choices=list(faults.FAULTS))
    p.add_argument("--n", type=int, default=2, help="random faults if --fault not given")
    p.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    p.add_argument("--seed", type=int)
    p.add_argument("--strategy", default="earned", choices=["autonomous", "human", "earned"])
    p.add_argument("--ledger", help="trust ledger JSON (e.g. from an eval run)")
    p.add_argument("--fixer", default="llm", choices=["llm", "runbook"])
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--keep-running", action="store_true", help="leave services up afterwards")
    args = p.parse_args()

    ids = args.fault or [f.id for f in faults.choose(args.n, random.Random(args.seed), args.split)]
    ledger = TrustLedger(args.ledger)
    trusted = [c for c in ledger.stats if ledger.is_trusted(c)]
    print(f"{C['b']}TrustOps{C['x']} — strategy: {args.strategy} | "
          f"autonomy earned for: {', '.join(trusted) or 'nothing yet (probation)'}")
    row, _, _ = run_round(ids, args.strategy, ledger, fixer_factory(args.fixer, args.provider, args.model),
                          on_event=on_event, stop_after=not args.keep_running)
    verdict = f"{C['grn']}CORRECT{C['x']}" if row["correct"] else f"{C['red']}MISS{C['x']}"
    print(f"\n  outcome: {row['outcome']} -> {verdict}"
          f" {C['dim']}(fixable incidents must be recovered; vendor-side ones must be escalated){C['x']}")
    print(f"\n{C['dim']}ground truth: {row['faults']} | steps {row['steps']} | {row['seconds']}s | "
          f"human reviews {row['human_reviews']} | autonomous actions {row['auto_actions']}{C['x']}")


if __name__ == "__main__":
    main()
