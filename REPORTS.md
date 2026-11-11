# Reports workspace

Reports now has one research scope for analytics, record previews, downloads, and run history.
The Reports-only layout uses the existing BluQQ colors/navigation; other sections are preserved.

## User flow

1. Open **Reports** and choose **Research scope** (all runs or one recent run).
2. Review the five activity totals and the two rate cards. Expand the metric definitions for
   denominators and data-coverage details. Metrics are always based on recorded activity.
3. Choose **Saved leads** or **Collected profiles**. Saved leads support review-status and text
   filters. Collected profiles support accepted/rejected decisions. Irrelevant filters are hidden
   and are not sent to the API.
4. Check the matching count and paginated preview (10 rows/page). Download CSV or Excel to get
   all matching rows across pages. Existing 5,000-row export limits and spreadsheet/formula
   protections remain unchanged. Empty/error previews disable downloads; export failures display
   the server's message rather than reporting a successful download.
5. **Reset filters** clears the research scope and filters, retaining the selected dataset.
   **Refresh reports** reloads history, analytics and preview. Each history row's **View run**
   selects that same research scope. History/select options show the existing API's recent runs
   (currently up to 50); All runs metrics/exports include all available records, not just history.

Search/status/decision filters affect the record preview and download, while analytics describes
the selected research run(s). This distinction is intentional: review status does not describe
historical invitations/messages. Replies are only those already synced into the workspace.

## Implementation

* New `interfaces/reports_workspace.py`: Reports-only HTML, responsive styles and controller.
* `workspace_navigation.py`: mounts that section/controller; preserves campaign/outreach handlers.
* `engagement_ui.py`: removes the older separate analytics widget/run selector; retains AI editor
  controls, conversations and incoming-reply UI.
* `report_exports.py`: new authenticated `GET /api/reports/preview` and `/api/reports/analytics`.
  Preview and the unchanged CSV/Excel routes share run/scope/filter validation and lead matching.
  Analytics reuses the existing analytics function and engagement store; local research-only pages
  show no synced replies. Neither endpoint calls AI/Unipile or sends anything.
* `observation_view.py`: run-history counts come from persisted observations instead of potentially
  incomplete cached counters in older imported runs. No database records or statuses are updated.

The controller protects against stale async results, handles loading/empty/error states, keeps
pagination and export availability in sync with current filters, and uses text nodes for data.
CSV/Excel filenames retain the server's timestamp rather than overwriting the same generic name.

No schema migrations, new packages, API keys, provider changes, or sending changes are required.

## Verification

`tests/integration/test_reports_workspace.py` checks pagination/counts/export parity, scoped
analytics, zero denominators, invalid filters, read-only behavior and authentication boundaries.
Existing export tests cover Unicode, formula escaping, XML cleanup, row limits, and private fields.

`tests/browser/check_reports_workspace.py` verifies shared research scope, dataset-specific filters,
CSV and Excel contents, pagination, empty results, error recovery, history navigation, JavaScript
syntax and a 390px mobile viewport without page overflow. It uses isolated fictional records and
makes no external-provider calls. Screenshots are saved under ignored `data/`.
