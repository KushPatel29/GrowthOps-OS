select
  cast(engagement_id as varchar) as engagement_id,
  cast(contact_id as varchar) as contact_id,
  cast(content_id as varchar) as content_id,
  nullif(cast(touch_id as varchar),'') as touch_id,
  try_cast(occurred_at as timestamptz) as occurred_at,
  cast(watched_seconds as bigint) as watched_seconds
from {{ ref('raw_content_engagements') }}
