# Power BI import handoff

The dbt marts are the governed source for the Power BI import. After the seed and dbt build in the README, run:

```powershell
python -m growthops.verify_dbt
python -m growthops.export_bi
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

Measures should use the existing columns, with explicit grain and denominator:

```DAX
Net Collected USD = DIVIDE(SUM(mart_growth_daily[net_cash_cents]), 100)
Paid Spend USD = DIVIDE(CALCULATE(SUM(mart_campaign_performance[spend_cents]), mart_campaign_performance[medium] IN {"paid_search", "paid_social"}), 100)
Paid Leads = CALCULATE(SUM(mart_campaign_performance[leads]), mart_campaign_performance[medium] IN {"paid_search", "paid_social"})
Cost per Paid Lead USD = DIVIDE([Paid Spend USD], [Paid Leads])
```

The daily mart uses event dates: refunds reduce cash on the refund date, while campaign ROAS uses the modeled lead-creation attribution. The campaign mart and measures above are all-time and should not respond to a daily date slicer. Do not sum `mart_revenue` or `mart_measurement_health` across unrelated tables; each is a single-row snapshot. Experiment confidence intervals and the cautious decision are computed by `/metrics/experiments/cta_growth_plan`, not by the mart. A native `.pbix` report has not yet been authored. The Streamlit app and Excel workbook are interactive reports in this repository. Power BI Desktop or a signed-in Power BI workspace is required to save and verify a native report. For the first report page, use four cards (booked, gross cash, refunds, net cash), a line chart of daily spend and net cash, a campaign bar chart, then a measurement-quality table. Label all pages **Synthetic ScaleLab case**.
