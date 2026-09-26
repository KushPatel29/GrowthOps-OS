-- One row per send. Human opens exclude machine (privacy-proxy) opens; rates are shares of delivered.
select
  email_id,
  sent_date,
  email_type,
  subject,
  sending_domain,
  sends,
  delivered,
  bounces,
  opens,
  machine_opens,
  opens - machine_opens as human_opens,
  clicks,
  unsubscribes,
  spam_complaints,
  round(bounces / nullif(sends, 0), 4) as bounce_rate,
  round((opens - machine_opens) / nullif(delivered, 0), 4) as human_open_rate,
  round(clicks / nullif(delivered, 0), 4) as click_rate,
  round(clicks / nullif(opens - machine_opens, 0), 4) as click_to_open_rate,
  round(spam_complaints / nullif(delivered, 0), 4) as complaint_rate
from {{ ref('stg_email_campaigns') }}
