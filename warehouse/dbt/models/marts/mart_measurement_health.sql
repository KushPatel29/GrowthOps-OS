with eligible as (
  select * from {{ ref('stg_touches') }} where eligible_acquisition
), contact_duplicates as (
  select coalesce(sum(n - 1), 0) as duplicate_rows
  from (select count(*) as n from {{ ref('stg_contacts') }} group by email_normalized having count(*) > 1)
), paid_journeys as (
  select *,
    case when lead_at is not null and mql_at is not null and booked_at is not null
      and attended_at is not null and opportunity_at is not null and won_at is not null
      and lead_at <= mql_at and mql_at <= booked_at and booked_at <= attended_at
      and attended_at <= opportunity_at and opportunity_at <= won_at and won_at <= paid_at
      then 1 else 0 end as valid_journey
  from {{ ref('int_journey_firsts') }} where paid_at is not null
)
select
  (select count(*) from eligible) as eligible_touches,
  (select count(*) from eligible where utm_source is not null) as touches_with_utm,
  (select count(*) from eligible where registry_valid = 1) as registered_touches,
  (select count(*) from {{ ref('stg_contacts') }} where owner_id is not null) as owned_contacts,
  (select count(*) from {{ ref('stg_contacts') }}) as contacts,
  (select duplicate_rows from contact_duplicates) as duplicate_contact_rows,
  (select count(*) from {{ ref('int_payment_cash') }} p
    left join {{ ref('stg_deals') }} d on d.deal_id = p.deal_id
    where d.deal_id is null) as unmatched_payments,
  (select count(*) from {{ ref('int_payment_cash') }}) as payments,
  (select count(*) from paid_journeys) as paid_contact_count,
  (select coalesce(sum(valid_journey), 0) from paid_journeys) as valid_paid_journeys
