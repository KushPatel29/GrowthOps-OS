select
  d.deal_id,
  d.contact_id as person_key,
  d.stage,
  q.qualified_at,
  q.status as qualification_status,
  q.reason as qualification_reason,
  q.amount_minor,
  q.currency,
  coalesce(t.campaign_id, '(unattributed)') as campaign_id
from {{ ref('stg_deals') }} d
join {{ ref('stg_deal_qualification') }} q on q.deal_id = d.deal_id
left join {{ ref('int_lead_touch') }} t on t.contact_id = d.contact_id
