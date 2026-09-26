select
  cast(payment_id as varchar) as payment_id,
  nullif(cast(deal_id as varchar), '') as deal_id,
  cast(customer_id as varchar) as customer_id,
  cast(amount_cents as bigint) as amount_cents,
  cast(status as varchar) as status,
  try_cast(paid_at as timestamptz) as paid_at,
  cast(payment_type as varchar) as payment_type,
  nullif(cast(subscription_id as varchar), '') as subscription_id,
  nullif(cast(product_id as varchar), '') as product_id
from {{ ref('raw_payments') }}
