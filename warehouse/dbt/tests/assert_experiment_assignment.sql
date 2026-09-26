select experiment_id, visitor_id
from {{ ref('stg_experiment_exposures') }}
group by experiment_id, visitor_id
having count(*) != 1
union all
select e.experiment_id, e.visitor_id
from {{ ref('stg_experiment_exposures') }} e
left join {{ ref('stg_experiment_variants') }} v
  on e.variant_id=v.variant_id and e.experiment_id=v.experiment_id
where v.variant_id is null
