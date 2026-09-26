select c.campaign_id, c.spend_cents as registry_spend,
       coalesce(sum(s.spend_cents),0) as daily_spend
from {{ ref('stg_campaigns') }} c
left join {{ ref('stg_ad_spend_daily') }} s on s.campaign_id=c.campaign_id
group by c.campaign_id, c.spend_cents
having c.spend_cents != coalesce(sum(s.spend_cents),0)
