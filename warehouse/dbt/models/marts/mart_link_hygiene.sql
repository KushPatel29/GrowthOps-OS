-- Short links checked against the campaign registry; source and medium must match exactly.
with latest as (
  select max(click_date) as as_of from {{ ref('stg_short_link_clicks') }}
), clicks as (
  select k.link_id, sum(k.clicks) as clicks,
         sum(case when k.click_date > l.as_of - interval 30 day then k.clicks else 0 end) as recent_clicks
  from {{ ref('stg_short_link_clicks') }} k cross join latest l
  group by k.link_id
)
select
  s.link_id,
  s.channel,
  s.utm_source,
  s.utm_medium,
  s.utm_campaign,
  s.utm_source is null or s.utm_medium is null or s.utm_campaign is null as missing_utm,
  s.utm_campaign is not null and c.campaign_id is null as unregistered_campaign,
  c.campaign_id is not null and (s.utm_source <> c.source or s.utm_medium <> c.medium) as off_taxonomy,
  coalesce(k.clicks, 0) as clicks,
  coalesce(k.recent_clicks, 0) as recent_clicks
from {{ ref('stg_short_links') }} s
left join {{ ref('stg_campaigns') }} c on c.campaign_name = s.utm_campaign
left join clicks k on k.link_id = s.link_id
