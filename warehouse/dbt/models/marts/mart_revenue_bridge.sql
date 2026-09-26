-- CRM closed-won value -> gross collected -> net collected, one row per named step.
with won as (
  select
    d.deal_id,
    d.amount_cents,
    d.contact_id in (select contact_id from {{ ref('int_duplicate_contacts') }}) as is_duplicate,
    coalesce(sum(p.amount_cents) filter (where p.status = 'succeeded'), 0) as collected_cents
  from {{ ref('stg_deals') }} d
  left join {{ ref('stg_payments') }} p on p.deal_id = d.deal_id
  where d.stage = 'closed_won'
  group by d.deal_id, d.amount_cents, d.contact_id
), totals as (
  select
    (select coalesce(sum(amount_cents), 0) from won) as booked,
    (select coalesce(sum(amount_cents), 0) from won where is_duplicate) as duplicates,
    (select coalesce(sum(greatest(amount_cents - collected_cents, 0)), 0) from won where not is_duplicate) as uncollected,
    (select coalesce(sum(p.gross_cents), 0) from {{ ref('int_payment_cash') }} p
       left join {{ ref('stg_deals') }} d on d.deal_id = p.deal_id
       where d.deal_id is null and p.payment_type <> 'renewal') as unlinked,
    (select coalesce(sum(gross_cents), 0) from {{ ref('int_payment_cash') }} where payment_type = 'renewal') as renewals,
    (select coalesce(sum(gross_cents), 0) from {{ ref('int_payment_cash') }}) as gross,
    (select coalesce(sum(refund_cents), 0) from {{ ref('int_payment_cash') }}) as refunds
)
select 1 as ordinal, 'crm_booked' as step, 'total' as kind, booked as cents from totals
union all select 2, 'duplicate_deals', 'delta', -duplicates from totals
union all select 3, 'not_yet_collected', 'delta', -uncollected from totals
union all select 4, 'unlinked_payments', 'delta', unlinked from totals
union all select 5, 'renewals', 'delta', renewals from totals
union all select 6, 'gross_collected', 'total', gross from totals
union all select 7, 'refunds', 'delta', -refunds from totals
union all select 8, 'net_collected', 'total', gross - refunds from totals
