"""Starts, stops and restarts the target services (like systemd / a container runtime)."""
import json
import os
import signal
import socket
import subprocess
import sys
import time

from .paths import LOGS, PAYMENTS_PORT, RUNTIME, SHOP_PORT, VENDOR, WORK

SERVICES = {"payments": ("payments.py", PAYMENTS_PORT), "shop": ("app.py", SHOP_PORT),
            "report_job": ("report_job.py", None)}  # batch job: no port, normally not running
START_ORDER = ["payments", "shop"]
ALL = START_ORDER + ["report_job"]
_PIDS = RUNTIME / "pids.json"
_procs = {}  # Popen handles for services started by this process


def _load():
    try:
        return json.loads(_PIDS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(pids):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    _PIDS.write_text(json.dumps(pids))


def _alive(name, pid):
    proc = _procs.get(name)
    if proc is not None and proc.pid == pid:
        return proc.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_running(name):
    pid = _load().get(name)
    return bool(pid) and _alive(name, pid)


def read_dotenv():
    env = {}
    path = WORK / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def wait_for_port(port, timeout=6.0, proc=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False  # process died during startup
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def start(name, wait=True):
    if is_running(name):
        return True
    script, port = SERVICES[name]
    LOGS.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PAYMENT") and not k.startswith("MallocStackLogging")}  # macOS log noise
    env.update(read_dotenv())
    env["PYTHONUNBUFFERED"] = "1"
    env["VENDOR_STATE_DIR"] = str(VENDOR)
    with open(LOGS / f"{name}.log", "a") as logf:
        proc = subprocess.Popen(
            [sys.executable, script], cwd=WORK, stdout=logf, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
    _procs[name] = proc
    pids = _load()
    pids[name] = proc.pid
    _save(pids)
    if port is None:
        time.sleep(0.5)
        return proc.poll() is None
    return wait_for_port(port, proc=proc) if wait else True


def stop(name):
    pids = _load()
    pid = pids.pop(name, None)
    _save(pids)
    proc = _procs.pop(name, None)
    if not pid:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        deadline = time.time() + 3
        while time.time() < deadline:
            if proc is not None and proc.poll() is not None:
                return
            if proc is None:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
            time.sleep(0.1)


def start_all():
    return {name: start(name) for name in START_ORDER}


def stop_all():
    for name in reversed(ALL):
        stop(name)


def restart(name):
    stop(name)
    return start(name)


def status():
    out = {name: ("running" if is_running(name) else "stopped") for name in START_ORDER}
    out["report_job"] = ("running (nightly batch job, scheduled 02:00, normally finishes in seconds)"
                         if is_running("report_job") else "idle (nightly batch job, scheduled 02:00)")
    return out
