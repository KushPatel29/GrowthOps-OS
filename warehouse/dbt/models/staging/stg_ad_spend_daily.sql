select
  cast(campaign_id as varchar) as campaign_id,
  try_cast(spend_date as date) as spend_date,
  cast(spend_cents as bigint) as spend_cents,
  cast(impressions as bigint) as impressions,
  cast(clicks as bigint) as clicks
from {{ ref('raw_ad_spend_daily') }}
