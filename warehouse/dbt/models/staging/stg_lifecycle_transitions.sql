select
  cast(transition_id as varchar) as transition_id,
  cast(person_key as varchar) as person_key,
  nullif(cast(from_stage as varchar), '') as from_stage,
  cast(to_stage as varchar) as to_stage,
  try_cast(occurred_at as timestamptz) as occurred_at,
  cast(source_event_id as varchar) as source_event_id,
  cast(policy_version as varchar) as policy_version
from {{ ref('raw_lifecycle_transitions') }}
