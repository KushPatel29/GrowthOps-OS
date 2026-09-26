select
  cast(attempt_id as varchar) as attempt_id,
  cast(subscription_id as varchar) as subscription_id,
  try_cast(attempted_at as timestamptz) as attempted_at,
  cast(outcome as varchar) as outcome,
  nullif(cast(failure_code as varchar),'') as failure_code
from {{ ref('raw_renewal_attempts') }}
