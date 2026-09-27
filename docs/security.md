# Security notes

| Area | Control | Enforced by |
|---|---|---|
| Configuration | `GROWTHOPS_ENV=production` refuses the demo webhook secret, short or missing API keys, a missing operator token, synthetic data, and half-configured adapters | `growthops/config.py`, API and worker startup, CI container test |
| API access | Read, ops, CRM, ask and docs routes need an API key (`X-API-Key` or `Authorization: Bearer`), compared in constant time; several keys are allowed for rotation | `request_context` middleware |
| Operator actions | Dead-letter replay needs a separate operator token | `POST /ops/events/{id}/replay` |
| Webhooks | HMAC-SHA256 over `"{timestamp}.{body}"` with a tolerance window, so captured requests cannot be replayed; event and payment IDs are idempotent, and a reused ID with a different payload is rejected (409) | `verify_signature`, `workflow.process_payment` |
| Outbound calls | HTTPS URLs required; bearer token for HubSpot; signed, idempotent requests to bridges; timeouts on every call | `growthops/adapters.py`, `config.problems()` |
| Responses | Security headers (`nosniff`, `DENY` framing, `no-referrer`, `no-store`); unhandled errors return a request ID, never a traceback | middleware |
| Ask your data | No language model and no API key; nothing typed is executed; SQL and prompt-injection patterns, personal-data requests and forecasts are refused before retrieval; answers only come from governed functions or cited documents | `growthops/ask_data.py`, question contract in CI |
| Supply chain | Embedding model archive and files are SHA-256 pinned and extracted by allow-list; `pip-audit` and Dependabot in CI; GitHub Actions from their official publishers | `growthops/embeddings.py`, CI |
| Runtime | Non-root user, read-only root filesystem, all capabilities dropped, `no-new-privileges`, ports bound to localhost behind a TLS proxy | `Dockerfile`, `compose.yaml` |
| Data | The repository ships synthetic data only. Logs carry IDs, not payloads. `ask_log` stores question text for audit, so set a retention period in a live deployment | docs, schema |

## Not covered (needs the deployment owner)

- TLS certificates, a WAF or rate limiting at the proxy.
- A secret manager (Vault, AWS Secrets Manager, Azure Key Vault) instead of an `.env`
  file, and off-host encrypted backups.
- A privacy review before loading real personal data: lawful basis, retention and
  deletion requests (PIPEDA in Canada), and a data-processing agreement with HubSpot
  and any bridge provider.
- A penetration test.
