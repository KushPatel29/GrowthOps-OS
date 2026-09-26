with dates as (
  select spend_date as day from {{ ref('stg_ad_spend_daily') }}
  union select cast(occurred_at as date) from {{ ref('stg_lifecycle_events') }}
    where stage in ('lead','mql','call_booked')
  union select cast(closed_at as date) from {{ ref('stg_deals') }} where stage='closed_won'
  union select cast(paid_at as date) from {{ ref('stg_payments') }} where status='succeeded'
  union select cast(refunded_at as date) from {{ ref('stg_refunds') }}
), spend as (
  select spend_date as day, sum(spend_cents) as spend_cents
  from {{ ref('stg_ad_spend_daily') }} group by spend_date
), stages as (
  select cast(occurred_at as date) as day,
    count(distinct contact_id) filter (where stage='lead') as leads,
    count(distinct contact_id) filter (where stage='mql') as mqls,
    count(distinct contact_id) filter (where stage='call_booked') as calls_booked
  from {{ ref('stg_lifecycle_events') }} group by cast(occurred_at as date)
), booked as (
  select cast(closed_at as date) as day, sum(amount_cents) as booked_cents
  from {{ ref('stg_deals') }} where stage='closed_won' group by cast(closed_at as date)
), gross as (
  select cast(paid_at as date) as day, sum(amount_cents) as gross_collected_cents
  from {{ ref('stg_payments') }} where status='succeeded' group by cast(paid_at as date)
), refunded as (
  select cast(refunded_at as date) as day, sum(amount_cents) as refunds_cents
  from {{ ref('stg_refunds') }} group by cast(refunded_at as date)
)
select
  d.day,
  coalesce(s.spend_cents,0) as spend_cents,
  coalesce(st.leads,0) as leads,
  coalesce(st.mqls,0) as mqls,
  coalesce(st.calls_booked,0) as calls_booked,
  coalesce(b.booked_cents,0) as booked_cents,
  coalesce(g.gross_collected_cents,0) as gross_collected_cents,
  coalesce(r.refunds_cents,0) as refunds_cents,
  coalesce(g.gross_collected_cents,0)-coalesce(r.refunds_cents,0) as net_cash_cents
from dates d
left join spend s on s.day=d.day
left join stages st on st.day=d.day
left join booked b on b.day=d.day
left join gross g on g.day=d.day
left join refunded r on r.day=d.day
