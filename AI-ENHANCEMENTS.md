# Optional AI and conversation enhancements

These additions preserve existing campaign definitions, research records, connection statuses,
draft-save guards, exact-text authorization, daily limits, delivery receipts, and retry rules.
They add no dependencies and do not replace the Unipile transport or sending methods.

## Configuration

Set backend environment variables (never put them in frontend code):

```dotenv
MESSAGE_AI_PROVIDER=openrouter
OPENROUTER_API_KEY=<owner key>
MESSAGE_AI_MODEL=google/gemini-2.5-flash
```

For direct OpenAI use `MESSAGE_AI_PROVIDER=openai`, `OPENAI_API_KEY`, and a model supporting
Responses structured outputs. A key and model must both be configured. Provider authentication,
quota, refusal, truncation, or schema errors return a safe failure; no saved draft is changed.
The ignored `.env.openrouter.local` is a local secret file, **not automatically loaded**.
Load it explicitly for a local process, or set the variables on the existing LinkedIn Railway
service before release. Never upload this file as source or change another service's settings.

OpenRouter requests require JSON-schema support and deny providers that collect data. Requests
contain the selected profile fields and conversation context, sent through OpenRouter to the
model provider. Direct OpenAI requests use `store=false`. No browser receives API keys.

Reply sync needs the existing connected Unipile V1 account and **Messaging Read** permission.
No extra Unipile key or webhook setup is necessary. V2 reply sync is not implemented; it reports
unavailable rather than guessing V1/V2 identity semantics. Existing V2 research is unaffected.

## Use

* Open a lead's existing Review editor. Personalize the connection draft, or suggest a follow-up
  after conversation history exists. Review/edit the suggestion, click **Use reviewed suggestion
  in draft**, then use the existing **Save client** action. Generating/applying a suggestion does
  not save, authorize, schedule, send, or change lead status. Authorized/running drafts retain
  the existing edit guard.
* The model selects relevant **saved** profile facts and neutral wording. Backend code inserts
  the exact name, role, company, or skills; the model cannot invent factual prose. Optional
  offer/context is supplied by the reviewer and inserted verbatim. This intentionally constrains
  phrasing; it is not unrestricted AI copywriting. Repeated drafts and outreach to a recipient
  expressing disinterest are blocked. User approval of a suggestion never resumes a reply hold.
* **Outreach → Incoming replies** shows the latest 100 synced replies and their intent. Use
  **Sync replies** or allow the existing cloud loop to poll every 60 seconds. The five intent
  labels are Interested, Not Interested, Question, Meeting Request, and General Reply. Explicit
  opt-outs use a conservative rule even without AI; other classification remains pending until
  AI is available. Classification is advisory and is not an approval or an automatic reply.
* Any validated incoming user reply, including an attachment-only reply, durably pauses pending
  follow-ups. The queue displays this hold separately; connection statuses remain unchanged.
  Acceptance checking continues, but held follow-ups cannot be claimed. There is deliberately no
  automatic resume. A fresh read also checks replies immediately before a cloud message send;
  read failure stops the send. Polling cannot guarantee detection of an external reply arriving
  in the small interval between the final read and the provider write.
* **Reports → Campaign analytics** shows all runs or one selected research run. Profiles searched
  counts collected observations, including repeats (not hidden cards). Accepted/sent invitation
  counts and message totals reuse existing app records; manual activity already writes these
  records and is not counted twice. Replies count synced incoming messages for saved profiles.
  Acceptance rate = accepted invitations / sent invitations. Reply rate = distinct app-messaged
  profiles with a later reply / distinct app-messaged profiles. Empty denominators give 0%.
  Provider-only outgoing history informs suggestions but is not added to existing app send totals.

## Storage and sync boundaries

New `engagement.sqlite3` resides alongside each workspace's existing databases. Additive tables
store provider message IDs, text, account/recipient bindings, classifications, persistent reply
holds, and sync pagination checkpoints. Existing research tables and records are not migrated.
Workspace roots remain isolated. Current-account filtering is used where the gateway supplies
the connected identity; shared reviewer access stays within its existing shared workspace.

Sync uses read-only V1 message/chat/profile methods, validates the connected account and exact
profile identity, and ignores unknown recipients, group chats, hidden/deleted/system events.
One page (up to 100 messages) is processed per polling cycle; cursor persists across restarts.
An overlap when advancing the watermark plus `(account,id)` dedup avoids duplicate reply counts.
Initial history may take several cycles. History not available through the provider cannot be
reconstructed. Only the latest 20 conversation entries enter a draft prompt. No AI auto-replies
or additional delivery actions are introduced.

## New modules

* `application/engagement.py`: grounded suggestions and analytics.
* `infrastructure/engagement_store.py`: additive conversation/hold storage.
* `infrastructure/message_ai.py`: optional OpenRouter/OpenAI adapter, bounded and validated output.
* `infrastructure/reply_sync.py`: read-only account-scoped sync and pre-send check.
* `interfaces/engagement.py`: authenticated workspace endpoints.
* `interfaces/engagement_ui.py`: existing-theme editor/Outreach/Reports additions.

Integration edits: `hosted_site.py` registers endpoints/UI; `cloud_search_runner.py` polls replies;
`website_cloud.py` ensures the existing account runner for enhancement requests;
`outreach_delivery.py` checks/displays recipient holds; `cloud_delivery.py` checks before sending;
`delivery_ui.py` displays the separate hold without adding any connection status.

Tests use fake AI/Unipile providers and isolated databases. Live AI smoke tests use synthetic data
only; no LinkedIn invitation or message is sent by these tests.

Validation on 2026-10-30:

* Complete regression suite: **321 passed**, five existing deprecation warnings.
* Final enhancement API/error-handling recheck: **18 passed**.
* All changed integration/new modules and tests passed Ruff checks.
* Headless Chrome: editable suggestion generation/apply, Reports analytics, and Outreach replies
  verified with a fictional lead and fake AI. All 11 scripts compiled; no page errors.
* Actual saved OpenRouter key + `google/gemini-2.5-flash`: one synthetic opt-out classified as
  Not Interested and one fictional-profile draft generated and validated. Neither was sent.
* No Railway deployment, production database changes, or real LinkedIn actions were performed.
  Real Unipile inbox sync/classification still needs deployment and a controlled live check.
