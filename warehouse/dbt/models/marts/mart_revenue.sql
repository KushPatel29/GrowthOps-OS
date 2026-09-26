select
  (select coalesce(sum(amount_cents), 0) from {{ ref('stg_deals') }} where stage = 'closed_won') as booked_cents,
  coalesce(sum(gross_cents), 0) as gross_collected_cents,
  coalesce(sum(refund_cents), 0) as refunds_cents,
  coalesce(sum(net_cash_cents), 0) as net_collected_cents
from {{ ref('int_payment_cash') }}
