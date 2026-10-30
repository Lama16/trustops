from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_SRC = ROOT / "target"       # pristine source, never modified
RUNTIME = ROOT / "runtime"         # everything generated per round
WORK = RUNTIME / "target"          # the live copy the Breaker breaks and the Fixer repairs
LOGS = RUNTIME / "logs"
VENDOR = RUNTIME / "vendor"        # state of third-party systems; outside the Fixer's reach

SHOP_PORT = 5050                   # the port the "load balancer" routes to (fixed contract)
PAYMENTS_PORT = 5051
SHOP_URL = f"http://127.0.0.1:{SHOP_PORT}"
PAYMENTS_URL = f"http://127.0.0.1:{PAYMENTS_PORT}"
