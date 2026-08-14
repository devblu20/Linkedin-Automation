"""Local human-review site for qualification and outreach drafts."""

# ruff: noqa: E501 - the embedded HTML/JavaScript is intentionally kept as one static asset.

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.application.outbound import OutboundOutreach
from linkedin_automation.application.outreach import OutreachReview
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.services import LeadProcessingError
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInOutreach
from linkedin_automation.infrastructure.runtime import RuntimeSettings


class ReviewUpdate(BaseModel):
    """Validated browser payload for a human review decision."""

    model_config = ConfigDict(extra="forbid")

    firm_type: str = ""
    funds_gbp: int | None = Field(default=None, ge=0)
    fund_evidence_url: str = ""
    fund_evidence_notes: str = ""
    connection_message: str = ""
    follow_up_message: str = ""
    status: OutreachStatus


class BatchAction(BaseModel):
    """Lead IDs selected by one explicit dashboard action."""

    model_config = ConfigDict(extra="forbid")
    lead_ids: list[UUID] = Field(min_length=1, max_length=20)


def create_review_app(database_path: Path, run_id: UUID) -> FastAPI:
    """Create a local-only review application for one immutable research run."""
    repository = SqliteResearchRepository(database_path)
    repository.initialize()
    review = OutreachReview(repository)
    outbound = OutboundOutreach(
        repository, PlaywrightLinkedInOutreach(RuntimeSettings.from_environment().browser_data_dir)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        repository.close()

    app = FastAPI(
        title="Lead Qualification Review", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _DASHBOARD_HTML.replace("__RUN_ID__", str(run_id))

    @app.get("/api/leads")
    def list_leads() -> list[dict[str, object]]:
        return [
            {"lead": asdict(item.lead), "outreach": asdict(item.outreach)}
            for item in review.list(run_id)
        ]

    @app.get("/api/summary")
    def summary() -> dict[str, object]:
        statuses = [record.status for record in repository.list_outreach(run_id)]
        return {
            "fetched": len(repository.list_leads(run_id)),
            "qualified": sum(s not in {OutreachStatus.NEEDS_VERIFICATION,
                                       OutreachStatus.REJECTED} for s in statuses),
            "connection_sent": sum(s in {OutreachStatus.CONNECTION_SENT,
                OutreachStatus.CONNECTED, OutreachStatus.MESSAGE_READY,
                OutreachStatus.MESSAGE_SENT} for s in statuses),
            "connected": sum(s in {OutreachStatus.CONNECTED,
                OutreachStatus.MESSAGE_READY, OutreachStatus.MESSAGE_SENT} for s in statuses),
            "message_sent": statuses.count(OutreachStatus.MESSAGE_SENT),
            "connection_requests": [asdict(x) for x in
                                    repository.list_connection_requests(run_id)],
            "messages": [asdict(x) for x in repository.list_sent_messages(run_id)],
        }

    @app.post("/api/actions/queue")
    def queue_connections(payload: BatchAction) -> dict[str, int]:
        return {"queued": outbound.queue(payload.lead_ids)}

    @app.post("/api/actions/send-connections")
    def send_connections(payload: BatchAction) -> dict[str, int]:
        try:
            return {"sent": outbound.send_connections(payload.lead_ids)}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/actions/check-connections")
    def check_connections() -> dict[str, int]:
        try:
            return {"accepted": outbound.check_connections(run_id)}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/actions/send-messages")
    def send_messages(payload: BatchAction) -> dict[str, int]:
        try:
            return {"sent": outbound.send_messages(payload.lead_ids)}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/leads/{lead_id}")
    def update_lead(lead_id: UUID, payload: ReviewUpdate) -> dict[str, object]:
        try:
            updated = review.update(lead_id, **payload.model_dump())
        except (LeadProcessingError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return asdict(updated)

    return app


_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Lead Review</title><style>
body{font:14px system-ui;margin:0;background:#f5f7fb;color:#172033}header{padding:20px 28px;background:#17365d;color:white}
main{padding:24px;display:grid;gap:18px}.card{background:white;border:1px solid #d8deea;border-radius:10px;padding:18px}.funnel{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.metric{background:white;border-radius:8px;padding:14px;text-align:center}.metric b{display:block;font-size:26px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}label{font-weight:600}input,textarea,select{box-sizing:border-box;width:100%;padding:8px;border:1px solid #b8c2d3;border-radius:6px}textarea{min-height:74px}
.actions{display:flex;gap:10px;align-items:center;margin-top:12px}button,a.button{background:#0a66c2;color:white;border:0;border-radius:6px;padding:9px 13px;text-decoration:none;cursor:pointer}.meta{color:#58657a}.error{color:#a11}
@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style></head><body><header><h1>BluQQ LinkedIn outreach dashboard</h1><div>Run __RUN_ID__ · Every outbound batch requires an explicit click.</div></header><main><div id="funnel" class="funnel"></div><div class="actions"><button onclick="batch('/api/actions/queue')">Queue ready leads</button><button onclick="batch('/api/actions/send-connections')">Approve &amp; Send Connections</button><button onclick="batch('/api/actions/send-messages')">Send Ready Messages</button><button onclick="checkConnections()">Check Connections</button><span id="batchResult"></span></div><div id="leads">Loading…</div></main>
<script>
const root=document.getElementById('leads');
async function loadSummary(){const s=await(await fetch('/api/summary')).json();document.getElementById('funnel').innerHTML=['fetched','qualified','connection_sent','connected','message_sent'].map(k=>`<div class="metric"><b>${s[k]}</b>${k.replaceAll('_',' ')}</div>`).join('')}
async function eligible(){const data=await(await fetch('/api/leads')).json();return data.filter(x=>['ready_for_review','approved','connection_queued','message_ready'].includes(x.outreach.status)).map(x=>x.lead.id)}
async function batch(url){const ids=await eligible();if(!ids.length){alert('No eligible leads for this action');return}if(!confirm(`Run this explicit batch action for ${ids.length} eligible lead(s)?`))return;const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({lead_ids:ids.slice(0,20)})});const body=await r.json();document.getElementById('batchResult').textContent=r.ok?JSON.stringify(body):(body.detail||'Action failed');if(r.ok){await load();await loadSummary()}}
async function checkConnections(){if(!confirm('Check pending connection statuses now?'))return;const r=await fetch('/api/actions/check-connections',{method:'POST'});document.getElementById('batchResult').textContent=JSON.stringify(await r.json());await load();await loadSummary()}
function field(label,value,name,type='text'){const wrap=document.createElement('div');const l=document.createElement('label');l.textContent=label;const el=document.createElement(type==='textarea'?'textarea':'input');el.name=name;el.value=value??'';if(type!=='textarea')el.type=type;wrap.append(l,el);return wrap}
async function load(){const data=await(await fetch('/api/leads')).json();root.textContent='';for(const item of data){const l=item.lead,o=item.outreach,c=document.createElement('section');c.className='card';const h=document.createElement('h2');h.textContent=`${l.full_name} — ${l.current_title||l.headline}`;const meta=document.createElement('div');meta.className='meta';meta.textContent=`${l.company||'Unknown company'} · ${l.location} · Score ${o.score}`;const form=document.createElement('form');const g=document.createElement('div');g.className='grid';g.append(field('Firm type',o.firm_type,'firm_type'),field('Verified funds (GBP)',o.funds_gbp,'funds_gbp','number'),field('Evidence URL',o.fund_evidence_url,'fund_evidence_url'),field('Evidence notes',o.fund_evidence_notes,'fund_evidence_notes','textarea'),field('Connection message',o.connection_message,'connection_message','textarea'));if(['connected','message_sent'].includes(o.status)){g.append(field('Post-connection message',o.follow_up_message,'follow_up_message','textarea'))}else{const hidden=document.createElement('input');hidden.type='hidden';hidden.name='follow_up_message';hidden.value=o.follow_up_message;g.append(hidden)}const status=document.createElement('select');status.name='status';for(const s of ['needs_verification','ready_for_review','approved','connection_sent','connected','message_sent','rejected']){const option=document.createElement('option');option.value=s;option.textContent=s.replaceAll('_',' ');option.selected=s===o.status;status.append(option)}const sw=document.createElement('div');const sl=document.createElement('label');sl.textContent='Status';sw.append(sl,status);g.append(sw);const actions=document.createElement('div');actions.className='actions';const save=document.createElement('button');save.textContent='Save review';save.type='submit';const open=document.createElement('a');open.className='button';open.href=l.profile_url;open.target='_blank';open.rel='noopener';open.textContent='Open LinkedIn';const result=document.createElement('span');actions.append(save,open,result);form.append(g,actions);form.onsubmit=async e=>{e.preventDefault();const f=new FormData(form);const payload=Object.fromEntries(f);payload.funds_gbp=payload.funds_gbp?Number(payload.funds_gbp):null;const response=await fetch(`/api/leads/${l.id}`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(payload)});const body=await response.json();result.className=response.ok?'':'error';result.textContent=response.ok?'Saved':(body.detail||'Could not save');if(response.ok)setTimeout(load,500)};c.append(h,meta,form);root.append(c)}}
loadSummary();load().catch(e=>{root.textContent=`Could not load review queue: ${e}`});
</script></body></html>"""
