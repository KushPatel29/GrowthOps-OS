select
  cast(refund_id as varchar) as refund_id,
  cast(payment_id as varchar) as payment_id,
  cast(amount_cents as bigint) as amount_cents,
  try_cast(refunded_at as timestamptz) as refunded_at
from {{ ref('raw_refunds') }}
