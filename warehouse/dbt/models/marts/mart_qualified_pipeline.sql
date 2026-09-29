select
  campaign_id,
  currency,
  count(*) as qualified_deals,
  count(distinct person_key) as qualified_people,
  sum(amount_minor) as created_minor,
  sum(case when stage = 'open' then amount_minor else 0 end) as open_minor,
  sum(case when stage = 'closed_won' then amount_minor else 0 end) as won_minor
from {{ ref('int_qualified_pipeline') }}
where qualification_status = 'qualified'
group by campaign_id, currency
