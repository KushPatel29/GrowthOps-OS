select
  cast(content_id as varchar) as content_id,
  cast(title as varchar) as title,
  cast(platform as varchar) as platform,
  try_cast(published_at as timestamptz) as published_at,
  cast(views as bigint) as views,
  cast(clicks as bigint) as clicks,
  cast(offer_id as varchar) as offer_id
from {{ ref('raw_content_items') }}
