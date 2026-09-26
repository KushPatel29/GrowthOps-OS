with first_content as (
  select engagement_id, contact_id, content_id
  from {{ ref('stg_content_engagements') }}
  qualify row_number() over (
    partition by contact_id order by occurred_at, engagement_id
  ) = 1
), mql_people as (
  select distinct contact_id from {{ ref('stg_lifecycle_events') }} where stage='mql'
), booked_people as (
  select distinct contact_id from {{ ref('stg_lifecycle_events') }} where stage='call_booked'
), cash_by_contact as (
  select customer_id, sum(net_cash_cents) as net_cash_cents
  from {{ ref('int_payment_cash') }} group by customer_id
)
select
  ci.content_id, ci.title, ci.platform, ci.views, ci.clicks,
  count(e.contact_id) as engaged_leads,
  count(m.contact_id) as mqls,
  count(b.contact_id) as calls_booked,
  count(cash.customer_id) as customers,
  coalesce(sum(cash.net_cash_cents),0) as influenced_net_cash_cents
from {{ ref('stg_content_items') }} ci
left join first_content e on e.content_id=ci.content_id
left join mql_people m on m.contact_id=e.contact_id
left join booked_people b on b.contact_id=e.contact_id
left join cash_by_contact cash on cash.customer_id=e.contact_id
group by ci.content_id, ci.title, ci.platform, ci.views, ci.clicks
