{% set as_of = var('as_of', '2026-09-25') %}
{% set due_soon_days = var('due_soon_days', 14) %}
with attempts as (
  select a.subscription_id,
    count(*) filter (
      where a.outcome = 'failed' and a.attempted_at >= s.renewal_due_at - interval 7 day
    ) as failed_attempts
  from {{ ref('stg_renewal_attempts') }} a
  join {{ ref('stg_subscriptions') }} s on s.subscription_id = a.subscription_id
  group by a.subscription_id
)
select
  s.subscription_id, s.customer_id, s.plan_id,
  cast(s.renewal_due_at as date) as due_date,
  coalesce(a.failed_attempts, 0) as failed_attempts,
  case
    when cast(s.renewal_due_at as date) <= date '{{ as_of }}' or coalesce(a.failed_attempts, 0) > 0 then 'high'
    when cast(s.renewal_due_at as date) <= date '{{ as_of }}' + interval {{ due_soon_days }} day then 'medium'
    else 'not_due'
  end as risk_level
from {{ ref('stg_subscriptions') }} s
left join attempts a on a.subscription_id = s.subscription_id
where s.status = 'active'
