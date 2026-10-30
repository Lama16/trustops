"""The Fixer's toolbox: the ONLY ways the Fixer agent may observe or change the system.

It sees what an on-call engineer sees (logs, metrics, files, smoke tests) and
never the Breaker's scenario. Every call is recorded in runtime/actions.jsonl.

Safety rails (worth mentioning in the presentation):
  - edits only to an allowlist of files, and only by exact find/replace
  - SQL access is read-only; data can be quarantined, never deleted
  - quarantine refuses to touch more than 20% of a table
  - no "reset everything" tool: some faults live outside the code
"""
import functools
import json
import re
import sqlite3
import stat
import subprocess
import sys
import time

import requests
import yaml

from . import referee, supervisor
from .paths import LOGS, PAYMENTS_URL, RUNTIME, SHOP_URL, WORK

VENDOR_FILES = {"payments.py"}  # third-party service: source not available to on-call

EDITABLE_FILES = {"config.yaml", ".env", "app.py"}
QUARANTINE_TABLES = {"orders"}
QUARANTINE_MAX_FRACTION = 0.2
_WRITE_SQL = re.compile(
    r";|\b(drop|delete|update|insert|alter|attach|detach|pragma|create|replace|vacuum)\b", re.I)

TOOLS = {}


def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            result = f"ERROR: {type(exc).__name__}: {exc}"
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with open(RUNTIME / "actions.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "tool": fn.__name__, "args": args,
                                "kwargs": kwargs, "result": str(result)[:500]}) + "\n")
        return result
    TOOLS[fn.__name__] = wrapper
    return wrapper


def _resolve(rel):
    p = (WORK / rel).resolve()
    root = WORK.resolve()
    if p != root and root not in p.parents:
        raise ValueError("path outside the service directory")
    return p


def _db_path():
    cfg = yaml.safe_load((WORK / "config.yaml").read_text())
    return WORK / cfg["database"]["path"]


# ---------------------------------------------------------------- observe
@tool
def service_status():
    """Which services are running."""
    return supervisor.status()


@tool
def read_logs(service, lines=60):
    """Last N lines of a service log ('shop' or 'payments')."""
    if service not in supervisor.SERVICES:
        raise ValueError(f"unknown service {service!r}")
    path = LOGS / f"{service}.log"
    if not path.exists():
        return "(no log yet)"
    text = re.sub(r"\x1b\[[0-9;]*m", "", path.read_text(errors="replace"))  # strip ANSI colours
    return "\n".join(text.splitlines()[-int(lines):])


@tool
def get_metrics():
    """Request counts, 5xx error rates, p50/p95 latency per endpoint, cache hits/misses."""
    return requests.get(f"{SHOP_URL}/metrics", timeout=3).json()


@tool
def run_smoke_tests():
    """End-to-end checks: health, catalog, latency, stats integrity, checkout."""
    return referee.run_checks()


@tool
def list_files():
    """Files in the service directory with permissions."""
    out = []
    for p in sorted(WORK.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out.append(f"{p.relative_to(WORK)}  {stat.filemode(p.stat().st_mode)}  {p.stat().st_size}B")
    return "\n".join(out)


READ_WINDOW = 120


def _readable(path):
    p = _resolve(path)
    if p.suffix == ".db":
        raise ValueError("binary database file; use query_db")
    if p.name in VENDOR_FILES:
        raise PermissionError(f"{p.name} is the payments vendor's service; its source is not available to on-call")
    return p


@tool
def read_file(path, start_line=1, end_line=None):
    """Read a text file with line numbers, in windows of up to 120 lines."""
    lines = _readable(path).read_text().splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line) if end_line else start + READ_WINDOW - 1, start + READ_WINDOW - 1)
    body = "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n... lines {end + 1}-{len(lines)} not shown; call again with start_line={end + 1}" if end < len(lines) else ""
    return f"{path}: lines {start}-{end} of {len(lines)}\n{body}{more}"


@tool
def search_files(pattern):
    """Case-insensitive regex search across the service's text files. Returns file:line: text (max 40)."""
    rx = re.compile(pattern, re.I)
    hits = []
    for p in sorted(WORK.rglob("*")):
        if not p.is_file() or p.suffix in (".db", ".pyc") or p.name in VENDOR_FILES or "__pycache__" in p.parts:
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(WORK)}:{i}: {line.strip()[:160]}")
    return "\n".join(hits[:40]) + (f"\n... {len(hits) - 40} more" if len(hits) > 40 else "") if hits else "no matches"


@tool
def payments_ledger():
    """Recent successful charges as shown in the payments provider's dashboard (last 50)."""
    return requests.get(f"{PAYMENTS_URL}/charges", timeout=3).json()


@tool
def query_db(sql):
    """Read-only SELECT against the database the app is configured to use (max 50 rows)."""
    if not sql.strip().lower().startswith(("select", "with")) or _WRITE_SQL.search(sql):
        raise ValueError("only single read-only SELECT statements are allowed")
    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description or []]
        return {"columns": cols, "rows": cur.fetchmany(50)}
    finally:
        conn.close()


# ---------------------------------------------------------------- act
@tool
def replace_in_file(path, old, new):
    """Exact find/replace in an allowlisted file (config.yaml, .env, app.py). `old` must occur once."""
    p = _resolve(path)
    if p.name not in EDITABLE_FILES:
        raise PermissionError(f"{path} is not editable; allowed: {sorted(EDITABLE_FILES)}")
    if old == new:
        raise ValueError("no-op edit (old == new); to inspect a file use read_file or search_files")
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise ValueError(f"`old` must occur exactly once, found {count}")
    p.write_text(text.replace(old, new, 1))
    return f"edited {path}; restart the affected service for the change to take effect"


@tool
def restart_service(name):
    """Restart a service. Returns whether it came up on its port."""
    ok = supervisor.restart(name)
    return {"service": name, "listening": ok}


@tool
def start_service(name):
    """Start a stopped service."""
    ok = supervisor.start(name)
    return {"service": name, "listening": ok}


@tool
def stop_service(name):
    """Stop a running service or job."""
    if name not in supervisor.SERVICES:
        raise ValueError(f"unknown service {name!r}")
    supervisor.stop(name)
    return {"service": name, "running": supervisor.is_running(name)}


@tool
def run_migration():
    """Run the idempotent schema migration (creates missing tables/reference data, never deletes)."""
    r = subprocess.run([sys.executable, "migrate.py"], cwd=WORK, capture_output=True, text=True, timeout=30)
    return (r.stdout + r.stderr).strip() or f"exit code {r.returncode}"


@tool
def quarantine_rows(table, where):
    """Move rows matching a WHERE clause into <table>_quarantine (never deletes data outright)."""
    if table not in QUARANTINE_TABLES:
        raise PermissionError(f"quarantine allowed only for {sorted(QUARANTINE_TABLES)}")
    if _WRITE_SQL.search(where):
        raise ValueError("WHERE clause contains forbidden keywords")
    conn = sqlite3.connect(_db_path())
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        if n == 0:
            return "no rows matched; nothing quarantined"
        if n > QUARANTINE_MAX_FRACTION * total:
            raise PermissionError(f"refusing: {n}/{total} rows matched (> {QUARANTINE_MAX_FRACTION:.0%}); escalate to a human")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table}_quarantine AS SELECT * FROM {table} WHERE 0")
        conn.execute(f"INSERT INTO {table}_quarantine SELECT * FROM {table} WHERE {where}")
        conn.execute(f"DELETE FROM {table} WHERE {where}")
        conn.commit()
        return f"quarantined {n} rows from {table} into {table}_quarantine"
    finally:
        conn.close()


@tool
def set_file_writable(path):
    """Make a database file under data/ writable by the service (mode 644)."""
    p = _resolve(path)
    if p.parent.name != "data" or p.suffix != ".db":
        raise PermissionError("only database files under data/ can be changed")
    p.chmod(0o644)
    return f"{path} is now {stat.filemode(p.stat().st_mode)}"
