-- One row per day and campaign, and the daily mart's spend equals total paid spend.
select 'duplicate' as problem, day::varchar as detail from {{ ref('mart_paid_efficiency_daily') }}
group by day, campaign_id having count(*) > 1
union all
select 'spend mismatch', cast(abs(a.total - b.total) as varchar)
from (select sum(spend_cents) as total from {{ ref('mart_paid_efficiency_daily') }}) a,
     (select sum(spend_cents) as total from {{ ref('stg_ad_spend_daily') }}) b
where a.total <> b.total
