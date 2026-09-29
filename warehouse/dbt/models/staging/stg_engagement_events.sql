select
  cast(event_id as varchar) as event_id,
  nullif(cast(person_key as varchar), '') as person_key,
  cast(platform as varchar) as platform,
  nullif(cast(content_id as varchar), '') as content_id,
  cast(event_type as varchar) as event_type,
  try_cast(occurred_at as timestamptz) as occurred_at,
  cast(duration_seconds as bigint) as duration_seconds
from {{ ref('raw_engagement_events') }}
