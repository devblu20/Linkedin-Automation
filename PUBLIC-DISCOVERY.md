# Public-web campaign discovery

The additive Public-web prospects panel uses the selected campaign's subjects,
roles and locations to search Brave's public web index for LinkedIn `/in/` links.
New campaign criteria are resolved at submission time and snapshotted for that run.
No LinkedIn browser, cookie or account login is used. Hidden identities are not
unlocked; public snippets may be incomplete or stale. A target of 60 is a maximum,
not a guarantee. Runs stop after at most 25 provider requests; failures are not retried.

Search interleaves precise role/business queries with broader campaign queries.
Additional public excerpts are requested in the same search call. Role evidence
comes from the public headline, not incidental snippet mentions; names and query
terms are not matching evidence. Location-only hits cannot fill a business campaign.
Results rank by fewer missing required dimensions, then more evidenced dimensions.
Incomplete targets may be replaced by stronger standalone observations within the
same request cap. Duplicate URLs retain their strongest single observation; evidence
from different observations is not combined to manufacture a match. Four consecutive
requests without an improved selection stop discovery early. No independent company
website verification is performed by this flow; current employment stays unverified.

## Owner configuration

- `BRAVE_SEARCH_API_KEY`: Search plan key, backend environment only.
- `BRAVE_SEARCH_STORAGE_ALLOWED=0`: default preview mode. Results remain only in
  process memory for 30 minutes (up to ten previews per workspace), disappear on
  restart, and cannot be exported or added to the research database.
- `BRAVE_SEARCH_STORAGE_ALLOWED=1`: use only after verifying that the selected
  provider plan permits result storage/export. This enables isolated workspace
  `public-discovery.sqlite3` storage and CSV downloads.

The ignored `.env.brave.local` file is a local provisioning input, not a frontend
asset. It must not be committed, packaged or logged. The Railway service requires
the variables in its own environment. Local settings are not uploaded automatically.

## User flow

Select an existing or newly created campaign in the pipeline. In Public-web
prospects, choose a target (1–100), then Discover public profiles. Results include
source URL, public title/snippet, matching criteria and missing evidence. Preview
results are temporary. The CSV button is available only for storage-permitted runs.

The web form starts with a five-request cap, adjustable from 1 to 25. The API
accepts `max_requests` (defaults to 25 for backward compatibility). The cap is
enforced server-side. The workspace usage counter tracks attempted provider calls,
including failed calls, from the time this release is installed. Aggregate monthly
UTC counters persist without identities, queries or result content. These counters
are not Brave billing records or remaining credits, and exclude earlier/other-app
usage. Use the linked Brave Credits dashboard to check actual credits and auto-reload.
Preview expiry time is displayed; expired previews are cleared from the interface.

Public prospects are separate from approved leads. Company, current job and buying
intent are never inferred from a query. Incomplete evidence remains marked for
verification. This module never approves a prospect, queues outreach, changes a
connection status or sends a message. Existing LinkedIn workflows remain separate
and still require a usable account and the existing delivery authorizations.

With storage rights enabled, select prospects and click **Save
selected for review**. The adapter uses the discovery run's original campaign
snapshot, never current edited rules. Stable run/profile IDs prevent repeated
clicks from duplicating leads within that discovery or overwriting review work.
Saved records always start at `needs_verification`; public snippets remain labeled
in the profile snapshot and role/company/location stay unknown until reviewed.
Incomplete prospects are also saved for manual verification, with missing criteria
recorded in their notes and evidence. Scores come from the same existing qualification
engine; no points or approval are invented to fill missing information. Import never invokes
automatic qualification, automatic queue preparation, LinkedIn or Unipile APIs.
Use the existing manual review and exact sender/recipient/draft delivery approvals
after verifying details. CSV downloads do not import records or authorize sending.

Completed storage-enabled discoveries sync automatically to the existing research
pipeline: every prospect becomes a Collected profile observation, with missing
required evidence recorded as a rejection reason. All discovered prospects also
become scored Saved leads in `needs_verification`. Collection decisions remain
separate from manual review status. Existing review work stays unchanged.
Saved review rows default to newest collected first. The **Review Brave profiles**
shortcut clears the current name/status filters and shows Brave-source rows in
the current workspace, with their existing scores/statuses. Source and sort filters
do not change underlying records or delivery approvals. The table and summary
refresh every 30 seconds while visible and no editor is open.

Explicitly labeled public-index location, current role/company, industry, about,
experience, education, skills and follower/connection counts are extracted when
available. A clear role-at-company headline also supplies unverified public fields.
Incidental locations, previous employers, query terms and truncated values do not
become current facts. The public panel expands available details with missing-field
labels; public CSV includes the extracted values and field evidence. New leads and
observations carry these fields into existing review/report screens. Re-syncing an
older stored run can fill missing fields on unreviewed public leads only, preserving
original observations, human edits, drafts and approved/delivery statuses. No extra
Brave API requests are made for this text extraction. Complete LinkedIn profile
coverage is not guaranteed by a public search index.

New discoveries automatically parse every result and use remaining request budget
for exact-profile public lookups, prioritizing missing role/company/location. Runs
with a cap of at least four reserve one request for detail checks; discovery stops
at the requested candidate count rather than spending the remainder on broader
queries. Discovery and detail checks share the same server-enforced cap. Results
must match the exact canonical profile URL and display name; no new recipients
are introduced by detail checking. Additional labeled fields retain their excerpt
and query evidence, never overwrite conflicting existing values, and do not confer
automatic approval. Two non-improving lookups stop early. Provider failure stops
detail checks without retry and preserves discovered prospects. Some profiles may
receive text-only checks when the budget is exhausted. No full-profile guarantee
or independently verified current employment is implied.
Stable run/profile/observation IDs make repeated sync idempotent. Reports and
analytics reuse those records; CSV exports include the research source. Use
**Sync to pipeline / reports** for older completed stored discoveries or to retry
sync without another Brave API request. Preview-only runs never sync.

## Validation

Offline tests cover 60 unique prospects, deduplication, dynamic campaign queries,
missing evidence, unsafe URLs, provider errors without retries/key disclosure,
transient preview expiry/no result persistence, workspace isolation, authentication,
CSRF, request bounds and CSV gating/formula protection.

One real Search API request is sufficient to validate a key. Never mark a mocked
60-result test as a real prospect collection. Live counts must be reported separately.
