"""Reproducible synthetic post-migration scenario; never uses real people."""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from growthops.db import connect, initialize


def seed(database: str, people: int = 240, seed_value: int = 29) -> None:
    rng = random.Random(seed_value)
    connection = connect(database)
    initialize(connection)
    connection.isolation_level = "DEFERRED"
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    channels = [
        ("meta-founder", "meta", "paid_social", 620000, 1),
        ("google-growth", "google", "paid_search", 530000, 1),
        ("youtube-guide", "youtube", "organic_video", 0, 1),
        ("newsletter", "newsletter", "owned_email", 0, 1),
        # Paid spend is retained even when the source/campaign name fails governance.
        ("FB-Broad", "FB", "paid_social", 190000, 0),
        ("direct", "direct", "none", 0, 1),
    ]
    with connection:
        connection.executemany(
            "INSERT OR IGNORE INTO campaigns VALUES (?, ?, ?, ?, ?, ?)",
            [(key, src, medium, key.replace("-", "_"), spend, valid)
             for key, src, medium, spend, valid in channels],
        )
        for index in range(people):
            contact_id = f"c-{index:05d}"
            campaign = channels[index % 5]
            source = None if index % 11 == 0 else campaign[1]
            owner = None if index % 17 == 0 else f"owner-{index % 6}"
            # A repeated synthetic email reproduces unresolved migration duplicates.
            email_id = index - 1 if index > 0 and index % 23 == 0 else index
            stage = "customer" if index % 8 == 0 else "lead"
            connection.execute(
                "INSERT OR IGNORE INTO contacts VALUES (?, ?, ?, ?, ?, ?)",
                (contact_id, f"person{email_id}@synthetic.scalelab.test", f"legacy-{index}", owner, source, stage),
            )
            acquired = now - timedelta(days=rng.randrange(1, 85), hours=index % 24)
            connection.execute(
                "INSERT OR IGNORE INTO touches VALUES (?, ?, ?, ?, ?, ?)",
                (f"t-{index:05d}", contact_id, campaign[0], acquired.isoformat(), "lead_creation", source),
            )
            if index % 4 == 0:
                discovery = channels[(index + 1) % 5]
                connection.execute(
                    "INSERT OR IGNORE INTO touches VALUES (?, ?, ?, ?, ?, ?)",
                    (f"t-{index:05d}-discovery", contact_id, discovery[0],
                     (acquired - timedelta(days=7)).isoformat(), "content_visit", discovery[1]),
                )
            if index % 5 == 0:
                connection.execute(
                    "INSERT OR IGNORE INTO touches VALUES (?, ?, ?, ?, ?, ?)",
                    (f"t-{index:05d}-return", contact_id, "direct",
                     (acquired + timedelta(days=3)).isoformat(), "site_visit", None),
                )
            if index % 7 == 0:
                return_campaign = channels[(index + 2) % 5]
                connection.execute(
                    "INSERT OR IGNORE INTO touches VALUES (?, ?, ?, ?, ?, ?)",
                    (f"t-{index:05d}-retarget", contact_id, return_campaign[0],
                     (acquired + timedelta(days=5)).isoformat(), "site_visit", return_campaign[1]),
                )
            connection.execute(
                "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                (f"le-{index:05d}-lead", contact_id, "lead", acquired.isoformat()),
            )
            if index % 3 == 0 or index % 8 == 0:
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-mql", contact_id, "mql", (acquired + timedelta(days=2)).isoformat()),
                )
            if index % 6 == 0 or index % 8 == 0:
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-booked", contact_id, "call_booked", (acquired + timedelta(days=4)).isoformat()),
                )
            if index % 12 == 0 or index % 8 == 0:
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-attended", contact_id, "call_attended", (acquired + timedelta(days=6)).isoformat()),
                )
                if index % 80 != 0:  # Deliberate CRM stage gap for the quality monitor.
                    connection.execute(
                        "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                        (f"le-{index:05d}-opp", contact_id, "opportunity", (acquired + timedelta(days=7)).isoformat()),
                    )
            if index % 8 == 0:
                amount = 45000 + (index % 5) * 10000
                deal_id = f"d-{index:05d}"
                paid_at = (acquired + timedelta(days=12)).isoformat()
                connection.execute(
                    "INSERT OR IGNORE INTO deals VALUES (?, ?, ?, 'closed_won', ?)",
                    (deal_id, contact_id, amount, paid_at),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-won", contact_id, "closed_won", paid_at),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-paid", contact_id, "paid", paid_at),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                    (f"le-{index:05d}-activated", contact_id, "activated", (acquired + timedelta(days=13)).isoformat()),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO access_entitlements VALUES (?, 'active', ?, ?)",
                    (contact_id, (acquired + timedelta(days=13)).isoformat(), f"seed-{index}"),
                )
                if index % 40 == 0:
                    connection.execute(
                        "INSERT OR IGNORE INTO lifecycle_events VALUES (?, ?, ?, ?)",
                        (f"le-{index:05d}-renewed", contact_id, "renewed", (acquired + timedelta(days=45)).isoformat()),
                    )
                # One unmatched payment reproduces a migration reconciliation gap.
                payment_deal = None if index == 0 else deal_id
                connection.execute(
                    "INSERT OR IGNORE INTO payments VALUES (?, ?, ?, ?, 'succeeded', ?)",
                    (f"p-{index:05d}", payment_deal, contact_id, amount, paid_at),
                )
                if index % 40 == 0:
                    connection.execute(
                        "INSERT OR IGNORE INTO refunds VALUES (?, ?, ?, ?)",
                        (f"r-{index:05d}", f"p-{index:05d}", 10000, (acquired + timedelta(days=20)).isoformat()),
                    )
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    parser.add_argument("--people", type=int, default=240)
    args = parser.parse_args()
    seed(args.database, args.people)
    print(f"Seeded {args.people} synthetic contacts in {args.database}")


if __name__ == "__main__":
    main()
