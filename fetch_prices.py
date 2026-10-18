"""Fetch Haryana mandi prices from data.gov.in and save them into Supabase Postgres.

Usage:
  python fetch_prices.py                 # today and the previous 3 days
  python fetch_prices.py 2026-10-01 2026-10-15   # a date range (backfill)

Needs two environment variables (in a .env file locally, GitHub Secrets in Actions):
  DATA_GOV_API_KEY   your data.gov.in key
  DATABASE_URL       Supabase connection string (Project Settings > Database)
"""
import os
import sys
import time
from datetime import date, datetime, timedelta

import psycopg
import requests
from dotenv import load_dotenv

load_dotenv()

RESOURCE = "35985678-0d79-46b4-9ed6-6f13308a1d24"
URL = f"https://api.data.gov.in/resource/{RESOURCE}"
STATE = "Haryana"
PAGE = 1000

UPSERT = """
insert into mandi_prices (arrival_date, state, district, market, commodity, variety, grade,
                          commodity_code, min_price, max_price, modal_price)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (arrival_date, state, district, market, commodity, variety, grade)
do update set min_price = excluded.min_price, max_price = excluded.max_price,
              modal_price = excluded.modal_price, commodity_code = excluded.commodity_code,
              fetched_at = now()
"""


def field(rec, name):
    """Field names differ in case between data.gov.in resources, so match case-insensitively."""
    for k, v in rec.items():
        if k.lower() == name.lower():
            return v
    return None


def price(v):
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def fetch_day(day: date):
    """Return every Haryana record for one arrival date, following pages."""
    rows, offset = [], 0
    while True:
        params = {
            "api-key": os.environ["DATA_GOV_API_KEY"],
            "format": "json",
            "limit": PAGE,
            "offset": offset,
            "filters[State]": STATE,
            "filters[Arrival_Date]": day.strftime("%d-%m-%Y"),
        }
        for attempt in range(4):
            try:
                r = requests.get(URL, params=params, timeout=60)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        batch = r.json().get("records", [])
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def to_row(rec, day):
    lo, hi, modal = (price(field(rec, f)) for f in ("Min_Price", "Max_Price", "Modal_Price"))
    if modal is None or (lo and hi and lo > hi):
        return None  # skip obviously broken rows
    return (
        day,
        field(rec, "State") or STATE,
        (field(rec, "District") or "").strip(),
        (field(rec, "Market") or "").strip(),
        (field(rec, "Commodity") or "").strip(),
        (field(rec, "Variety") or "").strip(),
        (field(rec, "Grade") or "").strip(),
        field(rec, "Commodity_Code"),
        lo, hi, modal,
    )


def main():
    missing = [k for k in ("DATA_GOV_API_KEY", "DATABASE_URL") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing secret(s): {', '.join(missing)}. Add them under "
                 "Settings > Secrets and variables > Actions > Repository secrets.")

    if len(sys.argv) == 3:
        start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        end = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
    else:
        end = date.today()
        start = end - timedelta(days=3)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # Create the table on first run (schema.sql only uses "if not exists").
        schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
        with open(schema) as f:
            conn.execute(f.read())
        conn.commit()

        day = start
        while day <= end:
            records = fetch_day(day)
            rows = [r for r in (to_row(rec, day) for rec in records) if r]
            with conn.cursor() as cur:
                cur.executemany(UPSERT, rows)
            conn.commit()
            print(f"{day}: {len(records)} fetched, {len(rows)} saved")
            day += timedelta(days=1)


if __name__ == "__main__":
    main()
