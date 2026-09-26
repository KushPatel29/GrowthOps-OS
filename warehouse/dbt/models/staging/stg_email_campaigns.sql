select
  cast(email_id as varchar) as email_id,
  try_cast(sent_at as timestamptz) as sent_at,
  cast(substr(cast(sent_at as varchar), 1, 10) as varchar) as sent_date,
  cast(email_type as varchar) as email_type,
  cast(subject as varchar) as subject,
  cast(sending_domain as varchar) as sending_domain,
  cast(sends as bigint) as sends,
  cast(delivered as bigint) as delivered,
  cast(bounces as bigint) as bounces,
  cast(opens as bigint) as opens,
  cast(machine_opens as bigint) as machine_opens,
  cast(clicks as bigint) as clicks,
  cast(unsubscribes as bigint) as unsubscribes,
  cast(spam_complaints as bigint) as spam_complaints
from {{ ref('raw_email_campaigns') }}
