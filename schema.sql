-- Run once in Supabase: SQL Editor > New query > paste > Run
create table if not exists mandi_prices (
  id             bigserial primary key,
  arrival_date   date        not null,
  state          text        not null,
  district       text        not null,
  market         text        not null,
  commodity      text        not null,
  variety        text        not null default '',
  grade          text        not null default '',
  commodity_code text,
  min_price      numeric(10,2),   -- Rs per quintal
  max_price      numeric(10,2),
  modal_price    numeric(10,2),
  fetched_at     timestamptz not null default now(),
  unique (arrival_date, state, district, market, commodity, variety, grade)
);

create index if not exists idx_prices_commodity_date on mandi_prices (commodity, arrival_date desc);
create index if not exists idx_prices_market_date    on mandi_prices (market, arrival_date desc);
