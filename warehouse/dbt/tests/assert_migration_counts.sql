select * from {{ ref('mart_migration_summary') }}
where legacy_contacts != migrated_contacts + missing_contacts
