with ranked as (
  select
    p.payment_id,
    t.touch_id,
    t.campaign_id,
    row_number() over (
      partition by p.payment_id order by t.occurred_at desc nulls last, t.touch_id desc
    ) as touch_rank
  from {{ ref('int_payment_cash') }} p
  left join {{ ref('stg_touches') }} t
    on t.contact_id = p.customer_id
    and t.touch_type = 'lead_creation'
    and t.occurred_at <= p.paid_at
)
select
  p.payment_id,
  p.customer_id,
  r.touch_id,
  r.campaign_id,
  p.paid_at,
  p.gross_cents,
  p.refund_cents,
  p.net_cash_cents
from {{ ref('int_payment_cash') }} p
left join ranked r on r.payment_id = p.payment_id and r.touch_rank = 1
