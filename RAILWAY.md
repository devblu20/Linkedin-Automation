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

The dashboard's primary action is Search and fetch: choose a campaign and a
profile limit. Railway queues the request; a local worker uses the saved LinkedIn
browser session to search automatically and sync accepted profiles for cloud review.
LinkedIn cookies and browser profiles stay on the worker computer. Browser outreach
endpoints remain disabled in hosted mode, and send controls are hidden. No local
file paths, raw page snapshots or existing outreach approvals are synced.

## Hosted automatic search worker

After completing manual sign-in, run this from the matching checkout with its
local research database and browser profile configured:

```powershell
python -m linkedin_automation worker --url https://<dashboard-domain> --credentials-file .env.railway.local
```

Keep the worker running while using the live dashboard. The UI reports worker
availability and queued/running/completed/failed searches. Requests survive Railway
restarts in the dedicated `/data/search-jobs.sqlite3` database. One job runs at a
time; queued or running jobs expire after 30 minutes without automatic replay.
The worker accepts only matching local version 2 campaign definitions and bounded
search limits (up to 25 profiles and 5 pages). The dashboard defaults to a budget
of three search pages, cycling campaign keywords before later pages. The profile
limit is a maximum to read, not a promised number of accepted leads. Hidden-only
queries are skipped within this budget; identities are never unlocked or inferred.
The UI identifies the last run's requested limits, visited pages, hidden result
cards and campaign rejection reasons. Hidden cards may repeat between queries.
`--once` checks for one job and exits. No profile URL is needed.
Account visibility restrictions are reported in the dashboard.
The worker never sends outreach. Computer off or worker stopped means no collection.

## Local collection to hosted review

The dashboard has a read-only Collected profiles view with run/decision filters,
pagination, rejection reasons and missing-field details. Saved leads for review
contains accepted campaign matches separately. Rejected collected profiles never
become approved leads merely because they were synced.

Profile enrichment waits briefly for lazy visible sections and performs two
bounded normal scrolls. About/experience/education/skills stop at the next profile
section; Featured posts, activity, footer and suggested profiles are excluded.
Current role/company are filled only from an explicit employer relationship in
the headline when the search card did not already provide them. Follower counts,
connections and company-size evidence come from the subject's header, not sidebar
recommendations. Unavailable fields remain uncollected. The details view shows
matching evidence and the fields used for campaign checks; About and past activity
do not silently become evidence of a current trading role.

The worker automatically syncs visible observations (accepted and rejected) through
`POST /api/import/research`. Acceptance is recomputed on the server. The existing
observations table is reused; no migration is needed. Imports are atomic and
idempotent per run/profile, preserve review edits and reject conflicting retries.
Raw browser snapshots, passwords, local file paths and approval state are excluded.
To backfill an existing completed local version 2 run:

```powershell
python -m linkedin_automation sync <run-id> --url https://<dashboard-domain> --credentials-file .env.railway.local --include-rejected --dry-run
python -m linkedin_automation sync <run-id> --url https://<dashboard-domain> --credentials-file .env.railway.local --include-rejected
```

For manual sign-in, use a separate local Chrome profile and keep the same profile
for subsequent collection:

```powershell
$env:LINKEDIN_AUTOMATION_BROWSER_DATA = Join-Path (Get-Location) 'browser-data-chrome'
$env:LINKEDIN_AUTOMATION_BROWSER_CHANNEL = 'chrome'
python -m linkedin_automation login
```

Complete LinkedIn sign-in and any verification manually, then close that browser
window before starting or resuming collection. The login command keeps the window
open after detecting sign-in, for up to 20 minutes by default. It does not collect
profiles or send outreach. Browser profiles must remain local and uncommitted.

If search results show only "LinkedIn Member" and View reports that profiles are
outside your network, those identities are unavailable to this account. A search
can explicitly narrow its scope using the optional YAML field:

```yaml
search:
  connection_degrees: [1st, 2nd]
```

Keep the other search fields in place. The default is unrestricted search; this
filter is never added automatically to an existing campaign. It cannot grant
access to restricted profiles, and a narrowly filtered search may return no leads.

To verify a known accessible profile using the same local database, matching and
Excel report pipeline:

```powershell
python -m linkedin_automation profile https://www.linkedin.com/in/<profile>/ config/<search>.yaml
```

This command reads one visibly accessible profile, applies the supplied search
criteria, and exports locally. It does not upload, connect or message. A profile
that fails the campaign criteria remains a rejected observation rather than a lead.
Use a separate definition and report filename for technical tests.

The local `python -m linkedin_automation review --port 8766` dashboard also uses
Search and fetch when viewing all campaigns. It searches directly in the local
Chrome profile, applies campaign matching and creates the local Excel report.
Direct profile URLs remain an optional CLI diagnostic, outside the primary UI.

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
validation only. Hosted search requests use separate authenticated `/api/search/*`
endpoints; browser execution always runs on the local worker.

## Verification

- Without credentials, `/` and `/api/leads` return 401.
- `/healthz` returns 200 without exposing database or credentials.
- Correct credentials open the dashboard; send actions return 409.
- Data survives a service restart because `/data` is a dedicated volume.
- Existing Railway services have the same settings/deployments as before this task.

Run the focused offline tests with:
`python -m pytest tests/integration/test_hosted_site.py`.
