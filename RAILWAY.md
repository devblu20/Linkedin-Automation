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

Collected-profile filters ignore stale responses, so a slow previous all-runs
request cannot overwrite a newly selected run. An opt-in browser regression can
be run with an existing local run containing exactly one collected profile:

```powershell
python tests/browser/check_observation_filters.py <run-id> --url http://127.0.0.1:8766/
```

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

## Saved campaign requirements

Use **New campaign** or **Edit selected campaign** in the dashboard to set job
titles, locations, industries, companies, include/exclude keywords, required
matches, connection degrees and collection limits. Saving selects that campaign;
it does not start a search. The original YAML templates remain unchanged.

Hosted edits persist in `/data/campaigns.sqlite3` on this service's own volume.
Local edits use `campaigns.sqlite3` inside the configured artifact directory.
Each edit requires the current revision, so a stale browser cannot silently
overwrite another edit. A queued search stores its campaign revision and rules;
later edits apply only to future searches.

The worker verifies saved search requirements against its trusted local template.
Qualification, outreach policy, report paths and maximum collection limits cannot
be replaced through the editor. Saved campaigns therefore work without creating
new local YAML files. Restart the local worker after updating to code that supports
saved campaign policies. Older queued jobs remain compatible: startup adds only
a nullable `policy` column to the existing search-jobs table.

Before deploying changes, back up this service's `research.sqlite3`,
`search-jobs.sqlite3` and, when present, `campaigns.sqlite3` using SQLite's backup
API. Keep backups on its own volume and never operate on another service's data.

## Verification

### Search accuracy and access

The dashboard previews the same bounded query plan executed by the worker.
Specific role phrases already covered by positive keywords are tried first;
remaining keywords/companies follow in order, with case-insensitive deduplication
and later-page cycling inside the selected page budget. Matching and qualification
rules stay unchanged. For example, Independent Trader is tried before broad
trading searches when the campaign already includes independent as a keyword.

The collector reads identified cards inside the actual people-search results,
supporting current semantic data-view-name cards and legacy result containers.
It excludes anonymous LinkedIn Member cards and unrelated links. A current,
matching profile URL and visible subject header are required before saving a
candidate; stale search-card fields are not a fallback for failed profile reads.
Current visible headline/location replace old search-card details, and obsolete
role/company/self-employment/size inferences are cleared. Failed or redirected
visits count toward the profile limit as well.

The account may still see only anonymous results. A bounded search then reports
the visibility limitation without inventing leads or approving inaccessible
people. This requires account access or another authorized source; deployment
alone cannot unlock LinkedIn visibility. Source-verified manual research is
labelled separately from automatic discovery.

### Dashboard downloads

**Download CSV** and **Download Excel** export collected profiles using the run
and collection-decision filters, or saved leads using search text and review
status. Downloads include all matching rows across pages, up to 5,000. Narrow
the filters if the limit is exceeded; the server refuses to silently truncate.
Empty selections produce a header-only report. Excel also includes an Export
scope sheet with the filters and row count.

Authenticated GET `/api/exports/observations.csv` and `.xlsx` accept `run_id` and
`decision`. `/api/exports/leads.csv` and `.xlsx` accept `run_id`, `status` and `q`.
Run-scoped review pages cannot export other runs. Files are generated in memory
from current records, without following stored report paths or creating public
download links. Raw browser snapshots, local paths and credentials are excluded.
CSV uses UTF-8 with a BOM; spreadsheet formula prefixes and invalid XML control
characters are escaped/removed. Fields exceeding Excel's cell limit require CSV.
Exports do not change qualification, review decisions or outreach state.

### Qualification review

**Review** opens the original run's qualification rules, score breakdown,
matching evidence and missing profile data. Form changes update a read-only
preview; only **Save client** writes the decision and drafts. Approval requires
an allowed firm type, the configured score and any required minimum-funds amount
with a valid HTTP(S) source URL. Sources are supplied and checked by the reviewer;
the app does not independently verify the financial claim.

Scores are recomputed from profile evidence on each save, rather than adjusted
from a previous score. A search match is not an approved customer. The Qualified
metric counts approved leads and later outreach states, excluding ready-for-review.
The hosted editor accepts only needs-verification, ready-for-review, approved and
rejected; it cannot manufacture sent or connected states. No approval sends outreach.
These changes require no database migration and do not rewrite existing reviews.

On Windows, start the local worker independently of a foreground terminal:

```powershell
.\scripts\start-browser-worker.ps1
```

The launcher avoids starting a duplicate for this dashboard, keeps credentials
out of command output and writes local logs under `data/worker-logs`. It does not
install a startup task. The computer must stay awake and online; after a reboot,
start it again. LinkedIn sign-in/security challenges still require manual action.
Pass `-PythonPath` if Python is not installed in this checkout's or its parent's
`.venv`. Keep the saved browser profile and credentials file local.

- Without credentials, `/` and `/api/leads` return 401.
- `/healthz` returns 200 without exposing database or credentials.
- Correct credentials open the dashboard; send actions return 409.
- Data survives a service restart because `/data` is a dedicated volume.
- Existing Railway services have the same settings/deployments as before this task.

Run the focused offline tests with:
`python -m pytest tests/integration/test_hosted_site.py`.
