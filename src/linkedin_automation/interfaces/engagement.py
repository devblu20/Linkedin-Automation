"""Small enhancement endpoints, installed inside the existing authenticated workspace."""

from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.application.engagement import analytics, suggest
from linkedin_automation.infrastructure.message_ai import AIUnavailable


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(connection|follow_up)$")
    context: str = Field(default="", max_length=1000)
    existing: str = Field(default="", max_length=10000)


def install_engagement(app, repository, store):
    app.state.engagement = store
    from linkedin_automation.infrastructure.message_ai import MessageAI

    app.state.message_ai = MessageAI()

    def account(request):
        bridge = getattr(app.state, "browser_engagement", None)
        if bridge:
            return bridge.current()
        return (request.scope.get("state", {}).get("cloud_delivery_account") or {}).get(
            "account_id"
        )

    def lead_for(identifier):
        lead = repository.get_lead(identifier)
        if lead is None:
            raise HTTPException(404, "Lead not found in this workspace")
        return lead

    def history(lead, sender):
        rows = store.history(lead.profile_url, sender)
        # Existing app receipts remain the source of truth for sent totals. Include them in
        # drafting context when they have not yet been synced from the provider.
        existing = {(x["content"], x["outgoing"]) for x in rows}
        for message in repository.list_sent_messages():
            other = repository.get_lead(message.lead_id)
            if (
                message.status == "sent"
                and other
                and other.profile_url == lead.profile_url
                and (message.content, True) not in existing
            ):
                rows.append(
                    {
                        "id": str(message.id),
                        "content": message.content,
                        "outgoing": 1,
                        "sent_at": message.sent_at.isoformat(),
                        "classification": None,
                        "classification_source": "app delivery record",
                    }
                )
        return sorted(rows, key=lambda x: x["sent_at"])

    @app.get("/api/enhancements/status")
    def status(request: Request):
        sender = account(request)
        sync = getattr(app.state, "reply_sync", None)
        state = store.sync_state(sender) if sender else {}
        return {
            "ai_configured": app.state.message_ai.configured,
            "reply_sync_available": bool(sync),
            "last_sync": state.get("last_success"),
            "sync_error": state.get("error"),
            "sync_in_progress": bool(state.get("cursor")),
        }

    @app.get("/api/enhancements/analytics")
    def campaign_analytics(request: Request, run_id: UUID | None = None):
        if run_id and repository.get_run(run_id) is None:
            raise HTTPException(404, "Run not found in this workspace")
        return analytics(repository, store, run_id, account(request))

    @app.get("/api/enhancements/replies")
    def replies(request: Request):
        sender = account(request)
        urls = {x.profile_url: x.full_name for x in repository.list_leads()}
        rows = [
            x
            for x in store.history(account=sender)
            if not x["outgoing"] and x["profile_url"] in urls
        ]
        return [
            {
                "id": x["id"],
                "name": urls[x["profile_url"]],
                "content": x["content"],
                "sent_at": x["sent_at"],
                "classification": x["classification"],
                "classification_source": x["classification_source"],
                "followups_paused": True,
            }
            for x in rows[-100:][::-1]
        ]

    @app.post("/api/enhancements/replies/sync")
    def sync_replies(request: Request):
        sync = getattr(app.state, "reply_sync", None)
        sender = account(request)
        if not sync or not sender:
            raise HTTPException(409, "Connect your LinkedIn account for reply sync")
        try:
            if sync.current() != sender:
                raise HTTPException(409, "LinkedIn account changed; refresh before syncing")
            sync.cycle(force=True)
        except ValueError as error:
            raise HTTPException(503, str(error)) from None
        return {"message": "Reply sync checked. All received replies pause pending follow-ups."}

    @app.get("/api/enhancements/leads/{lead_id}/conversation")
    def conversation(lead_id: UUID, request: Request):
        lead = lead_for(lead_id)
        sender = account(request)
        return {
            "messages": [
                {
                    k: x.get(k)
                    for k in (
                        "content",
                        "outgoing",
                        "sent_at",
                        "classification",
                        "classification_source",
                    )
                }
                for x in history(lead, sender)[-100:]
            ],
            "followup_hold": store.hold(lead.profile_url, sender),
        }

    @app.post("/api/enhancements/leads/{lead_id}/draft")
    def draft(lead_id: UUID, payload: DraftRequest, request: Request):
        lead = lead_for(lead_id)
        sender = account(request)
        try:
            return suggest(
                lead,
                history(lead, sender),
                app.state.message_ai,
                kind=payload.kind,
                context=payload.context,
                existing=payload.existing,
                hold=store.hold(lead.profile_url, sender),
            )
        except AIUnavailable as error:
            raise HTTPException(503, str(error)) from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
