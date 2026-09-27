# Streamlit Community Cloud deployment

Repository: `KushPatel29/GrowthOps-OS`; branch: `main`; entrypoint: `streamlit_app.py`. No secrets are required for the public synthetic dashboard. Streamlit installs the root `requirements.txt` and updates the app from GitHub pushes.

The app generates the full fifteen-month synthetic scenario (about three seconds) under the instance's temporary directory, builds local marts, and caches the read-only views. The theme follows the viewer's light or dark setting. It does not receive Stripe webhooks, write customer records, or run provider automations. The separate FastAPI simulator remains local. Ask Your Data uses its verified local MiniLM model when one is preloaded; on Community Cloud it uses keyword retrieval without downloading a model during a visitor's question.

For a local deployment smoke check:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

For Community Cloud, create an app from the repository and choose `main` and `streamlit_app.py`. Verify the ten views and the synthetic-data label after the build completes. Do not add real customer data or API keys to this public app.

A private deployment may set `GROWTHOPS_DASHBOARD_DATABASE` to an existing SQLite database whose marts have already been built. That path opens read-only and requires `GROWTHOPS_DASHBOARD_PASSWORD` of at least 16 characters. Place a real-data deployment behind an identity-aware proxy; the shared password is a minimum gate, not user-level access control.
