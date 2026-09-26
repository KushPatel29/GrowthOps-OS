select payment_id, gross_cents, refund_cents
from {{ ref('int_payment_cash') }}
where refund_cents < 0 or refund_cents > gross_cents
