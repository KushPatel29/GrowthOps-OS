with mql_people as (
  select distinct contact_id from {{ ref('stg_lifecycle_events') }} where stage = 'mql'
), payers as (
  select distinct customer_id from {{ ref('stg_payments') }} where status = 'succeeded'
), lead_counts as (
  select
    l.campaign_id,
    count(*) as leads,
    count(m.contact_id) as mqls,
    count(p.customer_id) as customers
  from {{ ref('int_lead_touch') }} l
  left join mql_people m on m.contact_id = l.contact_id
  left join payers p on p.customer_id = l.contact_id
  where l.campaign_id is not null
  group by l.campaign_id
), cash_counts as (
  select campaign_id, sum(net_cash_cents) as net_cash_cents
  from {{ ref('mart_attribution_lead_creation') }}
  group by campaign_id
), spend_counts as (
  select campaign_id, sum(spend_cents) as spend_cents
  from {{ ref('stg_ad_spend_daily') }} group by campaign_id
)
select
  c.campaign_id,
  c.source,
  c.medium,
  coalesce(s.spend_cents, 0) as spend_cents,
  coalesce(l.leads, 0) as leads,
  coalesce(l.mqls, 0) as mqls,
  coalesce(l.customers, 0) as customers,
  coalesce(cash.net_cash_cents, 0) as net_cash_cents
from {{ ref('stg_campaigns') }} c
left join spend_counts s on s.campaign_id = c.campaign_id
left join lead_counts l on l.campaign_id = c.campaign_id
left join cash_counts cash on cash.campaign_id = c.campaign_id
