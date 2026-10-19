-- Run once in Supabase: SQL Editor > New query > paste > Run
-- Mandi Bhav database (PostgreSQL / Supabase).
-- Safe to run many times: everything uses "if not exists".
--
-- states 1─* districts 1─* mandis 1─* prices *─1 crops 1─* msp

create table if not exists states (
  id   smallserial primary key,
  name text not null unique                       -- 'Haryana'
);

create table if not exists districts (
  id       serial primary key,
  state_id smallint not null references states(id),
  name     text not null,                         -- 'Karnal'
  unique (state_id, name)
);

create table if not exists mandis (
  id          serial primary key,
  district_id int  not null references districts(id),
  name        text not null,                      -- 'Karnal APMC'
  latitude    numeric(9,6),                       -- filled in later, for "nearest mandi"
  longitude   numeric(9,6),
  unique (district_id, name)
);

create table if not exists crops (
  id             serial primary key,
  name           text not null unique,            -- 'Paddy(Dhan)(Common)'
  name_hi        text,                            -- 'धान', for the app
  agmarknet_code text                             -- Commodity_Code from data.gov.in
);

-- One row per mandi, crop, variety, grade and day. Prices are Rs per quintal.
create table if not exists prices (
  id          bigserial primary key,
  mandi_id    int  not null references mandis(id),
  crop_id     int  not null references crops(id),
  variety     text not null default '',
  grade       text not null default '',
  price_date  date not null,
  min_price   numeric(10,2),
  max_price   numeric(10,2),
  modal_price numeric(10,2) not null,
  fetched_at  timestamptz not null default now(),
  unique (mandi_id, crop_id, variety, grade, price_date),
  check (min_price is null or max_price is null or min_price <= max_price)
);
create index if not exists idx_prices_crop_date  on prices (crop_id, price_date desc);
create index if not exists idx_prices_mandi_date on prices (mandi_id, price_date desc);

-- MSP is set by the Government of India per crop per season, the same for every
-- mandi, so it lives in its own table instead of being repeated on every price row.
create table if not exists msp (
  id         serial primary key,
  crop_id    int  not null references crops(id),
  season     text not null check (season in ('kharif', 'rabi')),
  valid_from date not null,                       -- start of marketing season
  valid_to   date not null,                       -- end of marketing season
  msp_price  numeric(10,2) not null,              -- Rs per quintal
  unique (crop_id, valid_from)
);

-- Everything joined, for reading and for the app: one row per price, with names and MSP.
create or replace view price_view as
select p.price_date,
       s.name  as state,
       d.name  as district,
       m.name  as mandi,
       c.name  as crop,
       p.variety,
       p.grade,
       p.min_price,
       p.max_price,
       p.modal_price,
       ms.msp_price
from prices p
join mandis    m on m.id = p.mandi_id
join districts d on d.id = m.district_id
join states    s on s.id = d.state_id
join crops     c on c.id = p.crop_id
left join msp ms on ms.crop_id = p.crop_id
                and p.price_date between ms.valid_from and ms.valid_to;

-- One-time copy from the first version's flat table, if it exists.
do $$
begin
  if to_regclass('public.mandi_prices') is not null then
    insert into states (name) select distinct state from mandi_prices on conflict do nothing;
    insert into districts (state_id, name)
      select distinct s.id, mp.district from mandi_prices mp join states s on s.name = mp.state
      on conflict do nothing;
    insert into mandis (district_id, name)
      select distinct d.id, mp.market from mandi_prices mp
      join states s on s.name = mp.state join districts d on d.state_id = s.id and d.name = mp.district
      on conflict do nothing;
    insert into crops (name, agmarknet_code)
      select commodity, max(commodity_code) from mandi_prices group by commodity
      on conflict do nothing;
    insert into prices (mandi_id, crop_id, variety, grade, price_date, min_price, max_price, modal_price)
      select m.id, c.id, mp.variety, mp.grade, mp.arrival_date, mp.min_price, mp.max_price, mp.modal_price
      from mandi_prices mp
      join states s on s.name = mp.state
      join districts d on d.state_id = s.id and d.name = mp.district
      join mandis m on m.district_id = d.id and m.name = mp.market
      join crops c on c.name = mp.commodity
      where mp.modal_price is not null
      on conflict do nothing;
  end if;
end $$;

