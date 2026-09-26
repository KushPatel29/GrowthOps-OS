# Streamlit Community Cloud deployment

Repository: `KushPatel29/GrowthOps-OS`; branch: `main`; entrypoint: `streamlit_app.py`. No secrets are required for the public synthetic dashboard. Streamlit installs the root `requirements.txt` and updates the app from GitHub pushes.

The app creates its synthetic SQLite sample under the instance's temporary directory, builds local marts, and caches the read-only views. It does not receive Stripe webhooks, write customer records, or run provider automations. The separate FastAPI simulator remains local.

For a local deployment smoke check:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

For Community Cloud, create an app from the repository and choose `main` and `streamlit_app.py`. Verify the six tabs and synthetic-data label after the build completes. Do not add real customer data or API keys to this public app.
