select
  cast(experiment_id as varchar) as experiment_id,
  cast(hypothesis as varchar) as hypothesis,
  cast(primary_metric as varchar) as primary_metric,
  cast(assignment_unit as varchar) as assignment_unit,
  try_cast(started_at as timestamptz) as started_at
from {{ ref('raw_experiments') }}
