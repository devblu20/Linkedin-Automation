"""The automatic search form shared by local and hosted dashboards."""

# ruff: noqa: E501 - embedded dashboard asset.

from linkedin_automation.interfaces.campaign_editor import CAMPAIGN_DIALOG, CAMPAIGN_SCRIPT
from linkedin_automation.interfaces.worker_controls import WORKER_PANEL, WORKER_SCRIPT

SEARCH_PANEL = """<section class="panel" id="automaticSearch">
<div class="toolbar" style="flex-wrap:wrap"><div class="search"><strong>Campaign requirements</strong><div class="sub">Choose an existing campaign or create your own.</div></div><button class="btn" id="newCampaign" type="button">New campaign</button><button class="btn" id="editCampaign" type="button">Edit selected campaign</button></div>
<form id="searchForm" class="toolbar">
<div class="search"><label for="searchCampaign">Find matching LinkedIn profiles</label><select id="searchCampaign" aria-label="Search campaign" required style="width:100%"><option value="">Choose campaign</option></select></div>
<div><label for="searchLimit">Profile limit</label><input id="searchLimit" type="number" min="1" max="100" value="5" required style="width:90px;padding:10px;border:1px solid #d0d5dd;border-radius:8px"></div>
<div><label for="searchPages">Search pages</label><input id="searchPages" type="number" min="1" max="5" value="3" required style="width:75px;padding:10px;border:1px solid #d0d5dd;border-radius:8px"></div>
<button class="btn primary" id="searchButton" type="submit">Search and fetch</button>
<div id="testProfileBox" hidden style="width:100%"><label for="testProfileUrl">Approved test profile URL (optional)</label><input id="testProfileUrl" type="url" maxlength="2048" style="width:100%"><small class="sub">Controlled test campaign only. Fetches this exact profile; normal campaign qualification and delivery approval still apply.</small></div></form><p id="searchCriteria" style="padding:0 19px;margin:0">Select a campaign to see its search criteria.</p>
<p id="workerStatus" role="status" style="padding:10px 19px;margin:0">Checking browser worker…</p>
<p id="searchDraft" role="status" style="padding:0 19px 10px;margin:0">Profile limit caps profile visits, including unreadable profiles. Saved leads depend on matching. No outreach is sent.</p>
<p id="searchPlan" style="padding:0 19px 10px;margin:0">Choose a campaign to preview its search queries.</p>
<p id="searchRunLabel" style="padding:0 19px 5px;margin:0;font-weight:700"></p>
<p id="searchResult" role="status" style="padding:0 19px 16px;margin:0">No search run selected yet.</p>
<div style="padding:0 19px 16px"><button class="btn" id="resumeSearch" type="button" hidden>Resume interrupted search</button><span class="sub" id="resumeHint" hidden> Uses the original campaign, remaining limits and checkpoint on the original worker computer.</span></div>
</section>"""

SEARCH_SCRIPT = """<script>
const searchForm=document.getElementById('searchForm'),searchButton=document.getElementById('searchButton'),searchResult=document.getElementById('searchResult'),searchCampaign=document.getElementById('searchCampaign'),workerStatus=document.getElementById('workerStatus'),searchDraft=document.getElementById('searchDraft');let campaignOptions=[],submitting=false;
function showSearchJob(job){
if(!job)return;const result=job.result||{},requested=job.requested||{};
const resume=document.getElementById('resumeSearch');resume.hidden=!(job.status==='failed'&&result.resume_available);document.getElementById('resumeHint').hidden=resume.hidden;resume.dataset.job=job.id;
document.getElementById('searchRunLabel').textContent=`Last search: ${job.campaign} · limit ${requested.profiles??'?'} profiles · up to ${requested.pages??'?'} pages · run ${job.id.slice(0,8)}`;
let text='';
if(job.status==='queued')text=deviceState.mode==='cloud'?'Cloud search queued. No browser worker is needed.':'Search queued. It will start when your browser worker is available.';
else if(job.status==='running')text=deviceState.mode==='cloud'?'Searching LinkedIn in the cloud and reading profiles.':'Searching LinkedIn and reading profiles. Please leave the worker browser open.';
else if(job.status==='completed')text=`Completed: ${result.observed||0} profiles read, ${result.accepted||0} saved, ${result.rejected||0} rejected. ${result.message||''}`;
else text=result.message||'Search stopped. Check browser access and retry.';
if(result.pages_visited)text+=` Checked ${result.pages_visited} search pages.`;
if(result.hidden_result_cards)text+=` LinkedIn hid ${result.hidden_result_cards} result cards (may repeat across searches).`;
const reasons=Object.entries(result.rejection_counts||{}).map(([reason,count])=>`${reason}: ${count}`);if(reasons.length)text+=' Rejection reasons — '+reasons.join('; ')+'.';
searchResult.textContent=text;
}
document.getElementById('resumeSearch').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await fetch('/api/search/jobs/'+b.dataset.job+'/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const data=await r.json();if(!r.ok)throw Error(data.detail||'Could not resume');showSearchJob(data);searchDraft.textContent='Resume queued. Keep the original worker computer online.';}catch(error){searchDraft.textContent=error.message}finally{b.disabled=false;await refreshSearch()}};
async function refreshSearch(){try{const r=await fetch('/api/search/settings');if(!r.ok)throw Error('Cannot load search settings');const state=await r.json();const selected=searchCampaign.value;if(JSON.stringify(state.campaigns)!==JSON.stringify(campaignOptions)){campaignOptions=state.campaigns;searchCampaign.replaceChildren(new Option('Choose campaign',''));for(const item of campaignOptions)searchCampaign.add(new Option(item.name,item.id));searchCampaign.value=selected;}workerStatus.textContent=state.mode==='local'?'Uses your saved LinkedIn login on this computer.':state.worker_online?(state.jobs||[]).some(j=>j.status==='running')?'Browser worker online — search in progress.':'Browser worker online — ready to run searches.':(state.jobs||[]).some(j=>j.status==='running')?'Browser worker disconnected. Completed results will sync when it reconnects; an unfinished search may expire.':'Browser worker offline. Searches will wait until it is started on your computer.';const active=(state.jobs||[]).some(j=>['queued','running'].includes(j.status));searchButton.disabled=submitting||active||!campaignOptions.length;if(state.jobs?.length)showSearchJob(state.jobs[0]);if(!active)await Promise.all([load(),loadSummary()]);}catch(error){workerStatus.textContent=error.message;}}
function renderSearchPlan(){const item=campaignOptions.find(x=>x.id===searchCampaign.value),limit=Number(document.getElementById('searchPages').value);const direct=item?.controlled_test&&document.getElementById('testProfileUrl').value.trim();searchButton.textContent=direct?'Fetch test profile':'Search and fetch';if(direct){document.getElementById('searchPlan').textContent='Known-profile test lookup: '+direct;return;}document.getElementById('searchPlan').textContent=item?'Planned searches: '+(item.queries||[]).slice(0,limit).map(q=>`${q.term} (page ${q.page})`).join(' → '):'Choose a campaign to preview its search queries.';}
function changedSearch(){searchDraft.textContent='Settings changed. Click Search and fetch to start a new run; the last result below is unchanged.';renderSearchPlan();}
searchCampaign.onchange=()=>{const item=campaignOptions.find(x=>x.id===searchCampaign.value);document.getElementById('searchCriteria').textContent=item?.criteria||'Campaign rules determine which profiles are accepted.';document.getElementById('testProfileBox').hidden=!item?.controlled_test;if(!item?.controlled_test)document.getElementById('testProfileUrl').value='';const profile=document.getElementById('searchLimit'),pages=document.getElementById('searchPages');profile.max=item?.profile_limit||100;pages.max=item?.page_limit||5;profile.value=item?.default_profiles||5;pages.value=item?.default_pages||3;changedSearch();};
document.getElementById('testProfileUrl').oninput=changedSearch;document.getElementById('searchLimit').oninput=changedSearch;document.getElementById('searchPages').oninput=changedSearch;
searchForm.onsubmit=async e=>{e.preventDefault();submitting=true;searchButton.disabled=true;searchDraft.textContent='Starting automatic LinkedIn search…';try{const r=await fetch('/api/search/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign:searchCampaign.value,max_results:Number(document.getElementById('searchLimit').value),max_pages:Number(document.getElementById('searchPages').value),...(campaignOptions.find(x=>x.id===searchCampaign.value)?.controlled_test&&document.getElementById('testProfileUrl').value.trim()?{profile_url:document.getElementById('testProfileUrl').value.trim()}:{})})});const job=await r.json();if(!r.ok)throw Error(typeof job.detail==='string'?job.detail:'Check campaign, profile limit and search pages');showSearchJob(job);searchDraft.textContent='Search submitted. The selected profile limit is a maximum, not a guaranteed number of matches.';}catch(error){searchDraft.textContent=error.message;}finally{submitting=false;await refreshSearch();}};
refreshSearch();setInterval(refreshSearch,5000);
</script>"""

SEARCH_PANEL += CAMPAIGN_DIALOG
SEARCH_PANEL = SEARCH_PANEL.replace('<p id="searchDraft"', WORKER_PANEL + '<p id="searchDraft"', 1)
SEARCH_SCRIPT = SEARCH_SCRIPT.replace(
    "const state=await r.json();const selected",
    "const state=await r.json();if(window.renderWorkerManagement)"
    "window.renderWorkerManagement(state);const selected",
    1,
)
SEARCH_SCRIPT += CAMPAIGN_SCRIPT + WORKER_SCRIPT
SEARCH_SCRIPT = SEARCH_SCRIPT.replace(
    "let campaignOptions=[],submitting=false;",
    "let campaignOptions=[],submitting=false,deviceState={};",
).replace(
    "const state=await r.json();",
    "const state=await r.json();deviceState=state;",
).replace(
    "const active=(state.jobs||[])",
    "if(state.worker_linkedin_paused)workerStatus.textContent='Browser worker running. LinkedIn jobs are paused until sign-in or verification is complete.';if(state.device_required&&!state.device_paired)workerStatus.textContent='Connect this computer before searching. LinkedIn will open on this PC.';if(state.mode==='cloud')workerStatus.textContent=state.cloud_connected?'Cloud research connected. No local worker is needed.':'Connect LinkedIn to start cloud research.';const active=(state.jobs||[])",
).replace(
    "e.preventDefault();submitting=true;",
    "e.preventDefault();if(deviceState.device_required&&!deviceState.device_paired){location.assign('/linkedin-automation/connect-computer');return;}if(deviceState.device_required&&!deviceState.worker_online){searchDraft.textContent='Start the helper on this PC, accept the browser Open prompt, then try Search and fetch again.';location.href='bluqq-desktop-worker://start';return;}submitting=true;",
)
SEARCH_SCRIPT = SEARCH_SCRIPT.replace(
    "searchButton.disabled=submitting||active||!campaignOptions.length;",
    "searchButton.disabled=submitting||active||!campaignOptions.length||(state.mode==='cloud'&&!state.cloud_connected);",
)
