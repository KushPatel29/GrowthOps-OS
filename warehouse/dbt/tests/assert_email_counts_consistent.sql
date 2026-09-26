select email_id
from {{ ref('mart_email_performance') }}
where delivered + bounces <> sends
   or human_opens < 0
   or clicks > delivered
   or human_open_rate > 1
