"""Idempotent schema migration for ShopLite.

Creates missing tables and reference data (products). Never modifies or
deletes existing rows, so it is safe to run on a live database.
"""
import os
import sqlite3

import yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    unit_price REAL NOT NULL,
    stock INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    quantity INTEGER,
    unit_price REAL,
    source TEXT DEFAULT 'web',
    payment_ref TEXT,
    created_at TEXT
);
"""

PRODUCTS = [
    (1, "Arabic coffee beans 1kg", 85.0, 120),
    (2, "Saffron 10g", 65.5, 40),
    (3, "Medjool dates 1kg", 48.0, 200),
    (4, "Dallah coffee pot", 120.0, 25),
    (5, "Oud incense pack", 95.0, 60),
    (6, "Cardamom 250g", 32.75, 150),
    (7, "Date syrup 500ml", 22.0, 90),
    (8, "Tea glass set", 54.0, 35),
]


def apply_schema(conn):
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", PRODUCTS)
    conn.commit()


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    path = os.path.join(base, cfg["database"]["path"])
    conn = sqlite3.connect(path)
    try:
        apply_schema(conn)
    finally:
        conn.close()
    print(f"migration applied to {cfg['database']['path']}")


if __name__ == "__main__":
    main()
