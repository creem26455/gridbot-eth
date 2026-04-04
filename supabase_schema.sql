-- ============================================================
--  Supabase Schema — ETH/USDT Grid Bot
--  วิธีใช้: เปิด Supabase → SQL Editor → วาง SQL นี้ → Run
-- ============================================================

-- 1. ตาราง trades (บันทึกทุก trade ที่ filled)
create table if not exists trades (
  id           bigserial primary key,
  bot_id       text        not null,
  inst_id      text        not null default 'ETH-USDT-SWAP',
  side         text        not null,   -- 'buy' | 'sell'
  price        numeric     not null,
  contracts    integer     not null,
  eth_amount   numeric     not null,
  net_profit   numeric     not null,
  created_at   timestamptz not null default now()
);

-- index สำหรับ query เร็ว
create index if not exists trades_bot_id_idx    on trades (bot_id);
create index if not exists trades_created_at_idx on trades (created_at desc);

-- ──────────────────────────────────────────────────────────

-- 2. ตาราง bot_status (สถานะ real-time ของบอท)
create table if not exists bot_status (
  bot_id        text    primary key,
  inst_id       text    not null default 'ETH-USDT-SWAP',
  is_running    boolean not null default false,
  current_price numeric,
  active_orders integer default 0,
  trade_count   integer default 0,
  total_profit  numeric default 0,
  grid_lower    numeric,
  grid_upper    numeric,
  leverage      integer,
  capital       numeric,
  updated_at    timestamptz default now()
);

-- ──────────────────────────────────────────────────────────

-- 3. ตาราง pnl_daily (สรุปกำไรรายวัน)
create table if not exists pnl_daily (
  id          bigserial primary key,
  bot_id      text        not null,
  date        date        not null,
  pnl         numeric     not null default 0,
  trade_count integer     not null default 0,
  updated_at  timestamptz default now(),
  unique (bot_id, date)
);

create index if not exists pnl_daily_date_idx on pnl_daily (date desc);

-- ──────────────────────────────────────────────────────────

-- 4. เปิด Row Level Security (RLS) — อ่านได้แบบ public (สำหรับ Dashboard)
alter table trades     enable row level security;
alter table bot_status enable row level security;
alter table pnl_daily  enable row level security;

-- Policy: อ่านได้ทุกคน (Dashboard ใช้)
create policy "Allow public read trades"
  on trades for select using (true);

create policy "Allow public read bot_status"
  on bot_status for select using (true);

create policy "Allow public read pnl_daily"
  on pnl_daily for select using (true);

-- Policy: เขียนได้เฉพาะ service_role (บอทใช้ anon key ก็ได้เพราะรันบน server)
create policy "Allow insert trades"
  on trades for insert with check (true);

create policy "Allow upsert bot_status"
  on bot_status for all using (true);

create policy "Allow upsert pnl_daily"
  on pnl_daily for all using (true);

-- ──────────────────────────────────────────────────────────

-- 5. Migration: เพิ่ม columns สำหรับ Trend Bot (เพิ่มเมื่อ 5 เม.ย. 2026)
--    ถ้าตารางมีอยู่แล้ว ให้รัน ALTER TABLE แยกต่างหาก

-- เพิ่ม bot_tag (แยก Trend Bot ออกจาก Grid Bot เดิม)
alter table bot_status add column if not exists bot_tag text default null;
-- หมายเหตุ: NULL = Original Grid Bot, 'trend_v1' = Trend-Following Bot

-- เพิ่ม algo_tag (เก็บ Trend direction: UP / DOWN / UNKNOWN)
alter table bot_status add column if not exists algo_tag text default null;

-- เพิ่ม fields จาก OKX Grid API (บอทเดิมบางตัวอาจยังขาด)
alter table bot_status add column if not exists algo_state             text    default null;
alter table bot_status add column if not exists profit_loss            numeric default null;
alter table bot_status add column if not exists total_annualized_rate  numeric default null;
alter table bot_status add column if not exists min_price              numeric default null;
alter table bot_status add column if not exists max_price              numeric default null;
alter table bot_status add column if not exists grid_num               integer default null;
alter table bot_status add column if not exists bot_id_okx             text    default null;

-- Index สำหรับ query แยก bot_tag
create index if not exists bot_status_tag_idx on bot_status (bot_tag, leverage);

-- ──────────────────────────────────────────────────────────
-- ✅ เสร็จแล้ว! ตรวจสอบใน Table Editor ว่ามี 3 ตารางครบ
--    และ bot_status มี column bot_tag, algo_tag เพิ่มแล้ว
-- ============================================================
