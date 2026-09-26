-- Deltas between consecutive totals must land exactly on the next total.
with steps as (select * from {{ ref('mart_revenue_bridge') }})
select 'gross' as checkpoint
from steps
having sum(case when ordinal <= 5 then cents end) != max(case when step = 'gross_collected' then cents end)
union all
select 'net'
from steps
having max(case when step = 'gross_collected' then cents end) + max(case when step = 'refunds' then cents end)
       != max(case when step = 'net_collected' then cents end)
