"""Proves the fault library is sound: every fault must (1) break the system and
(2) be recoverable by its oracle fix. Run this before trusting any Fixer result."""
import os

from . import env, faults, referee, supervisor


def run():
    rows = []
    env.reset()
    supervisor.start_all()
    base = referee.run_checks()
    rows.append(("baseline (no fault)", "-", "OK" if base["recovered"] else "BROKEN BASELINE", f"{base['passed']}/5"))

    for f in faults.FAULTS.values():
        env.reset()
        faults.inject([f], "pre")
        supervisor.start_all()
        faults.inject([f], "post")
        broken = referee.run_checks()
        faults.oracle_fix([f])
        supervisor.stop_all()
        supervisor.start_all()
        fixed = referee.run_checks()
        if broken["recovered"]:
            verdict = "SKIP (running as root ignores file permissions)" \
                if f.id == "db_read_only" and os.geteuid() == 0 else "INEFFECTIVE"
        elif not fixed["recovered"]:
            verdict = "NOT RECOVERABLE"
        else:
            verdict = "OK" if f.category != "external" else "OK (vendor-side: agent must escalate)"
        failing = ",".join(c["check"] for c in broken["checks"] if not c["ok"]) or "-"
        rows.append((f.id, f.split, verdict, f"broken {broken['passed']}/5 -> fixed {fixed['passed']}/5  [{failing}]"))
        print(f"  {f.id:<26} {verdict}")

    supervisor.stop_all()
    print()
    for r in rows:
        print(f"{r[0]:<26} {r[1]:<8} {r[2]:<10} {r[3]}")
    bad = [r for r in rows if not r[2].startswith(("OK", "SKIP"))]
    print("\nALL GOOD" if not bad else f"\n{len(bad)} PROBLEM(S)")
    return not bad
