select
  (select sum(influenced_net_cash_cents) from {{ ref('mart_content_performance') }}) as content_cash,
  (select net_collected_cents from {{ ref('mart_revenue') }}) as total_cash
where content_cash > total_cash
