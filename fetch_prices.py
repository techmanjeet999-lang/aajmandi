"""Fetch Haryana mandi prices from data.gov.in and save them into Supabase Postgres.

Usage:
  python fetch_prices.py                 # today and the previous 3 days
  python fetch_prices.py 2026-10-01 2026-10-15   # a date range (backfill)
  python fetch_prices.py --state Rajasthan       # another state (default Haryana)
  python fetch_prices.py --state "Bihar,Punjab"  # several states
  python fetch_prices.py --state all             # every state in India

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
# data.gov.in's firewall drops the default python-requests User-Agent.
HEADERS = {"User-Agent": "mandi-bhav/1.0 (+https://github.com/manjeet123/mandi)"}

# "do update" on a no-op change makes "returning id" work for rows that already exist.
GET_STATE = """insert into states (name) values (%s)
on conflict (name) do update set name = excluded.name returning id"""
GET_DISTRICT = """insert into districts (state_id, name) values (%s, %s)
on conflict (state_id, name) do update set name = excluded.name returning id"""
GET_MANDI = """insert into mandis (district_id, name) values (%s, %s)
on conflict (district_id, name) do update set name = excluded.name returning id"""
GET_CROP = """insert into crops (name, agmarknet_code) values (%s, %s)
on conflict (name) do update set agmarknet_code = coalesce(excluded.agmarknet_code, crops.agmarknet_code)
returning id"""

UPSERT = """
insert into prices (mandi_id, crop_id, variety, grade, price_date, min_price, max_price, modal_price)
values (%s, %s, %s, %s, %s, %s, %s, %s)
on conflict (mandi_id, crop_id, variety, grade, price_date)
do update set min_price = excluded.min_price, max_price = excluded.max_price,
              modal_price = excluded.modal_price, fetched_at = now()
"""


class Ids:
    """Looks up (or creates) state, district, mandi and crop ids, caching them per run."""

    def __init__(self, conn):
        self.conn, self.cache = conn, {}

    def _get(self, sql, *args):
        key = (sql, args)
        if key not in self.cache:
            self.cache[key] = self.conn.execute(sql, args).fetchone()[0]
        return self.cache[key]

    def price_row(self, row):
        day, state, district, market, commodity, variety, grade, code, lo, hi, modal = row
        district_id = self._get(GET_DISTRICT, self._get(GET_STATE, state), district)
        mandi_id = self._get(GET_MANDI, district_id, market)
        crop_id = self._get(GET_CROP, commodity, code)
        return (mandi_id, crop_id, variety, grade, day, lo, hi, modal)


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


def fetch_day(day: date, state: str):
    """Return every record for one state (or "all" states) and arrival date, following pages."""
    rows, offset = [], 0
    while True:
        params = {
            "api-key": os.environ["DATA_GOV_API_KEY"],
            "format": "json",
            "limit": PAGE,
            "offset": offset,
            "filters[Arrival_Date]": day.strftime("%d-%m-%Y"),
        }
        if state.lower() != "all":
            params["filters[State]"] = state
        for attempt in range(4):
            try:
                r = requests.get(URL, params=params, headers=HEADERS, timeout=(15, 90))
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


def to_row(rec, day, state):
    lo, hi, modal = (price(field(rec, f)) for f in ("Min_Price", "Max_Price", "Modal_Price"))
    state = (field(rec, "State") or "").strip() or (state if state.lower() != "all" else "")
    if modal is None or not state or (lo and hi and lo > hi):
        return None  # skip obviously broken rows
    return (
        day,
        state,
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

    # --state takes one name, a comma-separated list, or "all".
    states = [STATE]
    args = sys.argv[1:]
    if "--state" in args:
        i = args.index("--state")
        states = [s.strip() for s in args[i + 1].split(",") if s.strip()]
        del args[i:i + 2]

    if len(args) == 2:
        start = datetime.strptime(args[0], "%Y-%m-%d").date()
        end = datetime.strptime(args[1], "%Y-%m-%d").date()
    else:
        end = date.today()
        start = end - timedelta(days=3)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # Create the table on first run (schema.sql only uses "if not exists").
        schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
        with open(schema) as f:
            conn.execute(f.read())
        conn.commit()

        ids = Ids(conn)
        day = start
        while day <= end:
            for state in states:
                records = fetch_day(day, state)
                rows = [ids.price_row(r) for r in (to_row(rec, day, state) for rec in records) if r]
                with conn.cursor() as cur:
                    cur.executemany(UPSERT, rows)
                conn.commit()
                print(f"{state} {day}: {len(records)} fetched, {len(rows)} saved", flush=True)
            day += timedelta(days=1)


if __name__ == "__main__":
    main()
