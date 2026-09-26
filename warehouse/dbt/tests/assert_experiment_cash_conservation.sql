select e.experiment_id
from {{ ref('stg_experiments') }} e
cross join {{ ref('mart_revenue') }} r
left join (
  select experiment_id, sum(net_cash_cents) as attributed_cash
  from {{ ref('mart_experiment_variants') }} group by experiment_id
) v on v.experiment_id=e.experiment_id
where coalesce(v.attributed_cash,0) > r.net_collected_cents
