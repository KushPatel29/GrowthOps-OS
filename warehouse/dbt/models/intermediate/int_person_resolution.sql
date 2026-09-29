select
  p.person_key,
  p.created_at,
  p.resolution_version,
  p.status,
  count(l.id_hash) as linked_identifiers,
  count(case when l.confidence = 'verified' and l.state = 'active' then 1 end)
    as verified_identifiers
from {{ ref('stg_persons') }} p
left join {{ ref('stg_identity_links') }} l on l.person_key = p.person_key
group by p.person_key, p.created_at, p.resolution_version, p.status
