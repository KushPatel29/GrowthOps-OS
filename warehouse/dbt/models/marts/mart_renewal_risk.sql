with attempts as (
  select subscription_id,
    count(*) filter (where outcome='failed') as failed_attempts,
    count(*) filter (where outcome='succeeded') as successful_attempts
  from {{ ref('stg_renewal_attempts') }} group by subscription_id
)
select
  s.subscription_id, s.customer_id, s.plan_id,
  cast(s.renewal_due_at as date) as due_date,
  coalesce(a.failed_attempts,0) as failed_attempts,
  case
    when coalesce(a.successful_attempts,0)>0 then 'resolved'
    when cast(s.renewal_due_at as date)<date '2026-09-26' or coalesce(a.failed_attempts,0)>0 then 'high'
    when cast(s.renewal_due_at as date)<=date '2026-10-03' then 'medium'
    else 'not_due'
  end as risk_level
from {{ ref('stg_subscriptions') }} s
left join attempts a on a.subscription_id=s.subscription_id
where s.status='active'
