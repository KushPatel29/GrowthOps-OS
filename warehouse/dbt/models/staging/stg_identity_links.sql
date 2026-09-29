select
  cast(source_system as varchar) as source_system,
  cast(id_type as varchar) as id_type,
  cast(id_hash as varchar) as id_hash,
  cast(person_key as varchar) as person_key,
  try_cast(first_seen_at as timestamptz) as first_seen_at,
  cast(confidence as varchar) as confidence,
  cast(state as varchar) as state
from {{ ref('raw_identity_links') }}
