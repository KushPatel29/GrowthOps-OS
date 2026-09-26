select
  cast(t.touch_id as varchar) as touch_id,
  cast(t.contact_id as varchar) as contact_id,
  nullif(cast(t.campaign_id as varchar), '') as campaign_id,
  try_cast(t.occurred_at as timestamptz) as occurred_at,
  cast(t.touch_type as varchar) as touch_type,
  nullif(cast(t.utm_source as varchar), '') as utm_source,
  c.source,
  c.medium,
  coalesce(c.registry_valid, 0) as registry_valid,
  coalesce(t.campaign_id != 'direct', true) as eligible_acquisition
from {{ ref('raw_touches') }} t
left join {{ ref('stg_campaigns') }} c on t.campaign_id = c.campaign_id
