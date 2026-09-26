select
  cast(link_id as varchar) as link_id,
  try_cast(click_date as date) as click_date,
  cast(clicks as bigint) as clicks
from {{ ref('raw_short_link_clicks') }}
