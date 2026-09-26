select touch_id, contact_id, campaign_id, occurred_at
from {{ ref('stg_touches') }}
where touch_type = 'lead_creation'
qualify row_number() over (partition by contact_id order by occurred_at desc, touch_id desc) = 1
