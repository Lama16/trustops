"""Baseline: classic runbook automation (what teams had before AI agents).

Fixed if-this-then-that rules, no reasoning. Exists to answer the judges' question:
"does the LLM add value over a simpler alternative?"
"""
import time

from arena import tools as T


class RunbookFixer:
    def __init__(self, gate, on_event=None):
        self.gate = gate
        self.emit = on_event or (lambda kind, data: None)
        self.steps = 0
        self.causes = []

    def _act(self, tool, category, reason, **args):
        d = self.gate.check(tool, args, category, reason)
        self.emit("gate", {"tool": tool, "category": category, "reason": reason,
                           "mode": d.mode, "message": d.message})
        self.steps += 1
        if d.allowed:
            self.causes.append(category)
            return T.TOOLS[tool](**args)
        return d.message

    def run(self):
        t0 = time.time()
        rep = T.run_smoke_tests()
        self.steps += 1
        if not rep["recovered"]:
            # Rule 1: a stopped service gets started
            for svc, state in T.service_status().items():
                if state == "stopped" and svc != "report_job":
                    self._act("start_service", "dependency", f"{svc} is stopped", name=svc)
            # Rule 2: missing table -> run migrations
            if "no such table" in T.read_logs("shop", 200):
                self._act("run_migration", "schema", "log shows a missing table")
            # Rule 3: still failing -> restart the app
            if not T.run_smoke_tests()["recovered"]:
                self._act("restart_service", "dependency", "smoke tests still failing; restart", name="shop")
            self.steps += 3
        report = {"root_causes": [{"category": c, "component": "-", "evidence": "runbook rule"}
                                  for c in dict.fromkeys(self.causes)],
                  "summary": "runbook rules applied", "believes_recovered": T.run_smoke_tests()["recovered"]}
        return {"report": report, "escalation": None, "steps": self.steps, "seconds": round(time.time() - t0, 1),
                "usage": {"input_tokens": 0, "output_tokens": 0}, "transcript": []}
