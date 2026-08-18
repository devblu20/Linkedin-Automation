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
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import LeadProcessingError
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInOutreach
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
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


def create_review_app(database_url: str | Path, run_id: UUID | None = None) -> FastAPI:
    """Create a local review application for all leads or one research run."""
    repository = DatabaseResearchRepository(database_url)
    repository.initialize()
    review = OutreachReview(repository)
    settings = RuntimeSettings.from_environment()
    reporter = ExcelReportWriter(settings.artifact_dir)
    outbound = OutboundOutreach(
        repository, PlaywrightLinkedInOutreach(settings.browser_data_dir)
    )

    def refresh_excel() -> None:
        """Regenerate each affected run workbook from the latest database state."""
        run_ids = {lead.run_id for lead in repository.list_leads(run_id)}
        for current_run_id in run_ids:
            current_run = repository.get_run(current_run_id)
            if current_run is None or current_run.dry_run or not current_run.report_path:
                continue
            definition = SearchDefinition.model_validate_json(current_run.definition_json)
            reporter.write(
                current_run,
                repository.list_leads(current_run_id),
                repository.list_outreach(current_run_id),
                definition.output.file_name,
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
        scope = f"Run {run_id}" if run_id is not None else "All Neon campaigns"
        return _DASHBOARD_HTML.replace("__SCOPE__", scope)

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
        changed = outbound.queue(payload.lead_ids)
        refresh_excel()
        return {"queued": changed}

    @app.post("/api/actions/send-connections")
    def send_connections(payload: BatchAction) -> dict[str, int]:
        try:
            sent = outbound.send_connections(payload.lead_ids)
            refresh_excel()
            return {"sent": sent}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/actions/check-connections")
    def check_connections() -> dict[str, int]:
        try:
            accepted = outbound.check_connections(run_id)
            refresh_excel()
            return {"accepted": accepted}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/actions/send-messages")
    def send_messages(payload: BatchAction) -> dict[str, int]:
        try:
            sent = outbound.send_messages(payload.lead_ids)
            refresh_excel()
            return {"sent": sent}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/leads/{lead_id}")
    def update_lead(lead_id: UUID, payload: ReviewUpdate) -> dict[str, object]:
        try:
            updated = review.update(lead_id, **payload.model_dump())
        except (LeadProcessingError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        refresh_excel()
        return asdict(updated)

    return app


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BluQQ Revenue Workspace</title>
<style>
:root{--ink:#172235;--muted:#667085;--line:#e5e9f0;--paper:#fff;--canvas:#f4f6f9;--nav:#101828;--blue:#2962ff;--cyan:#27c5d9;--green:#12a66a;--amber:#d98b16;--red:#d04444}
*{box-sizing:border-box}body{margin:0;font:14px Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:var(--canvas);color:var(--ink)}button,input,select,textarea{font:inherit}.shell{min-height:100vh;display:grid;grid-template-columns:250px 1fr}
aside{background:var(--nav);color:#fff;padding:28px 20px;display:flex;flex-direction:column;gap:30px}.brand{display:flex;align-items:center;gap:12px;font-size:21px;font-weight:800}.mark{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,var(--blue),var(--cyan));display:grid;place-items:center}.brand small{display:block;color:#98a2b3;font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase}.nav-label{color:#667085;font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin:0 10px 8px}.nav-item{display:flex;gap:11px;align-items:center;padding:11px 12px;border-radius:9px;color:#aeb7c6}.nav-item.active{background:#243147;color:#fff}.scope{margin-top:auto;padding:14px;border:1px solid #344054;border-radius:10px;color:#d0d5dd;font-size:12px}.scope b{display:block;color:#fff;margin-bottom:4px}
.workspace{min-width:0}.topbar{height:78px;background:#fff;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;padding:0 32px}.topbar h1{font-size:19px;margin:0}.topbar p{margin:4px 0 0;color:var(--muted);font-size:12px}.live{display:flex;align-items:center;gap:8px;color:var(--green);font-weight:700}.live:before{content:"";width:8px;height:8px;background:var(--green);border-radius:50%;box-shadow:0 0 0 4px #dcfaec}
main{padding:28px 32px;display:grid;gap:22px}.metrics{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:14px}.metric{background:var(--paper);border:1px solid var(--line);border-radius:13px;padding:18px}.metric span{display:block;color:var(--muted);font-size:12px;text-transform:capitalize}.metric b{display:block;font-size:30px;margin-top:8px}.metric:first-child{background:linear-gradient(145deg,#1d2939,#101828);color:#fff;border:0}.metric:first-child span{color:#cbd5e1}
.panel{background:var(--paper);border:1px solid var(--line);border-radius:14px;overflow:hidden}.toolbar{padding:17px 19px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}.search{min-width:260px;flex:1;position:relative}.search input,.toolbar select{width:100%;padding:10px 12px;border:1px solid #d0d5dd;border-radius:8px;background:#fff}.toolbar select{width:190px}.btn{border:0;border-radius:8px;padding:10px 13px;cursor:pointer;font-weight:700;background:#eef2f6;color:#344054}.btn.primary{background:var(--blue);color:#fff}.btn.dark{background:#172235;color:#fff}.btn:hover{filter:brightness(.96)}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:950px}th{text-align:left;padding:12px 16px;background:#f8fafc;color:#667085;font-size:11px;text-transform:uppercase;letter-spacing:.06em}td{padding:14px 16px;border-top:1px solid #edf0f4;vertical-align:middle}.person{display:flex;align-items:center;gap:11px}.avatar{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:#e8eeff;color:#2f56c5;font-weight:800}.person b{display:block}.person small,.sub{color:var(--muted)}.score{font-weight:800}.status{display:inline-block;padding:5px 9px;border-radius:99px;font-size:11px;font-weight:800;background:#eef2f6;color:#475467}.status.approved,.status.connected,.status.message_sent{background:#dcfaec;color:#087a4d}.status.needs_verification{background:#fff3d6;color:#985c00}.status.rejected{background:#fee4e2;color:#b42318}.status.connection_sent,.status.message_ready{background:#e8eeff;color:#2f56c5}.row-actions{display:flex;gap:7px}.icon-btn{border:1px solid #d0d5dd;background:#fff;border-radius:7px;padding:7px 9px;color:#344054;cursor:pointer;text-decoration:none}
.empty{padding:60px;text-align:center;color:var(--muted)}dialog{border:0;border-radius:16px;padding:0;width:min(760px,92vw);box-shadow:0 30px 80px #10182855}dialog::backdrop{background:#10182899}.modal-head{padding:20px 22px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.modal-head h2{margin:0;font-size:18px}.close{border:0;background:none;font-size:22px;cursor:pointer}.form-grid{padding:22px;display:grid;grid-template-columns:1fr 1fr;gap:15px}.field.full{grid-column:1/-1}.field label{display:block;font-size:12px;font-weight:700;margin-bottom:6px}.field input,.field textarea,.field select{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:8px}.field textarea{min-height:92px;resize:vertical}.profile-value{border:1px solid #e4e7ec;background:#f8fafc;border-radius:8px;padding:10px;white-space:pre-wrap;max-height:150px;overflow:auto}.modal-actions{padding:16px 22px;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.toast{position:fixed;right:24px;bottom:24px;background:#172235;color:#fff;padding:12px 16px;border-radius:9px;display:none}
@media(max-width:1000px){.shell{grid-template-columns:1fr}aside{display:none}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){main{padding:18px}.topbar{padding:0 18px}.metrics{grid-template-columns:1fr}.toolbar{flex-wrap:wrap}.toolbar select{width:100%}.form-grid{grid-template-columns:1fr}.field.full{grid-column:auto}}
</style>
</head>
<body><div class="shell">
<aside><div class="brand"><div class="mark">BQ</div><div>BluQQ<small>Revenue workspace</small></div></div><div><div class="nav-label">Workspace</div><div class="nav-item active">◆ Lead pipeline</div><div class="nav-item">◎ Campaigns</div><div class="nav-item">↗ Outreach</div><div class="nav-item">▦ Reports</div></div><div class="scope"><b>Database scope</b>__SCOPE__<br><br>LinkedIn sending remains manual.</div></aside>
<section class="workspace"><header class="topbar"><div><h1>London client pipeline</h1><p>Research, qualify and prepare human-reviewed outreach</p></div><div class="live">Neon connected</div></header>
<main><section id="metrics" class="metrics"></section><section class="panel"><div class="toolbar"><div class="search"><input id="search" placeholder="Search name, company, title or location"></div><select id="statusFilter"><option value="">All statuses</option></select><button class="btn" onclick="checkConnections()">Refresh connections</button><button class="btn dark" onclick="batch('/api/actions/queue')">Queue ready</button><button class="btn primary" onclick="batch('/api/actions/send-connections')">Send approved</button><button class="btn primary" onclick="batch('/api/actions/send-messages')">Send ready messages</button></div><div class="table-wrap"><table><thead><tr><th>Client</th><th>Company & location</th><th>Campaign</th><th>Score</th><th>Status</th><th>Actions</th></tr></thead><tbody id="leadRows"></tbody></table><div id="empty" class="empty">Loading Neon leads…</div></div></section></main></section></div>
<dialog id="editor"><form id="editForm"><div class="modal-head"><div><h2 id="editorName"></h2><div id="editorMeta" class="sub"></div></div><button type="button" class="close" onclick="editor.close()">x</button></div><div id="formGrid" class="form-grid"></div><div class="modal-actions"><span id="saveResult" class="sub">Changes are saved to Neon</span><div><a id="profileLink" class="icon-btn" target="_blank" rel="noopener">Open LinkedIn</a> <button class="btn primary" type="submit">Save client</button></div></div></form></dialog><div id="toast" class="toast"></div>
<script>
const statuses=['needs_verification','ready_for_review','approved','connection_queued','connection_sent','connected','message_ready','message_sent','rejected'];let leads=[],editing=null;const rows=document.getElementById('leadRows'),empty=document.getElementById('empty'),editor=document.getElementById('editor');
const label=s=>s.replaceAll('_',' ');const initials=n=>n.split(/\s+/).slice(0,2).map(x=>x[0]||'').join('').toUpperCase();
function field(title,value,name,type='text',full=false){const w=document.createElement('div');w.className='field'+(full?' full':'');const l=document.createElement('label');l.textContent=title;let el;if(type==='select'){el=document.createElement('select');for(const s of statuses){const o=document.createElement('option');o.value=s;o.textContent=label(s);o.selected=s===value;el.append(o)}}else{el=document.createElement(type==='textarea'?'textarea':'input');el.value=value??'';if(type!=='textarea')el.type=type}el.name=name;w.append(l,el);return w}
function profileField(title,value,full=false){const w=document.createElement('div');w.className='field'+(full?' full':'');const l=document.createElement('label'),v=document.createElement('div');l.textContent=title;v.className='profile-value';v.textContent=Array.isArray(value)?(value.join(' | ')||'Not visible'):value||'Not visible';w.append(l,v);return w}
function render(){const q=document.getElementById('search').value.toLowerCase(),sf=document.getElementById('statusFilter').value;const view=leads.filter(x=>{const l=x.lead,o=x.outreach,text=[l.full_name,l.company,l.current_title,l.headline,l.location].join(' ').toLowerCase();return(!q||text.includes(q))&&(!sf||o.status===sf)});rows.textContent='';empty.style.display=view.length?'none':'block';empty.textContent=leads.length?'No clients match these filters.':'No qualified clients found in Neon.';for(const item of view){const l=item.lead,o=item.outreach,tr=document.createElement('tr');tr.innerHTML=`<td><div class="person"><div class="avatar">${initials(l.full_name)}</div><div><b>${l.full_name}</b><small>${l.current_title||l.headline||'Title unavailable'}</small></div></div></td><td><b>${l.company||'Unknown company'}</b><div class="sub">${l.location||'Location unavailable'}</div></td><td><span class="sub">${l.run_id.slice(0,8)}</span></td><td><span class="score">${o.score}</span>/100</td><td><span class="status ${o.status}">${label(o.status)}</span></td><td><div class="row-actions"><button class="icon-btn" onclick="openEditor('${l.id}')">Review</button><a class="icon-btn" href="${l.profile_url}" target="_blank" rel="noopener">LinkedIn ↗</a></div></td>`;rows.append(tr)}}
async function load(){const r=await fetch('/api/leads');if(!r.ok)throw new Error('API '+r.status);leads=await r.json();render()}
async function loadSummary(){const s=await(await fetch('/api/summary')).json(),keys=['fetched','qualified','connection_sent','connected','message_sent'];document.getElementById('metrics').innerHTML=keys.map(k=>`<div class="metric"><span>${label(k)}</span><b>${s[k]??0}</b></div>`).join('')}
function openEditor(id){editing=leads.find(x=>x.lead.id===id);const l=editing.lead,o=editing.outreach;document.getElementById('editorName').textContent=l.full_name;document.getElementById('editorMeta').textContent=`${l.company||'Unknown company'} · ${l.location} · Run ${l.run_id}`;document.getElementById('profileLink').href=l.profile_url;const g=document.getElementById('formGrid');g.textContent='';g.append(profileField('Company size',l.company_size_max?`${l.company_size_min||1}-${l.company_size_max}`:'Unknown'),profileField('Self employed',l.self_employed?'Yes':'No / unknown'),profileField('Connections',l.connections),profileField('Followers',l.followers),profileField('About',l.about,true),profileField('Experience',l.experience,true),profileField('Education',l.education,true),profileField('Skills',l.skills,true),field('Firm type',o.firm_type,'firm_type'),field('Verified funds (GBP)',o.funds_gbp,'funds_gbp','number'),field('Evidence URL',o.fund_evidence_url,'fund_evidence_url'),field('Status',o.status,'status','select'),field('Evidence notes',o.fund_evidence_notes,'fund_evidence_notes','textarea',true),field('Connection message',o.connection_message,'connection_message','textarea',true),field('Follow-up message',o.follow_up_message,'follow_up_message','textarea',true));editor.showModal()}
document.getElementById('editForm').onsubmit=async e=>{e.preventDefault();const p=Object.fromEntries(new FormData(e.target));p.funds_gbp=p.funds_gbp?Number(p.funds_gbp):null;const r=await fetch(`/api/leads/${editing.lead.id}`,{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify(p)}),body=await r.json();if(!r.ok){notice(body.detail||'Save failed');return}editor.close();notice('Client updated in Neon');await Promise.all([load(),loadSummary()])};
async function eligible(){return leads.filter(x=>['ready_for_review','approved','connection_queued','message_ready'].includes(x.outreach.status)).map(x=>x.lead.id)}
async function batch(url){const ids=await eligible();if(!ids.length){notice('No eligible leads for this action');return}if(!confirm(`Run this explicit action for ${Math.min(ids.length,20)} lead(s)?`))return;const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({lead_ids:ids.slice(0,20)})}),body=await r.json();notice(r.ok?JSON.stringify(body):(body.detail||'Action failed'));if(r.ok)await Promise.all([load(),loadSummary()])}
async function checkConnections(){if(!confirm('Check pending connection statuses now?'))return;const r=await fetch('/api/actions/check-connections',{method:'POST'}),body=await r.json();notice(r.ok?JSON.stringify(body):(body.detail||'Check failed'));await Promise.all([load(),loadSummary()])}
function notice(t){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',3500)}
document.getElementById('search').oninput=render;document.getElementById('statusFilter').innerHTML+=[...statuses].map(s=>`<option value="${s}">${label(s)}</option>`).join('');document.getElementById('statusFilter').onchange=render;Promise.all([load(),loadSummary()]).catch(e=>{empty.textContent=`Could not load Neon data: ${e.message}`});setInterval(()=>Promise.all([load(),loadSummary()]),30000);
</script></body></html>"""
