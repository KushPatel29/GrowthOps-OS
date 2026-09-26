-- Local SQL warehouse reference. These are views over synthetic source tables.
-- Rebuild order matters because later views depend on earlier views.
DROP VIEW IF EXISTS mart_measurement_health;
DROP VIEW IF EXISTS mart_growth_daily;
DROP VIEW IF EXISTS mart_content_performance;
DROP VIEW IF EXISTS mart_funnel;
DROP VIEW IF EXISTS mart_revenue;
DROP VIEW IF EXISTS mart_campaign_performance;
DROP VIEW IF EXISTS mart_attribution_lead_creation;
DROP VIEW IF EXISTS int_journey_firsts;
DROP VIEW IF EXISTS int_lead_touch;
DROP VIEW IF EXISTS int_payment_cash;
DROP VIEW IF EXISTS stg_touches;
DROP VIEW IF EXISTS stg_ad_spend_daily;
DROP VIEW IF EXISTS stg_content_engagements;
DROP VIEW IF EXISTS stg_content_items;
DROP VIEW IF EXISTS stg_campaigns;
DROP VIEW IF EXISTS stg_contacts;

CREATE VIEW stg_contacts AS
SELECT contact_id, LOWER(TRIM(email)) email_normalized, legacy_id, owner_id,
       original_source, current_stage,
       CASE WHEN owner_id IS NOT NULL THEN 1 ELSE 0 END has_owner
FROM contacts;

CREATE VIEW stg_campaigns AS
SELECT campaign_id, source, medium, campaign_name, spend_cents, registry_valid,
       CASE WHEN medium IN ('paid_social','paid_search') THEN 1 ELSE 0 END is_paid
FROM campaigns;

CREATE VIEW stg_ad_spend_daily AS
SELECT campaign_id, spend_date, spend_cents, impressions, clicks
FROM ad_spend_daily;

CREATE VIEW stg_content_items AS
SELECT content_id, title, platform, published_at, views, clicks, offer_id
FROM content_items;

CREATE VIEW stg_content_engagements AS
SELECT engagement_id, contact_id, content_id, touch_id, occurred_at, watched_seconds
FROM content_engagements;

CREATE VIEW stg_touches AS
SELECT t.touch_id, t.contact_id, t.campaign_id, t.occurred_at, t.touch_type,
       t.utm_source, c.source, c.medium, COALESCE(c.registry_valid,0) registry_valid,
       CASE WHEN t.campaign_id='direct' THEN 0 ELSE 1 END eligible_acquisition,
       CASE WHEN t.utm_source IS NOT NULL AND TRIM(t.utm_source)<>'' THEN 1 ELSE 0 END has_utm_source
FROM touches t LEFT JOIN stg_campaigns c ON c.campaign_id=t.campaign_id;

CREATE VIEW int_payment_cash AS
WITH refund_totals AS (
  SELECT payment_id, SUM(amount_cents) refund_cents FROM refunds GROUP BY payment_id
)
SELECT p.payment_id, p.customer_id, p.deal_id, p.paid_at,
       p.amount_cents gross_cents, COALESCE(r.refund_cents,0) refund_cents,
       p.amount_cents-COALESCE(r.refund_cents,0) net_cash_cents
FROM payments p LEFT JOIN refund_totals r ON r.payment_id=p.payment_id
WHERE p.status='succeeded';

CREATE VIEW int_lead_touch AS
WITH ranked AS (
  SELECT touch_id, contact_id, campaign_id, occurred_at,
         ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at DESC, touch_id DESC) rn
  FROM stg_touches WHERE touch_type='lead_creation'
)
SELECT touch_id, contact_id, campaign_id, occurred_at FROM ranked WHERE rn=1;

CREATE VIEW int_journey_firsts AS
SELECT contact_id,
       MIN(CASE WHEN stage='lead' THEN occurred_at END) lead_at,
       MIN(CASE WHEN stage='mql' THEN occurred_at END) mql_at,
       MIN(CASE WHEN stage='call_booked' THEN occurred_at END) booked_at,
       MIN(CASE WHEN stage='call_attended' THEN occurred_at END) attended_at,
       MIN(CASE WHEN stage='opportunity' THEN occurred_at END) opportunity_at,
       MIN(CASE WHEN stage='closed_won' THEN occurred_at END) won_at,
       MIN(CASE WHEN stage='paid' THEN occurred_at END) paid_at
FROM lifecycle_events GROUP BY contact_id;

CREATE VIEW mart_attribution_lead_creation AS
WITH ranked AS (
  SELECT p.payment_id, t.touch_id, t.campaign_id,
         ROW_NUMBER() OVER (PARTITION BY p.payment_id ORDER BY t.occurred_at DESC, t.touch_id DESC) rn
  FROM int_payment_cash p
  LEFT JOIN stg_touches t ON t.contact_id=p.customer_id
    AND t.touch_type='lead_creation' AND t.occurred_at<=p.paid_at
)
SELECT p.payment_id, p.customer_id, r.touch_id, r.campaign_id,
       p.gross_cents, p.refund_cents, p.net_cash_cents
FROM int_payment_cash p LEFT JOIN ranked r ON r.payment_id=p.payment_id AND r.rn=1;

CREATE VIEW mart_campaign_performance AS
WITH mql_people AS (
  SELECT DISTINCT contact_id FROM lifecycle_events WHERE stage='mql'
), lead_counts AS (
  SELECT l.campaign_id, COUNT(*) leads, SUM(CASE WHEN m.contact_id IS NOT NULL THEN 1 ELSE 0 END) mqls
  FROM int_lead_touch l LEFT JOIN mql_people m ON m.contact_id=l.contact_id
  GROUP BY l.campaign_id
), cash_counts AS (
  SELECT campaign_id, SUM(net_cash_cents) net_cash_cents
  FROM mart_attribution_lead_creation GROUP BY campaign_id
), spend_counts AS (
  SELECT campaign_id, SUM(spend_cents) spend_cents
  FROM stg_ad_spend_daily GROUP BY campaign_id
)
SELECT c.campaign_id, c.source, c.medium, COALESCE(s.spend_cents,0) spend_cents,
       COALESCE(l.leads,0) leads, COALESCE(l.mqls,0) mqls,
       COALESCE(cash.net_cash_cents,0) net_cash_cents
FROM stg_campaigns c
LEFT JOIN spend_counts s ON s.campaign_id=c.campaign_id
LEFT JOIN lead_counts l ON l.campaign_id=c.campaign_id
LEFT JOIN cash_counts cash ON cash.campaign_id=c.campaign_id;

CREATE VIEW mart_growth_daily AS
WITH dates AS (
  SELECT spend_date day FROM stg_ad_spend_daily
  UNION SELECT SUBSTR(occurred_at,1,10) FROM lifecycle_events WHERE stage IN ('lead','mql','call_booked')
  UNION SELECT SUBSTR(closed_at,1,10) FROM deals WHERE stage='closed_won'
  UNION SELECT SUBSTR(paid_at,1,10) FROM payments WHERE status='succeeded'
  UNION SELECT SUBSTR(refunded_at,1,10) FROM refunds
), spend AS (
  SELECT spend_date day, SUM(spend_cents) spend_cents FROM stg_ad_spend_daily GROUP BY spend_date
), stages AS (
  SELECT SUBSTR(occurred_at,1,10) day,
    COUNT(DISTINCT CASE WHEN stage='lead' THEN contact_id END) leads,
    COUNT(DISTINCT CASE WHEN stage='mql' THEN contact_id END) mqls,
    COUNT(DISTINCT CASE WHEN stage='call_booked' THEN contact_id END) calls_booked
  FROM lifecycle_events GROUP BY SUBSTR(occurred_at,1,10)
), booked AS (
  SELECT SUBSTR(closed_at,1,10) day, SUM(amount_cents) booked_cents
  FROM deals WHERE stage='closed_won' GROUP BY SUBSTR(closed_at,1,10)
), gross AS (
  SELECT SUBSTR(paid_at,1,10) day, SUM(amount_cents) gross_collected_cents
  FROM payments WHERE status='succeeded' GROUP BY SUBSTR(paid_at,1,10)
), refunded AS (
  SELECT SUBSTR(refunded_at,1,10) day, SUM(amount_cents) refunds_cents
  FROM refunds GROUP BY SUBSTR(refunded_at,1,10)
)
SELECT d.day,
  COALESCE(s.spend_cents,0) spend_cents,
  COALESCE(st.leads,0) leads,
  COALESCE(st.mqls,0) mqls,
  COALESCE(st.calls_booked,0) calls_booked,
  COALESCE(b.booked_cents,0) booked_cents,
  COALESCE(g.gross_collected_cents,0) gross_collected_cents,
  COALESCE(r.refunds_cents,0) refunds_cents,
  COALESCE(g.gross_collected_cents,0)-COALESCE(r.refunds_cents,0) net_cash_cents
FROM dates d
LEFT JOIN spend s ON s.day=d.day
LEFT JOIN stages st ON st.day=d.day
LEFT JOIN booked b ON b.day=d.day
LEFT JOIN gross g ON g.day=d.day
LEFT JOIN refunded r ON r.day=d.day;

CREATE VIEW mart_content_performance AS
WITH first_content AS (
  SELECT engagement_id, contact_id, content_id,
         ROW_NUMBER() OVER (PARTITION BY contact_id ORDER BY occurred_at, engagement_id) rn
  FROM stg_content_engagements
), mql_people AS (
  SELECT DISTINCT contact_id FROM lifecycle_events WHERE stage='mql'
), booked_people AS (
  SELECT DISTINCT contact_id FROM lifecycle_events WHERE stage='call_booked'
), cash_by_contact AS (
  SELECT customer_id, SUM(net_cash_cents) net_cash_cents
  FROM int_payment_cash GROUP BY customer_id
)
SELECT ci.content_id, ci.title, ci.platform, ci.views, ci.clicks,
       COUNT(e.contact_id) engaged_leads,
       COUNT(m.contact_id) mqls,
       COUNT(b.contact_id) calls_booked,
       COUNT(cash.customer_id) customers,
       COALESCE(SUM(cash.net_cash_cents),0) influenced_net_cash_cents
FROM stg_content_items ci
LEFT JOIN first_content e ON e.content_id=ci.content_id AND e.rn=1
LEFT JOIN mql_people m ON m.contact_id=e.contact_id
LEFT JOIN booked_people b ON b.contact_id=e.contact_id
LEFT JOIN cash_by_contact cash ON cash.customer_id=e.contact_id
GROUP BY ci.content_id, ci.title, ci.platform, ci.views, ci.clicks;

CREATE VIEW mart_revenue AS
SELECT (SELECT COALESCE(SUM(amount_cents),0) FROM deals WHERE stage='closed_won') booked_cents,
       COALESCE(SUM(gross_cents),0) gross_collected_cents,
       COALESCE(SUM(refund_cents),0) refunds_cents,
       COALESCE(SUM(net_cash_cents),0) net_collected_cents
FROM int_payment_cash;

CREATE VIEW mart_funnel AS
WITH stages(stage, ordinal) AS (VALUES
  ('lead',1),('mql',2),('call_booked',3),('call_attended',4),
  ('opportunity',5),('closed_won',6),('paid',7),('activated',8),('renewed',9)
), person_stage AS (
  SELECT contact_id, stage, MIN(occurred_at) first_at
  FROM lifecycle_events GROUP BY contact_id, stage
), counts AS (
  SELECT s.stage, COUNT(p.contact_id) people
  FROM stages s LEFT JOIN person_stage p ON p.stage=s.stage GROUP BY s.stage
), transitions AS (
  SELECT cur.stage,
         COUNT(DISTINCT prior.contact_id) prior_people,
         COUNT(DISTINCT CASE WHEN current.contact_id IS NOT NULL THEN prior.contact_id END) progressed
  FROM stages cur LEFT JOIN stages prev ON prev.ordinal=cur.ordinal-1
  LEFT JOIN person_stage prior ON prior.stage=prev.stage
  LEFT JOIN person_stage current ON current.stage=cur.stage AND current.contact_id=prior.contact_id
  GROUP BY cur.stage
)
SELECT s.stage, s.ordinal, c.people,
       CASE WHEN t.prior_people=0 THEN NULL
            ELSE ROUND(CAST(t.progressed AS REAL)/t.prior_people,4) END from_previous_rate
FROM stages s JOIN counts c ON c.stage=s.stage JOIN transitions t ON t.stage=s.stage;

CREATE VIEW mart_measurement_health AS
WITH eligible AS (
  SELECT * FROM stg_touches WHERE eligible_acquisition=1
), contact_duplicates AS (
  SELECT COALESCE(SUM(n-1),0) duplicate_rows FROM (
    SELECT COUNT(*) n FROM stg_contacts GROUP BY email_normalized HAVING COUNT(*)>1
  )
), paid_journeys AS (
  SELECT *, CASE WHEN lead_at IS NOT NULL AND mql_at IS NOT NULL AND booked_at IS NOT NULL
      AND attended_at IS NOT NULL AND opportunity_at IS NOT NULL AND won_at IS NOT NULL
      AND lead_at<=mql_at AND mql_at<=booked_at AND booked_at<=attended_at
      AND attended_at<=opportunity_at AND opportunity_at<=won_at AND won_at<=paid_at
    THEN 1 ELSE 0 END valid_journey
  FROM int_journey_firsts WHERE paid_at IS NOT NULL
)
SELECT (SELECT COUNT(*) FROM eligible) eligible_touches,
       (SELECT COUNT(*) FROM eligible WHERE has_utm_source=1) touches_with_utm,
       (SELECT COUNT(*) FROM eligible WHERE registry_valid=1) registered_touches,
       (SELECT COUNT(*) FROM stg_contacts WHERE has_owner=1) owned_contacts,
       (SELECT COUNT(*) FROM stg_contacts) contacts,
       (SELECT duplicate_rows FROM contact_duplicates) duplicate_contact_rows,
       (SELECT COUNT(*) FROM int_payment_cash WHERE deal_id IS NULL OR deal_id NOT IN (SELECT deal_id FROM deals)) unmatched_payments,
       (SELECT COUNT(*) FROM int_payment_cash) payments,
       (SELECT COUNT(*) FROM paid_journeys) paid_contact_count,
       (SELECT COALESCE(SUM(valid_journey),0) FROM paid_journeys) valid_paid_journeys;
