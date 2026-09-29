select
  cast(decision_id as varchar) as decision_id,
  cast(deal_id as varchar) as deal_id,
  try_cast(qualified_at as timestamptz) as qualified_at,
  cast(status as varchar) as status,
  cast(reason as varchar) as reason,
  cast(amount_minor as bigint) as amount_minor,
  cast(currency as varchar) as currency,
  cast(policy_version as varchar) as policy_version
from {{ ref('raw_deal_qualification') }}
