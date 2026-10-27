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
Collection still runs locally. Completed version 2 runs can be explicitly synced
with the command below. No cookies, credentials, browser profiles, local file paths,
raw page snapshots, or existing outreach approvals are included in the payload.

## Local collection to hosted review

Run from this checkout with the local research database configured:

```powershell
python -m linkedin_automation sync <run-id> --url https://<dashboard-domain> --credentials-file .env.railway.local --dry-run
python -m linkedin_automation sync <run-id> --url https://<dashboard-domain> --credentials-file .env.railway.local
```

The first command validates remotely without saving profiles. The second writes
the entire validated batch in one transaction. Repeating it skips existing profiles
and preserves cloud reviewer edits. Import recomputes campaign matching and scoring;
new profiles always start at `needs_verification`. The cloud run counters count
imported profiles, not rejected/local observations. The payload is limited to 250
profiles and 2 MiB. Redirects are refused so credentials are not forwarded.

`POST /api/import/runs` accepts the versioned JSON run-transfer contract. It uses
the same dashboard authentication and origin checks. Add `?dry_run=true` for
validation only. The hosted dashboard does not initiate collection.

## Verification

- Without credentials, `/` and `/api/leads` return 401.
- `/healthz` returns 200 without exposing database or credentials.
- Correct credentials open the dashboard; send actions return 409.
- Data survives a service restart because `/data` is a dedicated volume.
- Existing Railway services have the same settings/deployments as before this task.

Run the focused offline tests with:
`python -m pytest tests/integration/test_hosted_site.py`.
