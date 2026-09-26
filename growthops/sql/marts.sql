-- Local SQL warehouse reference. These are views over synthetic source tables.
-- Rebuild order matters because later views depend on earlier views.
DROP VIEW IF EXISTS mart_measurement_health;
DROP VIEW IF EXISTS mart_funnel;
DROP VIEW IF EXISTS mart_revenue;
DROP VIEW IF EXISTS mart_campaign_performance;
DROP VIEW IF EXISTS mart_attribution_lead_creation;
DROP VIEW IF EXISTS int_journey_firsts;
DROP VIEW IF EXISTS int_lead_touch;
DROP VIEW IF EXISTS int_payment_cash;
DROP VIEW IF EXISTS stg_touches;
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
)
SELECT c.campaign_id, c.source, c.medium, c.spend_cents,
       COALESCE(l.leads,0) leads, COALESCE(l.mqls,0) mqls,
       COALESCE(cash.net_cash_cents,0) net_cash_cents
FROM stg_campaigns c
LEFT JOIN lead_counts l ON l.campaign_id=c.campaign_id
LEFT JOIN cash_counts cash ON cash.campaign_id=c.campaign_id;

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
