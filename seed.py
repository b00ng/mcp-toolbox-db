#!/usr/bin/env python3
import argparse
import os
import sqlite3
import random
import math
from datetime import datetime, timezone

DB_DEFAULT = "./db/app.db"

STATUSES = ["pending", "paid", "shipped", "cancelled"]
STATUS_WEIGHTS = [0.10, 0.60, 0.25, 0.05]  # sums to 1.0

def ensure_dirs(db_path: str):
    d = os.path.dirname(os.path.abspath(db_path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def connect(db_path: str) -> sqlite3.Connection:
    ensure_dirs(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def create_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    # Base tables
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS customers (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY,
      sku TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      price_cents INTEGER NOT NULL,
      stock INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
      status TEXT NOT NULL CHECK (status IN ('pending','paid','shipped','cancelled')),
      total_cents INTEGER, -- cached after items are inserted
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS order_items (
      id INTEGER PRIMARY KEY,
      order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
      product_id INTEGER NOT NULL REFERENCES products(id),
      quantity INTEGER NOT NULL CHECK (quantity >= 1),
      price_cents INTEGER NOT NULL
    );
    -- Helpful indexes
    CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
    CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
    CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
    """)
    conn.commit()

def clear_data(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.executescript("""
    DELETE FROM order_items;
    DELETE FROM orders;
    DELETE FROM products;
    DELETE FROM customers;
    VACUUM;
    """)
    conn.commit()

def first_day_of_month_utc(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=timezone.utc)

def add_months(y: int, m: int, k: int):
    # Return (year, month) after adding k months to year=y, month=m
    idx = (y * 12 + (m - 1)) + k
    ny = idx // 12
    nm = (idx % 12) + 1
    return ny, nm

def days_in_month(year: int, month: int) -> int:
    # February leap years handled via simple rule
    if month in (1,3,5,7,8,10,12):
        return 31
    if month in (4,6,9,11):
        return 30
    # Feb
    is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
    return 29 if is_leap else 28

def month_sequence(months: int) -> list[datetime]:
    now = datetime.now(timezone.utc)
    start = first_day_of_month_utc(now)
    # we go back months-1 months to include current month as the last
    start_y, start_m = add_months(start.year, start.month, -(months - 1))
    seq = []
    for k in range(months):
        y, m = add_months(start_y, start_m, k)
        seq.append(datetime(y, m, 1, tzinfo=timezone.utc))
    return seq

def upsert_customers(conn: sqlite3.Connection, rng: random.Random, n: int = 12):
    cur = conn.cursor()
    customers = []
    for i in range(1, n+1):
        name = f"Customer {i:03d}"
        email = f"customer{i:03d}@example.com"
        created_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        customers.append((name, email, created_at))
    cur.executemany(
        "INSERT OR IGNORE INTO customers(name, email, created_at) VALUES (?, ?, ?)",
        customers
    )
    conn.commit()

def upsert_products(conn: sqlite3.Connection, rng: random.Random, n: int = 16):
    cur = conn.cursor()
    base_prices = [9900, 14900, 19900, 24900, 29900, 34900, 39900]
    products = []
    for i in range(1, n+1):
        sku = f"P{i:03d}"
        name = f"Product {i:03d}"
        price_cents = rng.choice(base_prices) + rng.choice([0, 500, 900])
        stock = rng.randint(20, 200)
        created_at = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
        products.append((sku, name, price_cents, stock, created_at))
    cur.executemany(
        "INSERT OR IGNORE INTO products(sku, name, price_cents, stock, created_at) VALUES (?, ?, ?, ?, ?)",
        products
    )
    conn.commit()

def pick_status(rng: random.Random) -> str:
    x = rng.random()
    acc = 0.0
    for s, w in zip(STATUSES, STATUS_WEIGHTS):
        acc += w
        if x <= acc:
            return s
    return STATUSES[-1]

def seed_orders(conn: sqlite3.Connection, rng: random.Random, months: int, zero_months: int):
    cur = conn.cursor()

    # Fetch ids for FK
    cust_ids = [row["id"] for row in cur.execute("SELECT id FROM customers").fetchall()]
    prod_rows = cur.execute("SELECT id, price_cents FROM products").fetchall()
    prod_ids = [r["id"] for r in prod_rows]
    prod_price = {r["id"]: r["price_cents"] for r in prod_rows}

    if not cust_ids or not prod_ids:
        raise RuntimeError("Seed customers/products first")

    months_seq = month_sequence(months)
    # Choose which months are zero-sales (deterministic)
    zero_idx = set(rng.sample(range(months), k=min(zero_months, months))) if zero_months > 0 else set()

    order_count_total = 0
    item_count_total = 0

    for i, month_start in enumerate(months_seq):
        # Seasonal pattern + jitter
        # Base ~10 orders/month, seasonal sine wave across 12 months
        seasonal = 10 * (1 + 0.6 * math.sin(2 * math.pi * ((i % 12) / 12.0)))
        count = max(0, int(round(seasonal + rng.uniform(-3, 3))))
        if i in zero_idx:
            count = 0

        y = month_start.year
        m = month_start.month
        dim = days_in_month(y, m)

        for _ in range(count):
            cust_id = rng.choice(cust_ids)
            # Random day in month, time mid-day to avoid TZ surprises
            day = rng.randint(1, dim)
            created_at = datetime(y, m, day, 12, 0, 0, tzinfo=timezone.utc).isoformat()
            status = pick_status(rng)

            # Insert order (total_cents null first)
            cur.execute(
                "INSERT INTO orders(customer_id, status, total_cents, created_at) VALUES (?, ?, NULL, ?)",
                (cust_id, status, created_at)
            )
            order_id = cur.lastrowid

            # 1–4 items
            n_items = rng.randint(1, 4)
            order_total = 0
            chosen_products = rng.sample(prod_ids, k=min(n_items, len(prod_ids)))
            for pid in chosen_products:
                qty = rng.randint(1, 5)
                price_cents = prod_price[pid]
                cur.execute(
                    "INSERT INTO order_items(order_id, product_id, quantity, price_cents) VALUES (?, ?, ?, ?)",
                    (order_id, pid, qty, price_cents)
                )
                order_total += qty * price_cents
                item_count_total += 1

            # Cache total on order
            cur.execute("UPDATE orders SET total_cents = ? WHERE id = ?", (order_total, order_id))
            order_count_total += 1

    conn.commit()
    return order_count_total, item_count_total, months_seq, zero_idx

def main():
    parser = argparse.ArgumentParser(description="Deterministic SQLite seeding for orders and order_items.")
    parser.add_argument("--db", default=DB_DEFAULT, help="Path to SQLite DB (default: ./db/app.db)")
    parser.add_argument("--months", type=int, default=18, help="Number of trailing months to seed (12–24 recommended)")
    parser.add_argument("--zero-months", type=int, default=2, help="How many months should have zero sales")
    parser.add_argument("--fresh", action="store_true", help="Clear all existing data before seeding")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for determinism")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    conn = connect(args.db)
    create_schema(conn)
    if args.fresh:
        clear_data(conn)

    upsert_customers(conn, rng, n=12)
    upsert_products(conn, rng, n=16)

    orders, items, months_seq, zero_idx = seed_orders(conn, rng, months=args.months, zero_months=args.zero_months)

    # Quick aggregate preview
    cur = conn.cursor()
    rows = cur.execute("""
      SELECT strftime('%Y-%m', created_at) AS ym, COUNT(*) AS orders, COALESCE(SUM(total_cents),0) AS total_cents
      FROM orders
      GROUP BY ym
      ORDER BY ym
    """).fetchall()

    print(f"Seed complete.")
    print(f" Orders inserted: {orders}")
    print(f" Order items inserted: {items}")
    print(f" Zero months at indices: {sorted(list(zero_idx))}")
    print(" Monthly summary (ym, orders, total_cents):")
    for r in rows:
        print(f"  {r['ym']}  {r['orders']:4d}  {r['total_cents']:>10,d}")

if __name__ == "__main__":
    main()
