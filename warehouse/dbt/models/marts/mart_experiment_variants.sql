with mql_people as (
  select distinct contact_id from {{ ref('stg_lifecycle_events') }} where stage='mql'
), cash_by_person as (
  select customer_id, sum(net_cash_cents) as net_cash_cents
  from {{ ref('int_payment_cash') }} group by customer_id
)
select
  v.experiment_id, v.variant_id, v.label, v.cta_text,
  count(e.exposure_id) as visitors,
  count(e.contact_id) as leads,
  count(m.contact_id) as mqls,
  count(c.customer_id) as customers,
  coalesce(sum(c.net_cash_cents),0) as net_cash_cents,
  round(count(e.contact_id)::double / nullif(count(e.exposure_id),0),4) as lead_rate,
  round(count(m.contact_id)::double / nullif(count(e.contact_id),0),4) as mql_per_lead,
  round(coalesce(sum(c.net_cash_cents),0)::double / nullif(count(e.exposure_id),0),2) as net_cash_per_visitor_cents
from {{ ref('stg_experiment_variants') }} v
left join {{ ref('stg_experiment_exposures') }} e on e.variant_id=v.variant_id and e.experiment_id=v.experiment_id
left join mql_people m on m.contact_id=e.contact_id
left join cash_by_person c on c.customer_id=e.contact_id
group by v.experiment_id, v.variant_id, v.label, v.cta_text
