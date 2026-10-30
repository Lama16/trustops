"""Resets the arena to a clean, healthy, deterministic state."""
import importlib.util
import json
import os
import random
import shutil
import sqlite3

from . import supervisor
from .paths import LOGS, RUNTIME, TARGET_SRC, VENDOR, WORK

SEED_ORDERS = 40


def _load_migrate():
    spec = importlib.util.spec_from_file_location("target_migrate", WORK / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed_database():
    m = _load_migrate()
    db_path = WORK / "data" / "shop.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    m.apply_schema(conn)
    prices = {pid: price for pid, _name, price, _stock in m.PRODUCTS}
    rng = random.Random(7)
    rows, revenue = [], 0.0
    for i in range(SEED_ORDERS):
        pid, qty = rng.randint(1, len(m.PRODUCTS)), rng.randint(1, 5)
        rows.append((pid, qty, prices[pid], "web", f"pay_seed{i:03d}",
                     f"2026-10-{i % 28 + 1:02d} 10:{i % 60:02d}:00"))
        revenue += qty * prices[pid]
    conn.executemany(
        "INSERT INTO orders (product_id, quantity, unit_price, source, payment_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    expected = {"orders": SEED_ORDERS, "revenue": round(revenue, 2), "products": len(m.PRODUCTS)}
    (RUNTIME / "expected.json").write_text(json.dumps(expected, indent=2))
    return expected


def reset():
    """Stop everything, copy a pristine target, seed data. Services are left stopped."""
    supervisor.stop_all()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if WORK.exists():
        for p in WORK.rglob("*"):  # undo any permission faults before deleting
            try:
                os.chmod(p, 0o755 if p.is_dir() else 0o644)
            except OSError:
                pass
        shutil.rmtree(WORK)
    shutil.copytree(TARGET_SRC, WORK, ignore=shutil.ignore_patterns("data", "__pycache__", "*.pyc"))
    if LOGS.exists():
        shutil.rmtree(LOGS)
    LOGS.mkdir(parents=True)
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    for name in ("scenario.json", "actions.jsonl"):
        (RUNTIME / name).unlink(missing_ok=True)
    return seed_database()
