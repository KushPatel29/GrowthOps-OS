with stages(stage, ordinal) as (values
  ('lead', 1), ('mql', 2), ('call_booked', 3), ('call_attended', 4),
  ('opportunity', 5), ('closed_won', 6), ('paid', 7), ('activated', 8), ('renewed', 9)
), person_stage as (
  select contact_id, stage, min(occurred_at) as first_at
  from {{ ref('stg_lifecycle_events') }}
  group by contact_id, stage
), counts as (
  select s.stage, count(p.contact_id) as people
  from stages s left join person_stage p on p.stage = s.stage
  group by s.stage
), transitions as (
  select
    cur.stage,
    count(distinct prior.contact_id) as prior_people,
    count(distinct case when current.contact_id is not null then prior.contact_id end) as progressed
  from stages cur
  left join stages prev on prev.ordinal = cur.ordinal - 1
  left join person_stage prior on prior.stage = prev.stage
  left join person_stage current
    on current.stage = cur.stage and current.contact_id = prior.contact_id
  group by cur.stage
)
select
  s.stage,
  s.ordinal,
  c.people,
  case when t.prior_people = 0 then null
    else round(cast(t.progressed as double) / t.prior_people, 4) end as from_previous_rate
from stages s
join counts c on c.stage = s.stage
join transitions t on t.stage = s.stage
