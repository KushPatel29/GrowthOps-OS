with assigned as (
  select coalesce(sum(net_cash_cents), 0) as cash_cents
  from {{ ref('mart_campaign_performance') }}
), unassigned as (
  select coalesce(sum(net_cash_cents), 0) as cash_cents
  from {{ ref('mart_attribution_lead_creation') }} where campaign_id is null
), revenue as (
  select net_collected_cents as cash_cents from {{ ref('mart_revenue') }}
)
select assigned.cash_cents as assigned_cents, unassigned.cash_cents as unassigned_cents,
       revenue.cash_cents as collected_cents
from assigned cross join unassigned cross join revenue
where assigned.cash_cents + unassigned.cash_cents != revenue.cash_cents
