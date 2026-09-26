with migrated as (
  select * from {{ ref('mart_migration_audit') }} where not missing_contact
), duplicates as (
  select coalesce(sum(n-1),0) as duplicate_rows from (
    select count(*) as n from {{ ref('stg_contacts') }} group by email_normalized having count(*)>1
  )
)
select
  (select count(*) from {{ ref('mart_migration_audit') }}) as legacy_contacts,
  (select count(*) from migrated) as migrated_contacts,
  (select count(*) from {{ ref('mart_migration_audit') }} where missing_contact) as missing_contacts,
  (select round(avg(cast(coalesce(owner_match,false) as double)),4) from migrated) as owner_match_rate,
  (select round(avg(cast(coalesce(source_match,false) as double)),4) from migrated) as source_match_rate,
  (select round(avg(cast(coalesce(stage_match,false) as double)),4) from migrated) as stage_match_rate,
  (select duplicate_rows from duplicates) as duplicate_crm_rows
