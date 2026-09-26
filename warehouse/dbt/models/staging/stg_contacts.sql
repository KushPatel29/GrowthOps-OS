select
  cast(contact_id as varchar) as contact_id,
  lower(trim(cast(email as varchar))) as email_normalized,
  nullif(cast(legacy_id as varchar), '') as legacy_id,
  nullif(cast(owner_id as varchar), '') as owner_id,
  nullif(cast(original_source as varchar), '') as original_source,
  cast(current_stage as varchar) as current_stage
from {{ ref('raw_contacts') }}
