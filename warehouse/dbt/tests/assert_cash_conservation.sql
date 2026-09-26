with attributed as (
  select coalesce(sum(net_cash_cents), 0) as cash_cents
  from {{ ref('mart_attribution_lead_creation') }}
), revenue as (
  select net_collected_cents as cash_cents from {{ ref('mart_revenue') }}
)
select attributed.cash_cents as attributed_cents, revenue.cash_cents as collected_cents
from attributed cross join revenue
where attributed.cash_cents != revenue.cash_cents
