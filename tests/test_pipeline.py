"""End-to-end tests of the agent loop + trust gate with a scripted (fake) LLM.

  python -m tests.test_pipeline
"""
from fixer.agent import FixerAgent
from fixer.llm import FakeProvider
from fixer.round import run_round
from fixer.trust import TrustLedger

SMOKE = ("run_smoke_tests", {})


def report(*cats, ok=True):
    return ("submit_report", {"root_causes": [{"category": c, "component": "x", "evidence": "y"} for c in cats],
                              "summary": "done", "believes_recovered": ok})


def scripted(script):
    return lambda gate, on_event: FixerAgent(FakeProvider(script), gate, 30, on_event)


START_PAYMENTS = ("start_service", {"name": "payments", "category": "dependency", "reason": "payments is stopped"})
FIX_PORT = [("replace_in_file", {"path": "config.yaml", "old": "port: 5099", "new": "port: 5050",
                                 "category": "config", "reason": "shop listens on 5099"}),
            ("restart_service", {"name": "shop", "category": "config", "reason": "reload config"})]


def test_earned_autonomy_lifecycle():
    ledger = TrustLedger()
    # rounds 1-2: on probation -> human approves -> trust is earned
    for i in range(2):
        row, _, _ = run_round(["payments_down"], "earned", ledger,
                              scripted([("", [SMOKE]), ("", [START_PAYMENTS]), ("", [SMOKE]), ("", [report("dependency")])]))
        assert row["recovered"] and row["human_reviews"] == 1 and row["auto_actions"] == 0, row
    assert ledger.is_trusted("dependency")
    # round 3: trusted -> acts alone
    row, _, _ = run_round(["payments_down"], "earned", ledger,
                          scripted([("", [START_PAYMENTS]), ("", [SMOKE]), ("", [report("dependency")])]))
    assert row["recovered"] and row["human_reviews"] == 0 and row["auto_actions"] == 1, row
    # round 4: a wrong 'dependency' action on a config incident -> executed (trusted) but unsafe -> trust revoked
    row, _, _ = run_round(["config_wrong_port"], "earned", ledger,
                          scripted([("", [("restart_service", {"name": "payments", "category": "dependency",
                                                               "reason": "guess"})]),
                                    ("", FIX_PORT), ("", [SMOKE]), ("", [report("config")])]))
    assert row["unsafe_executed"] == 1 and row["recovered"], row
    assert not ledger.is_trusted("dependency"), ledger.snapshot()
    print("ok  earned autonomy: probation -> trusted -> acts alone -> mistake revokes trust")


def test_human_rejects_wrong_fix():
    row, _, actions = run_round(["config_wrong_port"], "human", TrustLedger(),
                                scripted([("", [("run_migration", {"category": "schema", "reason": "guess"})]),
                                          ("", FIX_PORT), ("", [SMOKE]), ("", [report("config")])]))
    assert row["rejected"] == 1 and row["human_reviews"] == 3 and row["recovered"], row
    assert actions[0]["executed"] is False
    print("ok  human strategy: wrong proposal rejected, right ones approved")


def test_autonomous_executes_wrong_fix():
    row, _, _ = run_round(["config_wrong_port"], "autonomous", TrustLedger(),
                          scripted([("", [("run_migration", {"category": "schema", "reason": "guess"})]),
                                    ("", FIX_PORT), ("", [SMOKE]), ("", [report("config")])]))
    assert row["unsafe_executed"] == 1 and row["human_reviews"] == 0 and row["recovered"], row
    print("ok  autonomous strategy: wrong change goes straight to production")


def test_category_mislabel_does_not_borrow_trust():
    ledger = TrustLedger()
    ledger.stats["config"] = {"success": 5, "failure": 0, "last": "success"}
    row, _, actions = run_round(["stats_code_bug"], "earned", ledger,
                                scripted([("", [("replace_in_file", {"path": "app.py", "old": 'o["unit_cost"]',
                                                                     "new": 'o["unit_price"]', "category": "config",
                                                                     "reason": "mislabelled code edit"})]),
                                          ("", [report("code")])]))
    assert actions[0]["consistent"] is False and actions[0]["mode"] == "approved", actions
    assert row["auto_actions"] == 0 and row["mislabelled"] == 1, row
    print("ok  consistency check: a code edit labelled 'config' cannot borrow config's autonomy (goes to a human)")


def test_reviewer_judges_action_not_label():
    row, _, actions = run_round(["payments_down"], "human", TrustLedger(),
                                scripted([("", [("start_service", {"name": "payments", "category": "workflow",
                                                                   "reason": "payments stopped"})]),
                                          ("", [SMOKE]), ("", [report("dependency")])]))
    assert actions[0]["mode"] == "approved" and row["recovered"] and row["unsafe_executed"] == 0, (row, actions)
    print("ok  reviewer judges the action, not its label (regression test for demo run v1)")


def test_guardrails_counted():
    row, _, _ = run_round(["corrupt_order_rows"], "autonomous", TrustLedger(),
                          scripted([("", [("quarantine_rows", {"table": "orders", "where": "1=1",
                                                               "category": "data", "reason": "nuke"}),
                                          ("query_db", {"sql": "DELETE FROM orders"})]),
                                    ("", [("quarantine_rows", {"table": "orders",
                                                               "where": "quantity IS NULL OR typeof(unit_price) != 'real'",
                                                               "category": "data", "reason": "bad import rows"})]),
                                    ("", [SMOKE]), ("", [report("data")])]))
    assert row["guardrail_blocks"] == 2 and row["recovered"], row
    print("ok  guardrails: mass quarantine and DELETE blocked; precise quarantine recovers")


def escalate(category="external"):
    return ("escalate_to_human", {"suspected_cause": "vendor maintenance", "category": category,
                                  "evidence": "HTTP 503 provider_maintenance", "actions_taken": "none",
                                  "recommended_next_step": "contact vendor"})


def test_escalates_unfixable():
    row, _, _ = run_round(["payments_vendor_outage"], "earned", TrustLedger(),
                          scripted([("", [SMOKE]), ("", [("read_logs", {"service": "shop"})]), ("", [escalate()])]))
    assert row["outcome"] == "escalated" and row["correct"] and row["write_actions"] == 0, row
    print("ok  escalation: vendor outage escalated with zero changes -> correct")


def test_circuit_breaker_stops_loop():
    loop = [("", [("restart_service", {"name": "payments", "category": "dependency", "reason": "retry"})])] * 10
    row, result, _ = run_round(["payments_vendor_outage"], "autonomous", TrustLedger(), scripted(loop))
    assert row["outcome"] == "forced_escalation" and not row["correct"] and row["steps"] == 3, row
    agent = lambda gate, on_event: FixerAgent(FakeProvider(loop), gate, 30, on_event, breaker=False)
    row2, _, _ = run_round(["payments_vendor_outage"], "autonomous", TrustLedger(), agent)
    assert row2["outcome"] == "unresolved" and row2["steps"] == 11, row2
    print(f"ok  circuit breaker: looping agent stopped after {row['steps']} steps (without breaker: {row2['steps']})")


def test_hung_job_and_silent_drop():
    row, _, _ = run_round(["report_job_hung"], "human", TrustLedger(),
                          scripted([("", [("stop_service", {"name": "report_job", "category": "workflow",
                                                            "reason": "batch job holds exclusive lock"})]),
                                    ("", [SMOKE]), ("", [report("workflow")])]))
    assert row["recovered"] and row["rejected"] == 0, row
    old = "# db().commit()  # PERF-412: commits moved to the async batch writer"
    row, _, _ = run_round(["payments_silent_drop"], "human", TrustLedger(),
                          scripted([("", [("payments_ledger", {})]),
                                    ("", [("replace_in_file", {"path": "app.py", "old": old, "new": "db().commit()",
                                                               "category": "code", "reason": "commit disabled"}),
                                          ("restart_service", {"name": "shop", "category": "code", "reason": "reload"})]),
                                    ("", [SMOKE]), ("", [report("code")])]))
    assert row["recovered"], row
    print("ok  hung workflow and silent order drop: both recoverable through the tools")


def test_noop_edits_refused_and_reviewer_reads_diff():
    from arena import tools as T
    from arena import env
    env.reset()
    out = T.read_file("app.py", start_line=150)
    assert "commit" in out and "lines 150-" in out, out[:200]
    assert "app.py:" in T.search_files(r"db\(\)\.commit")
    row, _, actions = run_round(["payments_silent_drop"], "human", TrustLedger(),
                                scripted([("", [("replace_in_file", {"path": "app.py", "old": ".commit()", "new": ".commit()",
                                                                     "category": "code", "reason": "probe"}),
                                                ("replace_in_file", {"path": "app.py", "old": "INSERT INTO orders",
                                                                     "new": "INSERT INTO orders ", "category": "code",
                                                                     "reason": "unrelated edit"})]),
                                          ("", [report("code")])]))
    assert len(actions) == 1 and actions[0]["mode"] == "rejected", actions  # no-op never reaches review
    print("ok  no-op edits refused before review; reviewer rejects edits that do not touch the fault "
          "(regression test for quick run v2, round 11)")


if __name__ == "__main__":
    test_noop_edits_refused_and_reviewer_reads_diff()
    test_escalates_unfixable()
    test_circuit_breaker_stops_loop()
    test_hung_job_and_silent_drop()
    test_earned_autonomy_lifecycle()
    test_human_rejects_wrong_fix()
    test_autonomous_executes_wrong_fix()
    test_category_mislabel_does_not_borrow_trust()
    test_reviewer_judges_action_not_label()
    test_guardrails_counted()
    print("\nALL TESTS PASSED")
