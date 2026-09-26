select
  cast(subscription_id as varchar) as subscription_id,
  cast(customer_id as varchar) as customer_id,
  cast(plan_id as varchar) as plan_id,
  try_cast(started_at as timestamptz) as started_at,
  try_cast(renewal_due_at as timestamptz) as renewal_due_at,
  cast(status as varchar) as status
from {{ ref('raw_subscriptions') }}
