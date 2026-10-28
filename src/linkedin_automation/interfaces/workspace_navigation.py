"""Workspace navigation over existing campaign, review and report APIs."""

import json

# ruff: noqa: E501 - embedded dashboard assets.

WORKSPACE_NAV = """<nav class="workspace-nav" aria-label="Workspace">
<div class="nav-label">Workspace</div>
<a class="nav-item active" href="#pipeline" data-view="pipeline" aria-current="page">◆ Lead pipeline</a>
<a class="nav-item" href="#campaigns" data-view="campaigns">◎ Campaigns</a>
<a class="nav-item" href="#outreach" data-view="outreach">↗ Outreach</a>
<a class="nav-item" href="#reports" data-view="reports">▦ Reports</a>
</nav>"""

WORKSPACE_STYLE = """<style>
[hidden]{display:none!important}.workspace-nav .nav-item{text-decoration:none;cursor:pointer}.workspace-nav .nav-item:hover{background:#243147}.workspace-nav .nav-item:focus-visible{outline:2px solid #27c5d9;outline-offset:2px}.view-heading{padding:20px;margin:0;border-bottom:1px solid var(--line)}.view-heading h2{font-size:18px;margin:0 0 6px}.view-heading p{margin:0;color:var(--muted)}.view-status{padding:12px 20px;color:var(--muted)}.view-status:empty{display:none}.workspace-grid{padding:20px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}.workspace-grid label{display:block;font-size:12px;font-weight:700;margin-bottom:7px}.workspace-grid input,.workspace-grid select{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:8px;background:white}.draft-preview{white-space:pre-wrap;max-width:420px;max-height:120px;overflow:auto;color:var(--muted)}.view-actions{display:flex;gap:8px;flex-wrap:wrap}.report-card{border:1px solid var(--line);border-radius:10px;padding:18px}.report-card h3{margin:0 0 8px}
@media(max-width:1000px){aside{display:block;padding:16px 18px}.brand{margin-bottom:12px}.workspace-nav{display:flex;flex-wrap:wrap;gap:5px}.workspace-nav .nav-label,aside .scope{display:none}.workspace-nav .nav-item{padding:9px 12px}}@media(max-width:650px){.workspace-grid{grid-template-columns:1fr}.topbar{height:auto;min-height:78px;gap:12px;padding-top:12px;padding-bottom:12px}}
</style>"""

WORKSPACE_PANELS = """
<section class="panel" id="campaignsView" data-workspace-view="campaigns" hidden>
<div class="view-heading"><h2>Campaigns</h2><p>Manage your target audience and choose a campaign for your next search.</p></div>
<div class="toolbar"><button type="button" class="btn primary" id="workspaceNewCampaign">New campaign</button><button type="button" class="btn" id="refreshCampaignList">Refresh campaigns</button></div>
<div class="view-status" id="campaignListStatus" role="status"></div>
<div class="table-wrap"><table><thead><tr><th>Campaign</th><th>Audience</th><th>Search limits</th><th>Actions</th></tr></thead><tbody id="workspaceCampaignRows"></tbody></table></div>
</section>
<section class="panel" id="outreachView" data-workspace-view="outreach" hidden>
<div class="view-heading"><h2>Outreach review</h2><p>Review evidence, edit message drafts and copy approved drafts for manual sending. Approval does not send a message.</p></div>
<div class="workspace-grid"><div><label for="outreachSearch">Find a lead</label><input id="outreachSearch" type="search" placeholder="Name, company or location"></div><div><label for="outreachStatus">Review status</label><select id="outreachStatus"><option value="">All statuses</option></select></div></div>
<div class="toolbar"><button type="button" class="btn" id="refreshOutreach">Refresh leads</button><span class="sub" id="outreachCount" role="status"></span></div>
<div class="table-wrap"><table><thead><tr><th>Lead</th><th>Status / score</th><th>Connection draft</th><th>Follow-up draft</th><th>Actions</th></tr></thead><tbody id="workspaceOutreachRows"></tbody></table></div><div class="empty" id="outreachEmpty" hidden>No leads match these filters.</div>
</section>
<section class="panel" id="reportsView" data-workspace-view="reports" hidden>
<div class="view-heading"><h2>Reports</h2><p>Download saved leads or collected profiles, including rejected profiles. Filters below apply only to these downloads.</p></div>
<div class="workspace-grid"><div><label for="reportRun">Research run</label><select id="reportRun"><option value="">All runs</option></select></div><div><label for="reportLeadStatus">Lead review status</label><select id="reportLeadStatus"><option value="">All statuses</option></select></div><div><label for="reportQuery">Lead name, company, role or location</label><input id="reportQuery" type="search" placeholder="Filter saved leads"></div><div><label for="reportDecision">Profile collection decision</label><select id="reportDecision"><option value="all">All collected profiles</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option></select></div></div>
<div class="workspace-grid"><div class="report-card"><h3>Saved leads</h3><p class="sub">Includes qualification, review status, evidence and message drafts.</p><div class="view-actions"><button type="button" class="btn" data-report="leads" data-format="csv">Download leads CSV</button><button type="button" class="btn" data-report="leads" data-format="xlsx">Download leads Excel</button></div></div><div class="report-card"><h3>Collected profiles</h3><p class="sub">Includes collection decisions, rejection reasons and missing fields.</p><div class="view-actions"><button type="button" class="btn" data-report="observations" data-format="csv">Download profiles CSV</button><button type="button" class="btn" data-report="observations" data-format="xlsx">Download profiles Excel</button></div></div></div>
<div class="toolbar"><strong>Research history</strong><button type="button" class="btn" id="refreshReportRuns">Refresh history</button></div><div class="view-status" id="reportStatus" role="status"></div>
<div class="table-wrap"><table><thead><tr><th>Campaign / run</th><th>Created</th><th>Run status</th><th>Read</th><th>Accepted</th><th>Rejected</th></tr></thead><tbody id="workspaceRunRows"></tbody></table></div>
</section>
"""

WORKSPACE_SCRIPT = r"""<script>
document.addEventListener('DOMContentLoaded',()=>{
const main=document.querySelector('main'),title=document.querySelector('.topbar h1'),subtitle=document.querySelector('.topbar p');
const originalTitle=title.textContent,originalSubtitle=subtitle.textContent;
for(const child of main.children)if(child.tagName==='SECTION')child.dataset.workspaceView='pipeline';
main.insertAdjacentHTML('beforeend',__PANELS__);
const nav=[...document.querySelectorAll('[data-view]')],views=new Set(['pipeline','campaigns','outreach','reports']);
let current='pipeline',campaignSerial=0,reportSerial=0;
const el=id=>document.getElementById(id);
function cell(row,text){const td=document.createElement('td');td.textContent=text??'';row.append(td);return td}
function button(text,action){const b=document.createElement('button');b.type='button';b.className='icon-btn';b.textContent=text;b.onclick=action;return b}
function chooseCampaign(id){const select=el('searchCampaign');if(!select){notice('Campaign search is available on the main collection dashboard.');return false}select.value=id;select.dispatchEvent(new Event('change'));return true}
async function campaigns(){
const serial=++campaignSerial,status=el('campaignListStatus');status.textContent='Loading campaigns…';
try{const r=await fetch('/api/search/settings');if(!r.ok)throw Error('Campaign management is available on the collection dashboard.');const data=await r.json();if(serial!==campaignSerial)return;
const rows=el('workspaceCampaignRows');rows.replaceChildren();
for(const item of data.campaigns){const row=document.createElement('tr');cell(row,item.name);cell(row,item.criteria);cell(row,item.profile_limit+' profiles · '+item.page_limit+' pages per search');const actions=cell(row,'');const buttons=document.createElement('div');buttons.className='view-actions';buttons.append(button('Edit campaign',()=>{if(chooseCampaign(item.id))openCampaign(true)}),button('Use for search',()=>{if(chooseCampaign(item.id)){location.hash='pipeline';el('searchButton').focus()}}));actions.append(buttons);rows.append(row)}
status.textContent=data.campaigns.length?data.campaigns.length+' campaigns. Changes apply to new searches; past runs keep their original rules.':'No campaigns available. Create a campaign to get started.';
}catch(error){if(serial===campaignSerial)status.textContent=error.message}}
function outreach(){
const query=el('outreachSearch').value.toLowerCase(),status=el('outreachStatus').value;
const items=leads.filter(x=>(!status||x.outreach.status===status)&&[x.lead.full_name,x.lead.company,x.lead.current_title,x.lead.headline,x.lead.location].join(' ').toLowerCase().includes(query));
const rows=el('workspaceOutreachRows');rows.replaceChildren();el('outreachCount').textContent=items.length+' of '+leads.length+' saved leads';el('outreachEmpty').hidden=items.length>0;
for(const item of items){const l=item.lead,o=item.outreach,row=document.createElement('tr');cell(row,l.full_name+'\n'+(l.company||'Unknown company')+' · '+(l.location||'Location not collected'));cell(row,label(o.status)+' · '+o.score+'/100');
for(const value of [o.connection_message,o.follow_up_message]){const td=cell(row,'');const preview=document.createElement('div');preview.className='draft-preview';preview.textContent=value||'No draft saved';td.append(preview)}
const actions=cell(row,''),buttons=document.createElement('div');buttons.className='view-actions';buttons.append(button('Review / edit drafts',()=>openEditor(l.id)));
const approved=['approved','connection_queued','connection_sent','connected','message_ready','message_sent'].includes(o.status);
for(const [name,value] of [['Copy connection',o.connection_message],['Copy follow-up',o.follow_up_message]]){const copy=button(name,async()=>{try{await navigator.clipboard.writeText(value);notice('Draft copied. Sending remains manual.')}catch{notice('Open Review / edit drafts to select and copy the message.')}});copy.disabled=!approved||!value;copy.title=approved?'Copy a draft for manual sending':'Review and approve the lead before copying outreach';buttons.append(copy)}
actions.append(buttons);rows.append(row)}}
const baseRender=render;render=function(){baseRender();outreach()};
for(const id of ['outreachStatus','reportLeadStatus'])for(const s of statuses)el(id).add(new Option(label(s),s));
el('outreachSearch').oninput=outreach;el('outreachStatus').onchange=outreach;
el('refreshOutreach').onclick=()=>load().catch(()=>notice('Could not load leads. Check connection and retry.'));
async function reports(){
const serial=++reportSerial,status=el('reportStatus');status.textContent='Loading research history…';
try{const r=await fetch('/api/research/runs');if(!r.ok)throw Error('Could not load research history');const runs=await r.json();if(serial!==reportSerial)return;
const select=el('reportRun'),selected=select.value;select.replaceChildren(new Option('All runs',''));const rows=el('workspaceRunRows');rows.replaceChildren();
for(const run of runs){select.add(new Option(run.campaign+' · '+run.id.slice(0,8),run.id));const row=document.createElement('tr');cell(row,run.campaign+' · '+run.id.slice(0,8));cell(row,new Date(run.created_at).toLocaleString());cell(row,label(run.status));for(const key of ['read','accepted','rejected'])cell(row,run[key]);rows.append(row)}
select.value=[...select.options].some(x=>x.value===selected)?selected:'';status.textContent=runs.length?runs.length+' research runs. Downloads include all matching rows across pages, up to 5,000.':'No research runs yet. Start a search from Lead pipeline.';
}catch(error){if(serial===reportSerial)status.textContent=error.message}}
async function exportReport(b){
b.disabled=true;const status=el('reportStatus');
try{const dataset=b.dataset.report,format=b.dataset.format,query=new URLSearchParams();if(el('reportRun').value)query.set('run_id',el('reportRun').value);
if(dataset==='leads'){if(el('reportLeadStatus').value)query.set('status',el('reportLeadStatus').value);if(el('reportQuery').value)query.set('q',el('reportQuery').value)}else query.set('decision',el('reportDecision').value);
const r=await fetch('/api/exports/'+dataset+'.'+format+'?'+query);if(!r.ok){const data=await r.json();throw Error(typeof data.detail==='string'?data.detail:'Could not export; check filters')}
const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='linkedin-'+dataset+'.'+format;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='Downloaded '+r.headers.get('X-Export-Row-Count')+' matching rows.';
}catch(error){status.textContent=error.message}finally{b.disabled=false}}
for(const b of document.querySelectorAll('[data-report]'))b.onclick=()=>exportReport(b);
el('workspaceNewCampaign').onclick=()=>{if(typeof openCampaign==='function')openCampaign(false);else notice('Open the collection dashboard to create a campaign.')};
el('refreshCampaignList').onclick=campaigns;el('refreshReportRuns').onclick=reports;
if(el('campaignDialog'))el('campaignDialog').addEventListener('close',()=>{if(current==='campaigns')campaigns()});
function navigate(){
const requested=location.hash.slice(1);current=views.has(requested)?requested:'pipeline';if(!views.has(requested))history.replaceState(null,'','#pipeline');
for(const section of main.children)if(section.dataset.workspaceView)section.hidden=section.dataset.workspaceView!==current;
for(const item of nav){const active=item.dataset.view===current;item.classList.toggle('active',active);if(active)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current')}
const headings={pipeline:[originalTitle,originalSubtitle],campaigns:['Campaigns','Define who you want to reach'],outreach:['Outreach','Review leads and prepare messages'],reports:['Reports','Research history and filtered downloads']};[title.textContent,subtitle.textContent]=headings[current];
if(current==='campaigns')campaigns();if(current==='outreach'){outreach();load().catch(()=>notice('Could not load leads. Check connection and retry.'))}if(current==='reports')reports();}
window.addEventListener('hashchange',navigate);navigate();
});
</script>""".replace("__PANELS__", json.dumps(WORKSPACE_PANELS))


def install_workspace_navigation(html: str) -> str:
    start = '<div><div class="nav-label">Workspace</div>'
    end = '</div><div class="scope">'
    if start in html:
        first = html.index(start)
        last = html.index(end, first)
        html = html[:first] + WORKSPACE_NAV + html[last + len("</div>") :]
    return html.replace("</style>", "</style>" + WORKSPACE_STYLE, 1).replace(
        "</body>", WORKSPACE_SCRIPT + "</body>"
    )
