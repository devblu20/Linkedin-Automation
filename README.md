# LinkedIn Lead Research Automation

A supervised, configuration-driven backend that discovers LinkedIn people, normalizes and
deduplicates matching leads, persists auditable runs, generates Excel reports, and optionally
uploads reports to Google Drive.

The system tracks human-reviewed outreach but does not send invitations or messages
through browser automation, solve CAPTCHAs, conceal automation, or bypass access controls.
LinkedIn collection runs in a visible browser and stops when authentication, security challenges,
rate limits, or unrecognized layouts are detected.

## Requirements

- Python 3.12 or newer
- A user-authorized LinkedIn account for live collection
- A Google Cloud OAuth desktop client for Drive upload

## Installation

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

The Playwright browser installation is needed only for live LinkedIn collection. Tests never
contact LinkedIn or Google Drive.

## Search configuration

Copy and edit the example:

```powershell
Copy-Item config\search.example.yaml config\search.local.yaml
linkedin-automation validate config\search.local.yaml
```

Search definitions support included and excluded titles and keywords, plus locations,
industries, and companies. Values are normalized and deduplicated case-insensitively. At least
one positive criterion is required. `max_results` and `max_pages` bound each run, while
`max_daily_results` caps observations across all runs for the local database day.

Version 2 definitions also support `required_dimensions`, a minimum-funds evidence policy,
executive title priority, deterministic scoring, and manual-send-only outreach templates. The
BluQQ configuration is `config/bluqq-london-prop-family-offices.yaml`. It requires a senior title
and London location; a person cannot qualify merely by matching the location.

## Runtime configuration

Copy `.env.example` to a gitignored `.env` file and add local secrets there. The application loads
`.env` automatically; environment variables already set in the shell take precedence.

Important variables:

- `LINKEDIN_AUTOMATION_DATABASE_URL`: Neon PostgreSQL URL; when unset, SQLite is used.
- `LINKEDIN_AUTOMATION_DATABASE`: SQLite fallback path; defaults to `data/research.sqlite3`.
- `LINKEDIN_AUTOMATION_ARTIFACT_DIR`: report directory; defaults to `artifacts`.
- `LINKEDIN_AUTOMATION_BROWSER_DATA`: persistent Chromium profile directory.
- `LINKEDIN_AUTOMATION_SCREENSHOT_DIR`: diagnostic screenshot directory.
- `GOOGLE_DRIVE_FOLDER_ID`: destination folder used by the example search definition.
- `GOOGLE_OAUTH_CLIENT_FILE`: path to a Google OAuth desktop-client JSON file.
- `GOOGLE_OAUTH_TOKEN_FILE`: external path where the local OAuth token may be stored.

Keep OAuth files and tokens outside this repository.

### Neon PostgreSQL

Create a Neon project, copy its pooled connection string, and set it only in your shell or secret
manager. Standard `postgresql://` Neon URLs are accepted and automatically use the psycopg driver:

```powershell
$env:LINKEDIN_AUTOMATION_DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DB?sslmode=require"
$env:LINKEDIN_AUTOMATION_MIGRATION_URL = $env:LINKEDIN_AUTOMATION_DATABASE_URL
alembic upgrade head
linkedin-automation run config\search.local.yaml
```

After `LINKEDIN_AUTOMATION_DATABASE_URL` is set, collection, status, export, and the review site all
use Neon. If it is unset, the application continues using the local SQLite database. Do not put the
real Neon URL in a committed file.

## Commands

Run the three focused BluQQ London campaigns separately so each segment receives relevant
qualification and outreach drafts:

```powershell
linkedin-automation run config\bluqq-london-prop-family-offices.yaml
linkedin-automation run config\bluqq-london-small-funds-trading-teams.yaml
linkedin-automation run config\bluqq-london-independent-traders.yaml
```

Every fetched profile is stored in the configured database's `observations` table, including
rejected candidates and rejection reasons. Qualified, canonicalized clients are stored in `leads`,
and their editable human-reviewed drafts and lifecycle state are stored in `outreach_records`.
Connection and message audit events are stored in `connection_requests` and `messages_sent`.

Validate without creating runtime data:

```powershell
linkedin-automation validate config\search.local.yaml
```

Verify orchestration without opening LinkedIn or creating a report:

```powershell
linkedin-automation run config\search.local.yaml --dry-run
```

Start supervised collection and create an Excel report:

```powershell
linkedin-automation run config\search.local.yaml
```

Start collection and upload the completed report:

```powershell
linkedin-automation run config\search.local.yaml --upload
```

Inspect or continue a run:

```powershell
linkedin-automation status <run-id>
linkedin-automation resume <run-id>
linkedin-automation resume <run-id> --upload
```

Regenerate or upload a stored run's report:

```powershell
linkedin-automation export <run-id>
linkedin-automation upload <run-id>
```

## Manual authentication

LinkedIn uses the ignored `browser-data` directory as a dedicated persistent Chromium profile.
The browser is headed by default. Sign in directly inside that window. If LinkedIn presents a
login page, the run waits for up to five minutes and continues automatically after sign-in. It
also waits up to five minutes for you to manually complete a checkpoint or CAPTCHA, without
attempting to solve or bypass it. A rate limit, unresolved challenge, consent screen, or
unexpected page pauses the run and records an actionable error; use `resume` when appropriate.

For Google Drive, the first upload opens the standard local OAuth authorization flow. The
adapter requests only `drive.file`, which limits access to files created or opened by this app.
Uploads use the run UUID as an idempotency property so retries do not create uncontrolled
duplicates.

Authorize and verify the intended account before the first upload:

```powershell
linkedin-automation drive-auth --expected-email marketingcodex77@gmail.com
```

Select `marketingcodex77@gmail.com` in Google's consent screen. If an existing external token was
created for another account, remove that token outside the repository and authorize again. The
command fails closed when the returned account does not match.

## BluQQ qualification and outreach review

The dashboard sidebar opens separate Lead pipeline, Campaigns, Outreach and Reports views.
Campaigns can be created, edited, and selected for a search. Outreach provides searchable
saved leads, qualification review and editable drafts; copying requires an approved review
status and does not send anything. Reports has independent run/status/decision filters and
CSV/Excel downloads. Hash links preserve the selected view on refresh and browser navigation.

The review queue is aligned to BluQQ's service offer: owned trading automation and AI-enhanced
investment infrastructure, especially US options, multi-account execution, risk controls,
backtesting, reporting, private AI, and managed support. BluQQ is represented as software-only:
no custody, discretion, trading on behalf of a client, or investment advice.

After a version 2 research run, start the local review site:

```powershell
linkedin-automation review <run-id>
```

Open `http://127.0.0.1:8765`. Add a public or authorized evidence URL and verified GBP funds,
edit the drafts, and approve or reject the lead. Use **Open LinkedIn** to send an approved
connection request manually. Record `connection_sent`; after LinkedIn shows acceptance, record
`connected`. Only then does the site expose the post-connection draft. Record `message_sent`
after manually sending it.

The local review site supports manual tracking. The hosted dashboard additionally supports the
authorized browser delivery worker described below. Legacy direct-send routes remain disabled.

### Hosted automatic qualification and authorized delivery

Set `AUTOMATIC_QUALIFICATION=1` and `AUTOMATIC_QUEUE=1` on the hosted service to evaluate
newly collected profiles against their saved campaign rules and prepare eligible outreach.
Current role, company and location must be available, and the current target identity and
qualification threshold must pass. Missing facts remain Needs verification. Campaigns requiring
funds evidence retain human verification; institutional AUM is never treated as personal funds.
Fresh observations may enrich an older untouched lead, but cannot overwrite a human review or
the original research observations.

In **Outreach → Automatic outreach queue**, inspect the named recipient and exact draft, then
explicitly authorize delivery. Follow-up consent is separate and applies only to the displayed
draft after the connection is confirmed. Changed drafts require new authorization. Preparing
a queue or approving a lead does not send a request. Jobs deduplicate profile URLs across runs
and respect the saved daily limits.

The local browser worker must be online with `LINKEDIN_AUTOMATION_ENABLE_OUTREACH_WORKER=1`
(set by `scripts/start-browser-worker.ps1`). It uses the manually authenticated local Chrome
profile. Railway hosts the dashboard and durable queue; it does not host this visible browser.
Actual delivery is recorded only after the worker observes Pending, a first-degree connection,
or the sent message in the matching conversation. Acceptance checks run every six hours for
up to fourteen days, with a read-only **Check now** control. Ambiguous results pause delivery
as Uncertain; they are never automatically resent. Check LinkedIn before resolving such a job.

Delivery handoffs are retained privately under `artifacts/worker-delivery/`. A lost dashboard
response retries the saved result, rather than repeating the browser action. Preserve these
files during recovery. The delivery queue is stored in the existing `search-jobs.sqlite3`.
Browser layouts and account permissions can affect delivery; login/security checks still need
the account owner. Offline browser tests verify the adapter, but a live send requires a separate
recipient/draft authorization and subsequent observation of the real LinkedIn outcome.

## Database migrations

The repository initializes the current schema for local use. Versioned Alembic migrations are
also included for controlled upgrades:

```powershell
alembic upgrade head
```

Set `LINKEDIN_AUTOMATION_MIGRATION_URL` (for example,
`sqlite:///C:/path/to/research.sqlite3`) or override `sqlalchemy.url` in `alembic.ini` before
running migrations against a non-default database.

## Quality checks

```powershell
python -m pytest
ruff check .
ruff format --check .
mypy
```

All automated browser parsing tests use sanitized local HTML fixtures. Drive tests use fake
client objects.

## Data retention and security

### Browser worker recovery

The worker saves finished research and its dashboard completion in an atomic local handoff
under `artifacts/worker-pending/` before attempting network upload. After a connection failure
or worker restart it retries this handoff before claiming another search. Uploads are keyed by
research run ID; repeated identical completions are acknowledged without changing the saved
result. No browser search is repeated automatically. Keep this private directory with the
local research database: it includes a claim token and collected profile data, but no dashboard
password. Do not delete it while a result is awaiting upload.

If a claim has expired after 30 minutes, the worker can still sync its finished research;
the dashboard search remains expired and is not revived. A process killed *during* browser
collection can be resumed using **Resume interrupted search** after its claim expires. A normal
browser-close, sign-in or page-load interruption offers resume immediately when a checkpoint
exists. Resume uses the original worker computer, saved campaign rules and original remaining
visit/page budget; visits reserved before a crash are not retried. Completed profiles are saved
after each successful read in `artifacts/browser-checkpoints/`. A partially read search page may
be opened again, but reserved profile visits are skipped. Keep this directory private and do not
delete it before resuming. Login and security checks still require manual action. Hidden profiles
remain inaccessible and source-requested rate limits are not automatically retried.

### Evidence and manual outreach tracking

From Outreach, choose **Record / view activity** to record a source you personally checked,
fill missing company/current role/industry/experience, or record a manually sent request,
connection acceptance and follow-up. Supplements require a source and confirmation, cannot
overwrite collected fields, are audited, and return the lead to Needs verification. Original
collected observations remain unchanged. Qualification shows available and missing profile data.

Tracking advances only in order: approved → connection sent → connected → message sent.
Confirmations are reviewer supplied, not verified by LinkedIn. Draft snapshots, source URLs,
notes and server timestamps are retained in an append-only history with CSV download. Exact
retries are idempotent; stale status updates are rejected. No tracking endpoint sends anything.
Hosted review forms preserve tracked statuses and cannot fabricate or rewind delivery states.
The additive `0005` migration creates only `manual_activities`; existing lead data is preserved.

- Never commit `.env` files, OAuth credentials, tokens, cookies, browser profiles, databases,
  reports, screenshots, or collected personal data.
- Collect only fields visible to and authorized for the signed-in user.
- Delete old files from `artifacts`, `screenshots`, `browser-data`, and `data` according to your
  business retention policy.
- Treat profile text as untrusted input. Excel output escapes formula-like values.
- Back up the SQLite database before applying future migrations.
- Operators are responsible for ensuring their use complies with LinkedIn terms, applicable
  law, and organizational policy.

See `ARCHITECTURE.md`, `ROADMAP.md`, and `TASKS.md` for authoritative design and scope.
