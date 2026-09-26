with daily as (
  select sum(gross_collected_cents) as gross_cents,
         sum(refunds_cents) as refund_cents,
         sum(net_cash_cents) as net_cents
  from {{ ref('mart_growth_daily') }}
), revenue as (
  select gross_collected_cents, refunds_cents, net_collected_cents
  from {{ ref('mart_revenue') }}
)
select * from daily cross join revenue
where daily.gross_cents != revenue.gross_collected_cents
   or daily.refund_cents != revenue.refunds_cents
   or daily.net_cents != revenue.net_collected_cents
