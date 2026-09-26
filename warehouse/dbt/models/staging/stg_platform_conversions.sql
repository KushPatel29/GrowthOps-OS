-- Ad-platform self-reported purchase conversions (each platform's own window and pixel value).
select
  cast(conversion_id as varchar) as conversion_id,
  cast(platform as varchar) as platform,
  nullif(cast(campaign_id as varchar), '') as campaign_id,
  cast(contact_id as varchar) as contact_id,
  try_cast(reported_at as timestamptz) as reported_at,
  cast(reported_value_cents as bigint) as reported_value_cents,
  cast(click_through as integer) = 1 as click_through,
  cast(attribution_setting as varchar) as attribution_setting
from {{ ref('raw_platform_conversions') }}
