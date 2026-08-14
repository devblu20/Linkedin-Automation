# LinkedIn Lead Research Automation - Current Tasks

## Current phase

Phase 7A - BluQQ qualification and supervised outreach review.

Status: Completed on 2026-08-11; live Google OAuth authorization and LinkedIn smoke testing remain
manual prerequisites.

## Objective

Find London proprietary-trading firms, family offices, and small funds with independently
evidenced funds of at least GBP 1 million; rank senior decision-makers; generate BluQQ-specific
connection and post-connection message drafts; require human review; export the durable state to
Excel; and deliver it through Google Drive OAuth authorized by the intended account.

## Authorized work

- Add version 2 search definitions with strict required dimensions.
- Add firm type, minimum-funds evidence, executive priority, and scoring policy.
- Add durable outreach records and migration 0002.
- Generate concise messages grounded in BluQQ's services and discovery-call engagement path.
- Add a local review site to edit evidence and drafts, approve/reject leads, open LinkedIn profiles,
  and record connection/message lifecycle states.
- Show follow-up drafts only after a connection is marked connected.
- Extend Excel reporting with an `Outreach Review` sheet.
- Add Google OAuth authorization guidance for `marketingcodex77@gmail.com`.
- Add tests and update operating documentation.

## Constraints

- No browser-automated invitations or messages.
- No direct sending without approved LinkedIn Invitations/Messages API access.
- No CAPTCHA solving, evasion, credential storage, or account-password handling.
- A lead cannot advance beyond verification without evidence meeting the configured GBP threshold.
- BluQQ must be described as software-only: no custody, discretion, trading on behalf of clients,
  or investment advice.
- Normal tests must not contact LinkedIn, Google Drive, or enrichment services.

## Acceptance criteria

1. The London BluQQ configuration validates.
2. Strict required dimensions prevent location-only acceptance.
3. Fund evidence and the minimum threshold gate approval.
4. Senior-title and service-fit scoring is deterministic and tested.
5. Connection and follow-up drafts are editable and persist.
6. Follow-up is gated on a recorded accepted connection.
7. Excel includes the review queue and evidence.
8. The local review site never sends LinkedIn actions directly.
9. OAuth setup identifies the authorized Google account without storing secrets in the repository.
10. Ruff, formatting, mypy, migrations, and pytest pass.
