-- Paid campaign activity by UTC day, the grain Excel and Power BI need to compute CPL, cost per MQL,
-- cost per booked call, CPM, CTR and CPC for any date range. Activity basis: each event is dated when it
-- happened and credited to the campaign that created the lead (see growthops/performance.py).
with owner as (
  select contact_id, campaign_id from {{ ref('int_lead_touch') }} where campaign_id is not null
), paid as (
  select campaign_id, platform from {{ ref('stg_campaigns') }} where is_paid = 1
), spend as (
  select spend_date as day, campaign_id, sum(spend_cents) as spend_cents,
         sum(impressions) as impressions, sum(clicks) as clicks
  from {{ ref('stg_ad_spend_daily') }} group by 1, 2
), stages as (
  select cast(e.occurred_at at time zone 'UTC' as date) as day, o.campaign_id,
         count(*) filter (where e.stage = 'lead') as leads,
         count(*) filter (where e.stage = 'mql') as mqls,
         count(*) filter (where e.stage = 'call_booked') as calls_booked
  from {{ ref('stg_lifecycle_events') }} e join owner o on o.contact_id = e.contact_id
  group by 1, 2
), won as (
  select cast(d.closed_at at time zone 'UTC' as date) as day, o.campaign_id, count(*) as closed_won_deals
  from {{ ref('stg_deals') }} d join owner o on o.contact_id = d.contact_id
  where d.stage = 'closed_won' and coalesce(d.product_id, '') <> 'community'
  group by 1, 2
), keys as (
  select day, campaign_id from spend
  union select day, campaign_id from stages
  union select day, campaign_id from won
)
select
  k.day,
  k.campaign_id,
  p.platform,
  coalesce(s.spend_cents, 0) as spend_cents,
  coalesce(s.impressions, 0) as impressions,
  coalesce(s.clicks, 0) as clicks,
  coalesce(st.leads, 0) as leads,
  coalesce(st.mqls, 0) as mqls,
  coalesce(st.calls_booked, 0) as calls_booked,
  coalesce(w.closed_won_deals, 0) as closed_won_deals
from keys k
join paid p on p.campaign_id = k.campaign_id
left join spend s on s.day = k.day and s.campaign_id = k.campaign_id
left join stages st on st.day = k.day and st.campaign_id = k.campaign_id
left join won w on w.day = k.day and w.campaign_id = k.campaign_id
