select
  cast(person_key as varchar) as person_key,
  try_cast(created_at as timestamptz) as created_at,
  cast(resolution_version as varchar) as resolution_version,
  cast(status as varchar) as status
from {{ ref('raw_persons') }}
