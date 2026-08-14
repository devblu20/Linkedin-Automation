# LinkedIn Lead Research Automation — Architecture

## 1. Document Status

- Status: Initial single-user implementation completed
- Architecture version: 1.1
- Last updated: 2026-08-11
- Current delivery target: BluQQ qualification, supervised outreach review, Excel, and Google Drive

This document is the architectural source of truth. Implementation decisions must conform to it unless an explicitly reviewed update changes this document first.

## 2. Project Vision

Build a production-quality backend that reads a declarative lead-search definition, discovers matching public LinkedIn profiles through a supervised browser session, normalizes and deduplicates the results, creates an auditable Excel report, and uploads that report to a configured Google Drive folder.

The system optimizes for:

1. User control and review.
2. Maintainability when LinkedIn page structure changes.
3. Traceable and reproducible research runs.
4. Safe, conservative browser behavior.
5. Replaceable integrations, including a future official LinkedIn API adapter.

The system is a research assistant, not an unrestricted outreach bot. Automated connection requests, messaging, engagement, CAPTCHA solving, stealth/evasion behavior, and security-control bypasses are outside the initial scope.

## 3. Primary Workflow

1. The user creates a YAML search-definition file.
2. The backend validates and normalizes the definition.
3. A research run is created with a unique identifier and immutable input snapshot.
4. The user starts or resumes a persistent browser profile and authenticates manually when necessary.
5. The LinkedIn browser adapter performs conservative searches and collects permitted profile summary data.
6. Raw observations are stored with source URLs, timestamps, and run context.
7. The application normalizes, validates, scores, and deduplicates leads.
8. An Excel workbook is generated from the accepted results.
9. The workbook is uploaded to the configured Google Drive folder.
10. The run record stores its status, metrics, local artifact path, Drive file identifier, and errors.

## 4. Functional Scope

### 4.1 Initial scope

- Read one versioned YAML search definition per run.
- Search for people using job titles, locations, industries, companies, and keywords.
- Support include and exclude criteria.
- Enforce per-run and per-day collection limits.
- Collect only fields visible to the authenticated user during the supervised session.
- Normalize and deduplicate profiles primarily by canonical LinkedIn profile URL.
- Record why a lead matched.
- Generate an `.xlsx` workbook.
- Upload the workbook to Google Drive.
- Preserve structured logs, run state, and screenshots needed for diagnosis.
- Support dry-run and resume behavior.

### 4.2 Explicitly out of scope

- Automated invitations, messages, comments, reactions, or follows.
- CAPTCHA solving or bypassing access controls.
- Stealth plugins intended to conceal automation.
- Scraping private data or data not visible to the signed-in user.
- Password storage by the application.
- Distributed execution or multi-account orchestration in the first release.
- A public web UI in the first release.
- AI-based enrichment from unverified external sources in the first release.

## 5. Design Principles

### 5.1 Clean architecture

Business rules must not depend directly on Playwright, Google APIs, Excel libraries, database drivers, or CLI frameworks. Dependencies point inward:

```text
Interfaces / Infrastructure -> Application -> Domain
```

### 5.2 Ports and adapters

External capabilities are represented by typed interfaces (ports). Initial adapters include:

- Playwright LinkedIn adapter
- Google Drive API adapter
- SQLite repository adapter
- OpenPyXL report adapter

This allows browser automation to be replaced by an official API without rewriting use cases.

### 5.3 Supervised automation

Authentication and exceptional states remain user-supervised. The system must pause when login, checkpoint, CAPTCHA, consent, or an unexpected page is detected. It must never attempt to defeat those controls.

### 5.4 Idempotency and resumability

A run has a stable ID. Repeating export or upload operations must not create uncontrolled duplicates. Completed search pages and collected profile observations are checkpointed so interrupted runs can resume.

### 5.5 Evidence and auditability

Every accepted lead retains its source URL, collection timestamp, run ID, matching evidence, and normalized fields. Errors include operation context without leaking secrets.

### 5.6 Configuration over hard-coding

Search criteria, limits, output columns, browser behavior, Drive destination, and logging levels are configuration. Secrets are environment variables or OS-managed credentials and never committed.

## 6. System Context

```text
User
  | provides YAML, approves browser session, receives report
  v
CLI / Application Service
  | coordinates use cases
  +--> LinkedIn Port --> Playwright Adapter --> LinkedIn website
  +--> Lead Repository Port --> SQLite
  +--> Report Port --> OpenPyXL --> local XLSX
  +--> File Storage Port --> Google Drive API --> Drive folder
  +--> Observability --> structured logs and diagnostic screenshots
```

## 7. Component Architecture

### 7.1 Domain layer

Contains framework-independent models and rules:

- `SearchDefinition`
- `SearchCriterion`
- `LeadCandidate`
- `Lead`
- `MatchEvidence`
- `ResearchRun`
- `RunStatus`
- normalization and deduplication policies
- matching/scoring policies

Domain objects must not import infrastructure packages.

### 7.2 Application layer

Contains use cases and port definitions:

- validate search definition
- start research run
- collect lead candidates
- normalize and evaluate candidates
- generate report
- upload artifact
- resume failed/interrupted run
- query run status

Use cases receive dependencies through constructor injection. They own orchestration but not browser selectors, SQL statements, workbook formatting internals, or Google API calls.

### 7.3 Infrastructure layer

Contains concrete adapters:

- YAML configuration loader
- Playwright browser lifecycle and LinkedIn page adapter
- SQLite repositories and migrations
- OpenPyXL Excel report writer
- Google Drive OAuth and upload adapter
- filesystem artifact store
- logging configuration

### 7.4 Interface layer

The first interface is a typed CLI. Expected commands include:

```text
validate <search-file>
run <search-file> [--dry-run]
resume <run-id>
status <run-id>
export <run-id>
upload <run-id>
```

Command names are architectural intent, not an instruction to implement every command in the first task.

## 8. Proposed Folder Structure

```text
linkedin-automation/
|-- ARCHITECTURE.md
|-- ROADMAP.md
|-- TASKS.md
|-- README.md
|-- pyproject.toml
|-- .env.example
|-- .gitignore
|-- config/
|   `-- search.example.yaml
|-- src/
|   `-- linkedin_automation/
|       |-- __init__.py
|       |-- domain/
|       |   |-- models.py
|       |   |-- enums.py
|       |   `-- services.py
|       |-- application/
|       |   |-- ports/
|       |   `-- use_cases/
|       |-- infrastructure/
|       |   |-- browser/
|       |   |-- persistence/
|       |   |-- reporting/
|       |   |-- storage/
|       |   `-- config/
|       |-- interfaces/
|       |   `-- cli/
|       `-- observability/
|-- migrations/
|-- tests/
|   |-- unit/
|   |-- integration/
|   `-- fixtures/
|-- artifacts/              # ignored; generated reports/screenshots
`-- browser-data/           # ignored; persistent browser profile
```

Folders are created only when required by the current roadmap phase. Empty speculative modules must not be added.

## 9. Technology Stack

### 9.1 Core

- Python 3.12+
- Pydantic 2 for typed validation
- Typer for the CLI
- PyYAML for YAML parsing
- SQLAlchemy 2 and Alembic for persistence and migrations
- SQLite for the initial single-user deployment

### 9.2 Integrations

- Playwright for supervised Chromium automation
- OpenPyXL for Excel generation
- Google Drive API client with OAuth 2.0 for upload

### 9.3 Quality and operations

- `pytest` and `pytest-cov`
- Ruff for linting and formatting
- mypy in strict-oriented mode
- standard-library `logging` with JSON-capable structured context
- pre-commit hooks after the foundation phase

Exact compatible versions are pinned or bounded in `pyproject.toml` during implementation.

## 10. Search Definition Contract

YAML is selected because it is readable, reviewable, and suitable for version control. An initial conceptual example is:

```yaml
version: 1
name: india-ai-engineering-leads
search:
  titles:
    include: ["AI Engineer", "Machine Learning Engineer"]
    exclude: ["Intern", "Student"]
  locations: ["India"]
  industries: ["Software Development", "IT Services"]
  companies: []
  keywords:
    include: ["LLM", "generative AI"]
    exclude: []
limits:
  max_results: 100
  max_pages: 10
output:
  file_name: "india-ai-engineering-leads.xlsx"
  drive_folder_id_env: "GOOGLE_DRIVE_FOLDER_ID"
```

The implemented schema must reject unknown fields by default, provide actionable validation errors, and snapshot the normalized definition for each run.

## 11. Data Model

### 11.1 Research run

- ID (UUID)
- search name and schema version
- normalized input snapshot and input hash
- status
- dry-run flag
- started, updated, and completed timestamps
- counters: observed, accepted, rejected, duplicated, failed
- local report path
- Google Drive file ID and URL
- failure category and sanitized message

### 11.2 Lead

- ID (UUID)
- canonical LinkedIn profile URL (primary natural deduplication key)
- full name
- headline
- current title
- company
- location
- optional industry
- match evidence
- source query/page context
- first and last observed timestamps
- originating run ID

### 11.3 Observation

Raw observations are stored separately from normalized leads where practical. This preserves evidence when normalization rules evolve.

## 12. Excel Report Contract

The default workbook contains:

### `Leads` sheet

- Full Name
- Headline
- Current Title
- Company
- Location
- Industry
- LinkedIn Profile URL
- Match Evidence
- Source Search
- Collected At (UTC)
- Notes

### `Run Summary` sheet

- Run ID
- Search definition name and hash
- Start and completion timestamps
- Counts and limits
- Export timestamp
- Warnings or partial-run status

The workbook uses frozen headers, filters, readable widths, hyperlink cells for profile URLs, UTC timestamps, and deterministic column ordering. Formulas from collected web content are escaped to prevent spreadsheet formula injection.

## 13. LinkedIn Browser Adapter

### 13.1 Browser lifecycle

- Use a dedicated persistent browser-data directory excluded from version control.
- The user authenticates directly in the browser; the application does not request or store the LinkedIn password.
- Use headed mode by default.
- Dry-run mode validates navigation and planned queries without collecting or uploading a final report.

### 13.2 Page interaction

- Keep selectors and page-specific parsing inside the LinkedIn adapter.
- Prefer accessible roles, labels, and stable semantic attributes over fragile CSS chains.
- Represent page variants through small page objects or strategies.
- Capture sanitized screenshots on unexpected states.
- Detect login, checkpoint, CAPTCHA, rate-limit, and unexpected-layout states and pause safely.

### 13.3 Rate and volume controls

- Enforce configurable hard maximums.
- Use conservative pacing with bounded jitter for normal interaction timing, not evasion.
- Stop when the daily or run limit is reached.
- Do not retry access-control failures automatically.

Because third-party terms and page behavior can change, operators are responsible for ensuring their usage is authorized and compliant. The architecture does not guarantee platform permission.

## 14. Google Drive Integration

- Use OAuth 2.0 user authorization for an initial local/single-user deployment.
- Request the narrowest practical Drive scope.
- Store tokens outside the repository with restrictive local permissions or an OS credential store.
- Identify the destination using a folder ID supplied through environment-backed configuration.
- Upload only after report generation and validation succeed.
- Record the returned Drive file ID and web URL.
- Use a deterministic name and run metadata; retry transient failures with bounded exponential backoff.
- Do not duplicate uploads when a run already has a verified Drive file ID unless explicitly requested.

## 15. Error Handling and Recovery

Errors are typed into categories such as:

- configuration error
- authentication required
- browser checkpoint/security challenge
- page layout changed
- platform rate limited
- network/transient integration error
- persistence error
- report generation error
- Drive authorization/upload error

Use cases translate infrastructure exceptions into application-level errors. Logs include run ID and operation but exclude OAuth tokens, cookies, credentials, and unnecessary personal data. Interrupted runs retain checkpoints and can be resumed when safe.

## 16. Observability

- Structured logs with timestamp, level, event name, run ID, and sanitized context.
- Run-level metrics and counters persisted in the database.
- Screenshots only for diagnostics or explicit evidence settings.
- A final run summary suitable for CLI display and report metadata.
- Retention settings for artifacts, screenshots, and logs.

## 17. Security and Privacy

- Never commit `.env`, OAuth credentials, tokens, cookies, browser profiles, databases, reports, screenshots, or collected personal data.
- Collect the minimum data needed for the declared research purpose.
- Avoid sensitive personal data and private contact details.
- Sanitize external strings before logging and spreadsheet export.
- Use UTC internally and render local time only at interfaces.
- Provide deletion/retention procedures before multi-user deployment.
- Validate paths and prevent output from escaping configured artifact directories.
- Treat web content and profile fields as untrusted input.

## 18. Coding Standards

- Use explicit type hints for public functions and methods.
- Keep functions and classes focused on one responsibility.
- Favor composition and dependency injection over global state.
- Define ports with `Protocol` or abstract interfaces where substitution is needed.
- Avoid generic utility modules; place behavior with the owning concept.
- Use immutable domain values where practical.
- Do not catch `Exception` without adding context and re-raising or translating it at a boundary.
- Log events at boundaries; do not duplicate the same error across every layer.
- Use descriptive names and small modules.
- No business rules in CLI handlers, Playwright page objects, ORM models, or workbook formatting code.
- Tests must not access real LinkedIn or Google Drive by default.

## 19. Testing Strategy

### Unit tests

- configuration validation
- normalization and canonical URL behavior
- matching and exclusion policies
- deduplication
- run state transitions
- spreadsheet-safety escaping

### Integration tests

- SQLite repositories against temporary databases
- report generation and workbook inspection
- Google Drive adapter against mocked HTTP responses
- LinkedIn parsing against checked-in sanitized HTML fixtures where permitted

### Manual/smoke tests

- headed browser authentication
- one low-volume supervised search
- report inspection
- upload to a test Drive folder

Live LinkedIn tests must be explicitly selected and never run in the normal test suite or CI.

## 20. Scalability Path

The initial system is a modular monolith and single process. This is intentional. Future growth can add:

1. PostgreSQL by replacing/configuring the repository adapter.
2. A job queue and workers for non-browser report/upload tasks.
3. Object storage for reports and diagnostic artifacts.
4. A web/API interface over the existing application use cases.
5. Multi-user tenant boundaries and secret management.
6. An official LinkedIn API or authorized data-provider adapter.
7. Pluggable enrichment and CRM export adapters.

Browser sessions should remain serialized per authenticated account. Distributed scraping is not a target architecture.

## 21. Architectural Decisions

### ADR-001: Modular monolith first

Selected to minimize operational complexity while preserving clear boundaries. Microservices are unjustified for the initial single-user workflow.

### ADR-002: Browser integration behind a port

Selected because browser behavior is volatile and may later be replaced by an official API or authorized provider.

### ADR-003: YAML as the input contract

Selected for human readability and version control. Validation remains strict and typed.

### ADR-004: SQLite initially

Selected for local reliability and low operational burden. Repository boundaries and migrations preserve a PostgreSQL path.

### ADR-005: Supervised operation

Selected for account safety, authentication handling, and explicit user control.

## 22. Definition of Architectural Compliance

A change complies with this architecture when:

- it belongs to the current roadmap phase;
- business rules remain independent from external libraries;
- integrations implement application ports;
- configuration is validated and secrets remain external;
- runs are traceable, bounded, and resumable where applicable;
- tests cover new domain/application behavior;
- automation does not bypass platform security controls;
- documentation is updated when contracts or architectural decisions change.

## 23. Phase 7A - BluQQ Qualification and Supervised Outreach

Phase 7A targets London proprietary-trading firms, family offices, and small funds with
independently evidenced funds of at least GBP 1 million. It ranks founders, CEOs, chief
executives, managing partners, CIOs, principals, partners, managing directors, and heads of
trading.

Qualification is aligned to BluQQ's software services: US options and derivatives automation,
multi-account execution, risk controls, AI research and reporting, backtesting, institutional
infrastructure, private AI deployments, and ongoing support. BluQQ is always represented as a
software provider with no custody, discretion, client trading, or investment advice.

The domain adds `OutreachRecord` and `OutreachStatus`. Each record stores firm type, verified
funds in GBP, evidence URL and notes, score, editable connection and follow-up drafts, status, and
review timestamp. Reports add an `Outreach Review` sheet. A local FastAPI site supports evidence
review, approval, draft editing, LinkedIn profile handoff, and lifecycle tracking.

Connection invitations and messages are human-reviewed and manually sent by default. Follow-up
copy becomes actionable only after connection acceptance is recorded. Direct sending requires an
approved LinkedIn partner API adapter; browser automation must never send invitations or messages.

### ADR-006: Evidence before outreach

The minimum-funds requirement is company-level evidence and cannot be inferred reliably from a
person profile. A lead cannot advance beyond verification without a recorded evidence URL and a
value meeting the configured threshold.

### ADR-007: Approved communications adapters only

The application may generate drafts and track states. It may send LinkedIn actions only through
an approved API adapter with explicit credentials and audit controls. Missing API access keeps
the system in manual-handoff mode.
