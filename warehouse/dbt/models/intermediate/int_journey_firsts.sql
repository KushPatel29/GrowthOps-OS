select
  contact_id,
  min(occurred_at) filter (where stage = 'lead') as lead_at,
  min(occurred_at) filter (where stage = 'mql') as mql_at,
  min(occurred_at) filter (where stage = 'call_booked') as booked_at,
  min(occurred_at) filter (where stage = 'call_attended') as attended_at,
  min(occurred_at) filter (where stage = 'opportunity') as opportunity_at,
  min(occurred_at) filter (where stage = 'closed_won') as won_at,
  min(occurred_at) filter (where stage = 'paid') as paid_at,
  min(occurred_at) filter (where stage = 'activated') as activated_at,
  min(occurred_at) filter (where stage = 'renewed') as renewed_at
from {{ ref('stg_lifecycle_events') }}
group by contact_id
