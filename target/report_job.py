"""Nightly revenue report (batch job, scheduled at 02:00, normally finishes in seconds).

Takes an exclusive lock so the report is consistent, waits for the upstream ETL
export to finish, writes the daily report, then releases the lock.
Known weakness: there is no timeout on the wait.
"""
import logging
import os
import sqlite3
import sys
import time

import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [report_job] %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("report_job")
BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(BASE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    db_path = os.path.join(BASE, cfg["database"]["path"])
    marker = os.path.join(BASE, "data", ".export_done")
    conn = sqlite3.connect(db_path, timeout=5, isolation_level=None)
    log.info("nightly revenue report started")
    conn.execute("BEGIN EXCLUSIVE")
    log.info("acquired exclusive lock on %s", cfg["database"]["path"])
    total = conn.execute("SELECT COALESCE(SUM(quantity * unit_price), 0) FROM orders").fetchone()[0]
    log.info("waiting for upstream ETL export to finish (marker data/.export_done)")
    waited = 0
    while not os.path.exists(marker):
        time.sleep(1)
        waited += 1
        if waited % 10 == 0:
            log.info("still waiting for upstream ETL export (%ds), holding lock", waited)
    conn.execute("CREATE TABLE IF NOT EXISTS daily_reports (day TEXT, revenue REAL)")
    conn.execute("INSERT INTO daily_reports VALUES (date('now'), ?)", (total,))
    conn.execute("COMMIT")
    log.info("report written; lock released")


if __name__ == "__main__":
    main()
