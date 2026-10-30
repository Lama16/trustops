#!/usr/bin/env python3
"""Breaker vs. Fixer arena CLI.

  python arena.py selftest                 # prove every fault breaks + is recoverable
  python arena.py break --n 2 --seed 3     # reset, inject a random scenario, start, show damage
  python arena.py break --fault payments_down
  python arena.py check                    # referee verdict on the current system
  python arena.py reveal                   # show the hidden scenario (ground truth)
  python arena.py oracle                   # apply the ground-truth fix (sanity check)
  python arena.py faults | reset | start | stop | status
"""
import argparse
import json
import random
import sys
import time

from arena import env, faults, referee, selftest, supervisor
from arena.paths import RUNTIME


def print_report(rep):
    for c in rep["checks"]:
        print(f"  {'PASS' if c['ok'] else 'FAIL'}  {c['check']:<16} {c['detail']}")
    print(f"\n  {'RECOVERED' if rep['recovered'] else 'NOT RECOVERED'}  ({rep['passed']}/{rep['total']} checks)")


def cmd_break(args):
    env.reset()
    if args.fault:
        chosen = faults.resolve(args.fault)
    else:
        seed = args.seed if args.seed is not None else random.randrange(10_000)
        chosen = faults.choose(args.n, random.Random(seed), args.split)
        args.seed = seed
    faults.inject(chosen, "pre")
    supervisor.start_all()
    faults.inject(chosen, "post")
    (RUNTIME / "scenario.json").write_text(json.dumps({
        "faults": [f.id for f in chosen], "seed": args.seed, "split": args.split,
        "broken_at": time.time()}, indent=2))
    print(f"Breaker injected {len(chosen)} fault(s). Scenario saved (hidden from the Fixer).\n")
    print_report(referee.run_checks())


def cmd_reveal(_):
    sc = json.loads((RUNTIME / "scenario.json").read_text())
    for fid in sc["faults"]:
        f = faults.FAULTS[fid]
        print(f"  {f.id:<26} [{f.category}/{f.split}] {f.root_cause}")


def cmd_oracle(_):
    sc = json.loads((RUNTIME / "scenario.json").read_text())
    faults.oracle_fix(faults.resolve(sc["faults"]))
    supervisor.stop_all()
    supervisor.start_all()
    print_report(referee.run_checks())


def cmd_faults(_):
    for f in faults.FAULTS.values():
        print(f"  {f.id:<26} {f.category:<12} {f.split:<8} {f.root_cause}")


def main():
    p = argparse.ArgumentParser(description="Breaker vs. Fixer arena")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("break")
    b.add_argument("--n", type=int, default=1, help="number of simultaneous faults")
    b.add_argument("--seed", type=int)
    b.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    b.add_argument("--fault", nargs="+", choices=list(faults.FAULTS), help="explicit fault ids")
    for name in ("check", "reveal", "oracle", "faults", "selftest", "reset", "start", "stop", "status"):
        sub.add_parser(name)
    args = p.parse_args()

    if args.cmd == "break":
        cmd_break(args)
    elif args.cmd == "check":
        print_report(referee.run_checks())
    elif args.cmd == "reveal":
        cmd_reveal(args)
    elif args.cmd == "oracle":
        cmd_oracle(args)
    elif args.cmd == "faults":
        cmd_faults(args)
    elif args.cmd == "selftest":
        sys.exit(0 if selftest.run() else 1)
    elif args.cmd == "reset":
        print("reset done; expected baseline:", env.reset())
    elif args.cmd == "start":
        print(supervisor.start_all())
    elif args.cmd == "stop":
        supervisor.stop_all()
        print("stopped")
    elif args.cmd == "status":
        print(supervisor.status())


if __name__ == "__main__":
    main()
