"""Tool schemas shown to the Fixer LLM.

Read tools are always allowed. Write tools must declare which root-cause
category they address and why; the trust gate decides whether the action
runs autonomously or needs a human.
"""
from pathlib import Path

CATEGORIES = ["config", "performance", "secrets", "schema", "data", "code", "dependency", "permissions", "workflow"]
REPORT_CATEGORIES = CATEGORIES + ["external"]  # 'external' = outside our control; only for reports/escalations

_GATE = {
    "category": {"type": "string", "enum": CATEGORIES,
                 "description": "The kind of root cause this action addresses. dependency = a service we run "
                                "(shop, payments connector) is down; workflow = a hung/blocking batch job."},
    "reason": {"type": "string",
               "description": "One sentence: the evidence you saw and why this action fixes it."},
}


def _tool(name, description, props=None, required=(), gated=False):
    props = dict(props or {})
    required = list(required)
    if gated:
        props.update(_GATE)
        required += ["category", "reason"]
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required}}


READ_TOOLS = [
    _tool("run_smoke_tests",
          "Run the 5 end-to-end checks (health, catalog, latency, stats_integrity, checkout). "
          "Use it first to see symptoms and again to verify recovery."),
    _tool("service_status", "Which services (shop, payments) are running."),
    _tool("read_logs", "Last N lines of a service log.",
          {"service": {"type": "string", "enum": ["shop", "payments", "report_job"]},
           "lines": {"type": "integer", "description": "default 60"}}, ["service"]),
    _tool("get_metrics", "Per-endpoint request counts, 5xx error rate, p50/p95 latency, catalog cache hits/misses."),
    _tool("list_files", "Files in the service directory with permissions and sizes."),
    _tool("read_file", "Read a text file in the service directory with line numbers, up to 120 lines per call "
          "(e.g. config.yaml, .env, app.py, migrate.py, report_job.py). Use start_line to read further.",
          {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
          ["path"]),
    _tool("search_files", "Case-insensitive regex search across the service's files; returns file:line: text.",
          {"pattern": {"type": "string"}}, ["pattern"]),
    _tool("query_db", "Read-only SELECT against the app's configured database (max 50 rows).",
          {"sql": {"type": "string"}}, ["sql"]),
    _tool("payments_ledger", "Recent successful charges from the payments provider's dashboard (ref, amount, time)."),
]

WRITE_TOOLS = [
    _tool("replace_in_file",
          "Exact find/replace in config.yaml, .env or app.py. `old` must occur exactly once. "
          "Config and code are loaded at startup, so restart the service afterwards.",
          {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
          ["path", "old", "new"], gated=True),
    _tool("restart_service", "Restart a service.",
          {"name": {"type": "string", "enum": ["shop", "payments"]}}, ["name"], gated=True),
    _tool("start_service", "Start a stopped service.",
          {"name": {"type": "string", "enum": ["shop", "payments"]}}, ["name"], gated=True),
    _tool("stop_service", "Stop a running service or batch job.",
          {"name": {"type": "string", "enum": ["shop", "payments", "report_job"]}}, ["name"], gated=True),
    _tool("run_migration",
          "Run the idempotent schema migration: creates missing tables and reference data, never deletes rows.",
          gated=True),
    _tool("quarantine_rows",
          "Move rows matching a SQL WHERE clause into <table>_quarantine. Refuses to touch more than 20% of a table.",
          {"table": {"type": "string", "enum": ["orders"]}, "where": {"type": "string"}},
          ["table", "where"], gated=True),
    _tool("set_file_writable", "Make a database file under data/ writable by the service.",
          {"path": {"type": "string"}}, ["path"], gated=True),
]

FINISH_TOOL = _tool(
    "submit_report",
    "Finish the incident. Call once, after verifying with run_smoke_tests (or when you cannot make further progress).",
    {
        "root_causes": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "category": {"type": "string", "enum": REPORT_CATEGORIES},
                "component": {"type": "string"},
                "evidence": {"type": "string"},
            }, "required": ["category", "component", "evidence"]},
        },
        "summary": {"type": "string", "description": "What happened and what you changed, 2-4 sentences."},
        "believes_recovered": {"type": "boolean"},
    },
    ["root_causes", "summary", "believes_recovered"],
)

ESCALATE_TOOL = _tool(
    "escalate_to_human",
    "Hand the incident to the on-call human and stop working on it. Use it when the root cause is outside "
    "what you can safely change (third-party provider, a secret you cannot obtain, a decision that needs a "
    "human), or when you are not making progress. An early, well-evidenced escalation is a success; "
    "retrying the same things is a failure. Fix what you safely can first, then escalate the rest.",
    {
        "suspected_cause": {"type": "string"},
        "category": {"type": "string", "enum": REPORT_CATEGORIES},
        "evidence": {"type": "string", "description": "The concrete log lines / results that support it."},
        "actions_taken": {"type": "string"},
        "recommended_next_step": {"type": "string", "description": "What the human should do."},
    },
    ["suspected_cause", "category", "evidence", "actions_taken", "recommended_next_step"],
)

ALL_TOOLS = READ_TOOLS + WRITE_TOOLS + [FINISH_TOOL, ESCALATE_TOOL]
READ_NAMES = {t["name"] for t in READ_TOOLS}
WRITE_NAMES = {t["name"] for t in WRITE_TOOLS}


def allowed_categories(tool, args):
    """Which declared categories are plausible for this concrete action.
    Stops an agent from labelling a code edit as 'config' to borrow config's trust."""
    if tool == "replace_in_file":
        return {"config.yaml": {"config", "performance"},
                ".env": {"secrets", "config"},
                "app.py": {"code"}}.get(Path(str(args.get("path", ""))).name, set())
    if tool == "run_migration":
        return {"schema"}
    if tool == "quarantine_rows":
        return {"data"}
    if tool == "set_file_writable":
        return {"permissions"}
    if tool in ("restart_service", "start_service", "stop_service"):
        return set(CATEGORIES)
    return set()
