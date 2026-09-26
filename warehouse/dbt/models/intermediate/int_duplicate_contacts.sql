-- Non-surviving contacts that share a normalized email; the migrated (legacy) record survives.
select contact_id, email_normalized
from {{ ref('stg_contacts') }}
qualify row_number() over (
  partition by email_normalized order by legacy_id is null, created_at, contact_id
) > 1
