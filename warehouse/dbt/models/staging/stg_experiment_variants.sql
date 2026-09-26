select
  cast(variant_id as varchar) as variant_id,
  cast(experiment_id as varchar) as experiment_id,
  cast(label as varchar) as label,
  cast(cta_text as varchar) as cta_text
from {{ ref('raw_experiment_variants') }}
