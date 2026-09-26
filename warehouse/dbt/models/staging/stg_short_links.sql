select
  cast(link_id as varchar) as link_id,
  cast(destination_url as varchar) as destination_url,
  cast(channel as varchar) as channel,
  try_cast(created_at as date) as created_at,
  cast(owner as varchar) as owner,
  nullif(regexp_extract(cast(destination_url as varchar), '[?&]utm_source=([^&#]*)', 1), '') as utm_source,
  nullif(regexp_extract(cast(destination_url as varchar), '[?&]utm_medium=([^&#]*)', 1), '') as utm_medium,
  nullif(regexp_extract(cast(destination_url as varchar), '[?&]utm_campaign=([^&#]*)', 1), '') as utm_campaign
from {{ ref('raw_short_links') }}
