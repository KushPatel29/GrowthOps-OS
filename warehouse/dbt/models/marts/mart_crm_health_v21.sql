with contacts as (
  select count(*) total,
         count(case when original_source is not null then 1 end) with_source,
         count(case when current_stage in ('lead','mql','opportunity') then 1 end) actionable,
         count(case when current_stage in ('lead','mql','opportunity')
                         and owner_id is not null then 1 end) actionable_owned
  from {{ ref('stg_contacts') }}
), deals as (
  select count(*) total from {{ ref('stg_deals') }}
), issues as (
  select
    count(case when rule_id='duplicate_email_candidate' and state='open' then 1 end) duplicate_contacts,
    count(case when rule_id='deal_missing_lead_campaign' and state='open' then 1 end) deals_missing_campaign
  from {{ ref('stg_quality_issues') }}
)
select 'actionable_owner' component, c.actionable_owned passed_records, c.actionable eligible
from contacts c
union all
select 'source_present', c.with_source, c.total from contacts c
union all
select 'unique_email', c.total - i.duplicate_contacts, c.total
from contacts c cross join issues i
union all
select 'deal_campaign', d.total - i.deals_missing_campaign, d.total
from deals d cross join issues i
