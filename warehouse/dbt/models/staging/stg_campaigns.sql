select
  cast(campaign_id as varchar) as campaign_id,
  cast(source as varchar) as source,
  cast(medium as varchar) as medium,
  cast(campaign_name as varchar) as campaign_name,
  cast(spend_cents as bigint) as spend_cents,
  cast(registry_valid as integer) as registry_valid,
  cast(platform as varchar) as platform,
  nullif(cast(landing_page as varchar), '') as landing_page,
  medium in ('paid_social', 'paid_search') as is_paid
from {{ ref('raw_campaigns') }}
