select
  cast(payment_id as varchar) as payment_id,
  nullif(cast(deal_id as varchar), '') as deal_id,
  cast(customer_id as varchar) as customer_id,
  cast(amount_cents as bigint) as amount_cents,
  cast(status as varchar) as status,
  try_cast(paid_at as timestamptz) as paid_at
from {{ ref('raw_payments') }}
