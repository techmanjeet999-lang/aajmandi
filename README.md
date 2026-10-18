# Mandi Bhav backend

Fetches daily Haryana mandi prices from data.gov.in and saves them into a Supabase (PostgreSQL) database.

## Setup
1. The script creates the `mandi_prices` table itself on its first run (from `schema.sql`).
2. In this repo: **Settings > Secrets and variables > Actions**, add:
   - `DATA_GOV_API_KEY`: your data.gov.in API key
   - `DATABASE_URL`: Supabase **Session pooler** connection string
3. **Actions > Fetch mandi prices > Run workflow** to run it now. It also runs 3 times a day on its own.

## Run locally
```
pip install -r requirements.txt
cp .env.example .env   # then fill in the two values
python fetch_prices.py                         # today and the previous 3 days
python fetch_prices.py 2026-10-01 2026-10-15   # a date range (backfill)
```
