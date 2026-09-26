select
  cast(deal_id as varchar) as deal_id,
  cast(contact_id as varchar) as contact_id,
  cast(amount_cents as bigint) as amount_cents,
  cast(stage as varchar) as stage,
  try_cast(closed_at as timestamptz) as closed_at
from {{ ref('raw_deals') }}
