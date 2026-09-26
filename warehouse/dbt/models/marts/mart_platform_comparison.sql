-- What each ad platform claims versus the net cash the warehouse credits to it.
with spend as (
  select c.platform, sum(s.spend_cents) as spend_cents
  from {{ ref('stg_ad_spend_daily') }} s
  join {{ ref('stg_campaigns') }} c on c.campaign_id = s.campaign_id
  group by c.platform
), claimed as (
  select platform, count(*) as reported_conversions, sum(reported_value_cents) as reported_value_cents
  from {{ ref('stg_platform_conversions') }} group by platform
), warehouse as (
  select c.platform, sum(a.net_cash_cents) as warehouse_net_cash_cents
  from {{ ref('mart_attribution_lead_creation') }} a
  join {{ ref('stg_campaigns') }} c on c.campaign_id = a.campaign_id
  group by c.platform
)
select
  s.platform,
  s.spend_cents,
  coalesce(cl.reported_conversions, 0) as reported_conversions,
  coalesce(cl.reported_value_cents, 0) as reported_value_cents,
  coalesce(w.warehouse_net_cash_cents, 0) as warehouse_net_cash_cents
from spend s
left join claimed cl on cl.platform = s.platform
left join warehouse w on w.platform = s.platform
