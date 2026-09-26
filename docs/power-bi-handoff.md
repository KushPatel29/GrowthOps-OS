# Power BI import handoff

## Editable project

Open [GrowthOpsOS.pbip](../dashboards/powerbi-project/GrowthOpsOS.pbip) in Power BI Desktop. The project contains four PBIR pages: Executive Pulse (four cards and two charts), Acquisition (four KPI cards, campaign bar, and detail table), Funnel and Content (two charts and two tables), and Quality and Lifecycle (four KPI cards and four tables). Its TMDL semantic model embeds fourteen versioned synthetic marts (including `mart_revenue_bridge`, `mart_platform_comparison`, `mart_paid_efficiency_daily`, `mart_email_performance` and `mart_link_hygiene`), so it has no local CSV path or credentials to configure. A calculated `dim_date` (Date, Month, Week start) is related many-to-one to `mart_growth_daily.day`, `mart_paid_efficiency_daily.day` and `mart_email_performance.sent_date`, so one date slicer filters daily cash, paid efficiency and email together. Seventeen generated measures carry descriptions, format strings and display folders: **Paid efficiency** (Paid spend USD, Paid leads, CPL USD, Cost per MQL USD, Cost per booked call USD, CPM USD, CTR, CPC USD), **Email** (Emails delivered, Human open rate, Reported open rate, Email click rate, Click-to-open rate, Bounce rate, Complaint rate) and **Tracking** (Links with defects, Recent clicks on defective links share). The embedded data is regenerated from the verified dbt marts by `python -m growthops.export_bi --refresh-pbip`, which keeps every hand-authored measure; CI fails if the committed project or the Excel workbook is stale. All 31 report JSON files, `.pbip`, `.pbir`, and `.platform` files passed Microsoft's published JSON schemas. The project opened in Power BI Desktop and its 18 DAX measures across nine tables loaded; acquisition, quality, renewal, funnel, and content measures returned expected values in live Desktop DAX queries. Visual layout has not been manually inspected in Desktop because this environment cannot capture its native window. The date dimension, relationships and the new measures were added by script afterwards and have not yet been reopened in Desktop; the existing four pages do not use the new tables yet. The base theme comes from the [Microsoft Fabric CLI blank report template](https://github.com/microsoft/fabric-cli/tree/main/src/fabric_cli/commands/fs/payloads/Blank.Report).

The DAX measures and theme alongside the project are also available for a manual import workflow.

The dbt marts are the governed source for the Power BI import. After the seed and dbt build in the README, run:

```powershell
python -m growthops.verify_dbt
python -m growthops.export_bi --refresh-pbip   # versioned CSVs + embedded PBIP partitions
python -m growthops.export_excel               # formula-driven Excel dashboard
```

Import the versioned synthetic CSVs in `dashboards/powerbi-data` with **Get data → Text/CSV**. The same files can be regenerated in `data/powerbi` with the command above. Use the dbt mart names as table names. Parse `mart_growth_daily.day` as Date and all `_cents` fields as Whole Number before defining display measures. The [measure file](../dashboards/GrowthOps_PowerBI_Measures.dax) and [theme](../dashboards/GrowthOps_PowerBI_Theme.json) provide the report model and visual palette.

| Page | Tables | Question |
|---|---|---|
| Executive Pulse | `mart_revenue`, `mart_growth_daily`, `mart_measurement_health` | What reached collected cash and what changed this week? |
| Acquisition | `mart_campaign_performance`, `mart_growth_daily` | Which registered campaigns produced efficient paid leads and net cash? |
| Full Funnel | `mart_funnel` | Where did people stop progressing? |
| Content Intelligence | `mart_content_performance` | Which first identified content generated qualified pipeline and cash? |
| Migration Audit | `mart_migration_summary` | Which legacy contacts or properties failed to reconcile? |
| Experiments | `mart_experiment_variants` | Did lead lift translate into cash per visitor? |
| Renewal Risk | `mart_renewal_risk` | Which synthetic subscriptions are due soon or have a failed attempt? |
| Revenue Truth | `mart_revenue_bridge`, `mart_platform_comparison` | Why do CRM bookings, ad-platform claims and cash disagree? |

Measures should use the existing columns, with explicit grain and denominator:

```DAX
Net Collected USD = DIVIDE(SUM(mart_growth_daily[net_cash_cents]), 100)
Paid Spend USD = DIVIDE(CALCULATE(SUM(mart_campaign_performance[spend_cents]), mart_campaign_performance[medium] IN {"paid_search", "paid_social"}), 100)
Paid Leads = CALCULATE(SUM(mart_campaign_performance[leads]), mart_campaign_performance[medium] IN {"paid_search", "paid_social"})
Paid CPL USD = DIVIDE([Paid Spend USD], [Paid Leads])
```

The daily mart uses event dates: refunds reduce cash on the refund date, while campaign ROAS uses the modeled lead-creation attribution. The campaign mart and measures above are all-time and should not respond to a daily date slicer. Renewal risk levels are computed for the synthetic snapshot date, not relative to the viewer's current date. Do not sum `mart_revenue` or `mart_measurement_health` across unrelated tables; each is a single-row snapshot. Experiment confidence intervals and the cautious decision are computed by `/metrics/experiments/cta_growth_plan`, not by the mart. The Power BI project is editable source opened and queried in Desktop, not a saved `.pbix` or a published Power BI Service report. The Streamlit app and Excel workbook are working interactive reports in this repository. Label any additional Power BI pages **Synthetic ScaleLab case**.

## Production refresh

The embedded partitions make the project open with no credentials, which suits a portfolio demo. For a live
deployment, point each table's partition at the warehouse instead: a DuckDB file through the ODBC connector,
a PostgreSQL schema, or the CSV share that `export_bi` writes. Then publish to a workspace, configure an
on-premises or VNet data gateway, and schedule refresh after the nightly dbt build. Row-level security is not
defined because the report has one audience; add roles (for example by `medium` for a paid-media team)
before sharing it more widely.
