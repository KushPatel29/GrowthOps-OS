select
  cast(lifecycle_event_id as varchar) as lifecycle_event_id,
  cast(contact_id as varchar) as contact_id,
  cast(stage as varchar) as stage,
  try_cast(occurred_at as timestamptz) as occurred_at
from {{ ref('raw_lifecycle_events') }}
