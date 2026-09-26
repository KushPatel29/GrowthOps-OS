with duplicate_emails as (
  select email_normalized, count(*) as crm_rows
  from {{ ref('stg_contacts') }} group by email_normalized
)
select
  l.legacy_id,
  c.contact_id,
  c.contact_id is null as missing_contact,
  c.owner_id is not distinct from l.owner_id as owner_match,
  c.original_source is not distinct from l.original_source as source_match,
  -- Contacts keep progressing after cut-over; only a stage regression is a defect.
  case c.current_stage when 'lead' then 0 when 'mql' then 1 when 'opportunity' then 2 when 'customer' then 3 else 0 end
    >= case l.lifecycle_stage when 'lead' then 0 when 'mql' then 1 when 'opportunity' then 2 when 'customer' then 3 else 0 end
    as stage_match,
  c.email_normalized = l.email_normalized as email_match,
  coalesce(d.crm_rows,0) > 1 as duplicate_crm_email
from {{ ref('stg_legacy_contacts') }} l
left join {{ ref('stg_contacts') }} c on c.legacy_id = l.legacy_id
left join duplicate_emails d on d.email_normalized = c.email_normalized
