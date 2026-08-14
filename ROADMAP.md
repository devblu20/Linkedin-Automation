# LinkedIn Lead Research Automation — Roadmap

## 1. Roadmap Rules

- Implement one phase at a time.
- `TASKS.md` defines the exact work authorized in the current implementation.
- A phase is complete only after its acceptance criteria pass.
- Future-phase code must not be added speculatively.
- Any architectural change must update `ARCHITECTURE.md` before implementation.

## 2. Current Status

- Completed: Phases 0–6
- Current: Phase 7A - BluQQ qualification and supervised outreach review
- Future: Phase 7B approved LinkedIn communications adapter

## Phase 0 — Product and Architecture Definition

Status: Completed

### Outcomes

- Defined the supervised lead-research workflow.
- Selected a modular-monolith, ports-and-adapters architecture.
- Defined browser automation safety boundaries.
- Defined the Excel and Google Drive delivery direction.
- Created project governance through `ARCHITECTURE.md`, `ROADMAP.md`, and `TASKS.md`.

### Exit criteria

- Architecture, scope, constraints, and scalability path are documented.
- The first implementation phase is explicitly scoped.

## Phase 1 — Project Foundation and Search Contract

Status: Completed

### Goal

Create a tested Python foundation that can validate and normalize declarative lead-search files without accessing LinkedIn or Google Drive.

### Planned capabilities

- Python package and development tooling.
- Strict typed YAML search-definition schema.
- Domain models required for configuration and run identity.
- Configuration loader with actionable errors.
- `validate` CLI command.
- Structured logging baseline.
- Example search definition.
- Unit tests and project documentation.

### Exit criteria

- A valid example YAML file passes CLI validation.
- Invalid and unknown fields produce useful errors and a non-zero exit status.
- Ruff, mypy, and pytest pass.
- No secrets or generated artifacts are tracked.
- No real browser, LinkedIn, Excel, database, or Drive implementation exists yet.

## Phase 2 — Run Persistence and Lead Processing

Status: Completed

### Goal

Add durable run state and framework-independent lead normalization, matching, scoring, and deduplication.

### Planned capabilities

- SQLAlchemy models and Alembic migrations.
- SQLite repositories implementing application ports.
- Run lifecycle and valid state transitions.
- Raw observation and normalized lead storage.
- Canonical LinkedIn URL normalization.
- Include/exclude matching evidence.
- Resume checkpoints at the application level.

### Exit criteria

- Runs and observations persist across processes.
- Duplicate profile URLs resolve deterministically.
- Unit and integration tests cover state transitions and repositories.

## Phase 3 — Supervised LinkedIn Browser Adapter

Status: Completed

### Goal

Collect a small, bounded set of LinkedIn people-search results through a user-visible authenticated browser session.

### Planned capabilities

- Persistent Playwright Chromium context.
- Manual login and authentication-state detection.
- Search query construction from validated definitions.
- Page parsing isolated behind the LinkedIn port.
- Conservative pacing and strict limits.
- Checkpoint, CAPTCHA, rate-limit, and layout-change detection.
- Diagnostic screenshots and resumable page checkpoints.
- Sanitized fixture-based parser tests.

### Exit criteria

- A supervised low-volume search collects normalized candidates.
- Security challenges pause instead of being bypassed.
- Selector failures yield typed, diagnosable errors.
- Normal CI tests do not contact LinkedIn.

## Phase 4 — Excel Reporting

Status: Completed

### Goal

Generate a safe, readable, deterministic Excel workbook for a completed or partial research run.

### Planned capabilities

- `Leads` and `Run Summary` sheets.
- Stable columns, filters, frozen headers, widths, and hyperlinks.
- Formula-injection protection.
- Atomic artifact creation.
- Report validation and integration tests.

### Exit criteria

- Workbooks open successfully and contain expected records and metadata.
- Unsafe cell prefixes are escaped.
- Re-export is deterministic and does not corrupt an existing report.

## Phase 5 — Google Drive Delivery

Status: Completed (mocked verification; manual OAuth smoke test pending)

### Goal

Upload a verified workbook to a configured Google Drive folder and record the remote artifact identity.

### Planned capabilities

- OAuth 2.0 authorization flow.
- Narrow Drive permissions.
- Environment-backed folder configuration.
- Idempotent upload policy.
- Retry handling for transient failures.
- Persisted Drive file ID and URL.
- Mocked integration tests and a manual smoke test.

### Exit criteria

- A report uploads to a test folder.
- Re-running delivery does not create an uncontrolled duplicate.
- Credentials and tokens remain outside version control.

## Phase 6 — End-to-End Reliability

Status: Completed (offline verification; authenticated LinkedIn smoke test pending)

### Goal

Join validation, collection, processing, reporting, and upload into a resilient supervised workflow.

### Planned capabilities

- `run`, `resume`, and `status` commands.
- End-to-end run orchestration.
- Bounded retries and recovery guidance.
- Retention and cleanup policies.
- Operational documentation.
- Packaging and repeatable local setup.

### Exit criteria

- A user can complete and resume a bounded research run.
- Run summaries reconcile with database and workbook counts.
- Failures are actionable and do not leak secrets.

## Phase 7 — Production Hardening and Optional Extensions

Status: Future

### Goal

Prepare for broader authorized use only after the single-user workflow is stable.

### Possible capabilities

- PostgreSQL adapter.
- Web/API interface.
- Managed secret storage.
- Multi-user authorization and tenant boundaries.
- Object storage and retention automation.
- Official LinkedIn API or authorized-provider adapter.
- CRM and enrichment adapters.
- Non-browser background workers.

These items require separate approval and must not be implemented as part of earlier phases.

### Phase 7A - BluQQ Qualification and Supervised Outreach

Status: Completed (implementation; live Google OAuth authorization pending user credentials)

Add version 2 strict search definitions, evidence-backed GBP 1 million qualification, executive
ranking, BluQQ-specific drafts, a durable review queue, a local review site, an Excel outreach
sheet, and Google Drive authorization guidance for the intended account.

Exit criteria: strict matching prevents location-only acceptance; fund evidence gates approval;
follow-up messaging is gated on recorded acceptance; the site performs no LinkedIn send action;
and all migrations and automated quality checks pass.

### Phase 7B - Approved LinkedIn Communications Adapter

Status: Blocked on approved LinkedIn partner access

Direct invitation and messaging calls may be implemented only after valid LinkedIn API approval,
scopes, credentials, rate limits, and audit requirements are supplied. Browser-based sending is
not an acceptable substitute.

## 3. Next Recommended Task

Complete Phase 7A, then run a low-volume London search, qualify one firm, review one connection
draft, record one accepted connection, verify the follow-up draft, and upload through OAuth
authorized as `marketingcodex77@gmail.com`.
