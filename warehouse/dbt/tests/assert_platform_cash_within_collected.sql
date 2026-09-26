-- Warehouse credit per platform can never exceed total net collected cash.
select p.platform
from {{ ref('mart_platform_comparison') }} p
cross join {{ ref('mart_revenue') }} r
where p.warehouse_net_cash_cents > r.net_collected_cents or p.warehouse_net_cash_cents < 0
