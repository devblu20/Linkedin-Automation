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

Copy `.env.example` into your preferred secret-management workflow. The application reads
environment variables directly; it does not automatically load `.env` files.

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

Direct site-to-LinkedIn sending is intentionally unavailable without an approved LinkedIn
Invitations/Messages API adapter. LinkedIn passwords, cookies, and browser actions are never used
for outreach sending.

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
