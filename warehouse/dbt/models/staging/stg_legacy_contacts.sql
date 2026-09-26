select
  cast(legacy_id as varchar) as legacy_id,
  lower(trim(cast(email as varchar))) as email_normalized,
  nullif(cast(owner_id as varchar),'') as owner_id,
  nullif(cast(original_source as varchar),'') as original_source,
  cast(lifecycle_stage as varchar) as lifecycle_stage
from {{ ref('raw_legacy_contacts') }}
