"""Export the verified dbt marts as the governed BI snapshot, then regenerate the Power BI project.

``export`` writes one CSV per governed mart plus the three shapes a report needs
and a mart does not carry: a daily date dimension, a campaign dimension and
lead-creation cash attribution at payment grain. ``--refresh-pbip`` also rebuilds
the whole PBIP project from :mod:`growthops.bi` (model, measures, pages, theme),
embedding those CSVs in the import partitions so the project opens with no data
path or credentials. Run after a dbt build so Power BI and Excel carry the same
numbers as the warehouse.
"""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path

MARTS = (
    "mart_growth_daily", "mart_campaign_performance", "mart_funnel", "mart_revenue",
    "mart_content_performance", "mart_measurement_health", "mart_migration_summary",
    "mart_experiment_variants", "mart_renewal_risk", "mart_revenue_bridge", "mart_platform_comparison",
    "mart_paid_efficiency_daily", "mart_email_performance", "mart_link_hygiene",
    "mart_qualified_pipeline", "mart_crm_health_v21",
)
ORDER_BY = {
    "mart_growth_daily": "day",
    "mart_campaign_performance": "net_cash_cents DESC, campaign_id",
    "mart_funnel": "ordinal",
    "mart_content_performance": "influenced_net_cash_cents DESC, content_id",
    "mart_experiment_variants": "variant_id",
    "mart_renewal_risk": "due_date, subscription_id",
    "mart_revenue_bridge": "ordinal",
    "mart_platform_comparison": "platform",
    "mart_paid_efficiency_daily": "day, campaign_id",
    "mart_email_performance": "sent_date, email_id",
    "mart_link_hygiene": "link_id",
    "mart_qualified_pipeline": "campaign_id, currency",
    "mart_crm_health_v21": "component",
}
# Marts that need a readable label or an ordering key for a report axis. The added
# columns go last, so every column the Excel workbook addresses by letter stays put.
FUNNEL_LABELS = {"lead": "Lead", "mql": "MQL", "call_booked": "Call booked", "call_attended": "Call attended",
                 "opportunity": "Opportunity", "closed_won": "Closed won", "paid": "Paid",
                 "activated": "Access activated", "renewed": "Renewed"}
BRIDGE_STEPS = {
    "crm_booked": ("CRM bookings", "Closed-won deal value in the CRM"),
    "duplicate_deals": ("Duplicate deals", "Deals the CRM migration created twice"),
    "not_yet_collected": ("Not yet collected", "Booked, with instalments still to come"),
    "unlinked_payments": ("Unlinked payments", "Cash whose deal link the migration lost"),
    "renewals": ("Renewals", "Subscription renewals, which never pass through a deal"),
    "gross_collected": ("Gross collected", "Payments received"),
    "refunds": ("Refunds", "Money returned"),
    "net_collected": ("Net collected", "Cash the business kept"),
}
RISK_ORDER = {"high": 1, "medium": 2, "not_due": 3}


def _case(column: str, mapping: Mapping[str, object]) -> str:
    def quoted(value: object) -> str:
        return str(value) if isinstance(value, int) else "'" + str(value).replace("'", "''") + "'"
    return f"CASE {column} " + " ".join(f"WHEN '{k}' THEN {quoted(v)}" for k, v in mapping.items()) + " END"


SELECTS = {
    "mart_funnel": f"SELECT *, {_case('stage', FUNNEL_LABELS)} AS stage_label FROM mart_funnel",
    "mart_revenue_bridge": (
        f"SELECT *, {_case('step', {k: v[0] for k, v in BRIDGE_STEPS.items()})} AS step_label, "
        f"{_case('step', {k: v[1] for k, v in BRIDGE_STEPS.items()})} AS note FROM mart_revenue_bridge"),
    "mart_renewal_risk": f"SELECT *, {_case('risk_level', RISK_ORDER)} AS risk_order FROM mart_renewal_risk",
}
UNATTRIBUTED = "(unattributed)"
CHANNEL_GROUPS = {"paid_social": "Paid social", "paid_search": "Paid search", "owned_email": "Owned email",
                  "referral": "Partner", "organic_video": "Organic video", "event": "Webinar",
                  "none": "Direct"}
# BI-only shapes over dbt models. Payment dates are taken in UTC so a build on a
# machine in another time zone produces the same file (the CI drift gate runs in UTC).
EXTRAS = {
    "dim_campaign": f"""
        SELECT campaign_id, campaign_name, platform, source, medium,
               CASE medium {' '.join(f"WHEN '{k}' THEN '{v}'" for k, v in CHANNEL_GROUPS.items())}
                    ELSE medium END AS channel_group,
               coalesce(landing_page, '') AS landing_page, is_paid, registry_valid = 1 AS registry_valid,
               row_number() OVER (ORDER BY is_paid DESC, spend_cents DESC, campaign_id) AS campaign_order
        FROM stg_campaigns
        UNION ALL
        SELECT '{UNATTRIBUTED}', '{UNATTRIBUTED}', 'none', 'none', 'none', 'Unattributed', '', false, false, 99
        ORDER BY campaign_order""",
    "fact_cash_attribution": f"""
        SELECT payment_id, CAST(timezone('UTC', paid_at) AS DATE) AS paid_date,
               coalesce(campaign_id, '{UNATTRIBUTED}') AS campaign_id,
               CAST(gross_cents AS BIGINT) AS gross_cents, CAST(refund_cents AS BIGINT) AS refund_cents,
               CAST(net_cash_cents AS BIGINT) AS net_cash_cents
        FROM mart_attribution_lead_creation ORDER BY paid_date, payment_id""",
    # The single-row health and migration marts, pivoted to one row per check so a
    # report can rank them against a target instead of printing seven lone numbers.
    "quality_scorecard": """
        WITH h AS (SELECT * FROM mart_measurement_health), m AS (SELECT * FROM mart_migration_summary)
        SELECT * FROM (
            SELECT 1 AS check_order, 'Tracking' AS area, 'UTMs present' AS check_name,
                   round(touches_with_utm / eligible_touches, 4) AS rate, 0.95 AS target FROM h
            UNION ALL SELECT 2, 'Tracking', 'Registered campaign',
                   round(registered_touches / eligible_touches, 4), 0.95 FROM h
            UNION ALL SELECT 3, 'CRM', 'Owner assigned', round(owned_contacts / contacts, 4), 0.98 FROM h
            UNION ALL SELECT 4, 'Attribution', 'Buyer has a touch',
                   round(valid_paid_journeys / paid_contact_count, 4), 0.98 FROM h
            UNION ALL SELECT 5, 'Payments', 'Payment matched',
                   round(1 - unmatched_payments / payments, 4), 0.99 FROM h
            UNION ALL SELECT 6, 'Migration', 'Contact migrated',
                   round(migrated_contacts / legacy_contacts, 4), 0.99 FROM m
            UNION ALL SELECT 7, 'Migration', 'Owner kept', round(owner_match_rate, 4), 0.98 FROM m
            UNION ALL SELECT 8, 'Migration', 'Source kept', round(source_match_rate, 4), 0.98 FROM m
            UNION ALL SELECT 9, 'Migration', 'Stage kept', round(stage_match_rate, 4), 0.98 FROM m
            UNION ALL SELECT 10, 'CRM v2.1', 'Actionable owner',
                   round(passed_records / nullif(eligible, 0), 4), 0.98
                   FROM mart_crm_health_v21 WHERE component='actionable_owner'
            UNION ALL SELECT 11, 'CRM v2.1', 'Source present',
                   round(passed_records / nullif(eligible, 0), 4), 0.95
                   FROM mart_crm_health_v21 WHERE component='source_present'
            UNION ALL SELECT 12, 'CRM v2.1', 'Unique email candidate',
                   round(passed_records / nullif(eligible, 0), 4), 0.99
                   FROM mart_crm_health_v21 WHERE component='unique_email'
            UNION ALL SELECT 13, 'CRM v2.1', 'Deal campaign',
                   round(passed_records / nullif(eligible, 0), 4), 0.95
                   FROM mart_crm_health_v21 WHERE component='deal_campaign'
        ) ORDER BY check_order""",
    "incident_register": """
        SELECT incident_id, kind, CAST(substr(starts_at, 1, 10) AS DATE) AS started_on,
               CASE WHEN ends_at IS NULL OR ends_at = '' THEN NULL
                    ELSE CAST(substr(ends_at, 1, 10) AS DATE) END AS ended_on,
               entity, expected_signal AS signal, description
        FROM raw_incidents ORDER BY started_on, incident_id""",
}
# Tables with a date column the report filters by, and that column.
DATE_KEYS = {"mart_growth_daily": "day", "mart_paid_efficiency_daily": "day",
             "mart_email_performance": "sent_date", "fact_cash_attribution": "paid_date"}
TABLES = (*MARTS, *EXTRAS, "dim_date")


def _scalar(connection, sql: str):
    """The single value a query returns; a query that returns no row is a broken export, not a None."""
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"no row from: {sql}")
    return row[0]


def _write(path: Path, header: list[str], rows: list) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def date_dimension(first: date, as_of: date) -> tuple[list[str], list[list]]:
    """One row per day from the first data day to the as-of day (the last complete data day).

    ``days_before_as_of`` lets every "last 28 days" measure read a column instead of
    re-deriving the as-of date, and ``month_index`` / ``week_index`` are contiguous so a
    prior period is a subtraction rather than calendar arithmetic.
    """
    header = ["date", "year", "quarter_label", "month_start", "month_label", "month_index", "week_start",
              "week_index", "day_name", "day_of_week", "days_before_as_of", "is_last_28_days",
              "is_prior_28_days"]
    first_month = first.replace(day=1)
    first_week = first - timedelta(days=first.weekday())
    rows = []
    day = first
    while day <= as_of:
        back = (as_of - day).days
        week = day - timedelta(days=day.weekday())
        rows.append([
            day.isoformat(), day.year, f"Q{(day.month - 1) // 3 + 1} {day.year}", day.replace(day=1).isoformat(),
            day.strftime("%b %Y"), (day.year - first_month.year) * 12 + day.month - first_month.month,
            week.isoformat(), (week - first_week).days // 7, day.strftime("%a"), day.weekday() + 1, back,
            back < 28, 28 <= back < 56,
        ])
        day += timedelta(days=1)
    return header, rows


def export(warehouse_database: str, output: str = "data/powerbi") -> dict[str, int]:
    import duckdb  # Installed with the optional warehouse dependency.

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(warehouse_database, read_only=True)
    connection.execute("SET TimeZone = 'UTC'")  # same day boundaries on every machine
    try:
        counts = {}
        # Stage the entire snapshot before replacing versioned CSVs. A missing
        # dbt mart must never leave a half-refreshed dashboard data directory.
        with tempfile.TemporaryDirectory(prefix=".growthops-bi-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            queries = {mart: f"SELECT * FROM ({SELECTS.get(mart, 'SELECT * FROM ' + mart)})"
                             + (f" ORDER BY {ORDER_BY[mart]}" if mart in ORDER_BY else "")
                       for mart in MARTS}
            queries.update(EXTRAS)
            for name, query in queries.items():
                result = connection.execute(query)
                columns = [item[0] for item in result.description]
                rows = result.fetchall()
                _write(staging / f"{name}.csv", columns, rows)
                counts[name] = len(rows)
            # The calendar starts at the earliest date in ANY date-keyed table: a fact
            # row dated before it joins to nothing and shows up as a "(Blank)" month.
            # It ends at the last complete day of cash, which every window ends on.
            starts = " UNION ALL ".join(f"SELECT min(CAST({column} AS DATE)) AS d FROM staged_{table}"
                                        for table, column in DATE_KEYS.items())
            for table in DATE_KEYS:
                connection.execute(f"CREATE OR REPLACE TEMP VIEW staged_{table} AS "
                                   f"SELECT * FROM read_csv_auto('{(staging / f'{table}.csv').as_posix()}')")
            first = _scalar(connection, f"SELECT min(d) FROM ({starts})")
            as_of = _scalar(connection, "SELECT max(day) FROM mart_growth_daily")
            header, days = date_dimension(first, as_of)
            _write(staging / "dim_date.csv", header, days)
            counts["dim_date"] = len(days)
            destination.mkdir(parents=True, exist_ok=True)
            for name in counts:
                os.replace(staging / f"{name}.csv", destination / f"{name}.csv")
        return counts
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", default="data/growthops-warehouse.duckdb")
    parser.add_argument("--output", default="data/powerbi")
    parser.add_argument("--refresh-pbip", action="store_true",
                        help="write the versioned CSVs and regenerate the PBIP project from them")
    args = parser.parse_args()
    output = "dashboards/powerbi-data" if args.refresh_pbip else args.output
    print(export(args.warehouse, output))
    if args.refresh_pbip:
        from growthops.bi.build_pbip import main as build_pbip

        build_pbip([])


if __name__ == "__main__":
    main()
