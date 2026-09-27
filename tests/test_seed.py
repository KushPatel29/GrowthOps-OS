import hashlib
from datetime import datetime

from growthops import scenario as sc
from growthops.db import connect
from growthops.seed import seed


def _fingerprint(path) -> str:
    handle = connect(path)
    rows = handle.execute("SELECT * FROM payments ORDER BY payment_id").fetchall()
    handle.close()
    return hashlib.sha256(repr([tuple(row) for row in rows]).encode()).hexdigest()


def test_generator_is_deterministic(tmp_path):
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    assert seed(str(first), scale=0.2) == seed(str(second), scale=0.2)
    assert _fingerprint(first) == _fingerprint(second)
    assert seed(str(first), scale=0.2, seed_value=7) != seed(str(second), scale=0.2)


def test_scenario_is_realistic_and_internally_consistent(connection):
    one = lambda sql: connection.execute(sql).fetchone()[0]
    assert one("SELECT COUNT(*) FROM contacts") > 10_000
    assert one("SELECT COUNT(DISTINCT customer_id) FROM payments WHERE status='succeeded'") > 300
    # No campaign looks like a copy of another: lead volumes differ.
    leads = [row[0] for row in connection.execute(
        "SELECT COUNT(*) FROM touches WHERE touch_type='lead_creation' AND campaign_id IS NOT NULL GROUP BY campaign_id")]
    assert len(set(leads)) == len(leads)
    # Planted quality incident: the broad campaign qualifies far worse than the rest.
    rates = dict(connection.execute(
        """SELECT t.sim_true_campaign_id, AVG(EXISTS (SELECT 1 FROM lifecycle_events e
                  WHERE e.contact_id=t.contact_id AND e.stage='mql'))
           FROM touches t WHERE t.touch_type='lead_creation' GROUP BY 1""").fetchall())
    assert rates["meta_broad_v17"] < 0.1 < min(v for k, v in rates.items() if k != "meta_broad_v17")
    # Nothing happens after the data cut-off; refunds never exceed their payment.
    end = sc.as_of_datetime()
    for table, column in (("touches", "occurred_at"), ("lifecycle_events", "occurred_at"),
                          ("payments", "paid_at"), ("refunds", "refunded_at")):
        latest = one(f"SELECT MAX({column}) FROM {table}")
        assert datetime.fromisoformat(latest) <= end, table
    assert one("""SELECT COUNT(*) FROM refunds r JOIN payments p ON p.payment_id=r.payment_id
                  WHERE r.amount_cents > p.amount_cents""") == 0
    # Registry spend equals the sum of daily spend for every campaign.
    assert one("""SELECT COUNT(*) FROM campaigns c WHERE c.spend_cents !=
                  (SELECT COALESCE(SUM(spend_cents),0) FROM ad_spend_daily s WHERE s.campaign_id=c.campaign_id)""") == 0
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
