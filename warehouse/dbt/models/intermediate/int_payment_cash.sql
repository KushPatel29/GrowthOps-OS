with refunds as (
  select payment_id, sum(amount_cents) as refund_cents
  from {{ ref('stg_refunds') }}
  group by payment_id
)
select
  p.payment_id,
  p.customer_id,
  p.deal_id,
  p.paid_at,
  p.payment_type,
  p.product_id,
  p.amount_cents as gross_cents,
  coalesce(r.refund_cents, 0) as refund_cents,
  p.amount_cents - coalesce(r.refund_cents, 0) as net_cash_cents
from {{ ref('stg_payments') }} p
left join refunds r on p.payment_id = r.payment_id
where p.status = 'succeeded'
