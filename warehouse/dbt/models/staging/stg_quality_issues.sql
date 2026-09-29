select
  cast(issue_id as varchar) as issue_id,
  cast(rule_id as varchar) as rule_id,
  cast(entity_type as varchar) as entity_type,
  cast(entity_id as varchar) as entity_id,
  cast(severity as varchar) as severity,
  cast(state as varchar) as state
from {{ ref('raw_quality_issues') }}
