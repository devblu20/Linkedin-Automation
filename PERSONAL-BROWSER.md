# Personal browser mode

The hosted dashboard remains authenticated and stores existing research on the same Railway
volume. Shared admin research uses the owner's Windows PC instead of Unipile when
`WEBSITE_CLOUD_MODE=0`, `PERSONAL_BROWSER_MODE=1`, and `LOCAL_DEVICE_PAIRING=0`.
Existing account bindings, user workspaces and pairing records remain stored. Personal mode
only bypasses remembered device selection for the shared admin's browser requests; it does
not grant private users access to shared admin records or disable authentication.

1. On this installed PC run `scripts/start-personal-browser.ps1` once. It updates this
   installation's existing Start-worker link and, if present, its existing startup shortcut.
2. Open `https://www.bluqq.com/linkedin-automation/admin`, sign in with the existing admin
   credentials, and open the pipeline. The worker should show online.
3. Select a campaign and click Search and fetch. The existing headed Chrome/Chromium
   collector opens on this PC. Complete any permitted LinkedIn login yourself and leave the
   window open during collection. Do not run a separate login window against the same profile
   at the same time.
4. Readable matching observations sync to the same hosted dashboard. Account restrictions,
   challenges, hidden results and search quotas still stop collection; this mode cannot
   remove them. Restore a restricted account through LinkedIn before searching.

The computer must be awake and online. No download or pairing is required for this existing
installation. The personal helper reads existing ignored admin credentials locally, launches
only this project and uses `browser-data-chrome`. The worker processes connection, acceptance
check and message jobs only after the existing recipient/draft authorization flow. New
authorizations are pinned to `PERSONAL_LINKEDIN_PROFILE_URL`; old unpinned or cloud-sender
approvals never transfer automatically. Before sending, the browser verifies its signed-in
profile. Messages additionally require a verified recipient, a first-degree connection, and
a fresh conversation read. Any incoming reply/opt-out pauses pending follow-ups. Unknown
layout, direction or timestamps block follow-ups rather than inventing evidence. Interrupted
sends retain the existing durable receipt and never resend blindly.

Unipile credentials are preserved for a reversible return to cloud mode. Google login and AI
configuration are retained; Google private workspaces remain separate from shared admin.

## Replies and AI

The existing Sync replies button queues a bounded, read-only check of up to five saved
connected/message leads on this PC. Successful batches rotate through larger lead lists.
The worker can refresh eligible conversations periodically; a failed check requires inspection
and an explicit Sync replies retry. Only visible, verified one-to-one messages are imported.
It does not claim to backfill an entire LinkedIn inbox or classify unreadable messages.
Observed replies reuse the existing AI classifier and durable opt-out holds. Message drafting,
follow-up suggestions and report analytics keep their original APIs and UI. OpenRouter is
still needed for AI generation/classification; no Unipile key or Brave key is needed for
the personal browser transport.

## LinkedIn sign-in from Outreach

After an exact connection note or message is authorized, the dashboard can launch the
installed personal helper when it has no worker heartbeat. Accept the Windows Open prompt
or use **Open LinkedIn sign-in** on the queued action. Run `scripts/start-personal-browser.ps1`
once to register the delivery link on the worker PC.

If the worker is online but its session has expired, the visible delivery Chrome window
waits for manual sign-in or verification. The worker rechecks the approved sender before
continuing. If the browser profile was already paused, the helper opens the normal recovery
window. In that window, finish sign-in and close it once the LinkedIn feed loads, so the
worker can reuse the profile. LinkedIn's verification and rate limits still apply.

Current LinkedIn layouts may expose Message as a full-page compose link. The adapter follows
only the verified profile link, validates its recipient ID and one selected recipient, and
checks the conversation before sending. First-party profile aliases are bound using that
recipient ID and the distinct verified sender name in platform author metadata. Displayed
Today/Yesterday and local 12-hour times are parsed in the worker PC timezone at minute
precision; unsupported dates remain unreadable rather than being guessed.

A reply keeps automatic follow-ups paused. A reviewer can approve one exact response using
the additional reviewed-reply checkbox. Approval is bound to the current incoming-history
signature, sender, recipient and draft. Any new/edited reply invalidates it, opt-outs cannot
be overridden, and the global follow-up hold is never cleared by this approval.

Reply-rate reporting can use the verified order of messages from one complete browser snapshot
when LinkedIn displays minute-level times. It matches a unique observed outgoing body to an
existing app delivery receipt and counts only replies appearing after that body in the same
snapshot/account/conversation. It never invents timestamp seconds or treats pre-message replies
as responses. Ambiguous repeated text or mixed snapshots remain excluded from this fallback.

In New campaign, enter the search defaults and choose **Add template manually** to save
a new qualification policy and message drafts. Firm types, priority roles, score threshold,
company size, self employment preference and optional funds requirements are reviewer supplied.
The template is persisted in the workspace's existing campaigns database, automatically selected,
and reusable in later new campaigns. Save campaign separately before searching. Templates do not
send anything, change existing campaigns/runs, increase delivery limits, or prove product demand.
Saved policies are immutable; create another template for different rules. Workers load custom
definitions separately from the authenticated dashboard and still verify job hashes and ceilings.

The manual template dialog also exposes editable search criteria and required-match checkboxes.
Review those fields before saving: changing a template/campaign name never changes its search.
Matching checks complete words/phrases, so Intern does not match International. Title filters and
qualification priorities use the structured current role when available, falling back to the
headline only when it is missing. Unknown required evidence remains unmatched. Existing run
observations and decisions are retained; corrected criteria apply to subsequent searches.
When titles are required and the existing subject query does not already express a target role,
bounded discovery queries pair one configured role with one subject term. Optional-role campaigns
keep their subject-only searches. The final role, location and keyword evidence checks remain
required; a focused query does not itself prove a profile matches.

The personal helper retries transient process-inventory and startup failures without launching
a duplicate worker when inventory is unknown. The Windows startup shortcut keeps the existing
browser profile and owner credentials. Delivery receipts are flushed to disk before proceeding;
a brief Windows lock retries only the atomic receipt replacement, never a LinkedIn send.

Reply timestamps support Today/Yesterday, recent weekday headings (strictly preceding 1-6 days),
and full dates with a year. Same-day weekday labels, missing dates and future full dates remain
blocked for inspection. Clock values retain their displayed minute precision. A real next-day
read-back is compared with previously observed message times to check that no dates shift or
duplicate messages are created. No additional outreach is needed for this read-only check.

Profile limits support up to 100 visits per run, subject to the selected campaign/template ceiling. Existing saved campaign limits must be edited explicitly; page budgets, daily collection limits, account checks and outreach limits are unchanged. A maximum of 100 visits does not guarantee 100 matching saved leads.
