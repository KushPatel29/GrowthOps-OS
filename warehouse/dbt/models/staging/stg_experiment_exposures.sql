select
  cast(exposure_id as varchar) as exposure_id,
  cast(experiment_id as varchar) as experiment_id,
  cast(variant_id as varchar) as variant_id,
  cast(visitor_id as varchar) as visitor_id,
  cast(session_id as varchar) as session_id,
  nullif(cast(contact_id as varchar),'') as contact_id,
  try_cast(exposed_at as timestamptz) as exposed_at
from {{ ref('raw_experiment_exposures') }}
