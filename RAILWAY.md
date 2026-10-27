# Isolated Railway review dashboard

Create a NEW project/service for this application. Do not redeploy, edit variables,
reuse databases, or change domains on Lead Intelligence, Restaurant, or other services.

Deploy this directory using its Dockerfile. Attach a dedicated volume at `/data`,
set `HOSTED_DATA_DIR=/data`, and configure `DASHBOARD_USERNAME` plus a unique
random `DASHBOARD_PASSWORD` of at least 24 characters. Keep both in Railway's
service variables. Do not commit credentials. Use `/healthz` as the health check.
The process listens on Railway's `PORT`. Use a Railway-generated HTTPS domain.

All pages and data endpoints require HTTP Basic authentication except `/healthz`,
which returns only a mode/status. The hosted factory always creates a separate
SQLite database on its own volume and never uses an inherited database URL.
Run one replica. Back up the volume/database before future schema changes.

This deployment provides dashboard review, not cloud LinkedIn collection. The
current collector requires a visible browser and manual sign-in. Browser outreach
and queue endpoints are disabled in hosted mode, and send controls are hidden.
Collection still runs locally; synchronization with this new database has not been
implemented. No existing leads, cookies, secrets, or browser profiles are uploaded.

## Verification

- Without credentials, `/` and `/api/leads` return 401.
- `/healthz` returns 200 without exposing database or credentials.
- Correct credentials open the dashboard; send actions return 409.
- Data survives a service restart because `/data` is a dedicated volume.
- Existing Railway services have the same settings/deployments as before this task.

Run the focused offline tests with:
`python -m pytest tests/integration/test_hosted_site.py`.
